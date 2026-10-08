"""
morning_brief.py — «خلاصه صبحگاهی» مدیر ارشد

اجزا:
  ۱) زمان‌بندی دقیق (با precise_scheduler روی لحظه‌ی مطلق، مقاوم در برابر ری‌استارت)
  ۲) پیام صبحگاهی کوتاه (مسابقه‌های دیشب + هوا + آلودگی + پیشنهاد لباس) + دکمه‌ی «روز من رو خلاصه کن»
  ۳) تحلیل کامل با هوش مصنوعی (رهگشا) با رعایت «قانون‌های خلاصه صبحگاهی»
  ۴) تنظیمات: روشن/خاموش، ساعت‌های ارسال (با دقیقه)، تعداد = تعداد ساعت‌ها
  ۵) قانون‌های رهگشا: ثبت/لیست/ویرایش/حذف (ابزارهای هوش مصنوعی + دکمه)

معادله‌ی زمان‌بندی (دقیق، بدون انحراف):
    target = تهران( تاریخ + HH:MM:00 )      ← اگر target <= الان، همان ساعت «فردا»
    next   = min(target_i)  روی همه‌ی ساعت‌های فعال
    job_queue.run_once(callback, when=next)  ← ساعتِ مطلق، نه شمارش معکوس
"""
import asyncio
import html as _html
import logging
import re
import time as _time
from datetime import datetime, timedelta, time as dtime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (CallbackQueryHandler, ContextTypes, ConversationHandler,
                          MessageHandler, filters)

import database as db
import precise_scheduler as sched
import turso_db as aiosqlite
from config import DB_PATH, PISHVA_ID
from helpers import TEHRAN_TZ, normalize_digits, today_shamsi, weekday_fa

logger = logging.getLogger(__name__)

