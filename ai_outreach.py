"""
ai_outreach.py — «رهگشا به نام خودش پیام می‌دهد» (حتی به کسی که دسترسیش در ربات تأیید نشده)

مدیر ارشد به رهگشا می‌گوید «به فلانی پیام بده» / «هر شب ساعت ۹ درباره‌ی فلان موضوع بهش پیام بده».
اگر طرف مدیر ثبت‌شده نباشد (دسترسی‌اش تأیید نشده، در صف است، یا فقط یک‌بار ربات را استارت کرده)،
ابزار قدیمیِ message_admin جوابِ «پیدا نشد» می‌داد. این ماژول همان کار را برای هر کسی که ربات را
استارت کرده انجام می‌دهد، با این قیدها:

۱) تأییدِ اجباری: هر ارسال و هر زمان‌بندی *همیشه* اول از مدیر ارشد تأیید می‌گیرد (مستقل از تنظیم
   «confirm» رهگشا). علاوه بر گیتِ چت، خودِ ابزار هم بدون تأییدِ معتبر اجرا نمی‌شود، پس از مسیرهای
   دیگر (مثل batch_execute یا زمان‌بندیِ اقدام) هم دور زده نمی‌شود.
۲) فقط به کسی که ربات را استارت کرده: اول از رکوردهای خودمان (مدیر/غریبه/درخواست دسترسی) چک می‌شود؛
   حرفِ آخر را خودِ تلگرام می‌زند — اگر طرف هرگز استارت نکرده یا ربات را بلاک کرده، ارسال
   شکست می‌خورد و صادقانه گزارش می‌شود (و زمان‌بندیِ او متوقف می‌شود).
۳) پیام فقط «از رهگشا» دیده می‌شود؛ نام مدیر ارشد در پیام نیست. ردِ ارسال‌ها فقط در جدول‌های
   مخصوصِ همین ماژول (و گزارش خصوصی به مدیر ارشد) می‌ماند، نه در لاگ‌هایی که مدیرهای دیگر می‌بینند.
۴) زمان‌بندی: یک‌بار / روزانه / هفتگی / هر N روز، با متنِ ثابت یا چرخشِ موضوع‌ها. روی هر پیامِ
   تکراری یک دکمه‌ی «🔕 دیگه نفرست» هست که گیرنده می‌تواند با آن همه‌ی زمان‌بندی‌هایش را متوقف کند.
"""
import json
import logging
from datetime import datetime, timedelta

import turso_db
import database as db
from config import DB_PATH, PISHVA_ID, ROLE_PISHVA
from helpers import TEHRAN_TZ, box, normalize_digits, now_shamsi

logger = logging.getLogger(__name__)

BOSS = [ROLE_PISHVA]

# ابزارهایی که همیشه تأیید صریح لازم دارند (حتی اگر confirm=never باشد)
ALWAYS_CONFIRM = frozenset({"message_contact", "schedule_outreach"})

MAX_TEXT = 1500
MAX_TOPICS = 30
MAX_ACTIVE_PER_TARGET = 3
MAX_SENDS_PER_TARGET_PER_DAY = 3
LATE_GRACE = timedelta(hours=3)   # اگر ربات خاموش بود و بیش از این دیر شد، آن نوبت رد می‌شود (نه ارسالِ نابه‌هنگام)

FREQS = ("once", "daily", "weekly", "every_n_days")


# ═══════════════════════ زیرساخت ═══════════════════════
async def _ensure_tables():
    async with turso_db.connect(DB_PATH) as conn:
        for q in (
            "CREATE TABLE IF NOT EXISTS outreach_schedules (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, "
            "target_id INTEGER, target_name TEXT, mode TEXT, text TEXT, topics TEXT, theme TEXT, freq TEXT, time_hm TEXT, "
            "weekdays TEXT, interval_days INTEGER DEFAULT 1, next_run TEXT, last_sent TEXT, sent_count INTEGER DEFAULT 0, "
            "max_sends INTEGER, until_date TEXT, status TEXT DEFAULT 'active', stop_reason TEXT, cursor INTEGER DEFAULT 0, "
            "created_at TEXT)",
            "CREATE TABLE IF NOT EXISTS outreach_log (id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER, "
            "target_id INTEGER, text TEXT, outcome TEXT, at TEXT)",
        ):
            await conn.execute(q)
        await conn.commit()


