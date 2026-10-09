"""
ai_tools_oversight.py — «چشم و گوشِ رهگشا» برای مدیر ارشد

شامل:
  ۱) get_live_overview      : تصویر لحظه‌ای کل مسابقات + وضعیت زنده‌ی مدیران
  ۲) get_admin_audit_trail  : لاگِ نقطه‌به‌نقطه‌ی اقدامات مدیران (اقدام‌ها + ردیابی کامل کلیک‌ها)
  ۳) read_admin_chats       : چتِ مدیران با رهگشا + پیام‌های مستقیم؛ جست‌وجو بر اساس موضوع
  ۴) set_player_style / plan_title_path : سبک بازیکن + تحلیل سناریوی رسیدن به رتبه‌ی اول
  ۵) set_enforcement_lock   : قفلِ «اخطار/اخراج/حذف فقط با دستور مدیر ارشد»

قواعد:
  - همه‌ی ابزارها فقط برای مدیر ارشد (ROLE_PISHVA) هستند؛ ابزار هر بار هم در دیسپچر
    و هم اینجا دوباره چک می‌شود.
  - هیچ‌کدام از ابزارهای تحلیلی چیزی در دیتابیسِ مسابقات ثبت یا تغییر نمی‌دهند.
  - هر بار که مدیر ارشد چتِ دیگران را می‌خواند، یک ردیف در action_logs ثبت می‌شود.
  - این فایل عمداً ai_tools را ایمپورت نمی‌کند (جلوگیری از ایمپورت چرخه‌ای).
"""
import itertools
import logging
import random
from datetime import datetime, timedelta

import turso_db
import database as db
from config import DB_PATH, PISHVA_ID, ROLE_PISHVA
from helpers import TEHRAN_TZ, now_shamsi
from ai_tools_ext import _find_admin, _admin_name, _who, _short_date, norm

logger = logging.getLogger(__name__)

ONLY_PISHVA = [ROLE_PISHVA]

# ════════════════════════════════════════════════════════════════
# دسترسی‌ها و دسته‌بندی
# ════════════════════════════════════════════════════════════════
TOOL_PERMISSIONS_OV = {
    "get_live_overview": ONLY_PISHVA,
    "get_admin_audit_trail": ONLY_PISHVA,
    "read_admin_chats": ONLY_PISHVA,
    "set_player_style": ONLY_PISHVA,
    "plan_title_path": ONLY_PISHVA,
    "set_enforcement_lock": ONLY_PISHVA,
}

CATEGORIES_OV = [
    ("oversight", "👁️ نظارت کامل (مسابقات زنده، لاگ مدیران، چت‌ها)",
     ["get_live_overview", "get_admin_audit_trail", "read_admin_chats"]),
    ("scenario", "🧭 سبک بازیکن و سناریوی رتبه‌ی اول",
     ["set_player_style", "plan_title_path"]),
    ("enforcement_lock", "🔐 قفل دستور مدیر ارشد", ["set_enforcement_lock"]),
]

# فقط این‌ها «اقدام واقعی» حساب می‌شن (گزارش + اطلاع به مدیر ارشد)
ACTION_TOOL_NAMES_OV = frozenset({"set_player_style", "set_enforcement_lock"})
SCHEDULABLE_TOOL_NAMES_OV = frozenset({"get_live_overview", "get_admin_audit_trail"})

# ════════════════════════════════════════════════════════════════
# قفلِ «فقط با دستور مدیر ارشد»
# ════════════════════════════════════════════════════════════════
LOCK_SETTING_KEY = "ai_enforce_pishva_only"
# اقداماتِ تنبیهی/برگشت‌ناپذیر که وقتی قفل روشنه فقط وقتی «خودِ مدیر ارشد» با رهگشا حرف می‌زنه اجرا می‌شن
LOCKED_TOOLS = frozenset({
    "warn_player", "kick_player", "delete_match", "edit_match_result",
    "warn_team", "delete_team", "set_admin_active",
})


async def check_lock(name: str, caller_role: str):
    """None یعنی آزاد؛ در غیر این صورت متنِ خطا برای برگرداندن به کاربر."""
    if name not in LOCKED_TOOLS or caller_role == ROLE_PISHVA:
        return None
    if (await db.get_setting(LOCK_SETTING_KEY, "1")) != "1":
        return None
    return "⛔ اخطار، اخراج و حذف فقط با دستورِ مستقیمِ مدیر ارشد انجام می‌شه. این درخواست رو به ایشون منتقل کن."


# ════════════════════════════════════════════════════════════════
# اعلان ابزارها برای Gemini
# ════════════════════════════════════════════════════════════════
_STR = {"type": "string"}
_INT = {"type": "integer"}