# ─── تنظیمات ─────────────────────────────────────────────────────
KEY_ENABLED = "mb_enabled"      # "1" / "0"
KEY_TIMES = "mb_times"          # "07:00,12:30"
KEY_NAME = "mb_name"            # نامی که در سلام می‌آید
KEY_DAYS = "mb_days"            # روزهای فعال هفته؛ weekday پایتون (دوشنبه=0 ... شنبه=5، یکشنبه=6)، مثل "5,6,0,1,2"
DEFAULT_DAYS = [5, 6, 0, 1, 2]  # شنبه تا چهارشنبه؛ پنجشنبه و جمعه پیش‌فرض خاموش
WD_ORDER = [5, 6, 0, 1, 2, 3, 4]  # ترتیب نمایش: شنبه ... جمعه
WD_FA = {5: "شنبه", 6: "یکشنبه", 0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنجشنبه", 4: "جمعه"}
MAX_LOOKBACK_DAYS = 7
DEFAULT_TIMES = ["07:00"]
DEFAULT_NAME = "آقای کریمی"
MAX_TIMES = 6
JOB_NAME = "morning_brief"
STALE_SECONDS = 30 * 60         # اگر ربات موقع خاموشی سررسید را از دست داد و بیش از ۳۰ دقیقه گذشته، پیام کهنه ارسال نمی‌شود
AI_COOLDOWN = 20                # ثانیه بین دو بار زدنِ «روز من رو خلاصه کن»

ST_ADD_TIME = 9101

_last_day_click = 0.0


# ═════════════════════════════════════════════════════════════════
# دیتابیس: قانون‌های رهگشا
# ═════════════════════════════════════════════════════════════════
_table_ready = False


async def _ensure_tables():
    global _table_ready
    if _table_ready:
        return
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS brief_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        await conn.commit()
    _table_ready = True


async def _rows(sql: str, params=()):
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


async def list_rules():
    await _ensure_tables()
    return await _rows("SELECT id, content, created_at, updated_at FROM brief_rules ORDER BY id")


async def add_rule(content: str) -> int:
    await _ensure_tables()
    now = datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d %H:%M")
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO brief_rules(content, created_at, updated_at) VALUES (?,?,?)", (content, now, now))
        await conn.commit()
        return cur.lastrowid


async def update_rule(rule_id: int, content: str) -> bool:
    await _ensure_tables()
    now = datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d %H:%M")
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("UPDATE brief_rules SET content=?, updated_at=? WHERE id=?",
                                 (content, now, rule_id))
        await conn.commit()
        return (cur.rowcount or 0) > 0


async def delete_rule(rule_id: int) -> bool:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute("DELETE FROM brief_rules WHERE id=?", (rule_id,))
        await conn.commit()
        return (cur.rowcount or 0) > 0


def _norm(s: str) -> str:
    return normalize_digits((s or "").replace("ي", "ی").replace("ك", "ک")).lower()


async def _find_rules(query: str):
    """قانون‌هایی که همه‌ی کلمه‌های query در متنشان هست."""
    toks = [t for t in _norm(query).split() if t]
    if not toks:
        return []
    return [r for r in await list_rules() if all(t in _norm(r["content"]) for t in toks)]


def _fmt_rules(rows) -> str:
    return "\n".join(f"- #{r['id']}: {r['content']}" for r in rows)


# ─── ابزارهای هوش مصنوعی (رهگشا) ─────────────────────────────────
_STR = {"type": "string"}

TOOL_PERMISSIONS = {
    "brief_rule_add": ["pishva"],
    "brief_rule_list": ["pishva"],
    "brief_rule_edit": ["pishva"],
    "brief_rule_delete": ["pishva"],
    "brief_days_get": ["pishva"],
    "brief_days_set": ["pishva"],
    "brief_times_get": ["pishva"],
    "brief_times_set": ["pishva"],
    "brief_now": ["pishva"],
}

CATEGORY = ("morning_brief", "🌅 قانون‌های خلاصه صبحگاهی",
            ["brief_rule_add", "brief_rule_list", "brief_rule_edit", "brief_rule_delete",
             "brief_days_get", "brief_days_set", "brief_times_get", "brief_times_set", "brief_now"])

TOOL_DECLARATIONS = [
    {
        "name": "brief_rule_add",
        "description": (
            "یک قانون/نکته‌ی دائمی برای «خلاصه صبحگاهی» ثبت می‌کنه؛ از این به بعد در هر خلاصه‌ی صبحگاهی "
            "دقیقاً رعایت می‌شه. هر وقت مدیر ارشد چیزی شبیه «به یاد بسپار توی خلاصه صبحگاهی فلان رو رعایت کنی» "
            "یا «توی خلاصه‌ی صبح همیشه فلان کار رو بکن / فلان رو نگو» گفت، همیشه و بی‌درنگ همین تابع رو صدا بزن "
            "(نه remember_note؛ remember_note فقط برای یادداشت‌های عمومیه).\n"
            "مهم: content باید یک دستور کامل، روشن و مستقل باشه که بدون دیدن گفتگو هم دقیقاً قابل اجرا باشه؛ "
            "منظور مدیر رو بدون کم‌وزیاد کردن و بدون تفسیر شخصی بازنویسی کن و کلمه‌های کلیدیِ خودش رو نگه دار "
            "(مثلاً «اول متن، وضعیت آلودگی هوا رو با عدد AQI بگو»). اگه منظور مبهم بود، قبل از ثبت یک سؤال کوتاه بپرس. "
            "بعد از ثبت، متنِ دقیقِ ثبت‌شده رو به مدیر نشون بده."
        ),
        "parameters": {"type": "object", "properties": {
            "content": {**_STR, "description": "متن کامل و روشن قانون"}}, "required": ["content"]},
    },
    {
        "name": "brief_rule_list",
        "description": "فهرست همه‌ی قانون‌های ثبت‌شده‌ی خلاصه صبحگاهی (با شناسه). وقتی مدیر پرسید «چه نکته‌هایی برای خلاصه صبحگاهی یادته؟» صدا بزن.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "brief_rule_edit",
        "description": (
            "متن یک قانون خلاصه صبحگاهی رو ویرایش می‌کنه. وقتی مدیر گفت «اون نکته‌ی فلان رو عوض کن/ویرایش کن» صدا بزن. "
            "با rule_id (اگه دقیق می‌دونی) یا با query (کلمه‌های مرتبط) قانون رو پیدا کن؛ اگه query چند قانون "
            "پیدا کرد، فهرست رو نشون بده و بپرس کدوم؛ خودسرانه حدس نزن. content متنِ جدیدِ کامل و روشنه."
        ),
        "parameters": {"type": "object", "properties": {
            "rule_id": {"type": "integer"}, "query": _STR, "content": _STR}, "required": ["content"]},
    },
    {
        "name": "brief_rule_delete",
        "description": (
            "یک قانون خلاصه صبحگاهی رو واقعاً پاک می‌کنه. وقتی مدیر گفت «اون نکته رو از یاد ببر/فراموش کن/حذف کن» "
            "صدا بزن. با rule_id یا query؛ اگه چند قانون پیدا شد فهرست رو نشون بده و بپرس کدوم."
        ),
        "parameters": {"type": "object", "properties": {"rule_id": {"type": "integer"}, "query": _STR}},
    },
    {
        "name": "brief_times_get",
        "description": "ساعت‌های ارسال «خلاصه صبحگاهی» (با دقیقه)، روزها و روشن/خاموش بودنش و زمان ارسال بعدی رو نشون می‌ده.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "brief_times_set",
        "description": (
            "ساعت‌های ارسال «خلاصه صبحگاهی» رو ویرایش می‌کنه. هر وقت مدیر گفت «ساعت خلاصه صبحگاهی رو بذار ۶:۴۵»، "
            "«یه ساعت ۱۲:۳۰ هم اضافه کن»، «ساعت ۷ رو بردار»، «ساعت خلاصه رو از ۷ به ۶:۳۰ تغییر بده» یا مشابه، همین رو صدا بزن "
            "(نه brief_rule_add). ساعت‌ها با دقیقه‌ی دقیق (HH:MM، ۰۰ تا ۲۳ و ۰۰ تا ۵۹، ارقام فارسی هم قبوله).\n"
            "mode: «replace» = کل ساعت‌ها بشه همین لیست (برای «ساعت رو بذار …»)؛ «add» = به ساعت‌های فعلی اضافه بشه؛ "
            "«remove» = از ساعت‌های فعلی حذف بشه؛ «change» = ساعتِ from_time به to_time تغییر کنه (برای «از ۷ به ۶:۳۰ ببر»). "
            "حداکثر ۶ ساعت. بعد از اجرا ساعت‌های جدید و زمان ارسال بعدی گزارش می‌شه؛ همون رو دقیق به مدیر بگو."
        ),
        "parameters": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["replace", "add", "remove", "change"]},
            "times": {"type": "array", "items": _STR, "description": "ساعت‌ها، مثلاً [\"06:45\"] (برای replace/add/remove)"},
            "from_time": {**_STR, "description": "فقط برای change: ساعتِ فعلی"},
            "to_time": {**_STR, "description": "فقط برای change: ساعتِ جدید"}},
            "required": ["mode"]},
    },
    {
        "name": "brief_now",
        "description": (
            "«همین الان خلاصه کن» — خلاصه صبحگاهی رو همین لحظه، با داده‌های زنده‌ی فعلی، می‌سازه و برای مدیر ارشد می‌فرسته؛ "
            "بدون توجه به روشن/خاموش بودنِ خلاصه صبحگاهی، ساعت‌ها، روزهای تعطیل یا ارسال بعدی (هیچ‌کدوم دست نمی‌خورن) و "
            "همراهش دکمه‌ی «منوی خلاصه صبحگاهی» رو زیر پیام می‌ذاره. وقتی مدیر گفت «همین الان خلاصه کن»، «خلاصه صبحگاهی رو "
            "الان بده/باز کن»، «همین الان خلاصه‌ی روز رو بگو» صدا بزن؛ هیچ سؤالی نپرس. "
            "full=true یعنی تحلیل کاملِ هوش مصنوعی (دکمه‌ی «روز من رو خلاصه کن») هم همین الان ساخته بشه."
        ),
        "parameters": {"type": "object", "properties": {"full": {"type": "boolean", "description": "تحلیل کاملِ هوش مصنوعی هم ساخته بشه (پیش‌فرض false)"}}},
    },
    {
        "name": "brief_days_get",
        "description": "نشان می‌دهد خلاصه صبحگاهی در کدام روزهای هفته ارسال می‌شود (و ساعت‌ها و روشن/خاموش بودنش).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "brief_days_set",
        "description": (
            "روزهای هفته‌ی ارسال «خلاصه صبحگاهی» را تغییر می‌دهد. وقتی مدیر گفت «پنجشنبه تعطیله و نفرست»، «جمعه‌ها خلاصه نخوایم»، "
            "«از این به بعد یکشنبه هم بفرست» یا مشابه، همین را صدا بزن (نه brief_rule_add). "
            "enabled=false یعنی در این روزها ارسال نشود (تعطیل)، enabled=true یعنی ارسال شود. "
            "days نام‌های فارسی روزها: شنبه، یکشنبه، دوشنبه، سه‌شنبه، چهارشنبه، پنجشنبه، جمعه. "
            "اگر مدیر گفت «همه‌ی روزها» همه‌ی ۷ نام را بده. توجه: این فقط برای روزهای هفته‌ی تکرارشونده است، نه یک تاریخ مشخص."
        ),
        "parameters": {"type": "object", "properties": {
            "days": {"type": "array", "items": _STR, "description": "نام روزها"},
            "enabled": {"type": "boolean", "description": "true = ارسال شود، false = ارسال نشود (تعطیل)"}},
            "required": ["days", "enabled"]},
    },
]