async def _rows(sql, args=()):
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(sql, tuple(args)) as cur:
            return list(await cur.fetchall())


async def _exec(sql, args=()):
    async with turso_db.connect(DB_PATH) as conn:
        cur = await conn.execute(sql, tuple(args))
        await conn.commit()
        return getattr(cur, "lastrowid", None)


def _now() -> datetime:
    """لحظه‌ی الان، به وقت تهران، بدون tzinfo (همان قالبی که توی next_run ذخیره می‌شود)."""
    return datetime.now(TEHRAN_TZ).replace(tzinfo=None)


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts))
    except Exception:
        return None


def _clean(s, n=MAX_TEXT) -> str:
    return str(s or "").strip()[:n]


def _fmt(dt: datetime) -> str:
    try:
        import jdatetime
        jd = jdatetime.datetime.fromgregorian(datetime=dt)
        return jd.strftime("%Y/%m/%d %H:%M")
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M")


def _canon(args: dict) -> str:
    """دقیقاً همان قالبی که ai_persona.confirm_gate و cb_confirm برای «تأییدشده» ثبت می‌کنند."""
    return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)


def _is_confirmed(name: str, args: dict, ctx) -> bool:
    try:
        ok = ctx.user_data.get("_aip_confirmed")
    except Exception:
        return False
    return bool(ok) and tuple(ok) == (name, _canon(args))


# ═══════════════════════ پیدا کردنِ مخاطب ═══════════════════════
async def _norm_fn():
    import ai_tools_ext
    return ai_tools_ext.norm


async def find_contact(identifier):
    """→ (contact_dict, None) | (None, پیام خطا).
    contact: {id, name, source}. فقط کسانی که طبق رکوردهای ما ربات را استارت/استفاده کرده‌اند."""
    norm = await _norm_fn()
    ident = norm(identifier).lstrip("@")
    if not ident:
        return None, "❌ مشخص نیست پیام برای کیست."

    cands = {}   # id -> dict

    # ۱) مدیرهای ثبت‌شده (فعال یا نه — هنوز طرف ربات را استارت کرده)
    try:
        import ai_tools_ext
        a = await ai_tools_ext._find_admin(identifier)
        if a:
            tid = int(a["telegram_id"])
            cands[tid] = {"id": tid, "name": a["display_name"] or a["full_name"] or str(tid), "source": "مدیر ثبت‌شده"}
    except Exception:
        logger.warning("outreach: admin lookup failed", exc_info=True)

    # ۲) غریبه‌ها (هر پیام/دستور/دکمه‌ای که به ربات زده‌اند، از جمله /start)
    try:
        rows = await _rows("SELECT telegram_id, username, full_name FROM stranger_log ORDER BY id DESC LIMIT 3000")
        seen = set()
        for r in rows:
            tid = int(r["telegram_id"])
            if tid in seen:
                continue
            seen.add(tid)
            nm = r["full_name"] or r["username"] or str(tid)
            if (ident.isdigit() and int(ident) == tid) or (r["username"] or "").lstrip("@").lower() == ident \
                    or ident in norm(r["full_name"]):
                cands.setdefault(tid, {"id": tid, "name": nm, "source": "ربات را استارت کرده (دسترسی تأییدنشده)"})
    except Exception:
        logger.warning("outreach: stranger lookup failed", exc_info=True)

    # ۳) درخواست‌های دسترسی (هر وضعیتی: pending / queued / رد‌شده …)
    try:
        rows = await _rows("SELECT telegram_id, username, full_name FROM access_requests ORDER BY id DESC LIMIT 2000")
        seen = set()
        for r in rows:
            tid = int(r["telegram_id"])
            if tid in seen:
                continue
            seen.add(tid)
            nm = r["full_name"] or r["username"] or str(tid)
            if (ident.isdigit() and int(ident) == tid) or (r["username"] or "").lstrip("@").lower() == ident \
                    or ident in norm(r["full_name"]):
                cands.setdefault(tid, {"id": tid, "name": nm, "source": "درخواست دسترسی داده"})
    except Exception:
        logger.warning("outreach: access_requests lookup failed", exc_info=True)

    if not cands:
        if ident.isdigit():
            return None, (f"❌ برای آیدی {ident} هیچ ردی نیست که ربات را استارت کرده باشد؛ تلگرام اجازه نمی‌دهد "
                          "ربات اول به کسی پیام بدهد. باید اول خودش ربات را استارت کند.")
        return None, f"❌ کسی با مشخصات «{identifier}» بین افرادی که ربات را استارت کرده‌اند پیدا نشد."
    if len(cands) > 1:
        names = "؛ ".join(f"{c['name']} (id={c['id']})" for c in list(cands.values())[:6])
        return None, f"❓ چند نفر با این مشخصات هستند: {names}. آیدی عددی بده."
    c = next(iter(cands.values()))
    if int(c["id"]) == int(PISHVA_ID):
        return None, "❌ این خودِ شماست؛ برای پیام به خودتان از یادآور استفاده کنید."
    try:
        if await db.get_blocked_user(int(c["id"])):
            return None, (f"⛔ «{c['name']}» در فهرست بلاکِ ربات است؛ برای ارسال پیام اول باید از بلاک درش بیاورید.")
    except Exception:
        pass
    return c, None