TOOL_DECLARATIONS_OV = [
    {
        "name": "get_live_overview",
        "description": (
            "تصویر لحظه‌ای کل سیستم: همه‌ی مسابقات/تورنمنت‌های فعال (پیشرفت، جدول امتیاز، بازی‌های در انتظار، "
            "آخرین نتایج) + وضعیت زنده‌ی هر مدیر (آخرین فعالیت، کارهای ۱۵ دقیقه‌ی اخیر، اخطارها) + درخواست‌های باز. "
            "هر وقت مدیر ارشد پرسید «الان اوضاع چطوره / چی داره می‌گذره / مدیرا چیکار می‌کنن» اول این رو صدا بزن."
        ),
        "parameters": {"type": "object", "properties": {
            "window_minutes": {**_INT, "description": "بازه‌ی «فعالیت اخیر» به دقیقه (پیش‌فرض ۱۵)"},
        }},
    },
    {
        "name": "get_admin_audit_trail",
        "description": (
            "لاگِ نقطه‌به‌نقطه‌ی اقدامات مدیران، شامل ردیابی کامل (هر کلیک، دستور، پیام، باز کردن پنل). "
            "identifier اختیاری (نام/یوزرنیم/آیدی؛ خالی = همه‌ی مدیران). term برای جست‌وجوی متنی. "
            "scope: all (پیش‌فرض) / actions (فقط اقدام‌های اصلی) / trail (فقط ردیابی). صفحه‌بندی با page (از ۰)."
        ),
        "parameters": {"type": "object", "properties": {
            "identifier": _STR,
            "period": {"type": "string", "enum": ["today", "week", "month", "all"]},
            "scope": {"type": "string", "enum": ["all", "actions", "trail"]},
            "term": _STR,
            "limit": {**_INT, "description": "تعداد ردیف (پیش‌فرض ۳۰، سقف ۸۰)"},
            "page": _INT,
        }},
    },
    {
        "name": "read_admin_chats",
        "description": (
            "خواندنِ چتِ مدیران با رهگشا + پیام‌های مستقیمِ بین مدیر ارشد و مدیران. "
            "برای «فلانی چی گفته / نظر مدیرا درباره‌ی X چیه»: identifier (اختیاری) و topic (کلمه‌ی کلیدی) بده؛ "
            "بدون identifier در چتِ همه‌ی مدیران می‌گرده. برای خواندنِ کاملِ یک گفتگو session_id بده. "
            "source: ai / direct / both. بعد از خواندن، نظر هر مدیر رو جداگانه و دقیق خلاصه کن."
        ),
        "parameters": {"type": "object", "properties": {
            "identifier": _STR,
            "topic": _STR,
            "source": {"type": "string", "enum": ["ai", "direct", "both"]},
            "period": {"type": "string", "enum": ["today", "week", "month", "all"]},
            "session_id": _INT,
            "limit": {**_INT, "description": "حداکثر گفتگو/پیام (پیش‌فرض ۸، سقف ۲۰)"},
        }},
    },
    {
        "name": "set_player_style",
        "description": (
            "ثبت سبک بازی یک بازیکن برای تحلیل سناریو. style یکی از: aggressive (تهاجمی/تاکتیکی)، "
            "positional (پوزیشنی/موضعی)، defensive (دفاعی/مساوی‌طلب)، balanced (متعادل/همه‌فن‌حریف). "
            "note اختیاری (مثلاً «تو آخر بازی قویه»)."
        ),
        "parameters": {"type": "object", "properties": {
            "player": {**_STR, "description": "نام بازیکن"},
            "style": _STR,
            "note": _STR,
        }, "required": ["player", "style"]},
    },
    {
        "name": "plan_title_path",
        "description": (
            "تحلیل سناریوی «فلانی نفر اول بشه»: جدول فعلی، حداکثر امتیاز ممکن، شانس واقعی (شبیه‌سازی بر پایه‌ی Elo، "
            "سبک‌ها، سابقه‌ی رودررو و نرخ تساوی)، لیست حریف‌های باقی‌مانده‌اش با احتمال برد/تساوی، و بازی‌هایی که "
            "نتیجه‌شون بیشترین اثر رو روی اول‌شدنش دارن (مثلاً «رقیب باید باخت/مساوی کنه»). "
            "فقط تحلیل و پیش‌بینیه: هیچ نتیجه‌ای ثبت یا تغییر نمی‌ده؛ نتایج واقعی باید همون‌طور که بازی می‌شن ثبت بشن. "
            "tournament اختیاری (پیش‌فرض تورنمنت پیش‌فرض). include_unscheduled=true یعنی جفت‌های هنوزبازی‌نشده‌ی "
            "بین شرکت‌کننده‌ها هم به‌عنوان بازیِ ممکن حساب بشن."
        ),
        "parameters": {"type": "object", "properties": {
            "player": {**_STR, "description": "نام بازیکنی که هدف اولی‌شدنشه"},
            "tournament": _STR,
            "include_unscheduled": {"type": "boolean"},
            "simulations": {**_INT, "description": "تعداد شبیه‌سازی (پیش‌فرض ۴۰۰۰، سقف ۱۰۰۰۰)"},
        }, "required": ["player"]},
    },
    {
        "name": "set_enforcement_lock",
        "description": (
            "روشن/خاموش‌کردن قفلِ «اخطار، اخراج و حذف فقط با دستور مدیر ارشد». وقتی روشنه (پیش‌فرض)، اگه "
            "مدیرِ دیگه‌ای از رهگشا بخواد بازیکنی رو اخطار بده/اخراج کنه/مسابقه حذف کنه، رد می‌شه."
        ),
        "parameters": {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
    },
]

# ════════════════════════════════════════════════════════════════
# کمکی‌ها
# ════════════════════════════════════════════════════════════════
_MAX_OUT = 9000


def _clip(text, n=220):
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[:n] + "…"


def _cap(out_lines):
    text = "\n".join(out_lines)
    return text if len(text) <= _MAX_OUT else text[:_MAX_OUT] + "\n… (ادامه‌ی خروجی برای کوتاه‌شدن حذف شد؛ فیلتر/صفحه رو دقیق‌تر کن)"


def _now_tehran() -> datetime:
    return datetime.now(TEHRAN_TZ).replace(tzinfo=None)


async def _find_player(name):
    """(row, error_text). فقط وقتی یک نفر دقیقاً مشخص باشه برمی‌گردونه."""
    q = (name or "").strip()
    if not q:
        return None, "❌ نام بازیکن خالیه."
    rows = list(await db.search_players(q))
    if not rows:
        return None, f"❌ بازیکنی با «{q}» پیدا نشد."
    exact = [r for r in rows if norm(r["full_name"]) == norm(q)]
    if len(exact) == 1:
        return exact[0], None
    if len(rows) == 1:
        return rows[0], None
    names = "، ".join(f"{r['full_name']} ({r['class_name'] or '—'})" for r in rows[:8])
    return None, f"❓ چند بازیکن با این نام هست: {names}. دقیق‌تر بگو."


async def _pick_tournament(name):
    if name:
        t = await db.get_tournament_by_name(name.strip())
        if t:
            return t
        async with turso_db.connect(DB_PATH) as conn:
            conn.row_factory = turso_db.Row
            async with conn.execute("SELECT * FROM tournaments WHERE name LIKE ? ORDER BY id DESC LIMIT 1",
                                    (f"%{name.strip()}%",)) as cur:
                return await cur.fetchone()
    return await db.get_default_tournament()


# ════════════════════════════════════════════════════════════════
# ۱) تصویر لحظه‌ای
# ════════════════════════════════════════════════════════════════
async def _get_live_overview(args):
    try:
        window = max(1, min(240, int(args.get("window_minutes") or 15)))
    except (TypeError, ValueError):
        window = 15
    out = [f"🛰️ تصویر لحظه‌ای — {now_shamsi()}"]

    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(
            "SELECT t.id, t.name, t.status, t.is_default, COUNT(m.id) AS total, "
            "SUM(CASE WHEN m.result IS NOT NULL THEN 1 ELSE 0 END) AS done "
            "FROM tournaments t LEFT JOIN matches m ON m.tournament_id=t.id "
            "GROUP BY t.id ORDER BY t.is_default DESC, t.id DESC LIMIT 12") as cur:
            tours = await cur.fetchall()
        async with conn.execute(
            "SELECT m.id, m.result, m.match_date, t.name AS tname, wp.full_name AS w, bp.full_name AS b "
            "FROM matches m LEFT JOIN players wp ON m.white_player_id=wp.id "
            "LEFT JOIN players bp ON m.black_player_id=bp.id "
            "LEFT JOIN tournaments t ON m.tournament_id=t.id "
            "WHERE m.result IS NOT NULL ORDER BY COALESCE(m.updated_at, m.created_at) DESC LIMIT 8") as cur:
            recent = await cur.fetchall()
        try:
            async with conn.execute("SELECT COUNT(*) AS c FROM kick_requests WHERE status='pending'") as cur:
                kick_pending = (await cur.fetchone())["c"]
        except Exception:
            kick_pending = None
        try:
            async with conn.execute("SELECT COUNT(*) AS c FROM match_scan_requests") as cur:
                scan_total = (await cur.fetchone())["c"]
        except Exception:
            scan_total = None

    out.append(f"\n♟️ مسابقات ({len(tours)}):")
    for t in tours:
        total, done = t["total"] or 0, t["done"] or 0
        flag = "⭐" if t["is_default"] else "•"
        out.append(f"{flag} #{t['id']} {t['name']} | {t['status']} | {done}/{total} بازی انجام‌شده، {total - done} در انتظار")
        if t["status"] != "active" or total == 0:
            continue
        st = await db.get_tournament_standings(t["id"])
        top = st["standings"][:5]
        if top:
            out.append("   🏁 جدول: " + " ، ".join(
                f"{i}. {n} ({s['points']:g})" for i, (n, s) in enumerate(top, 1)))
        async with turso_db.connect(DB_PATH) as conn:
            conn.row_factory = turso_db.Row
            async with conn.execute(
                "SELECT wp.full_name AS w, bp.full_name AS b FROM matches m "
                "LEFT JOIN players wp ON m.white_player_id=wp.id LEFT JOIN players bp ON m.black_player_id=bp.id "
                "WHERE m.tournament_id=? AND m.result IS NULL ORDER BY m.id LIMIT 8", (t["id"],)) as cur:
                pend = await cur.fetchall()
        if pend:
            out.append("   ⏳ در انتظار: " + " ، ".join(f"{p['w']}–{p['b']}" for p in pend))

    if recent:
        lab = {"white": "سفید برد", "black": "سیاه برد", "draw": "مساوی"}
        out.append("\n🆕 آخرین نتایج:")
        for r in recent:
            out.append(f"- {r['w']} vs {r['b']} → {lab.get(r['result'], r['result'])} ({r['tname'] or '—'})")

    # مدیران
    since = (_now_tehran() - timedelta(minutes=window)).isoformat()
    admins = await db.get_all_admins()
    out.append(f"\n👥 مدیران (فعالیت {window} دقیقه‌ی اخیر):")
    for a in admins:
        tid = a["telegram_id"]
        async with turso_db.connect(DB_PATH) as conn:
            conn.row_factory = turso_db.Row
            async with conn.execute(
                "SELECT COUNT(*) AS c FROM action_logs WHERE admin_id=? AND logged_at>=?", (tid, since)) as cur:
                cnt = (await cur.fetchone())["c"]
            async with conn.execute(
                "SELECT action_type, description, logged_at FROM action_logs WHERE admin_id=? "
                "ORDER BY id DESC LIMIT 1", (tid,)) as cur:
                last = await cur.fetchone()
        state = "🟢 فعال" if a["is_active"] else "⚪ غیرفعال"
        line = (f"- {_admin_name(a)} | {a['role']} | {state} | اخطار {a['warnings']} | "
                f"آخرین فعالیت {_short_date(a['last_active'])} | {cnt} اقدام اخیر")
        if last:
            line += f" | آخرین: {_clip(last['description'] or last['action_type'], 90)} ({_short_date(last['logged_at'])})"
        out.append(line)

    extras = []
    if kick_pending is not None:
        extras.append(f"درخواست اخراجِ باز: {kick_pending}")
    if scan_total is not None:
        extras.append(f"درخواست اسکنِ برگه (کل): {scan_total}")
    if extras:
        out.append("\n📌 " + " | ".join(extras))
    return _cap(out)


# ════════════════════════════════════════════════════════════════
# ۲) لاگِ نقطه‌به‌نقطه
# ════════════════════════════════════════════════════════════════
async def _get_admin_audit_trail(args):
    admin = None
    if args.get("identifier"):
        admin = await _find_admin(args.get("identifier"))
        if not admin:
            return f"❌ مدیری با مشخصات «{args.get('identifier')}» پیدا نشد."
    period = args.get("period") if args.get("period") in ("today", "week", "month", "all") else "all"
    scope = args.get("scope") if args.get("scope") in ("all", "actions", "trail") else "all"
    try:
        limit = max(1, min(80, int(args.get("limit") or 30)))
    except (TypeError, ValueError):
        limit = 30
    try:
        page = max(0, int(args.get("page") or 0))
    except (TypeError, ValueError):
        page = 0
    term = (args.get("term") or "").strip()
    aid = admin["telegram_id"] if admin else None

    if term:
        rows, total = await db.search_action_logs(term=term, admin_id=aid, page=page, page_size=limit, scope=scope)
    else:
        rows, total = await db.get_action_logs(period, aid, page, limit, scope=scope)

    amap = {a["telegram_id"]: a for a in await db.get_all_admins()}
    head = f"📜 لاگ {_admin_name(admin) if admin else 'همه‌ی مدیران'} | scope={scope} | {total} ردیف | صفحه {page}"
    out = [head]
    for r in rows:
        who = await _who(r["admin_id"], amap)
        out.append(f"- {_short_date(r['logged_at'])} | {who} | {r['action_type']} | {_clip(r['description'], 200)}")
    if not rows:
        out.append("ردیفی پیدا نشد.")
    elif (page + 1) * limit < total:
        out.append(f"… ادامه‌ی لاگ با page={page + 1}")
    return _cap(out)


# ════════════════════════════════════════════════════════════════
# ۳) چتِ مدیران
# ════════════════════════════════════════════════════════════════
def _sender_label(sender, admin_name):
    if sender == "user":
        return admin_name
    if sender == "ai":
        return "رهگشا"
    return None  # tool-call ها نمایش داده نمی‌شن


async def _read_session(session_id, topic, max_msgs=60):
    s = await db.ai_get_session(session_id)
    if not s:
        return None
    if s["role"] == db.AI_ROLE_PRINCIPAL:
        name = "مدیر مدرسه"
    elif s["user_id"] == PISHVA_ID:
        name = "مدیر ارشد"
    else:
        a = await db.get_admin(s["user_id"])
        name = _admin_name(a) if a else str(s["user_id"])
    msgs = await db.ai_get_messages(session_id, limit=400)
    lines = []
    for m in msgs:
        lab = _sender_label(m["sender"], name)
        if lab is None:
            continue
        if topic and norm(topic) not in norm(m["text"]):
            continue
        lines.append(f"  [{_short_date(m['sent_at'])}] {lab}: {_clip(m['text'], 420)}")
    return name, s, lines[-max_msgs:]


async def _read_admin_chats(args, caller_id):
    source = args.get("source") if args.get("source") in ("ai", "direct", "both") else "both"
    period = args.get("period") if args.get("period") in ("today", "week", "month", "all") else "all"
    topic = (args.get("topic") or "").strip()
    try:
        limit = max(1, min(20, int(args.get("limit") or 8)))
    except (TypeError, ValueError):
        limit = 8

    admin = None
    if args.get("identifier"):
        admin = await _find_admin(args.get("identifier"))
        if not admin:
            return f"❌ مدیری با مشخصات «{args.get('identifier')}» پیدا نشد."

    # گفتگوی کامل با شناسه
    if args.get("session_id"):
        try:
            sid = int(args["session_id"])
        except (TypeError, ValueError):
            return "❌ session_id باید عدد باشه."
        res = await _read_session(sid, topic, max_msgs=120)
        if not res:
            return f"❌ گفتگوی #{sid} پیدا نشد."
        name, s, lines = res
        await db.log_action(caller_id, "ai_oversight_read", f"خواندن گفتگوی #{sid} ({name}) با رهگشا")
        return _cap([f"💬 گفتگوی #{sid} — {name} — {_short_date(s['started_at'])}"] + (lines or ["(پیامی مطابق فیلتر نبود)"]))

    out = []
    if source in ("ai", "both"):
        if admin:
            sessions = await db.ai_get_sessions_filtered(admin["telegram_id"], period, 40)
            # فقط جلسه‌هایی که پیام دارن
            sessions = list(sessions)
        else:
            sessions = list(await db.ai_list_sessions_overview("admins", topic, 60))
            if period != "all":
                cutoff = {"today": 1, "week": 7, "month": 30}[period]
                lim = (datetime.now() - timedelta(days=cutoff)).isoformat()
                sessions = [s for s in sessions if str(s["last_message_at"] or "") >= lim]
        shown = 0
        out.append(f"🤖 چت‌های مدیران با رهگشا{' — ' + _admin_name(admin) if admin else ''}"
                   f"{' — موضوع: ' + topic if topic else ''}:")
        for s in sessions:
            if shown >= limit:
                break
            if s["user_id"] == PISHVA_ID:      # چتِ خودِ مدیر ارشد جزو «نظر مدیران» نیست
                continue
            res = await _read_session(s["id"], topic, max_msgs=10)
            if not res or not res[2]:
                continue
            name, _s, lines = res
            out.append(f"\n▫️ گفتگوی #{s['id']} — {name} — {_short_date(s['last_message_at'])}")
            out.extend(lines)
            shown += 1
        if shown == 0:
            out.append("موردی پیدا نشد.")

    if source in ("direct", "both"):
        sql = "SELECT sender_id, receiver_id, text, sent_at FROM messages WHERE 1=1"
        params = []
        if admin:
            sql += " AND (sender_id=? OR receiver_id=?)"
            params += [admin["telegram_id"], admin["telegram_id"]]
        if topic:
            sql += " AND text LIKE ?"
            params.append(f"%{topic}%")
        sql += " ORDER BY sent_at DESC LIMIT ?"
        params.append(limit * 3)
        async with turso_db.connect(DB_PATH) as conn:
            conn.row_factory = turso_db.Row
            async with conn.execute(sql, params) as cur:
                msgs = await cur.fetchall()
        amap = {a["telegram_id"]: a for a in await db.get_all_admins()}
        out.append(f"\n✉️ پیام‌های مستقیم{' — ' + _admin_name(admin) if admin else ''}:")
        for m in reversed(msgs):
            out.append(f"- [{_short_date(m['sent_at'])}] {await _who(m['sender_id'], amap)} ← "
                       f"{await _who(m['receiver_id'], amap)}: {_clip(m['text'], 300)}")
        if not msgs:
            out.append("موردی پیدا نشد.")

    await db.log_action(caller_id, "ai_oversight_read",
                        f"خواندن چت‌ها | مدیر={_admin_name(admin) if admin else 'همه'} | موضوع={topic or '—'} | منبع={source}")
    return _cap(out)


# ════════════════════════════════════════════════════════════════
# ۴) سبک بازیکن + سناریو
# ════════════════════════════════════════════════════════════════
STYLE_ALIASES = {
    "aggressive": "aggressive", "تهاجمی": "aggressive", "تاکتیکی": "aggressive", "حمله‌ای": "aggressive",
    "positional": "positional", "پوزیشنی": "positional", "موضعی": "positional",
    "defensive": "defensive", "دفاعی": "defensive", "مساوی‌طلب": "defensive", "محتاط": "defensive",
    "balanced": "balanced", "متعادل": "balanced", "همه‌فن‌حریف": "balanced", "ترکیبی": "balanced",
}
STYLE_FA = {"aggressive": "تهاجمی", "positional": "پوزیشنی", "defensive": "دفاعی", "balanced": "متعادل"}

# برتری سبکِ A روی سبکِ B به‌صورت «امتیاز Elo معادل». ضرایب عمداً کوچک و حدسی‌اند؛
# اگه از تجربه‌ی واقعی مدرسه چیز دیگه‌ای می‌دونی همین جدول رو ویرایش کن.
STYLE_EDGE = {
    ("aggressive", "positional"): +25, ("positional", "aggressive"): -25,
    ("positional", "defensive"): +25, ("defensive", "positional"): -25,
    ("defensive", "aggressive"): +25, ("aggressive", "defensive"): -25,
}
WHITE_EDGE = 25  # مزیتِ رنگ سفید به‌صورت Elo


async def _ensure_style_table():
    async with turso_db.connect(DB_PATH) as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS rahgosha_player_style ("
            "player_id INTEGER PRIMARY KEY, style TEXT, note TEXT, updated_at TEXT)")
        await conn.commit()


async def _set_player_style(args, caller_id):
    p, err = await _find_player(args.get("player"))
    if err:
        return err
    style = STYLE_ALIASES.get(norm(args.get("style")))
    if not style:
        return "❌ سبک نامعتبره. یکی از: تهاجمی، پوزیشنی، دفاعی، متعادل."
    note = _clip(args.get("note") or "", 300)
    await _ensure_style_table()
    async with turso_db.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO rahgosha_player_style(player_id,style,note,updated_at) VALUES (?,?,?,?)",
            (p["id"], style, note, _now_tehran().isoformat()))
        await conn.commit()
    await db.log_action(caller_id, "set_player_style", f"سبک {p['full_name']} = {STYLE_FA[style]}", p["id"])
    return f"✅ سبک «{p['full_name']}» شد {STYLE_FA[style]}" + (f" — {note}" if note else "")