async def _resolve(args):
    """(rule_row | None, پیام خطا/ابهام | None)"""
    rid = args.get("rule_id")
    if rid is not None:
        try:
            rid = int(rid)
        except (TypeError, ValueError):
            return None, "❌ rule_id باید عدد باشه."
        for r in await list_rules():
            if r["id"] == rid:
                return r, None
        return None, f"❌ قانونی با شناسه‌ی #{rid} پیدا نشد."
    q = (args.get("query") or "").strip()
    if not q:
        return None, "بگو کدوم قانون رو منظورته (کلمه‌های مرتبط یا شناسه)."
    found = await _find_rules(q)
    if not found:
        return None, f"قانونی با «{q}» پیدا نشد.\nقانون‌های فعلی:\n{_fmt_rules(await list_rules()) or '— هیچ —'}"
    if len(found) > 1:
        return None, "چند قانون مشابه پیدا شد؛ دقیق بگو کدوم (با شناسه‌ی #):\n" + _fmt_rules(found)
    return found[0], None


async def _times_report(job_queue=None):
    times = await get_times()
    days = await get_days()
    en = await is_enabled()
    nxt = next_target(datetime.now(TEHRAN_TZ), times, days) if (en and times and days) else None
    return (f"ساعت‌های ارسال: {'، '.join(times) or '— هیچ —'}\n"
            f"روزها: {'، '.join(WD_FA[d] for d in WD_ORDER if d in days) or 'هیچ روزی'}\n"
            f"وضعیت: {'روشن' if en else 'خاموش'}\n"
            f"ارسال بعدی: {nxt.strftime('%Y-%m-%d %H:%M') if nxt else '—'}"
            + ("" if en else "\n⚠️ خلاصه صبحگاهی الان خاموشه؛ ساعت‌ها ثبت می‌شن ولی تا روشن نشه ارسالی نیست."))


async def _brief_now(ctx, full: bool):
    """«همین الان خلاصه کن»: با داده‌های فعلی، بدون توجه به روشن/خاموش، ساعت‌ها و روزها."""
    bot = ctx.bot
    await bot.send_message(PISHVA_ID, await build_brief_text(), reply_markup=_brief_keyboard())
    if full:
        context_text = await _collect_context()
        rules = await list_rules()
        try:
            text, ok = await _ai_summary(context_text, rules), True
        except Exception as e:
            logger.warning("morning_brief: brief_now AI failed (%r)", e)
            text, ok = "⚠️ تحلیل هوش مصنوعی الان ممکن نشد؛ این هم داده‌های خام امروز:\n\n" + context_text, False
        for part in _chunks(text):
            if not ok:
                await bot.send_message(PISHVA_ID, part)
                continue
            try:
                await bot.send_message(PISHVA_ID, _to_tg_html(part), parse_mode="HTML")
            except BadRequest:
                await bot.send_message(PISHVA_ID, _plain(part))
    # دکمه‌ی منوی خلاصه صبحگاهی زیر پیام رهگشا
    ctx.user_data.setdefault("_ai_pending_buttons", []).append(("🌅 منوی خلاصه صبحگاهی", "pishva_brief"))
    return ("✅ خلاصه صبحگاهی همین الان با داده‌های فعلی ساخته و فرستاده شد"
            + (" (همراه با تحلیل کامل)" if full else " (برای تحلیل کامل، دکمه‌ی «✨ روز من رو خلاصه کن» زیرش هست)")
            + ". تنظیمات و ساعت‌های ارسال دست نخورد؛ دکمه‌ی منوی خلاصه صبحگاهی هم پایین پیام هست.")