# ═══════════════════════ ساختِ پیام و ارسال ═══════════════════════
def _wrap(text: str) -> str:
    """ظاهرِ پیام: فقط از رهگشا. نام مدیر ارشد هیچ‌جا نیست."""
    return f"{box('🤖 پیام از رهگشا')}\n\n{text}\n\n🤖 ارسال‌شده توسط رهگشا"


def _stop_kb(target_id: int):
    from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
    return M([[B("🔕 دیگه نفرست", callback_data=f"outr:stop:{target_id}")]])


async def _send(bot, target_id: int, text: str, recurring: bool):
    """→ (ok, خطا|None, forbidden: bool)"""
    from telegram.error import Forbidden
    try:
        await bot.send_message(chat_id=target_id, text=_wrap(text),
                               reply_markup=_stop_kb(target_id) if recurring else None)
        return True, None, False
    except Forbidden as e:
        return False, f"تلگرام اجازه نداد ({e}) — احتمالاً هرگز ربات را استارت نکرده یا بلاکش کرده", True
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", False


async def _sent_today(target_id: int) -> int:
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    r = await _rows("SELECT COUNT(*) AS n FROM outreach_log WHERE target_id=? AND outcome='sent' AND at>=?",
                    (target_id, start))
    return int(r[0]["n"]) if r else 0


async def _log(schedule_id, target_id, text, outcome):
    await _exec("INSERT INTO outreach_log(schedule_id, target_id, text, outcome, at) VALUES (?,?,?,?,?)",
                (schedule_id, target_id, text, outcome, _now().isoformat()))


async def _tell_owner(bot, text: str):
    try:
        await bot.send_message(chat_id=PISHVA_ID, text=text)
    except Exception:
        logger.warning("outreach: owner report failed", exc_info=True)


async def _compose(sched, recent_texts) -> str:
    """نوشتنِ پیامِ این نوبت برای زمان‌بندی‌های «موضوعی». متنِ ثابت مستقیم برمی‌گردد."""
    if sched["mode"] == "fixed":
        return sched["text"]
    topics = json.loads(sched["topics"] or "[]")
    if topics:
        topic = topics[int(sched["cursor"] or 0) % len(topics)]
    else:
        topic = sched["theme"] or "یک نکته‌ی کوتاه و مفید"
    brief = sched["text"] or ""
    prev = "\n".join(f"- {t[:160]}" for t in recent_texts) or "—"
    prompt = (
        "تو «رهگشا» هستی، دستیار هوشمند یک ربات تلگرامی. یک پیام کوتاهِ دوستانه و طبیعی به فارسی بنویس.\n"
        f"موضوعِ این نوبت: {topic}\n"
        + (f"راهنمای مدیر درباره‌ی لحن/هدف: {brief}\n" if brief else "")
        + f"پیام‌های قبلیِ همین مخاطب (تکرارشان نکن):\n{prev}\n\n"
        "قوانین سخت:\n"
        "- حداکثر ۵ خط. مقدمه‌چینی و سلامِ رسمیِ طولانی نکن.\n"
        "- هیچ‌وقت نگو کسی این پیام را خواسته یا فرستاده؛ از طرفِ خودت (رهگشا) حرف بزن و اسمِ هیچ مدیری را نیاور.\n"
        "- خودت را آدم جا نزن؛ اگر لازم شد، رهگشا (دستیار هوشمند) هستی.\n"
        "- عدد، خبر یا ادعای واقعیِ ساختگی نساز؛ اگر مطمئن نیستی، عمومی و بی‌خطر بنویس.\n"
        "- فقط خودِ متنِ پیام را بده، بدون توضیح و بدون گیومه."
    )
    try:
        import ai_assistant as aia
        data = await aia._call_gemini([{"role": "user", "parts": [{"text": prompt}]}], None)
        txt = "".join(p.get("text", "") for p in aia._extract_parts(data)).strip()
        if txt:
            return txt[:MAX_TEXT]
    except Exception:
        logger.warning("outreach: compose failed", exc_info=True)
    return ""   # بدون متن چیزی نمی‌فرستیم (به جای ارسالِ پیامِ بی‌ربط)