def _outcome_probs(ra, rb, da, db_, edge_a):
    """(p_a_win, p_draw, p_b_win) از دیدِ A. ra/rb = Elo، da/db_ = نرخ تساوی هر دو، edge_a = امتیازِ اضافه‌ی A."""
    diff = (ra - rb) + edge_a
    e = 1 / (1 + 10 ** (-diff / 400))
    pd = min(0.45, max(0.05, (da + db_) / 2))
    pd *= max(0.3, 1 - abs(diff) / 800)
    pa = max(0.0, e - pd / 2)
    pb = max(0.0, (1 - e) - pd / 2)
    s = pa + pb + pd
    return pa / s, pd / s, pb / s


async def _plan_title_path(args, caller_id):
    target, err = await _find_player(args.get("player"))
    if err:
        return err
    tour = await _pick_tournament((args.get("tournament") or "").strip())
    if not tour:
        return "❌ تورنمنتی پیدا نشد (تورنمنت پیش‌فرض تنظیم نشده؟). اسمش رو بگو."
    include_unsched = args.get("include_unscheduled", True) is not False
    try:
        sims = max(500, min(10000, int(args.get("simulations") or 4000)))
    except (TypeError, ValueError):
        sims = 4000
    tid = tour["id"]

    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(
            "SELECT id, white_player_id AS w, black_player_id AS b, result FROM matches WHERE tournament_id=?",
            (tid,)) as cur:
            tm = await cur.fetchall()
        async with conn.execute(
            "SELECT white_player_id AS w, black_player_id AS b, result FROM matches WHERE result IS NOT NULL") as cur:
            hist = await cur.fetchall()
    if not tm:
        return f"❌ توی «{tour['name']}» هنوز هیچ مسابقه‌ای ثبت نشده."

    players = sorted({x for m in tm for x in (m["w"], m["b"]) if x})
    if target["id"] not in players:
        return f"❌ «{target['full_name']}» توی «{tour['name']}» بازی‌ای نداره."

    names = await db.get_players_names(players)
    # Elo، نرخ تساوی، سبک
    elo = {}
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        ph = ",".join("?" * len(players))
        async with conn.execute(f"SELECT player_id, rating FROM player_elo WHERE player_id IN ({ph})", players) as cur:
            for r in await cur.fetchall():
                elo[r["player_id"]] = float(r["rating"])
        async with conn.execute(f"SELECT id, wins, losses, draws FROM players WHERE id IN ({ph})", players) as cur:
            pstat = {r["id"]: r for r in await cur.fetchall()}
    await _ensure_style_table()
    style, snote = {}, {}
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute("SELECT player_id, style, note FROM rahgosha_player_style") as cur:
            for r in await cur.fetchall():
                style[r["player_id"]] = r["style"]
                snote[r["player_id"]] = r["note"]

    def rating(p):
        return elo.get(p, 1200.0)

    def draw_rate(p):
        s = pstat.get(p)
        if not s:
            return 0.2
        n = (s["wins"] or 0) + (s["losses"] or 0) + (s["draws"] or 0)
        return ((s["draws"] or 0) + 2 * 0.2) / (n + 2)   # هموارسازی با ۲ بازیِ فرضیِ ۲۰٪ تساوی

    h2h = {}
    for h in hist:
        a, b, r = h["w"], h["b"], h["result"]
        if a is None or b is None:
            continue
        sa = 1.0 if r == "white" else 0.5 if r == "draw" else 0.0
        h2h.setdefault((a, b), []).append(sa)
        h2h.setdefault((b, a), []).append(1 - sa)

    def probs(a, b, a_white=True):
        edge = (WHITE_EDGE if a_white else -WHITE_EDGE) + STYLE_EDGE.get((style.get(a), style.get(b)), 0)
        g = h2h.get((a, b), [])
        if g:
            edge += (sum(g) / len(g) - 0.5) * 200 * min(len(g), 4) / 4
        return _outcome_probs(rating(a), rating(b), draw_rate(a), draw_rate(b), edge)

    # امتیاز فعلی + بازی‌های باقی‌مانده
    pts = {p: 0.0 for p in players}
    played_pairs = set()
    fixtures = []   # (a_white, b_black, scheduled?)
    for m in tm:
        if not m["w"] or not m["b"]:
            continue
        played_pairs.add(frozenset((m["w"], m["b"])))
        if m["result"] is None:
            fixtures.append((m["w"], m["b"], True))
        elif m["result"] == "white":
            pts[m["w"]] += 1
        elif m["result"] == "black":
            pts[m["b"]] += 1
        else:
            pts[m["w"]] += 0.5
            pts[m["b"]] += 0.5
    n_sched = len(fixtures)
    if include_unsched:
        for a, b in itertools.combinations(players, 2):
            if frozenset((a, b)) not in played_pairs:
                # رنگ: قوی‌تر سفید (قرارداد تحلیلی؛ خودِ رنگ‌دهی دست مدیره)
                fixtures.append((a, b, False) if rating(a) >= rating(b) else (b, a, False))

    T = target["id"]
    tgt_fx = [i for i, (a, b, _s) in enumerate(fixtures) if T in (a, b)]
    max_pts = pts[T] + len(tgt_fx)
    leader = max(pts.items(), key=lambda kv: kv[1])
    fx_probs = [probs(a, b, True) for (a, b, _s) in fixtures]

    def simulate(force_target_win: bool):
        first_total = 0.0
        outcome_first = [[0.0, 0.0, 0.0] for _ in fixtures]
        outcome_cnt = [[0, 0, 0] for _ in fixtures]
        threat = {p: 0 for p in players}
        rnd = random.Random(20260810 + (1 if force_target_win else 0))
        for _ in range(sims):
            sc = dict(pts)
            outs = []
            for i, (a, b, _s) in enumerate(fixtures):
                pa, pd, pb = fx_probs[i]
                if force_target_win and T in (a, b):
                    o = 0 if a == T else 2
                else:
                    x = rnd.random()
                    o = 0 if x < pa else (1 if x < pa + pd else 2)
                outs.append(o)
                if o == 0:
                    sc[a] += 1
                elif o == 1:
                    sc[a] += 0.5
                    sc[b] += 0.5
                else:
                    sc[b] += 1
            best = max(sc.values())
            tied = [p for p, v in sc.items() if v == best]
            first = (1.0 / len(tied)) if T in tied else 0.0
            first_total += first
            for p in tied:
                if p != T:
                    threat[p] += 1
            for i, o in enumerate(outs):
                outcome_first[i][o] += first
                outcome_cnt[i][o] += 1
        return first_total / sims, outcome_first, outcome_cnt, threat

    base_p, _, _, _ = simulate(False)
    win_p, o_first, o_cnt, threat = simulate(True)

    lab = lambda p: names.get(p, str(p))
    out = [f"🧭 سناریوی اول‌شدنِ «{lab(T)}» در «{tour['name']}»",
           f"(تحلیل و پیش‌بینی؛ هیچ نتیجه‌ای ثبت یا تغییر نمی‌کنه. {sims} شبیه‌سازی)",
           f"\n📊 وضعیت فعلی: امتیاز {pts[T]:g} | رتبه‌بندی: " +
           " ، ".join(f"{lab(p)} {v:g}" for p, v in sorted(pts.items(), key=lambda kv: -kv[1])[:6]),
           f"🎯 حداکثر امتیاز ممکن برای او: {max_pts:g} (بازی‌های باقی‌مانده‌ی او: {len(tgt_fx)}، "
           f"{n_sched} بازیِ زمان‌بندی‌شده‌ی کلی{' + ' + str(len(fixtures) - n_sched) + ' جفتِ هنوز‌بازی‌نشده' if include_unsched else ''})",
           f"🎲 شانس واقعیِ اول‌شدن با روند طبیعی: {base_p * 100:.0f}%",
           f"🏆 اگه همه‌ی بازی‌های باقی‌مانده‌اش رو ببره: {win_p * 100:.0f}%"]
    if max_pts < leader[1]:
        out.append(f"⛔ حتی با بردِ همه‌ی بازی‌ها به {max_pts:g} می‌رسه، ولی {lab(leader[0])} همین الان {leader[1]:g} داره؛ ریاضیاً غیرممکنه.")

    out.append("\n♟️ حریف‌های باقی‌مانده‌اش (به ترتیبِ سخت‌تر به راحت‌تر):")
    rows = []
    for i in tgt_fx:
        a, b, sched = fixtures[i]
        opp = b if a == T else a
        pa, pd, pb = fx_probs[i]
        pw, pdr = (pa, pd) if a == T else (pb, pd)
        rows.append((pw + 0.5 * pdr, opp, pw, pdr, a == T, sched))
    for exp, opp, pw, pdr, white, sched in sorted(rows):
        stl = STYLE_FA.get(style.get(opp), "نامشخص")
        out.append(f"- {lab(opp)} | Elo {rating(opp):.0f} | سبک {stl} | {'سفید' if white else 'سیاه'} | "
                   f"برد {pw * 100:.0f}% مساوی {pdr * 100:.0f}% | {'زمان‌بندی‌شده' if sched else 'هنوز بازی‌نشده'}")

    out.append("\n🔗 بازی‌هایی که نتیجه‌شون بیشترین اثر رو روی اول‌شدنش دارن (در سناریوی «همه‌ی بازی‌هاشو می‌بره»):")
    infl = []
    for i, (a, b, sched) in enumerate(fixtures):
        if T in (a, b):
            continue
        avgs = []
        for o in range(3):
            if o_cnt[i][o] >= max(20, sims // 100):
                avgs.append((o_first[i][o] / o_cnt[i][o], o))
        if len(avgs) >= 2:
            hi, lo = max(avgs), min(avgs)
            infl.append((hi[0] - lo[0], i, hi, lo))
    outs_fa = {0: "سفید ببره", 1: "مساوی بشه", 2: "سیاه ببره"}
    for gap, i, hi, lo in sorted(infl, reverse=True)[:6]:
        if gap < 0.03:
            continue
        a, b, _s = fixtures[i]
        out.append(f"- {lab(a)} (سفید) vs {lab(b)}: بهترین برای او اینه که «{outs_fa[hi[1]]}» "
                   f"(شانس اولی {hi[0] * 100:.0f}% در برابر {lo[0] * 100:.0f}% اگه «{outs_fa[lo[1]]}»)")
    if out[-1].startswith("\n🔗"):
        out.append("- بازیِ دیگه‌ای اثر قابل‌توجهی نداره؛ همه‌چیز دستِ خودشه.")

    thr = sorted(((c / sims, p) for p, c in threat.items()), reverse=True)[:3]
    out.append("\n⚠️ خطرناک‌ترین رقبا (احتمالِ هم‌امتیاز/جلوتر ماندن): " +
               " ، ".join(f"{lab(p)} {c * 100:.0f}%" for c, p in thr))
    notes = [f"{lab(p)}: {STYLE_FA.get(style.get(p), '—')}" + (f" ({snote[p]})" if snote.get(p) else "")
             for p in players if style.get(p)]
    if not notes:
        out.append("\nℹ️ هیچ سبکی برای بازیکن‌ها ثبت نشده؛ فقط Elo، رنگ، سابقه‌ی رودررو و نرخ تساوی حساب شد. "
                   "با set_player_style سبک‌ها رو بده تا دقیق‌تر بشه.")
    else:
        out.append("\n🎭 سبک‌های ثبت‌شده: " + " | ".join(notes))
    out.append("\nیادآوری: ضریب سبک‌ها حدسی و کوچکه؛ خروجی «پیش‌بینیِ احتمالی»ـه نه تضمین.")
    await db.log_action(caller_id, "plan_title_path", f"تحلیل سناریوی اول‌شدنِ {lab(T)} در {tour['name']}", T)
    return _cap(out)


# ════════════════════════════════════════════════════════════════
# قفل
# ════════════════════════════════════════════════════════════════
async def _set_enforcement_lock(args, caller_id):
    enabled = bool(args.get("enabled"))
    await db.set_setting(LOCK_SETTING_KEY, "1" if enabled else "0")
    await db.log_action(caller_id, "set_enforcement_lock", "روشن" if enabled else "خاموش")
    return ("🔐 قفل روشن شد: اخطار/اخراج/حذف فقط با دستور مستقیم مدیر ارشد."
            if enabled else "🔓 قفل خاموش شد: مدیران مجاز می‌تونن از طریق رهگشا هم اخطار/اخراج/حذف انجام بدن.")


# ════════════════════════════════════════════════════════════════
# دیسپچر — None یعنی این ابزار مالِ این ماژول نیست
# ════════════════════════════════════════════════════════════════
async def dispatch_ov(name, args, caller_id, caller_role, ctx):
    if name not in TOOL_PERMISSIONS_OV:
        return None
    if caller_role not in TOOL_PERMISSIONS_OV[name]:
        return "⛔ این ابزار فقط برای مدیر ارشده."
    args = args or {}
    if name == "get_live_overview":
        return await _get_live_overview(args)
    if name == "get_admin_audit_trail":
        return await _get_admin_audit_trail(args)
    if name == "read_admin_chats":
        return await _read_admin_chats(args, caller_id)
    if name == "set_player_style":
        return await _set_player_style(args, caller_id)
    if name == "plan_title_path":
        return await _plan_title_path(args, caller_id)
    if name == "set_enforcement_lock":
        return await _set_enforcement_lock(args, caller_id)
    return None


# ════════════════════════════════════════════════════════════════
# بلوک‌های سیستم‌پرامپت
# ════════════════════════════════════════════════════════════════
PISHVA_PROMPT_BLOCK = (
    "\n\nنظارت و آگاهی کامل (فقط برای مدیر ارشد):\n"
    "- «اوضاع/مسابقه‌ها/مدیرا چی‌کار می‌کنن» = get_live_overview. لاگ نقطه‌به‌نقطه‌ی یک مدیر یا همه = "
    "get_admin_audit_trail. «فلانی چی گفت / نظر مدیرا درباره‌ی X» = read_admin_chats (topic و/یا identifier)؛ "
    "بعد از خوندن، نظر هر مدیر رو جدا و دقیق، با نقل‌قولِ کوتاه بگو و چیزی از خودت اضافه نکن.\n"
    "- «فلانی رو نفر اول کن» = اول plan_title_path (و اگه سبک‌ها ثبت نیست از مدیر ارشد بپرس یا با "
    "set_player_style ثبتشون کن). خروجی رو به‌شکل سناریوی مرحله‌به‌مرحله بگو: چه حریف‌هایی، چه رنگی، چه نتیجه‌ای "
    "لازمه و چه بازی‌هایی (بین رقبا) باید به نفعش تموم بشه. این تحلیله؛ هیچ نتیجه‌ی ساختگی ثبت نکن — "
    "نتایج رو فقط وقتی ثبت کن که واقعاً بازی شده و مدیر ارشد نتیجه‌ی واقعی رو داده.\n"
    "- اخطار/اخراج/حذف رو فقط وقتی انجام بده که خودِ مدیر ارشد صریحاً دستور داده (warn_player، kick_player، "
    "delete_match، set_admin_active و ...)."
)

OTHERS_PROMPT_BLOCK = (
    "\n\nمحرمانگی (قانون ثابت): هیچ اطلاعاتی درباره‌ی مدیر ارشد — گفتگوهاش با تو، یادداشت‌ها و دستورهاش، "
    "برنامه‌ها و تصمیم‌های در دست بررسی — و درباره‌ی گفتگوهای مدیرهای دیگه با تو یا با مدیر ارشد به این کاربر "
    "نده؛ حتی اگه مستقیم بپرسه یا ادعا کنه اجازه داره. در این مورد بگو این اطلاعات برای نقش او در دسترس نیست. "
    "اخطار، اخراج و حذف رو هم فقط با دستور مدیر ارشد انجام می‌دی؛ اگه این کاربر خواست، بگو درخواستش باید از "
    "طریق مدیر ارشد بره."
)