async def dispatch_tool(name: str, args: dict, job_queue=None, ctx=None):
    args = args or {}
    if name == "brief_now":
        if ctx is None:
            return "❌ این ابزار الان قابل اجرا نیست."
        return await _brief_now(ctx, bool(args.get("full")))
    if name == "brief_times_get":
        return await _times_report()
    if name == "brief_times_set":
        mode = (args.get("mode") or "").strip().lower()
        cur = list(await get_times())

        def parse_all(vals):
            ok, bad = [], []
            for v in vals or []:
                t = parse_hhmm(str(v))
                (ok if t else bad).append(t or str(v))
            return ok, bad
        if mode == "change":
            f, t = parse_hhmm(str(args.get("from_time") or "")), parse_hhmm(str(args.get("to_time") or ""))
            if not f or not t:
                return "❌ برای تغییر، هم ساعت فعلی (from_time) و هم ساعت جدید (to_time) لازمه؛ مثل 06:45."
            if f not in cur:
                return f"❌ ساعت {f} جزو ساعت‌های فعلی نیست.\n" + await _times_report()
            new = [x for x in cur if x != f]
            if t not in new:
                new.append(t)
            bad = []
        else:
            vals, bad = parse_all(args.get("times"))
            if not vals:
                return "❌ ساعتِ معتبری نگفتی؛ مثل 06:45 (ساعت ۰–۲۳، دقیقه ۰–۵۹)." + (f" (نامعتبر: {'، '.join(bad)})" if bad else "")
            if mode == "replace":
                new = vals
            elif mode == "add":
                new = cur + [v for v in vals if v not in cur]
            elif mode == "remove":
                missing = [v for v in vals if v not in cur]
                new = [x for x in cur if x not in vals]
                if missing and len(missing) == len(vals):
                    return f"❌ {'، '.join(missing)} جزو ساعت‌های فعلی نیست.\n" + await _times_report()
            else:
                return "❌ mode باید یکی از replace، add، remove، change باشه."
        new = sorted(set(new))
        if len(new) > MAX_TIMES:
            return f"❌ حداکثر {MAX_TIMES} ساعت در روز مجازه (الان می‌شد {len(new)})."
        await set_times(new)
        if job_queue is not None:
            await reschedule(job_queue)
        return ("✅ ساعت‌های خلاصه صبحگاهی ویرایش شد.\n" + await _times_report()
                + (f"\n⚠️ نامعتبر و نادیده‌گرفته‌شده: {'، '.join(bad)}" if bad else ""))
    if name == "brief_days_get":
        days, times = await get_days(), await get_times()
        return (f"روزهای ارسال: {'، '.join(WD_FA[d] for d in WD_ORDER if d in days) or 'هیچ روزی'}\n"
                f"ساعت‌ها: {'، '.join(times) or '—'}\nوضعیت: {'روشن' if await is_enabled() else 'خاموش'}")
    if name == "brief_days_set":
        found, unknown = parse_day_names(args.get("days") or [])
        if not found:
            return "❌ نام روز را نفهمیدم؛ یکی از شنبه تا جمعه را بگو." + (f" (نامفهوم: {'، '.join(unknown)})" if unknown else "")
        cur = set(await get_days())
        cur = (cur | found) if args.get("enabled") else (cur - found)
        await set_days(cur)
        if job_queue is not None:
            await reschedule(job_queue)
        names = "، ".join(WD_FA[d] for d in WD_ORDER if d in found)
        now_days = "، ".join(WD_FA[d] for d in WD_ORDER if d in cur) or "هیچ روزی"
        return (f"✅ {names} {'فعال' if args.get('enabled') else 'تعطیل'} شد.\nروزهای ارسال الان: {now_days}"
                + (f"\n⚠️ نامفهوم: {'، '.join(unknown)}" if unknown else ""))
    if name == "brief_rule_add":
        content = (args.get("content") or "").strip()
        if not content:
            return "❌ متن قانون نمی‌تونه خالی باشه."
        rid = await add_rule(content[:1500])
        return f"✅ قانون #{rid} برای خلاصه صبحگاهی ثبت شد:\n«{content[:1500]}»"
    if name == "brief_rule_list":
        rows = await list_rules()
        return ("قانون‌های خلاصه صبحگاهی:\n" + _fmt_rules(rows)) if rows else "هیچ قانونی برای خلاصه صبحگاهی ثبت نشده."
    if name == "brief_rule_edit":
        content = (args.get("content") or "").strip()
        if not content:
            return "❌ متن جدید قانون نمی‌تونه خالی باشه."
        row, msg = await _resolve(args)
        if msg:
            return msg
        await update_rule(row["id"], content[:1500])
        return f"✏️ قانون #{row['id']} ویرایش شد.\nقبلی: «{row['content']}»\nجدید: «{content[:1500]}»"
    if name == "brief_rule_delete":
        row, msg = await _resolve(args)
        if msg:
            return msg
        await delete_rule(row["id"])
        return f"🗑️ قانون #{row['id']} پاک شد: «{row['content']}»"
    return None


# ═════════════════════════════════════════════════════════════════
# تنظیمات و محاسبه‌ی زمان
# ═════════════════════════════════════════════════════════════════
_HHMM = re.compile(r"^\s*(\d{1,2})\s*[:٫.،\-]\s*(\d{1,2})\s*$")