# ═══════════════════════ زمان‌بندی ═══════════════════════
def _parse_hm(s):
    s = normalize_digits(str(s or "")).strip().replace("٫", ":").replace(".", ":")
    try:
        h, m = s.split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except Exception:
        pass
    return None


def _next_after(freq, hm, weekdays, interval_days, after: datetime):
    """نزدیک‌ترین لحظه‌ی سرِ ساعتِ hm (وقت تهران) که اکیداً بعد از after باشد."""
    h, m = hm
    cand = after.replace(hour=h, minute=m, second=0, microsecond=0)
    if freq == "weekly" and weekdays:
        for add in range(0, 8):
            d = (after + timedelta(days=add)).replace(hour=h, minute=m, second=0, microsecond=0)
            if d > after and d.weekday() in weekdays:
                return d
    if cand <= after:
        cand += timedelta(days=1)
    return cand


def _advance(row, after: datetime):
    """نوبتِ بعدیِ یک زمان‌بندیِ تکراری، یا None اگر تمام شد."""
    if row["freq"] == "once":
        return None
    hm = _parse_hm(row["time_hm"])
    wd = json.loads(row["weekdays"] or "[]")
    base = after
    if row["freq"] == "every_n_days":
        step = max(1, int(row["interval_days"] or 1))
        cur = _parse(row["next_run"]) or after
        nxt = cur + timedelta(days=step)
        while nxt <= after:
            nxt += timedelta(days=step)
        return nxt
    return _next_after(row["freq"], hm, wd, 1, base)


async def _tick_server(bot):
    await _ensure_tables()
    now = _now()
    due = await _rows("SELECT * FROM outreach_schedules WHERE status='active' AND next_run<=? ORDER BY next_run LIMIT 20",
                      (now.isoformat(),))
    for s in due:
        try:
            await _run_one(bot, s, now)
        except Exception:
            logger.warning("outreach: schedule %s failed", s["id"], exc_info=True)


async def _run_one(bot, s, now: datetime):
    sid, tid = s["id"], int(s["target_id"])
    planned = _parse(s["next_run"]) or now
    nxt = _advance(s, now)

    async def finish(status="active", reason=None, sent=False):
        if nxt is None and status == "active":
            status = "done"
        mx = s["max_sends"]
        cnt = int(s["sent_count"] or 0) + (1 if sent else 0)
        if status == "active" and mx and cnt >= int(mx):
            status = "done"
        until = _parse(s["until_date"]) if s["until_date"] else None
        if status == "active" and until and nxt and nxt.date() > until.date():
            status = "done"
        await _exec(
            "UPDATE outreach_schedules SET next_run=?, last_sent=?, sent_count=?, status=?, stop_reason=?, cursor=? WHERE id=?",
            ((nxt or now).isoformat(), now.isoformat() if sent else s["last_sent"], cnt, status, reason,
             int(s["cursor"] or 0) + (1 if sent else 0), sid))

    if now - planned > LATE_GRACE:               # ربات مدتی خاموش بوده؛ این نوبت از دست رفته
        await _log(sid, tid, "", "missed")
        await finish()
        return
    try:
        if await db.get_blocked_user(tid):
            await _log(sid, tid, "", "blocked")
            await finish("cancelled", "کاربر در فهرست بلاک قرار گرفت")
            await _tell_owner(bot, f"⛔ زمان‌بندیِ #{sid} برای «{s['target_name']}» متوقف شد (در فهرست بلاک است).")
            return
    except Exception:
        pass
    if await _sent_today(tid) >= MAX_SENDS_PER_TARGET_PER_DAY:
        await _log(sid, tid, "", "daily_cap")
        await finish()
        return

    recent = [r["text"] for r in await _rows(
        "SELECT text FROM outreach_log WHERE schedule_id=? AND outcome='sent' ORDER BY id DESC LIMIT 5", (sid,))]
    text = await _compose(s, recent)
    if not text:
        await _log(sid, tid, "", "compose_failed")
        await finish()
        await _tell_owner(bot, f"⚠️ برای زمان‌بندیِ #{sid} («{s['target_name']}») این نوبت متنی ساخته نشد؛ چیزی ارسال نشد.")
        return

    ok, err, forbidden = await _send(bot, tid, text, recurring=(s["freq"] != "once"))
    if ok:
        await _log(sid, tid, text, "sent")
        await finish(sent=True)
        await _tell_owner(bot, f"📮 (زمان‌بندی #{sid}) از طرف رهگشا برای «{s['target_name']}» ارسال شد:\n\n{text}")
    else:
        await _log(sid, tid, text, f"failed: {err}")
        if forbidden:
            await finish("cancelled", "تلگرام اجازه‌ی ارسال نداد")
            await _tell_owner(bot, f"❌ ارسال به «{s['target_name']}» ممکن نشد و زمان‌بندیِ #{sid} متوقف شد.\n{err}")
        else:
            await finish()
            await _tell_owner(bot, f"⚠️ ارسالِ زمان‌بندیِ #{sid} به «{s['target_name']}» خطا خورد (نوبت بعدی دوباره تلاش می‌شود):\n{err}")