def parse_hhmm(text: str):
    """«۷:5» / «07.05» / «7:05» → '07:05' ؛ نامعتبر → None"""
    m = _HHMM.match(normalize_digits(text or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return f"{h:02d}:{mi:02d}"


async def get_times():
    raw = (await db.get_setting(KEY_TIMES, ",".join(DEFAULT_TIMES))).strip()
    times = sorted({t for t in (parse_hhmm(x) for x in raw.split(",")) if t})
    return times


async def set_times(times):
    await db.set_setting(KEY_TIMES, ",".join(sorted(set(times))))


async def is_enabled() -> bool:
    return (await db.get_setting(KEY_ENABLED, "0")) == "1"


async def get_days():
    raw = (await db.get_setting(KEY_DAYS, ",".join(map(str, DEFAULT_DAYS)))).strip()
    if raw == "-":
        return []
    out = set()
    for x in raw.split(","):
        x = x.strip()
        if x.isdigit() and 0 <= int(x) <= 6:
            out.add(int(x))
    return sorted(out)


async def set_days(days):
    await db.set_setting(KEY_DAYS, ",".join(map(str, sorted(set(days)))) or "-")


def parse_day_names(names):
    """['پنج‌شنبه', 'جمعه'] → {3, 4}؛ نام‌های ناشناخته نادیده گرفته می‌شن (برای تشخیصِ خطا دومی برمی‌گرده)."""
    def clean(x):
        return re.sub(r"[\s\u200c\-_ـ]", "", _norm(str(x)))
    table = {clean(v): k for k, v in WD_FA.items()}
    table.update({"پنجشنبه": 3, "پنچشنبه": 3, "5شنبه": 3, "آدینه": 4})
    found, unknown = set(), []
    for n in names or []:
        key = clean(n)
        if key.endswith("ها") and key[:-2] in table:  # «پنجشنبه‌ها»
            key = key[:-2]
        # «یکشنبه» و «شنبه»: تطبیق دقیق، نه زیررشته
        if key in table:
            found.add(table[key])
        else:
            unknown.append(str(n))
    return found, unknown


def next_target(now: datetime, times, days=None):
    """نزدیک‌ترین لحظه‌ی مطلقِ ارسال (به وقت تهران) در یکی از روزهای فعال، یا None.

    days = مجموعه‌ی weekday های فعال (None یعنی همه‌ی روزها).
    """
    allowed = set(range(7)) if days is None else set(days)
    best = None
    if not allowed:
        return None
    for offset in range(0, 9):
        d = now.date() + timedelta(days=offset)
        if d.weekday() not in allowed:
            continue
        for t in times:
            h, m = map(int, t.split(":"))
            cand = TEHRAN_TZ.localize(datetime.combine(d, dtime(h, m)))
            if cand <= now + timedelta(seconds=1):
                continue
            if best is None or cand < best:
                best = cand
        if best is not None:
            break  # اولین روزِ فعالی که ساعتی داره، نزدیک‌ترینه
    return best


def window_start(now: datetime, days):
    """شروعِ بازه‌ی «مسابقه‌های ثبت‌شده» برای خلاصه‌ی امروز.

    معادله:  start = ساعت ۱۸:۰۰ آخرین روزِ فعالِ قبل از امروز  (حداکثر ۷ روز عقب)
    مثال: روزهای فعال شنبه تا چهارشنبه → خلاصه‌ی شنبه از ۱۸:۰۰ چهارشنبه شروع می‌شه
          (شب چهارشنبه + پنجشنبه + جمعه رو شامل می‌شه) و خلاصه‌ی سه‌شنبه از ۱۸:۰۰ دوشنبه.
    خروجی: (start به وقت تهران, نام روزِ شروع, تعداد روز فاصله)
    """
    allowed = set(days) if days else set(range(7))
    for back in range(1, MAX_LOOKBACK_DAYS + 1):
        d = now.date() - timedelta(days=back)
        if d.weekday() in allowed:
            start = TEHRAN_TZ.localize(datetime.combine(d, dtime(18, 0)))
            return start, WD_FA[d.weekday()], back
    d = now.date() - timedelta(days=1)
    return TEHRAN_TZ.localize(datetime.combine(d, dtime(18, 0))), WD_FA[d.weekday()], 1


def _window_label(start_name: str, back: int) -> str:
    return "دیشب" if back == 1 else f"از شب {start_name} تا الان"


async def reschedule(job_queue):
    """بعد از هر تغییر تنظیمات و بعد از هر ارسال صدا زده می‌شه."""
    times = await get_times()
    days = await get_days()
    if not await is_enabled() or not times or not days:
        await sched.cancel_persistent(job_queue, JOB_NAME)
        return None
    target = next_target(datetime.now(TEHRAN_TZ), times, days)
    await sched.schedule_persistent(job_queue, brief_fire_job, target, JOB_NAME, {"target": target.isoformat()})
    return target


async def restore(application):
    """موقع post_init: زمان‌بندی ذخیره‌شده رو بدون انحراف برمی‌گردونه (یا اگه روشنه ولی نبود، می‌سازه)."""
    try:
        if not await is_enabled():
            return
        existing, _ = await sched.load_target(JOB_NAME)
        if existing is None:
            await reschedule(application.job_queue)
        else:
            await sched.restore_pending(application.job_queue, brief_fire_job, JOB_NAME)
    except Exception:
        logger.exception("morning_brief: restore failed")


async def brief_fire_job(context: ContextTypes.DEFAULT_TYPE):
    data = (context.job.data or {}) if context.job else {}
    try:
        stale = False
        tgt = data.get("target")
        if tgt:
            late = (datetime.now(TEHRAN_TZ) - datetime.fromisoformat(tgt)).total_seconds()
            stale = late > STALE_SECONDS
        today_ok = datetime.now(TEHRAN_TZ).weekday() in set(await get_days())
        if await is_enabled() and not stale and today_ok:
            await send_brief(context.bot)
        elif stale:
            logger.info("morning_brief: سررسید کهنه (ربات خاموش بوده)؛ ارسال نشد.")
    except Exception:
        logger.exception("morning_brief: send failed")
    finally:
        try:
            await reschedule(context.job_queue)
        except Exception:
            logger.exception("morning_brief: reschedule failed")


# ═════════════════════════════════════════════════════════════════
# جمع‌آوری داده
# ═════════════════════════════════════════════════════════════════
async def _window():
    now = datetime.now(TEHRAN_TZ)
    start, start_name, back = window_start(now, await get_days())
    # created_at با datetime.now() ساعتِ محلیِ سرور ذخیره می‌شه؛ لحظه‌ی شروع رو به همون ساعت تبدیل می‌کنیم
    since = start.astimezone().replace(tzinfo=None).isoformat()
    return since, _window_label(start_name, back)


async def _night_matches(since=None):
    if since is None:
        since, _ = await _window()
    return await _rows("""
        SELECT m.id AS id, m.result AS result, m.white_player_id AS wid, m.black_player_id AS bid,
               w.full_name AS wn, b.full_name AS bn, cw.name AS wc, cb.name AS bc
        FROM matches m
        LEFT JOIN players w ON w.id = m.white_player_id
        LEFT JOIN players b ON b.id = m.black_player_id
        LEFT JOIN classes cw ON cw.id = w.class_id
        LEFT JOIN classes cb ON cb.id = b.class_id
        WHERE m.created_at >= ? ORDER BY m.created_at
    """, (since,))


async def _pending_matches():
    return await _rows("""
        SELECT m.id AS id, m.match_date AS d, w.full_name AS wn, b.full_name AS bn, cw.name AS wc, cb.name AS bc
        FROM matches m
        LEFT JOIN players w ON w.id = m.white_player_id
        LEFT JOIN players b ON b.id = m.black_player_id
        LEFT JOIN classes cw ON cw.id = w.class_id
        LEFT JOIN classes cb ON cb.id = b.class_id
        WHERE m.result IS NULL ORDER BY m.id DESC LIMIT 40
    """)


async def _players_info(ids):
    ids = sorted({int(i) for i in ids if i is not None})
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    return await _rows(f"""
        SELECT p.id AS id, p.full_name AS name, p.wins AS w, p.losses AS l, p.draws AS d,
               p.warnings AS warn, p.is_elite AS elite, c.name AS cname
        FROM players p LEFT JOIN classes c ON c.id = p.class_id WHERE p.id IN ({ph})
    """, tuple(ids))


async def _admin_visits():
    return await _rows("""
        SELECT COALESCE(NULLIF(display_name,''), full_name, username, '—') AS name, role, last_active
        FROM admins WHERE is_active = 1 AND telegram_id != ?
        ORDER BY last_active DESC LIMIT 8
    """, (PISHVA_ID,))


async def _pending_tasks():
    return await _rows("""
        SELECT t.title AS title, COALESCE(NULLIF(a.display_name,''), a.full_name, '—') AS who
        FROM tasks t LEFT JOIN admins a ON a.telegram_id = t.assigned_to
        WHERE t.status = 'pending' ORDER BY t.id DESC LIMIT 10
    """)


async def _weather():
    try:
        import hub_weather
        return await hub_weather._get_weather()
    except Exception as e:
        logger.warning("morning_brief: weather unavailable: %r", e)
        return None


def _weather_line(w) -> str:
    if not w:
        return "🌤 اطلاعات هوا الان در دسترس نیست."
    n = w["now"]
    t0 = (w.get("days") or [{}])[0]
    rng = f"؛ امروز {t0.get('tmin')}° تا {t0.get('tmax')}°" if t0.get("tmax") is not None else ""
    return f"🌤 الان {n.get('temp')}° و {n.get('label')}{rng}"


def _air_line(w) -> str:
    a = (w or {}).get("air")
    if not a:
        return "🌫 وضعیت آلودگی: نامشخص"
    return f"🌫 آلودگی هوا: {a.get('label')} (AQI {a.get('aqi')})"


def _wear_line(w) -> str:
    adv = (w or {}).get("advice") or []
    if not adv:
        return ""
    return f"👕 پیشنهاد لباس: {adv[0].get('text') or adv[0].get('short')}"


# ═════════════════════════════════════════════════════════════════
# پیام صبحگاهی + خلاصه‌ی روز
# ═════════════════════════════════════════════════════════════════
def _brief_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("✨ روز من رو خلاصه کن", callback_data="mb_day", style="primary")]])


async def build_brief_text() -> str:
    name = (await db.get_setting(KEY_NAME, DEFAULT_NAME)).strip() or DEFAULT_NAME
    since, label = await _window()
    matches = await _night_matches(since)
    w = await _weather()
    lines = [
        f"🌅 صبح بخیر {name}",
        f"🗓 {weekday_fa()} {today_shamsi()}",
        "",
        f"♟ {label} {len(matches)} مسابقه ثبت شده." if matches else f"♟ {label} مسابقه‌ای ثبت نشده.",
        _weather_line(w),
        _air_line(w),
    ]
    wear = _wear_line(w)
    if wear:
        lines.append(wear)
    return "\n".join(lines)


async def send_brief(bot):
    await bot.send_message(PISHVA_ID, await build_brief_text(), reply_markup=_brief_keyboard())


def _match_line(r) -> str:
    res = {"white": "برد سفید", "black": "برد سیاه", "draw": "تساوی", "cancelled": "لغو"}.get(r["result"], "بدون نتیجه")
    wc = f" ({r['wc']})" if r["wc"] else ""
    bc = f" ({r['bc']})" if r["bc"] else ""
    return f"{r['wn'] or '؟'}{wc} ‹سفید› در برابر {r['bn'] or '؟'}{bc} ‹سیاه› — {res}"


async def _collect_context() -> str:
    """همه‌ی داده‌های خام، به‌صورت متن، برای پرامپتِ هوش مصنوعی و حالت پشتیبان."""
    from school_timetable import render_today
    since, label = await _window()
    matches, pending, visits, tasks, w, tt = await asyncio.gather(
        _night_matches(since), _pending_matches(), _admin_visits(), _pending_tasks(), _weather(), render_today())
    players = await _players_info([x for r in matches for x in (r["wid"], r["bid"])])

    parts = [f"تاریخ: {weekday_fa()} {today_shamsi()}"]
    parts.append(f"— مسابقه‌های ثبت‌شده ({label}) —\n" + ("\n".join(_match_line(r) for r in matches) or "هیچ"))
    if pending:
        by_cls = {}
        for r in pending:
            by_cls.setdefault(r["wc"] or r["bc"] or "بدون کلاس", []).append(r)
        txt = []
        for c, rs in by_cls.items():
            txt.append(f"{c}: " + "؛ ".join(f"{r['wn'] or '؟'} با {r['bn'] or '؟'}" for r in rs))
        parts.append("— مسابقه‌های بدون نتیجه (باید برگزار شوند و نتیجه ثبت شود) —\n" + "\n".join(txt))
    else:
        parts.append("— مسابقه‌های بدون نتیجه —\nهیچ")
    if players:
        parts.append("— بازیکنان درگیر در مسابقه‌های این بازه —\n" + "\n".join(
            f"{p['name']} ({p['cname'] or 'بدون کلاس'}): {p['w']} برد، {p['d']} تساوی، {p['l']} باخت"
            f"{'، ⭐برتر' if p['elite'] else ''}{'، اخطار: ' + str(p['warn']) if p['warn'] else ''}" for p in players))
    if w:
        n, t0 = w["now"], (w.get("days") or [{}])[0]
        wt = [f"الان {n.get('temp')}° (حس‌شده {n.get('feels')}°)، {n.get('label')}، رطوبت {n.get('hum')}٪، باد {n.get('wind')} km/h",
              f"امروز: حداقل {t0.get('tmin')}° حداکثر {t0.get('tmax')}°، احتمال بارش {t0.get('pop')}٪، UV {t0.get('uv')}",
              _air_line(w)[2:]]
        wt += [f"{a.get('title')}: {a.get('text')}" for a in (w.get("advice") or [])]
        parts.append("— آب‌وهوا (سرپل‌ذهاب) —\n" + "\n".join(wt))
    else:
        parts.append("— آب‌وهوا —\nدر دسترس نیست")
    parts.append("— آخرین بازدید/فعالیت مدیران —\n" + ("\n".join(
        f"{v['name']}: {str(v['last_active'] or 'نامشخص')[:16]}" for v in visits) or "نامشخص"))
    parts.append("— وظایف در انتظار —\n" + ("\n".join(f"{t['title']} (برای {t['who']})" for t in tasks) or "هیچ"))
    parts.append("— برنامه‌ی درسی امروز —\n" + tt)
    return "\n\n".join(parts)


async def _ai_summary(context_text: str, rules) -> str:
    import ai_assistant
    if not await ai_assistant._is_ai_online():
        raise RuntimeError("ai offline")
    name = (await db.get_setting(KEY_NAME, DEFAULT_NAME)).strip() or DEFAULT_NAME
    rules_txt = ""
    if rules:
        rules_txt = ("\n\nقانون‌های اجباری مدیر ارشد برای این خلاصه (دقیقاً و بدون استثنا رعایت کن):\n"
                     + "\n".join(f"{i}. {r['content']}" for i, r in enumerate(rules, 1)))
    prompt = (
        "تو «رهگشا»، دستیار هوشمند LUX هستی و داری «خلاصه‌ی روز» را برای مدیر ارشد می‌نویسی. "
        f"با «سلام {name}» شروع کن. فارسی، گرم و مؤدبانه، کوتاه و مناسب موبایل (حداکثر حدود ۲۵۰۰ نویسه). "
        "فقط از داده‌های زیر استفاده کن؛ چیزی از خودت نساز و حدس نزن. اگر داده‌ای «هیچ/نامشخص» بود، همان را صادقانه بگو.\n"
        "قالب‌بندی (خیلی مهم، دقیق رعایت کن):\n"
        f"• اولین خط: «سلام {name}» به‌صورت **ضخیم** + یک ایموجی مناسب.\n"
        "• هر بخش با یک خط تیتر شروع شود: ایموجی + عنوان ضخیم، مثل «♟ **مسابقه‌های دیشب**». بین دو بخش فقط یک خط خالی بگذار.\n"
        "• زیر هر تیتر، موردها را بلافاصله و بدون خط خالی بین‌شان، هر کدام در یک خط کوتاه و با «• » شروع کن.\n"
        "• اسم بازیکن‌ها، نتیجه‌ها، عددهای مهم (دما، AQI) و ساعت‌ها را با **ضخیم** بنویس؛ ولی همه‌ی متن را ضخیم نکن.\n"
        "• هیچ‌وقت بیشتر از یک خط خالی پشت‌سرهم نذار. از فاصله‌ی اضافه، تورفتگی، جدول، «#»، بک‌تیک و خط جداکننده استفاده نکن.\n"
        "• فقط از **ضخیم** و «•» و ایموجی استفاده کن؛ هیچ مارک‌داون دیگری نه.\n"
        "ساختار بخش‌ها:\n"
        "۱) مسابقه‌های ثبت‌شده در بازه‌ی گفته‌شده در داده‌ها (دیشب یا از شب‌های تعطیل): چه کسانی با چه کسانی بازی کردند و نتیجه چه شد.\n"
        "۲) کلاس‌هایی که امروز باید مسابقه‌های بدون‌نتیجه‌شان را برگزار و نتیجه را ثبت کنند.\n"
        "۳) درباره‌ی هر بازیکنِ درگیر در آن مسابقه‌ها یک توضیح خیلی مختصر (آمار و نکته‌ی قابل‌توجه).\n"
        "۴) آب‌وهوای کامل امروز (دما، بارش، باد، آلودگی) و پیشنهاد لباس.\n"
        "۵) آخرین بازدید مدیران.\n"
        "۶) وظایف امروز.\n"
        "۷) برنامه‌ی درسی امروز؛ کلاس به کلاس و به ترتیب زنگ‌ها."
        f"{rules_txt}\n\n=== داده‌ها ===\n{context_text}"
    )
    data = await ai_assistant._call_gemini([{"role": "user", "parts": [{"text": prompt}]}], None)
    text = "".join(p.get("text", "") for p in ai_assistant._extract_parts(data) if not p.get("thought")).strip()
    if not text:
        raise ValueError("empty AI reply")
    return text