async def tick(context):
    import servers
    try:
        await servers.for_each_active(_tick_server, context.bot)
    except Exception:
        logger.warning("outreach: tick failed", exc_info=True)


def start(application):
    jq = application.job_queue
    if jq:
        jq.run_repeating(tick, interval=60, first=45, name="outreach_tick")
    logger.info("ai_outreach: scheduled")


# ═══════════════════════ دکمه‌ی «دیگه نفرست» (برای گیرنده) ═══════════════════════
async def on_cb(update, ctx):
    q = update.callback_query
    parts = (q.data or "").split(":")
    if len(parts) != 3 or parts[1] != "stop":
        await q.answer()
        return
    uid = q.from_user.id
    if int(parts[2]) != uid:                      # فقط خودِ گیرنده
        await q.answer("⛔", show_alert=True)
        return
    await _ensure_tables()
    rows = await _rows("SELECT id, target_name FROM outreach_schedules WHERE target_id=? AND status='active'", (uid,))
    await _exec("UPDATE outreach_schedules SET status='paused', stop_reason='خودِ گیرنده متوقف کرد' "
                "WHERE target_id=? AND status='active'", (uid,))
    await q.answer("باشه، دیگه این پیام‌ها رو نمی‌فرستم.", show_alert=True)
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    if rows:
        await _tell_owner(ctx.bot, "🔕 «%s» با دکمه‌ی «دیگه نفرست» زمان‌بندی‌های #%s را متوقف کرد."
                          % (rows[0]["target_name"], "، #".join(str(r["id"]) for r in rows)))


def register(app):
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(on_cb, pattern=r"^outr:"), group=-49)


# ═══════════════════════ متنِ تأییدیه (برای confirm_gate) ═══════════════════════
async def describe_for_confirm(fname: str, fargs: dict) -> str:
    fargs = fargs or {}
    c, err = await find_contact(fargs.get("identifier"))
    who = f"{c['name']} — {c['source']}" if c else (err or "؟")
    lines = ["🛡️ قبل از ارسال تأیید می‌خوام (از طرف رهگشا، بدون ذکر نامِ شما):", f"👤 گیرنده: {who}"]
    if fname == "message_contact":
        lines.append(f"✉️ متن:\n{_clean(fargs.get('text'))}")
    else:
        lines.append(f"🔁 تکرار: {_freq_label(fargs)}")
        if fargs.get("topics"):
            lines.append("🧩 موضوع‌ها (به‌ترتیب): " + "؛ ".join(map(str, fargs["topics"])))
        elif fargs.get("theme"):
            lines.append(f"🧩 موضوع: {fargs['theme']}")
        if fargs.get("text"):
            lines.append(("✉️ متن:\n" if not (fargs.get("topics") or fargs.get("theme")) else "📝 راهنمای لحن:\n") + _clean(fargs["text"]))
        if fargs.get("topics") or fargs.get("theme"):
            lines.append("ℹ️ متنِ هر نوبت را رهگشا همان موقع می‌نویسد و هر ارسال را همان لحظه برای شما هم گزارش می‌دهد.")
        if fargs.get("max_sends"):
            lines.append(f"🔢 حداکثر ارسال: {fargs['max_sends']}")
        if fargs.get("until_date"):
            lines.append(f"📅 تا تاریخ: {fargs['until_date']}")
    return "\n".join(lines)