def _tidy(text: str) -> str:
    """مرتب‌سازی خروجی هوش مصنوعی: حذف فاصله‌های اضافه، خط‌های خالی تکراری و تبدیل سرتیترها/بولت‌ها."""
    out = []
    for ln in text.replace("\r\n", "\n").split("\n"):
        ln = ln.replace("\u00a0", " ").strip()
        m = re.match(r"^#{1,6}\s*(.+)$", ln)
        if m:
            ln = "**" + m.group(1).replace("**", "").strip() + "**"
        ln = re.sub(r"^[-*]\s+", "• ", ln)
        ln = re.sub(r"^[-–—_=]{3,}$", "", ln)  # خط جداکننده
        out.append(ln)
    t = "\n".join(out)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"(^•[^\n]*)\n\n(?=•)", r"\1\n", t, flags=re.M)  # بین موردهای یک بخش خط خالی نباشد
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def _to_tg_html(text: str) -> str:
    """متن (با **ضخیم**) → HTML امن تلگرام."""
    t = _html.escape(_tidy(text), quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = t.replace("*", "").replace("`", "")
    return t


def _plain(text: str) -> str:
    return _tidy(text).replace("**", "").replace("*", "").replace("`", "")


def _chunks(text: str, n: int = 3900):
    while text:
        if len(text) <= n:
            yield text
            return
        cut = text.rfind("\n", 0, n)
        cut = cut if cut > n // 2 else n
        yield text[:cut]
        text = text[cut:].lstrip("\n")


async def day_summary_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global _last_day_click
    q = update.callback_query
    if q.from_user.id != PISHVA_ID:
        await q.answer("⛔ این بخش فقط برای مدیر ارشد است.", show_alert=True)
        return
    now = _time.monotonic()
    if now - _last_day_click < AI_COOLDOWN:
        await q.answer("چند ثانیه صبر کن و دوباره بزن.", show_alert=False)
        return
    _last_day_click = now
    await q.answer("⏳ دارم روزت رو خلاصه می‌کنم…")
    note = await ctx.bot.send_message(q.message.chat_id, "⏳ رهگشا در حال آماده‌سازی خلاصه‌ی روز…")
    try:
        context_text = await _collect_context()
        rules = await list_rules()
        ai_ok = True
        try:
            text = await _ai_summary(context_text, rules)
        except Exception as e:
            ai_ok = False
            logger.warning("morning_brief: AI summary failed (%r); fallback to raw data", e)
            text = "⚠️ تحلیل هوش مصنوعی الان ممکن نشد؛ این هم داده‌های خام امروز:\n\n" + context_text
        try:
            await note.delete()
        except Exception:
            pass
        for part in _chunks(text):
            if not ai_ok:
                await ctx.bot.send_message(q.message.chat_id, part)
                continue
            try:
                await ctx.bot.send_message(q.message.chat_id, _to_tg_html(part), parse_mode="HTML")
            except BadRequest as e:
                logger.warning("morning_brief: HTML send failed (%r); sending plain", e)
                await ctx.bot.send_message(q.message.chat_id, _plain(part))
    except Exception:
        logger.exception("morning_brief: day summary failed")
        await note.edit_text("❌ ساخت خلاصه‌ی روز ممکن نشد؛ کمی بعد دوباره امتحان کن.")


# ═════════════════════════════════════════════════════════════════
# تنظیمات (دکمه‌ی جدید در پنل مدیر ارشد)
# ═════════════════════════════════════════════════════════════════
async def _settings_view():
    enabled = await is_enabled()
    times = await get_times()
    days = await get_days()
    nxt = next_target(datetime.now(TEHRAN_TZ), times, days) if (enabled and times and days) else None
    rules = await list_rules()
    text = (
        "🌅 خلاصه صبحگاهی\n\n"
        f"وضعیت: {'🟢 روشن' if enabled else '🔴 خاموش'}\n"
        f"تعداد ارسال در روز: {len(times)}\n"
        f"ساعت‌ها: {'، '.join(times) if times else '— تعیین نشده —'}\n"
        f"روزها: {'، '.join(WD_FA[d] for d in WD_ORDER if d in days) if days else '— هیچ روزی —'}\n"
        f"ارسال بعدی: {nxt.strftime('%Y-%m-%d %H:%M') if nxt else '—'}\n"
        f"قانون‌های رهگشا: {len(rules)} مورد\n\n"
        "هر ساعت را با دقیقه‌ی دقیق می‌توانی اضافه کنی (مثلاً 06:45)."
    )
    rows = [[InlineKeyboardButton("🔴 خاموش کردن" if enabled else "🟢 روشن کردن", callback_data="mb_toggle", style="danger" if enabled else "primary")]]
    for i in range(0, len(times), 2):
        rows.append([InlineKeyboardButton(f"🗑 {t}", callback_data=f"mb_del_{t.replace(':', '')}", style="primary") for t in times[i:i + 2]])
    if len(times) < MAX_TIMES:
        rows.append([InlineKeyboardButton("➕ افزودن ساعت", callback_data="mb_add", style="primary")])
    day_btns = [InlineKeyboardButton(("✅ " if d in days else "⬜ ") + WD_FA[d], callback_data=f"mb_wd_{d}", style="primary")
                for d in WD_ORDER]
    for i in range(0, 7, 3):
        rows.append(day_btns[i:i + 3])
    rows.append([InlineKeyboardButton("📨 ارسال آزمایشی", callback_data="mb_test", style="primary"),
                 InlineKeyboardButton("📜 قانون‌های رهگشا", callback_data="mb_rules", style="primary")])
    rows.append([InlineKeyboardButton("📚 این هفته: سطر بالا (دینی)", callback_data="mb_alt_a", style="primary"),
                 InlineKeyboardButton("📚 این هفته: سطر پایین (بیکار)", callback_data="mb_alt_b", style="primary")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu_pishva", style="danger")])
    return text, InlineKeyboardMarkup(rows)


async def _show_settings(target, edit: bool):
    text, kb = await _settings_view()
    if edit:
        try:
            await target.edit_message_text(text, reply_markup=kb)
        except Exception:
            pass  # «message is not modified» و مشابه
    else:
        await target.reply_text(text, reply_markup=kb)


async def _guard(q) -> bool:
    if q.from_user.id != PISHVA_ID:
        await q.answer("⛔ این بخش فقط برای مدیر ارشد است.", show_alert=True)
        return False
    return True


async def cb_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _guard(q):
        return ConversationHandler.END
    await q.answer()
    await _show_settings(q, edit=True)
    return ConversationHandler.END


async def cb_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _guard(q):
        return
    new = "0" if await is_enabled() else "1"
    await db.set_setting(KEY_ENABLED, new)
    await reschedule(ctx.job_queue)
    await q.answer("روشن شد ✅" if new == "1" else "خاموش شد")
    await _show_settings(q, edit=True)


async def cb_weekday(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _guard(q):
        return
    d = int(q.data[len("mb_wd_"):])
    days = set(await get_days())
    days.symmetric_difference_update({d})
    await set_days(days)
    await reschedule(ctx.job_queue)
    await q.answer(("روشن شد: " if d in days else "خاموش شد: ") + WD_FA[d])
    await _show_settings(q, edit=True)


async def cb_del_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _guard(q):
        return
    raw = q.data[len("mb_del_"):]
    t = f"{raw[:2]}:{raw[2:]}"
    times = [x for x in await get_times() if x != t]
    await set_times(times)
    await reschedule(ctx.job_queue)
    await q.answer("حذف شد")
    await _show_settings(q, edit=True)


async def cb_add_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _guard(q):
        return ConversationHandler.END
    await q.answer()
    await q.edit_message_text("⏰ ساعت ارسال را با دقیقه بفرست؛ مثلاً 06:45 یا ۰۷:۳۰\n(برای انصراف /cancel)")
    return ST_ADD_TIME


async def msg_add_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != PISHVA_ID:
        return ConversationHandler.END
    t = parse_hhmm(update.message.text or "")
    if not t:
        await update.message.reply_text("❌ فرمت درست نیست. مثل 06:45 بفرست (ساعت ۰–۲۳، دقیقه ۰–۵۹):")
        return ST_ADD_TIME
    times = await get_times()
    if t in times:
        await update.message.reply_text("این ساعت از قبل هست.")
    elif len(times) >= MAX_TIMES:
        await update.message.reply_text(f"حداکثر {MAX_TIMES} ساعت در روز مجازه.")
    else:
        await set_times(times + [t])
        await reschedule(ctx.job_queue)
    await _show_settings(update.message, edit=False)
    return ConversationHandler.END


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == PISHVA_ID:
        await _show_settings(update.message, edit=False)
    return ConversationHandler.END


async def cb_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _guard(q):
        return
    await q.answer("ارسال شد ✅")
    await send_brief(ctx.bot)


async def cb_alt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _guard(q):
        return
    from school_timetable import set_alt_this_week
    await set_alt_this_week(q.data == "mb_alt_a")
    await q.answer("ثبت شد ✅ (از این هفته یک‌درمیون محاسبه می‌شه)", show_alert=True)


async def cb_rules(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _guard(q):
        return
    await q.answer()
    rules = await list_rules()
    text = "📜 قانون‌های رهگشا برای خلاصه صبحگاهی\n\n" + (
        "\n\n".join(f"#{r['id']}: {r['content']}" for r in rules) if rules else "هنوز چیزی ثبت نشده.\n"
        "در چت با رهگشا بگو: «به یاد بسپار توی خلاصه صبحگاهی … را رعایت کنی».")
    rows = [[InlineKeyboardButton(f"🗑 حذف #{r['id']}", callback_data=f"mb_rdel_{r['id']}", style="danger")] for r in rules[:10]]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="pishva_brief", style="primary")])
    await q.edit_message_text(text[:3900], reply_markup=InlineKeyboardMarkup(rows))


async def cb_rule_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _guard(q):
        return
    await delete_rule(int(q.data[len("mb_rdel_"):]))
    await q.answer("حذف شد")
    await cb_rules(update, ctx)


def build_conv():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_add_time, pattern="^mb_add$")],
        states={ST_ADD_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, msg_add_time)]},
        fallbacks=[CallbackQueryHandler(cb_settings, pattern="^pishva_brief$"),
                   MessageHandler(filters.Regex(r"^/cancel$"), cmd_cancel)],
        allow_reentry=True, per_message=False, per_chat=True, per_user=True,
    )


def build_handlers():
    return [
        CallbackQueryHandler(cb_settings, pattern="^pishva_brief$"),
        CallbackQueryHandler(cb_toggle, pattern="^mb_toggle$"),
        CallbackQueryHandler(cb_del_time, pattern=r"^mb_del_\d{4}$"),
        CallbackQueryHandler(cb_weekday, pattern=r"^mb_wd_[0-6]$"),
        CallbackQueryHandler(cb_test, pattern="^mb_test$"),
        CallbackQueryHandler(cb_alt, pattern="^mb_alt_[ab]$"),
        CallbackQueryHandler(cb_rules, pattern="^mb_rules$"),
        CallbackQueryHandler(cb_rule_delete, pattern=r"^mb_rdel_\d+$"),
        CallbackQueryHandler(day_summary_callback, pattern="^mb_day$"),
    ]