def _freq_label(a: dict) -> str:
    f = a.get("frequency") or "once"
    t = a.get("time") or a.get("at_datetime") or (f"{a.get('in_minutes')} دقیقه‌ی دیگر" if a.get("in_minutes") else "؟")
    if f == "once":
        return f"یک‌بار — {t}"
    if f == "daily":
        return f"هر روز ساعت {t}"
    if f == "weekly":
        return f"هفتگی (روزهای {a.get('weekdays')}) ساعت {t}"
    return f"هر {a.get('interval_days') or '؟'} روز ساعت {t}"


# ═══════════════════════ ابزارهای رهگشا ═══════════════════════
TOOL_PERMISSIONS = {
    "message_contact": BOSS, "schedule_outreach": BOSS, "list_outreach": BOSS, "cancel_outreach": BOSS,
}
CATEGORY = ("outreach", "📮 پیام از طرفِ رهگشا به افرادِ دارای استارت (حتی بدون دسترسی)",
            ["message_contact", "schedule_outreach", "list_outreach", "cancel_outreach"])
ACTION_NAMES = frozenset({"message_contact", "schedule_outreach", "cancel_outreach"})

_S = {"type": "string"}
TOOL_DECLARATIONS = [
    {"name": "message_contact",
     "description": (
         "ارسالِ فوریِ یک پیام از طرفِ «رهگشا» به هر کسی که ربات را استارت کرده، *حتی اگر دسترسیش در ربات تأیید نشده* "
         "(برخلاف message_admin که فقط مدیرهای ثبت‌شده را پیدا می‌کند). پیام فقط از رهگشا دیده می‌شود و اسمِ مدیر ارشد در آن نیست؛ "
         "پس در متن هرگز اسمِ مدیر ارشد را ننویس. ارسال همیشه اول از مدیر ارشد تأیید می‌گیرد، پس بدون نگرانی صدایش بزن و "
         "نگو «فرستادم» تا وقتی نتیجه‌ی ابزار نگفته. اگر طرف هرگز ربات را استارت نکرده، ابزار صادقانه خطا می‌دهد؛ دورش نزن."),
     "parameters": {"type": "object", "properties": {
         "identifier": {**_S, "description": "آیدی عددی، یوزرنیم یا نامِ فرد"},
         "text": {**_S, "description": "متنِ پیام، به لحنِ رهگشا"}},
         "required": ["identifier", "text"]}},
    {"name": "schedule_outreach",
     "description": (
         "زمان‌بندیِ پیامِ خودکار از طرفِ رهگشا به یک نفر (هم‌قواعدِ message_contact): یک‌بار در لحظه‌ای مشخص، یا روزانه/هفتگی/هر N روز "
         "(مثلاً «هر شب ساعت ۲۱»، «روزی یک‌بار با موضوع‌های مختلف»). یا متنِ ثابت بده (text)، یا فهرستِ موضوع‌ها (topics) تا رهگشا هر "
         "نوبت درباره‌ی یکی‌شان (به‌ترتیب و چرخشی) پیامِ تازه بنویسد، یا فقط یک theme کلی. هر ارسال برای مدیر ارشد هم گزارش می‌شود و "
         "گیرنده دکمه‌ی «دیگه نفرست» دارد. همیشه اول از مدیر ارشد تأیید می‌گیرد."),
     "parameters": {"type": "object", "properties": {
         "identifier": {**_S, "description": "آیدی عددی، یوزرنیم یا نامِ فرد"},
         "frequency": {**_S, "description": "once | daily | weekly | every_n_days (پیش‌فرض once)"},
         "time": {**_S, "description": "ساعتِ روز به وقتِ تهران، مثل 21:30 (برای daily/weekly/every_n_days)"},
         "weekdays": {"type": "array", "items": {"type": "integer"},
                      "description": "فقط weekly: دوشنبه=0 سه‌شنبه=1 چهارشنبه=2 پنجشنبه=3 جمعه=4 شنبه=5 یکشنبه=6"},
         "interval_days": {"type": "integer", "description": "فقط every_n_days"},
         "at_datetime": {**_S, "description": "فقط once: 'YYYY-MM-DD HH:MM' میلادی به وقتِ تهران"},
         "in_minutes": {"type": "integer", "description": "فقط once: چند دقیقه‌ی دیگر"},
         "text": {**_S, "description": "متنِ ثابت (اگر topics/theme نیست) یا راهنمای لحن/هدف برای نوبت‌های موضوعی"},
         "topics": {"type": "array", "items": _S, "description": "موضوع‌های چرخشی؛ هر نوبت یکی"},
         "theme": {**_S, "description": "اگر فهرستِ دقیق نیست، موضوعِ کلی؛ رهگشا هر بار زاویه‌ی تازه می‌گیرد"},
         "max_sends": {"type": "integer", "description": "سقفِ تعدادِ ارسال (اختیاری)"},
         "until_date": {**_S, "description": "تا این تاریخ 'YYYY-MM-DD' (اختیاری)"}},
         "required": ["identifier"]}},
    {"name": "list_outreach",
     "description": "فهرستِ زمان‌بندی‌های پیامِ رهگشا (فعال/متوقف) با شناسه، گیرنده، تکرار و نوبتِ بعدی.",
     "parameters": {"type": "object", "properties": {"include_finished": {"type": "boolean"}}}},
    {"name": "cancel_outreach",
     "description": "لغو/توقفِ یک زمان‌بندیِ پیام با شناسه (از list_outreach)، یا همه‌ی زمان‌بندی‌های یک نفر با identifier.",
     "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "identifier": _S}}},
]


def _need_confirm_msg(name):
    return ("⛔ این ارسال فقط بعد از تأییدِ صریحِ مدیر ارشد اجرا می‌شود و تأییدی ثبت نشده؛ "
            "چیزی ارسال یا زمان‌بندی نشد. (درخواست را مستقیم و جدا بده تا دکمه‌ی تأیید بیاید.)")


async def dispatch_tool(name, args, caller_id, caller_role, ctx):
    await _ensure_tables()
    args = args or {}
    if caller_role != ROLE_PISHVA:
        return "⛔ این ابزار فقط برای مدیر ارشد است."

    if name == "message_contact":
        if not _is_confirmed(name, args, ctx):
            return _need_confirm_msg(name)
        text = _clean(args.get("text"))
        if not text:
            return "❌ متنِ پیام نمی‌تواند خالی باشد."
        c, err = await find_contact(args.get("identifier"))
        if not c:
            return err
        if await _sent_today(c["id"]) >= MAX_SENDS_PER_TARGET_PER_DAY:
            return f"⛔ امروز برای «{c['name']}» به سقفِ {MAX_SENDS_PER_TARGET_PER_DAY} پیامِ رهگشا رسیدیم؛ فردا دوباره."
        ok, e, _forb = await _send(ctx.bot, c["id"], text, recurring=False)
        await _log(None, c["id"], text, "sent" if ok else f"failed: {e}")
        if not ok:
            return f"❌ ارسال به «{c['name']}» انجام نشد: {e}"
        return f"✅ پیام از طرفِ رهگشا برای «{c['name']}» ({c['source']}) ارسال شد."

    if name == "schedule_outreach":
        if not _is_confirmed(name, args, ctx):
            return _need_confirm_msg(name)
        c, err = await find_contact(args.get("identifier"))
        if not c:
            return err
        freq = str(args.get("frequency") or "once").strip()
        if freq not in FREQS:
            return "❌ frequency باید یکی از once / daily / weekly / every_n_days باشد."
        topics = [_clean(t, 200) for t in (args.get("topics") or []) if _clean(t, 200)][:MAX_TOPICS]
        theme = _clean(args.get("theme"), 200)
        text = _clean(args.get("text"))
        mode = "topics" if (topics or theme) else "fixed"
        if mode == "fixed" and not text:
            return "❌ یا text (متنِ ثابت) بده، یا topics / theme تا رهگشا خودش بنویسد."
        n_active = (await _rows("SELECT COUNT(*) AS n FROM outreach_schedules WHERE target_id=? AND status='active'",
                                (c["id"],)))[0]["n"]
        if n_active >= MAX_ACTIVE_PER_TARGET:
            return f"❌ برای «{c['name']}» از قبل {n_active} زمان‌بندیِ فعال هست؛ اول یکی را لغو کن."

        now = _now()
        hm = None
        if freq == "once":
            import ai_scheduler
            tgt, e = ai_scheduler.resolve_target(args)
            if e:
                return "❌ " + e
            nxt = tgt.replace(tzinfo=None)
        else:
            hm = _parse_hm(args.get("time"))
            if not hm:
                return "❌ برای تکرار، ساعت لازم است (مثل 21:30)."
            wd = []
            if freq == "weekly":
                wd = sorted({int(d) for d in (args.get("weekdays") or []) if 0 <= int(d) <= 6})
                if not wd:
                    return "❌ برای هفتگی، روزهای هفته (weekdays) لازم است."
            if freq == "every_n_days":
                try:
                    if int(args.get("interval_days")) < 1:
                        raise ValueError
                except Exception:
                    return "❌ interval_days باید عددِ مثبت باشد."
            nxt = _next_after(freq, hm, wd, 1, now)
        until = None
        if args.get("until_date"):
            until = _parse(normalize_digits(str(args["until_date"])))
            if not until:
                return "❌ until_date باید به شکلِ YYYY-MM-DD باشد."
        mx = None
        if args.get("max_sends"):
            try:
                mx = max(1, int(args["max_sends"]))
            except Exception:
                return "❌ max_sends باید عدد باشد."
        wd_json = json.dumps(sorted({int(d) for d in (args.get("weekdays") or [])}) if freq == "weekly" else [])
        sid = await _exec(
            "INSERT INTO outreach_schedules(owner_id, target_id, target_name, mode, text, topics, theme, freq, time_hm, weekdays, "
            "interval_days, next_run, max_sends, until_date, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?)",
            (caller_id, c["id"], c["name"], mode, text, json.dumps(topics, ensure_ascii=False), theme, freq,
             args.get("time") if hm else None, wd_json, int(args.get("interval_days") or 1), nxt.isoformat(), mx,
             until.isoformat() if until else None, now.isoformat()))
        return (f"✅ زمان‌بندیِ #{sid} ثبت شد: «{c['name']}» — {_freq_label(args)}؛ "
                f"اولین نوبت {_fmt(nxt)}. هر ارسال برای شما هم گزارش می‌شود.")

    if name == "list_outreach":
        q = "SELECT * FROM outreach_schedules" + ("" if args.get("include_finished") else " WHERE status IN ('active','paused')") + " ORDER BY id DESC LIMIT 40"
        rows = await _rows(q)
        if not rows:
            return "📭 زمان‌بندیِ پیامِ رهگشا ثبت نشده."
        out = []
        for r in rows:
            nx = _parse(r["next_run"])
            what = ("موضوع‌ها: " + "؛ ".join(json.loads(r["topics"] or "[]"))) if r["topics"] and r["topics"] != "[]" \
                else (f"موضوع: {r['theme']}" if r["theme"] else f"متن: {(r['text'] or '')[:60]}")
            out.append(f"#{r['id']} | {r['target_name']} | {r['freq']} {r['time_hm'] or ''} | {r['status']}"
                       f"{(' (' + r['stop_reason'] + ')') if r['stop_reason'] else ''} | ارسال‌شده {r['sent_count']} | "
                       f"بعدی {_fmt(nx) if (nx and r['status'] == 'active') else '—'} | {what}")
        return "\n".join(out)

    if name == "cancel_outreach":
        if args.get("id"):
            r = await _rows("SELECT id, target_name, status FROM outreach_schedules WHERE id=?", (int(args["id"]),))
            if not r:
                return f"❌ زمان‌بندیِ #{args['id']} پیدا نشد."
            await _exec("UPDATE outreach_schedules SET status='cancelled', stop_reason='لغو توسط مدیر ارشد' "
                        "WHERE id=? AND status IN ('active','paused')", (int(args["id"]),))
            return f"✅ زمان‌بندیِ #{args['id']} («{r[0]['target_name']}») لغو شد."
        if args.get("identifier"):
            c, err = await find_contact(args["identifier"])
            if not c:
                return err
            await _exec("UPDATE outreach_schedules SET status='cancelled', stop_reason='لغو توسط مدیر ارشد' "
                        "WHERE target_id=? AND status IN ('active','paused')", (c["id"],))
            return f"✅ همه‌ی زمان‌بندی‌های «{c['name']}» لغو شد."
        return "❌ id یا identifier بده."
    return None
