"""
ai_proactive.py — «رهگشا خودش پیام می‌دهد»

رهگشا هر چند دقیقه داده‌های همه‌ی بخش‌ها را برای هر مدیر بررسی می‌کند و فقط وقتی چیزِ «واقعاً مهم» پیدا کرد،
یک پیام کوتاه و شخصی‌سازی‌شده (با لحن و تنظیماتِ همان آدم) برایش می‌فرستد. ساکت‌بودن پیش‌فرض است.

۱) ۱۹ مدلِ بررسیِ داخلی (TOPICS): مثلاً چند روزه سر نزدی، کارِ عقب‌افتاده، نتیجه‌ی ثبت‌نشده، تورنمنتِ راکد،
   درخواست‌های منتظر، مدیرِ ساکت، انبوه اخطار، فعالیتِ غیرعادی، هشدار سیستم، تعطیلیِ فردا، بازیکنِ درخشان (نظرخواهی)…
۲) «قانون‌های من» (watch rules): هر مدیر با چت می‌گوید «اگر فلان شد بهم بگو» و رهگشا خودش یک قانونِ دقیق می‌سازد
   (ابزار create_watch_rule). این قانون‌ها اجرایِ دقیق‌اند (نه حدسِ مدل) و به ساعت سکوت احترام می‌گذارند.
۳) هوشمندی: هر یافته امتیازِ اهمیت (۱ تا ۵) دارد؛ فقط ≥ آستانه می‌رود؛ سقفِ روزانه و فاصله‌ی حداقلی؛ ضدِ تکرار (cooldown)؛
   قاضیِ نهاییِ مدل می‌تواند بگوید «نفرست»؛ بازخوردِ 👎 همان موضوع را برای همان آدم ساکت می‌کند.
۴) تنظیم: هر نفر برای خودش (روشن/خاموش + ساعت سکوت؛ پیش‌فرض ۲۳:۴۵ تا ۰۹:۴۰) و مدیر ارشد برای همه
   (/proactive یا «پنل من» → مدیریت دستیار): کلی، دسته‌ها، سقف‌ها، آستانه، افراد.
"""
import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta

import jdatetime
import turso_db
import database as db
from config import DB_PATH, PISHVA_ID, ROLE_PISHVA, ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER
from helpers import TEHRAN_TZ, normalize_digits

logger = logging.getLogger(__name__)

ALL_ROLES = [ROLE_PISHVA, ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER]
MGR = [ROLE_PISHVA, ROLE_TOURNAMENT_MANAGER]
SEC = [ROLE_PISHVA, ROLE_SECURITY_MANAGER]
BOSS = [ROLE_PISHVA]
DEFAULT_QUIET = "23:45-09:40"

CFG_DEFAULTS = {
    "aipro_on": "1",            # کلید کلی (روشن/خاموش)
    "aipro_max_day": "2",       # حداکثر پیامِ خودکار در روز برای هر نفر
    "aipro_gap_h": "4",         # حداقل فاصله‌ی دو پیام (ساعت)
    "aipro_min_imp": "3",       # آستانه‌ی اهمیت (۱ تا ۵)
    "aipro_quiet": DEFAULT_QUIET,
    "aipro_llm": "1",           # قاضی/نویسنده‌ی مدل
}


# ═══════════════════════ زیرساخت ═══════════════════════
async def _ensure_tables():
    async with turso_db.connect(DB_PATH) as conn:
        for q in (
            "CREATE TABLE IF NOT EXISTS aipro_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, topic TEXT, dkey TEXT, "
            "importance INTEGER, outcome TEXT, text TEXT, at TEXT, feedback INTEGER)",
            "CREATE TABLE IF NOT EXISTS aipro_state (user_id INTEGER PRIMARY KEY, snooze_until TEXT, muted TEXT DEFAULT '{}')",
            "CREATE TABLE IF NOT EXISTS aipro_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, notify_id INTEGER, "
            "title TEXT, metric TEXT, target TEXT DEFAULT '', op TEXT, value REAL, cooldown_h REAL DEFAULT 12, repeat INTEGER DEFAULT 0, "
            "active INTEGER DEFAULT 1, created_at TEXT, last_fired TEXT, cursor INTEGER DEFAULT 0)",
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


def _now_teh() -> datetime:
    return datetime.now(TEHRAN_TZ).replace(tzinfo=None)


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts)[:19])
    except Exception:
        return None


def _age_days(ts, now):
    d = _parse(ts)
    return None if d is None else max(0.0, (now - d).total_seconds() / 86400)


async def cfg(key):
    return str(await db.get_setting(key, CFG_DEFAULTS[key]))


async def _json_setting(key, default):
    try:
        v = json.loads(await db.get_setting(key, "") or "")
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


# ═══════════════════════ ساعت سکوت ═══════════════════════
def parse_quiet(spec):
    """'23:45-09:40' → (1425, 580). 'off' → None. نامعتبر → پیش‌فرض."""
    spec = normalize_digits(str(spec or "")).strip().lower()
    if spec in ("off", "none", "no", "خاموش", "بدون"):
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*[-–—تا ]+\s*(\d{1,2}):(\d{2})", spec.replace("تا", "-"))
    if not m:
        return parse_quiet(DEFAULT_QUIET)
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return parse_quiet(DEFAULT_QUIET)
    return h1 * 60 + m1, h2 * 60 + m2


def normalize_quiet(spec):
    """برای ذخیره: 'HH:MM-HH:MM' یا 'off'؛ نامعتبر → None."""
    spec = normalize_digits(str(spec or "")).strip().lower()
    if spec in ("off", "none", "no", "خاموش", "بدون"):
        return "off"
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})", spec.replace("تا", "-"))
    if not m:
        return None
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return None
    return f"{h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}"


def in_quiet(spec, now=None) -> bool:
    q = parse_quiet(spec)
    if q is None:
        return False
    now = now or _now_teh()
    m = now.hour * 60 + now.minute
    a, b = q
    return (a <= m < b) if a < b else (m >= a or m < b)


async def person_quiet_spec(uid, prefs=None):
    import ai_persona
    prefs = prefs if prefs is not None else await ai_persona.get_prefs(uid)
    return prefs.get("pro_quiet") or await cfg("aipro_quiet")


# ═══════════════════════ مدلِ یافته ═══════════════════════
def F(topic, dkey, imp, facts, cooldown_h, hint="", opinion=False):
    return {"topic": topic, "dkey": str(dkey), "imp": int(imp), "facts": facts, "cool": cooldown_h, "hint": hint, "opinion": opinion}


class Env:
    def __init__(self, uid, role, admin, now):
        self.uid, self.role, self.admin, self.now = uid, role, admin, now          # now: ساعتِ سرور (مثل بقیه‌ی جدول‌ها)

    async def rows(self, sql, args=()):
        return await _rows(sql, args)


def _name(r, *keys):
    for k in keys:
        try:
            v = r[k]
        except Exception:
            v = None
        if v:
            return str(v)
    return "—"


# ═══════════════════════ ۱۹ مدلِ بررسی ═══════════════════════
async def d_self_inactive(E):
    a = E.admin
    if not a:
        return []
    days = _age_days(a["last_active"], E.now)
    if days is None or days < 3:
        return []
    b, imp = (0, 2) if days < 5 else (1, 3) if days < 10 else (2, 4)
    return [F("self_inactive", f"b{b}", imp, f"آخرین فعالیتِ این شخص در ربات حدود {int(days)} روز پیش بوده.", 72,
              "با مهربانی حال‌واحوال بپرس و بپرس مشکلی یا چیزی هست که کمک لازم داشته باشه؛ سرزنش نکن.")]


async def d_my_tasks(E):
    rs = await E.rows("SELECT title, assigned_at FROM tasks WHERE assigned_to=? AND status='pending' ORDER BY assigned_at", (E.uid,))
    if not rs:
        return []
    days = _age_days(rs[0]["assigned_at"], E.now) or 0
    if days < 2:
        return []
    imp = 3 if days < 5 else 4
    return [F("my_tasks", len(rs), imp, f"{len(rs)} وظیفه‌ی انجام‌نشده داره؛ قدیمی‌ترینش «{_name(rs[0], 'title')}» و {int(days)} روز پیش به او سپرده شده.", 48,
              "یادآوری دوستانه بده و بپرس کمک یا فرصت بیشتری لازم داره یا مانعی هست.")]


async def d_unread_messages(E):
    rs = await E.rows("SELECT sent_at FROM messages WHERE receiver_id=? AND is_read=0 ORDER BY sent_at", (E.uid,))
    if not rs:
        return []
    days = _age_days(rs[0]["sent_at"], E.now) or 0
    if days < 1:
        return []
    return [F("unread_messages", len(rs), 3 if days < 3 else 4, f"{len(rs)} پیام مستقیمِ نخوانده دارد؛ قدیمی‌ترینش {int(days)} روز پیش رسیده.", 24,
              "کوتاه یادآوری کن که پیام‌هایش را ببیند.")]


async def d_pending_results(E):
    rs = await E.rows("SELECT created_at FROM matches WHERE result IS NULL ORDER BY created_at")
    if len(rs) < 5:
        return []
    days = _age_days(rs[0]["created_at"], E.now) or 0
    if days < 3:
        return []
    imp = 4 if (days >= 7 or len(rs) >= 15) else 3
    return [F("pending_results", f"{len(rs) // 5}", imp, f"{len(rs)} بازی هنوز نتیجه‌ی ثبت‌شده ندارد و قدیمی‌ترینش {int(days)} روز پیش ساخته شده.", 48,
              "پیشنهاد بده نتیجه‌ها را ثبت/پیگیری کنند و بپرس کمک بخواهد برای ثبتِ گروهی.")]


async def d_stale_tournament(E):
    out = []
    for t in await E.rows("SELECT id, name FROM tournaments WHERE status='active'"):
        r = await E.rows("SELECT MAX(created_at) AS m, COUNT(*) AS n FROM matches WHERE tournament_id=?", (t["id"],))
        if not r or not r[0]["n"]:
            continue
        days = _age_days(r[0]["m"], E.now) or 0
        if days >= 10:
            out.append(F("stale_tournament", t["id"], 3, f"تورنمنتِ «{_name(t, 'name')}» هنوز فعال است ولی {int(days)} روز است بازیِ تازه‌ای ثبت نشده.", 120,
                         "بپرس برنامه‌اش چیست: ادامه، مکث یا پایان؟ نظر کوتاه خودت را هم بگو.", opinion=True))
    return out[:1]


async def d_scan_requests(E):
    rs = await E.rows("SELECT created_at FROM match_scan_requests WHERE status='pending' ORDER BY created_at")
    if not rs:
        return []
    h = (_age_days(rs[0]["created_at"], E.now) or 0) * 24
    if h < 12:
        return []
    return [F("scan_requests", len(rs), 3 if h < 48 else 4, f"{len(rs)} درخواستِ ثبتِ نتیجه با عکس منتظر تأیید است؛ قدیمی‌ترینش حدود {int(h)} ساعت.", 24,
              "یادآوری کن بررسی و تأییدشان کند.")]


async def d_kick_requests(E):
    rs = await E.rows("SELECT requested_at FROM kick_requests WHERE status='pending' ORDER BY requested_at")
    if not rs:
        return []
    h = (_age_days(rs[0]["requested_at"], E.now) or 0) * 24
    if h < 6:
        return []
    return [F("kick_requests", len(rs), 4, f"{len(rs)} درخواستِ اخراجِ بازیکن منتظر تصمیم است؛ قدیمی‌ترینش حدود {int(h)} ساعت.", 12,
              "با لحنِ مسئولانه یادآوری کن و بپرس چه تصمیمی دارد.")]


async def d_access_requests(E):
    rs = await E.rows("SELECT requested_at FROM access_requests WHERE status='pending' ORDER BY requested_at")
    if not rs:
        return []
    h = (_age_days(rs[0]["requested_at"], E.now) or 0) * 24
    if h < 12:
        return []
    return [F("access_requests", len(rs), 3, f"{len(rs)} درخواستِ دسترسی به ربات منتظر بررسی است؛ قدیمی‌ترینش حدود {int(h)} ساعت.", 24,
              "کوتاه یادآوری کن.")]


async def d_admin_quiet(E):
    rs = await E.rows("SELECT telegram_id, COALESCE(NULLIF(display_name,''), full_name, username) AS nm, last_active FROM admins "
                      "WHERE is_active=1 AND telegram_id != ?", (E.uid,))
    quiet = []
    for r in rs:
        d = _age_days(r["last_active"], E.now)
        if d is not None and d >= 5:
            quiet.append((r["telegram_id"], _name(r, "nm"), int(d)))
    if not quiet:
        return []
    quiet.sort(key=lambda x: -x[2])
    imp = 4 if quiet[0][2] >= 10 else 3
    names = "، ".join(f"{n} ({d} روز)" for _i, n, d in quiet[:5])
    return [F("admin_quiet", ",".join(str(i) for i, *_ in quiet[:5]), imp, f"این مدیرها چند روزه سر نزده‌اند: {names}.", 96,
              "بپرس می‌خواهد خودم به آن‌ها پیامِ ملایم بدهم یا خودش پیگیری می‌کند؛ پیشنهادِ مشخص بده.", opinion=True)]


async def d_warning_pileup(E):
    rs = await E.rows("SELECT id, full_name, warnings FROM players WHERE status='active' AND warnings>=3 ORDER BY warnings DESC LIMIT 5")
    if not rs:
        return []
    imp = 4 if rs[0]["warnings"] >= 5 else 3
    txt = "، ".join(f"{_name(r, 'full_name')} ({r['warnings']} اخطار)" for r in rs)
    return [F("warning_pileup", "|".join(f"{r['id']}:{r['warnings']}" for r in rs), imp, f"بازیکن‌هایی با اخطارِ زیاد هنوز فعال‌اند: {txt}.", 168,
              "نظرت را درباره‌ی گام بعدی (تذکر، تعلیق، بررسی) کوتاه بگو و بپرس تصمیم با خودش.", opinion=True)]


async def d_warning_spike(E):
    since = (E.now - timedelta(hours=24)).isoformat()
    r = await E.rows("SELECT COUNT(*) AS n FROM warnings_log WHERE issued_at>=?", (since,))
    n = r[0]["n"] if r else 0
    if n < 6:
        return []
    return [F("warning_spike", E.now.strftime("%Y-%m-%d"), 3 if n < 12 else 4, f"در ۲۴ ساعت گذشته {n} اخطار صادر شده که بیشتر از معمول است.", 48,
              "بپرس الگوی خاصی (یک کلاس/مدیر) پشتش هست یا نه.")]


async def d_unusual_activity(E):
    since = (E.now - timedelta(hours=3)).isoformat()
    rs = await E.rows(
        "SELECT admin_id, COUNT(*) AS n FROM action_logs WHERE logged_at>=? AND (action_type LIKE '%delete%' OR action_type LIKE '%kick%' "
        "OR action_type LIKE '%remove%') AND admin_id != ? GROUP BY admin_id HAVING n>=8 ORDER BY n DESC LIMIT 1", (since, E.uid))
    if not rs:
        return []
    a = await db.get_admin(rs[0]["admin_id"])
    nm = (a["display_name"] or a["full_name"]) if a else str(rs[0]["admin_id"])
    return [F("unusual_activity", f"{rs[0]['admin_id']}:{E.now:%Y-%m-%d-%H}", 4, f"{nm} در ۳ ساعت اخیر {rs[0]['n']} اقدامِ حذف/اخراج انجام داده که غیرعادی است.", 12,
              "بدونِ اتهام، فقط اطلاع بده و بپرس می‌خواهد لاگ‌ها را با هم مرور کنند.")]


async def d_system_alert(E):
    st = str(await db.get_setting("system_status", "normal"))
    if st == "normal":
        return []
    return [F("system_alert", st, 5, f"وضعیتِ سیستم اکنون «{st}» است (عادی نیست).", 12, "خلاصه و مستقیم بگو و بپرس کمک لازم است یا نه.")]


async def d_calendar_tomorrow(E):
    tm = jdatetime.date.fromgregorian(date=(E.now + timedelta(days=1)).date()).strftime("%Y/%m/%d")
    r = await E.rows("SELECT day_type, title FROM calendar_days WHERE jdate=?", (tm,))
    if not r:
        return []
    return [F("calendar_tomorrow", tm, 3, f"در تقویمِ سیستم، فردا ({tm}) «{_name(r[0], 'title')}» ثبت شده (نوع: {_name(r[0], 'day_type')}).", 20,
              "بپرس برنامه‌ی مسابقه‌ها/وظایف برای فردا باید تغییر کند یا نه.", opinion=True)]


async def d_feedback_waiting(E):
    rs = await E.rows("SELECT sent_at FROM feedback WHERE COALESCE(reply,'')='' ORDER BY sent_at")
    if not rs:
        return []
    days = _age_days(rs[0]["sent_at"], E.now) or 0
    if days < 2:
        return []
    return [F("feedback_waiting", len(rs), 3, f"{len(rs)} بازخوردِ بی‌پاسخ هست؛ قدیمی‌ترینش {int(days)} روز پیش آمده.", 48, "یادآوری کوتاه.")]


async def d_unclassed_players(E):
    r = await E.rows("SELECT COUNT(*) AS n FROM players WHERE status='active' AND class_id IS NULL")
    n = r[0]["n"] if r else 0
    if n < 3:
        return []
    return [F("unclassed_players", n, 2, f"{n} بازیکنِ فعال به هیچ کلاسی وصل نیستند.", 168, "پیشنهاد بده تکلیفشان را روشن کنند.")]


async def d_idle_players(E):
    cut30 = (E.now - timedelta(days=30)).isoformat()
    cut14 = (E.now - timedelta(days=14)).isoformat()
    recent = await E.rows("SELECT COUNT(*) AS n FROM matches WHERE created_at>=?", (cut14,))
    if not recent or recent[0]["n"] < 5:
        return []
    r = await E.rows("SELECT COUNT(*) AS n FROM players p WHERE p.status='active' AND NOT EXISTS (SELECT 1 FROM matches m WHERE "
                     "(m.white_player_id=p.id OR m.black_player_id=p.id) AND m.created_at>=?)", (cut30,))
    n = r[0]["n"] if r else 0
    if n < 6:
        return []
    return [F("idle_players", n // 3, 2, f"{n} بازیکنِ فعال در ۳۰ روز گذشته هیچ بازی‌ای نداشته‌اند، در حالی‌که سیستم در حال برگزاریِ مسابقه است.", 240,
              "نظرت را بگو که چطور دوباره درگیرشان کنند.", opinion=True)]


async def d_weekly_digest(E):
    since = (E.now - timedelta(days=7)).isoformat()
    m = (await E.rows("SELECT COUNT(*) AS n FROM matches WHERE created_at>=?", (since,)))[0]["n"]
    p = (await E.rows("SELECT COUNT(*) AS n FROM players WHERE created_at>=?", (since,)))[0]["n"]
    w = (await E.rows("SELECT COUNT(*) AS n FROM warnings_log WHERE issued_at>=?", (since,)))[0]["n"]
    pend = (await E.rows("SELECT COUNT(*) AS n FROM matches WHERE result IS NULL"))[0]["n"]
    if m + p + w == 0:
        return []
    return [F("weekly_digest", f"{E.now.isocalendar()[0]}-{E.now.isocalendar()[1]}", 3,
              f"خلاصه‌ی ۷ روز گذشته: {m} بازی ثبت‌شده، {p} بازیکنِ جدید، {w} اخطار، و {pend} بازیِ بدون نتیجه.", 160,
              "یک جمع‌بندیِ ۲ جمله‌ای بده و یک نکته‌ی قابل‌اقدام برای هفته‌ی بعد پیشنهاد کن.")]


async def d_player_milestone(E):
    r = await E.rows("SELECT id, full_name, wins, losses, draws FROM players WHERE status='active' AND is_elite=0 AND wins>=8 AND losses=0 "
                     "ORDER BY wins DESC LIMIT 1")
    if not r:
        return []
    x = r[0]
    return [F("player_milestone", x["id"], 3, f"{_name(x, 'full_name')} با {x['wins']} برد و بدونِ باخت هنوز «برتر» نشده است.", 336,
              "نظرت را در یک جمله بگو و بپرس موافق است برترش کنند؟", opinion=True)]


TOPICS = [
    # key, برچسب، نقش‌ها، تابع
    ("self_inactive", "🕰 چند روزه سر نزدی", ALL_ROLES, d_self_inactive),
    ("my_tasks", "📋 وظیفه‌ی عقب‌افتاده", ALL_ROLES, d_my_tasks),
    ("unread_messages", "📨 پیام نخوانده", ALL_ROLES, d_unread_messages),
    ("pending_results", "⏳ نتیجه‌ی ثبت‌نشده", MGR, d_pending_results),
    ("stale_tournament", "🏆 تورنمنتِ راکد", MGR, d_stale_tournament),
    ("scan_requests", "📷 ثبتِ عکسیِ منتظر", BOSS, d_scan_requests),
    ("kick_requests", "🚫 درخواستِ اخراجِ منتظر", BOSS, d_kick_requests),
    ("access_requests", "🔑 درخواستِ دسترسیِ منتظر", BOSS, d_access_requests),
    ("admin_quiet", "👥 مدیرِ ساکت", BOSS, d_admin_quiet),
    ("warning_pileup", "⚠️ انبوهِ اخطار", SEC, d_warning_pileup),
    ("warning_spike", "📈 جهشِ اخطار", SEC, d_warning_spike),
    ("unusual_activity", "🕵️ فعالیتِ غیرعادی", BOSS, d_unusual_activity),
    ("system_alert", "🚨 هشدارِ سیستم", SEC, d_system_alert),
    ("calendar_tomorrow", "📅 رویدادِ فردا", MGR, d_calendar_tomorrow),
    ("feedback_waiting", "💬 بازخوردِ بی‌پاسخ", BOSS, d_feedback_waiting),
    ("unclassed_players", "🧩 بازیکنِ بدونِ کلاس", MGR, d_unclassed_players),
    ("idle_players", "😴 بازیکنانِ بی‌بازی", MGR, d_idle_players),
    ("weekly_digest", "🗓 جمع‌بندیِ هفته", BOSS, d_weekly_digest),
    ("player_milestone", "🌟 بازیکنِ درخشان (نظرخواهی)", MGR, d_player_milestone),
]
TOPIC_LABELS = {k: l for k, l, _r, _f in TOPICS}


# ═══════════════════════ وضعیتِ افراد (سکوت/بی‌صدا/بلاک) ═══════════════════════
async def _state(uid):
    r = await _rows("SELECT snooze_until, muted FROM aipro_state WHERE user_id=?", (uid,))
    if not r:
        return {"snooze": None, "muted": {}}
    try:
        muted = json.loads(r[0]["muted"] or "{}")
    except Exception:
        muted = {}
    return {"snooze": _parse(r[0]["snooze_until"]) if r[0]["snooze_until"] else None, "muted": muted}


async def _save_state(uid, snooze=None, muted=None):
    cur = await _state(uid)
    s = snooze if snooze is not None else cur["snooze"]
    m = muted if muted is not None else cur["muted"]
    await _exec("INSERT INTO aipro_state(user_id,snooze_until,muted) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "snooze_until=excluded.snooze_until, muted=excluded.muted",
                (uid, s.isoformat() if s else None, json.dumps(m, ensure_ascii=False)))


async def _topics_off():
    return set(await _json_setting("aipro_topics_off", []))


async def _blocked():
    return set(int(x) for x in await _json_setting("aipro_blocked", []))


# ═══════════════════════ گردآوری و انتخابِ یافته ═══════════════════════
async def collect(uid, role, include_all=False):
    """همه‌ی یافته‌های این آدم، مرتب‌شده بر اساس اهمیت (برای اسکن/نمایش). include_all=True یعنی ضدتکرار را نادیده بگیر."""
    admin = await db.get_admin(uid)
    E = Env(uid, role, admin, datetime.now())
    off = await _topics_off()
    st = await _state(uid)
    nowt = _now_teh()
    muted = {k for k, until in st["muted"].items() if _parse(until) and _parse(until) > nowt}
    seen = {}
    if not include_all:
        for r in await _rows("SELECT topic, dkey, outcome, at FROM aipro_log WHERE user_id=? AND at>=?", (uid, (nowt - timedelta(days=21)).isoformat())):
            seen.setdefault((r["topic"], r["dkey"]), []).append((r["outcome"], _parse(r["at"])))
    out = []
    for key, _label, roles, fn in TOPICS:
        if role not in roles or key in off or key in muted:
            continue
        try:
            fs = await fn(E)
        except Exception:
            logger.warning("aipro: detector %s failed", key, exc_info=True)
            continue
        for f in fs or []:
            if not include_all:
                hist = seen.get((f["topic"], f["dkey"]), [])
                hours = f["cool"]
                if any(o == "skipped" and t and (nowt - t).total_seconds() < 12 * 3600 for o, t in hist):
                    continue
                if any(o == "sent" and t and (nowt - t).total_seconds() < hours * 3600 for o, t in hist):
                    continue
            out.append(f)
    out.sort(key=lambda f: -f["imp"])
    return out


async def eligibility(uid, prefs=None, now=None):
    """(ok, دلیل). همه‌ی دروازه‌های ورود قبل از هر کارِ سنگین."""
    import ai_persona
    now = now or _now_teh()
    if (await cfg("aipro_on")) != "1":
        return False, "کلیدِ کلی خاموش است"
    prefs = prefs if prefs is not None else await ai_persona.get_prefs(uid)
    if prefs.get("pro_on", "1") != "1":
        return False, "خودش خاموش کرده"
    if uid in await _blocked():
        return False, "توسط مدیر ارشد مسدود شده"
    st = await _state(uid)
    if st["snooze"] and st["snooze"] > now:
        return False, "تا فردا بی‌صدا شده"
    if in_quiet(await person_quiet_spec(uid, prefs), now):
        return False, "ساعتِ سکوت"
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    sent_today = await _rows("SELECT at FROM aipro_log WHERE user_id=? AND outcome='sent' AND at>=? ORDER BY at DESC", (uid, today0))
    if len(sent_today) >= int(await cfg("aipro_max_day")):
        return False, "سقفِ امروز پر شده"
    last = await _rows("SELECT at FROM aipro_log WHERE user_id=? AND outcome='sent' ORDER BY at DESC LIMIT 1", (uid,))
    if last and (now - _parse(last[0]["at"])).total_seconds() < float(await cfg("aipro_gap_h")) * 3600:
        return False, "فاصله‌ی حداقلی رعایت نشده"
    return True, ""


# ═══════════════════════ نوشتن پیام با مدل ═══════════════════════
def _json_from(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    m = re.search(r"\{.*\}", t, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


async def compose(uid, role, finding, prefs):
    """(send: bool, text). مدل هم قاضی است هم نویسنده؛ هر خطایی → برای اهمیتِ ≥۴ متنِ ساده، وگرنه ساکت."""
    import ai_assistant as aia
    import ai_persona
    fallback = f"{finding['facts']}" if finding["imp"] >= 4 else None
    if (await cfg("aipro_llm")) != "1" or not aia.GEMINI_API_KEY:
        return (fallback is not None), (fallback or "")
    admin = await db.get_admin(uid)
    name = ""
    try:
        name = (admin["display_name"] or admin["full_name"]) if admin else ""
    except Exception:
        pass
    block = await ai_persona.build_block(uid, role, prefs)
    role_fa = {ROLE_PISHVA: "مدیر ارشد", ROLE_TOURNAMENT_MANAGER: "مسئول مسابقات", ROLE_SECURITY_MANAGER: "مسئول انتظامات"}.get(role, "مدیر")
    prompt = (
        "تو «رهگشا» هستی، دستیار هوشمندِ سامانه‌ی مدیریت مسابقات شطرنجِ یک مدرسه. می‌خواهی خودت (بدون اینکه کسی خواسته باشد) برای "
        f"{name or 'این مدیر'} ({role_fa}) پیام بدهی.{block}\n\n"
        f"موضوع: {TOPIC_LABELS.get(finding['topic'], finding['topic'])}\n"
        f"داده‌ی مطمئن (فقط همین‌ها درست است): {finding['facts']}\n"
        f"راهنمای لحن/هدف: {finding['hint'] or 'کوتاه و مفید'}\n"
        f"اهمیت از ۵: {finding['imp']}\n\n"
        "قوانین سخت:\n"
        "- اگر این موضوع واقعاً ارزش مزاحمت‌کردن ندارد یا شبیه نق‌زدن/تکرارِ بی‌فایده است، send=false بده. ساکت‌ماندن اشکال ندارد.\n"
        "- پیام فارسی، حداکثر ۴ خط، طبیعی و مطابق لحن این شخص، با یک پیشنهاد یا سؤالِ مشخص.\n"
        "- فقط از داده‌ی بالا استفاده کن؛ عدد یا واقعیتِ جدید نساز. اسمِ شخصِ دیگری را بدونِ دلیل لو نده.\n"
        "- هرگز نگو «مدیر ارشد گفت/خواست». تو خودت داری پیشنهاد می‌دهی.\n"
        "- سلامِ رسمی و مقدمه‌چینیِ طولانی نکن.\n"
        "خروجی فقط یک JSON: {\"send\": true|false, \"message\": \"...\"}")
    try:
        data = await aia._call_gemini([{"role": "user", "parts": [{"text": prompt}]}], None)
        txt = "".join(p.get("text", "") for p in aia._extract_parts(data))
        j = _json_from(txt)
        if not j:
            return (fallback is not None), (fallback or "")
        if not j.get("send"):
            return False, ""
        msg = str(j.get("message") or "").strip()
        return (bool(msg), msg)
    except Exception:
        logger.warning("aipro: compose failed", exc_info=True)
        return (fallback is not None), (fallback or "")


# ═══════════════════════ ارسال ═══════════════════════
def _kb(log_id):
    from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
    return M([[B("💬 جواب بده", callback_data=f"aipro:r:{log_id}")],
              [B("👍 به‌جا بود", callback_data=f"aipro:f:{log_id}:1"), B("👎 بی‌مورد", callback_data=f"aipro:f:{log_id}:0")],
              [B("🔕 امروز بس کن", callback_data=f"aipro:z:{log_id}"), B("⚙️ تنظیمات", callback_data="aipro:s")]])


async def _remember_in_chat(uid, role, text):
    try:
        import ai_shared
        sid = await ai_shared.ensure_active_session(uid, role)
        await db.ai_add_message(sid, "ai", text)
    except Exception:
        logger.warning("aipro: could not store in chat", exc_info=True)


async def _send(bot, uid, role, finding, text):
    now = _now_teh().isoformat()
    log_id = await _exec("INSERT INTO aipro_log(user_id,topic,dkey,importance,outcome,text,at) VALUES(?,?,?,?,?,?,?)",
                         (uid, finding["topic"], finding["dkey"], finding["imp"], "sent", text, now))
    try:
        await bot.send_message(uid, f"🤖 {text}", reply_markup=_kb(log_id))
    except Exception:
        await _exec("UPDATE aipro_log SET outcome='failed' WHERE id=?", (log_id,))
        logger.info("aipro: could not deliver to %s", uid)
        return False
    await _remember_in_chat(uid, role, text)
    return True


async def process_person(bot, uid, role):
    import ai_persona
    prefs = await ai_persona.get_prefs(uid)
    ok, _why = await eligibility(uid, prefs)
    if not ok:
        return False
    min_imp = int(await cfg("aipro_min_imp"))
    fs = [f for f in await collect(uid, role) if f["imp"] >= min_imp]
    if not fs:
        return False
    f = fs[0]
    send, text = await compose(uid, role, f, prefs)
    if not send:
        await _exec("INSERT INTO aipro_log(user_id,topic,dkey,importance,outcome,text,at) VALUES(?,?,?,?,?,?,?)",
                    (uid, f["topic"], f["dkey"], f["imp"], "skipped", "", _now_teh().isoformat()))
        return False
    return await _send(bot, uid, role, f, text)


# ═══════════════════════ «قانون‌های من» (watch rules) ═══════════════════════
OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def _today_prefix():
    return datetime.now().strftime("%Y-%m-%d")


async def _count(sql, args=()):
    r = await _rows(sql, args)
    return r[0][0] if r else 0


async def _find_player_row(target):
    r = await _rows("SELECT id, full_name, wins, losses, warnings FROM players WHERE full_name LIKE ? ORDER BY id LIMIT 1", (f"%{target}%",))
    return r[0] if r else None


async def m_pending_matches(t, rule): return await _count("SELECT COUNT(*) FROM matches WHERE result IS NULL"), "بازیِ بدون نتیجه"
async def m_pending_tasks(t, rule): return await _count("SELECT COUNT(*) FROM tasks WHERE status='pending'"), "وظیفه‌ی انجام‌نشده"
async def m_scan_requests(t, rule): return await _count("SELECT COUNT(*) FROM match_scan_requests WHERE status='pending'"), "درخواستِ ثبتِ عکسیِ منتظر"
async def m_kick_requests(t, rule): return await _count("SELECT COUNT(*) FROM kick_requests WHERE status='pending'"), "درخواستِ اخراجِ منتظر"
async def m_access_requests(t, rule): return await _count("SELECT COUNT(*) FROM access_requests WHERE status='pending'"), "درخواستِ دسترسیِ منتظر"
async def m_feedback(t, rule): return await _count("SELECT COUNT(*) FROM feedback WHERE COALESCE(reply,'')=''"), "بازخوردِ بی‌پاسخ"
async def m_active_players(t, rule): return await _count("SELECT COUNT(*) FROM players WHERE status='active'"), "بازیکنِ فعال"
async def m_new_players_today(t, rule): return await _count("SELECT COUNT(*) FROM players WHERE created_at LIKE ?", (_today_prefix() + "%",)), "بازیکنِ تازه‌ثبت‌نام امروز"
async def m_matches_today(t, rule): return await _count("SELECT COUNT(*) FROM matches WHERE created_at LIKE ?", (_today_prefix() + "%",)), "بازیِ ثبت‌شده‌ی امروز"
async def m_warnings_today(t, rule): return await _count("SELECT COUNT(*) FROM warnings_log WHERE issued_at LIKE ?", (_today_prefix() + "%",)), "اخطارِ صادرشده‌ی امروز"
async def m_unread_me(t, rule): return await _count("SELECT COUNT(*) FROM messages WHERE receiver_id=? AND is_read=0", (rule["notify_id"],)), "پیامِ نخوانده برای تو"


async def m_admin_idle_days(t, rule):
    import ai_tools_ext
    a = await ai_tools_ext._find_admin(t)
    if not a:
        return None, f"مدیری با نام «{t}» پیدا نشد"
    d = _age_days(a["last_active"], datetime.now())
    return (None if d is None else round(d, 1)), f"روز از آخرین فعالیتِ {a['display_name'] or a['full_name']}"


async def m_player_wins(t, rule):
    p = await _find_player_row(t)
    return (p["wins"], f"برد برای {p['full_name']}") if p else (None, f"بازیکنی با نام «{t}» پیدا نشد")


async def m_player_losses(t, rule):
    p = await _find_player_row(t)
    return (p["losses"], f"باخت برای {p['full_name']}") if p else (None, f"بازیکنی با نام «{t}» پیدا نشد")


async def m_player_warnings(t, rule):
    p = await _find_player_row(t)
    return (p["warnings"], f"اخطار برای {p['full_name']}") if p else (None, f"بازیکنی با نام «{t}» پیدا نشد")


async def m_tournament_matches(t, rule):
    r = await _rows("SELECT id, name FROM tournaments WHERE name LIKE ? ORDER BY id DESC LIMIT 1", (f"%{t}%",))
    if not r:
        return None, f"تورنمنتی با نام «{t}» پیدا نشد"
    return await _count("SELECT COUNT(*) FROM matches WHERE tournament_id=?", (r[0]["id"],)), f"بازی در تورنمنتِ {r[0]['name']}"


async def m_new_logs(t, rule):
    """رویدادمحور: تعدادِ اقداماتِ جدید (از آخرین بررسی) که نوعشان شاملِ target باشد."""
    like = f"%{t}%" if t else "%"
    cur = int(rule["cursor"] or 0)
    r = await _rows("SELECT COUNT(*) AS n, COALESCE(MAX(id),?) AS mx FROM action_logs WHERE id>? AND (action_type LIKE ? OR description LIKE ?)",
                    (cur, cur, like, like))
    rule["_new_cursor"] = r[0]["mx"] if r else cur
    return (r[0]["n"] if r else 0), "اقدامِ جدید مطابق با «" + (t or "همه") + "»"


METRICS = {
    "pending_matches": ("تعدادِ بازی‌های بدونِ نتیجه", False, m_pending_matches),
    "pending_tasks": ("تعدادِ وظایفِ انجام‌نشده (همه‌ی مدیران)", False, m_pending_tasks),
    "pending_scan_requests": ("تعدادِ درخواست‌های ثبتِ عکسیِ منتظر", False, m_scan_requests),
    "pending_kick_requests": ("تعدادِ درخواست‌های اخراجِ منتظر", False, m_kick_requests),
    "pending_access_requests": ("تعدادِ درخواست‌های دسترسیِ منتظر", False, m_access_requests),
    "unanswered_feedback": ("تعدادِ بازخوردهای بی‌پاسخ", False, m_feedback),
    "active_players": ("تعدادِ بازیکنانِ فعال", False, m_active_players),
    "new_players_today": ("تعدادِ ثبت‌نامِ امروز", False, m_new_players_today),
    "matches_today": ("تعدادِ بازی‌های ثبت‌شده‌ی امروز", False, m_matches_today),
    "warnings_today": ("تعدادِ اخطارهای امروز", False, m_warnings_today),
    "unread_messages_me": ("تعدادِ پیام‌های نخوانده‌ی خودت", False, m_unread_me),
    "admin_idle_days": ("چند روز است فلان مدیر فعالیت نکرده (target = نامِ مدیر)", True, m_admin_idle_days),
    "player_wins": ("بُردِ یک بازیکن (target = نامِ بازیکن)", True, m_player_wins),
    "player_losses": ("باختِ یک بازیکن (target = نامِ بازیکن)", True, m_player_losses),
    "player_warnings": ("اخطارِ یک بازیکن (target = نامِ بازیکن)", True, m_player_warnings),
    "tournament_matches": ("تعدادِ بازی‌های یک تورنمنت (target = نامِ تورنمنت)", True, m_tournament_matches),
    "new_logs": ("اقدامِ جدیدِ ثبت‌شده در سیستم؛ رویدادمحور (target = بخشی از نوع/توضیحِ اقدام، مثل record_match یا اخراج)", False, m_new_logs),
}


async def _fire_rule(bot, rule, value, desc):
    from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
    txt = (f"🔔 رهگشا — همان چیزی که خواسته بودی بهت خبر بدم:\n«{rule['title']}»\n"
           f"وضعیتِ الان: {value:g} {desc}")
    await bot.send_message(rule["notify_id"], txt, reply_markup=M([[B("⚙️ قانون‌های من", callback_data="aipro:rules")]]))
    await _remember_in_chat(rule["notify_id"], await _role_of(rule["notify_id"]), txt)


async def _role_of(uid):
    from helpers import get_user_role
    try:
        return await get_user_role(uid) or ""
    except Exception:
        return ""


async def process_rules(bot):
    now_t = _now_teh()
    for r in await _rows("SELECT * FROM aipro_rules WHERE active=1"):
        rule = dict(r)
        meta = METRICS.get(rule["metric"])
        if not meta:
            continue
        try:
            if in_quiet(await person_quiet_spec(rule["notify_id"]), now_t):
                continue                                        # ساعت سکوت: بعد از پایانش خودکار می‌رود
            val, desc = await meta[2](rule["target"], rule)
            if val is None:
                continue
            lf = _parse(rule["last_fired"]) if rule["last_fired"] else None
            if lf and (now_t - lf).total_seconds() < float(rule["cooldown_h"]) * 3600:
                if rule["metric"] == "new_logs":
                    await _exec("UPDATE aipro_rules SET cursor=? WHERE id=?", (rule.get("_new_cursor", rule["cursor"]), rule["id"]))
                continue
            if OPS[rule["op"]](float(val), float(rule["value"])):
                await _fire_rule(bot, rule, float(val), desc)
                await _exec("UPDATE aipro_rules SET last_fired=?, cursor=?, active=? WHERE id=?",
                            (now_t.isoformat(), rule.get("_new_cursor", rule["cursor"]), 1 if rule["repeat"] else 0, rule["id"]))
            elif rule["metric"] == "new_logs":
                await _exec("UPDATE aipro_rules SET cursor=? WHERE id=?", (rule.get("_new_cursor", rule["cursor"]), rule["id"]))
        except Exception:
            logger.warning("aipro: rule %s failed", rule.get("id"), exc_info=True)


# ═══════════════════════ حلقه‌ی اصلی ═══════════════════════
async def _tick_server(bot):
    await _ensure_tables()
    await process_rules(bot)
    if (await cfg("aipro_on")) != "1":
        return
    import servers
    people = {}
    for a in await db.get_all_admins():
        if a["is_active"]:
            people[int(a["telegram_id"])] = a["role"]
    try:
        owner = servers.owner_tid()
        if owner:
            people[int(owner)] = ROLE_PISHVA
    except Exception:
        pass
    for uid, role in people.items():
        try:
            if await process_person(bot, uid, role):
                await asyncio.sleep(1)
        except Exception:
            logger.warning("aipro: person %s failed", uid, exc_info=True)


async def tick(context):
    import servers
    try:
        await servers.for_each_active(_tick_server, context.bot)
    except Exception:
        logger.warning("aipro: tick failed", exc_info=True)


def start(application):
    jq = application.job_queue
    if jq:
        jq.run_repeating(tick, interval=600, first=120, name="aipro_tick")
    logger.info("ai_proactive: scheduled")


# ═══════════════════════ تنظیمات برای مدیر ارشد (مشترکِ تلگرام و هاب) ═══════════════════════
IMP_CHOICES = [2, 3, 4, 5]
MAXDAY_CHOICES = [1, 2, 3, 5]
GAP_CHOICES = [2, 4, 8, 12]
QUIET_PRESETS = ["23:45-09:40", "22:00-08:00", "23:00-07:00", "00:00-10:00", "off"]


async def admin_state():
    off = await _topics_off()
    blocked = await _blocked()
    people = []
    for a in await db.get_all_admins():
        people.append({"id": a["telegram_id"], "name": (a["display_name"] or a["full_name"] or str(a["telegram_id"])),
                       "blocked": int(a["telegram_id"]) in blocked, "active": bool(a["is_active"])})
    return {
        "on": (await cfg("aipro_on")) == "1", "max_day": int(await cfg("aipro_max_day")), "gap_h": int(await cfg("aipro_gap_h")),
        "min_imp": int(await cfg("aipro_min_imp")), "quiet": await cfg("aipro_quiet"), "llm": (await cfg("aipro_llm")) == "1",
        "topics": [{"key": k, "label": l, "on": k not in off, "roles": [r for r in roles]} for k, l, roles, _f in TOPICS],
        "people": people,
    }


def _cycle(lst, cur):
    return lst[(lst.index(cur) + 1) % len(lst)] if cur in lst else lst[0]


async def admin_apply(body: dict):
    """body: {key:'on'|'max_day'|'gap_h'|'min_imp'|'quiet'|'llm', value?} | {topic, on?} | {uid, blocked?}. بدونِ value یعنی چرخشی."""
    key = body.get("key")
    if key == "on":
        await db.set_setting("aipro_on", "0" if await cfg("aipro_on") == "1" else "1")
    elif key == "llm":
        await db.set_setting("aipro_llm", "0" if await cfg("aipro_llm") == "1" else "1")
    elif key == "max_day":
        await db.set_setting("aipro_max_day", str(_cycle(MAXDAY_CHOICES, int(await cfg("aipro_max_day")))))
    elif key == "gap_h":
        await db.set_setting("aipro_gap_h", str(_cycle(GAP_CHOICES, int(await cfg("aipro_gap_h")))))
    elif key == "min_imp":
        await db.set_setting("aipro_min_imp", str(_cycle(IMP_CHOICES, int(await cfg("aipro_min_imp")))))
    elif key == "quiet":
        v = body.get("value")
        v = normalize_quiet(v) if v else _cycle(QUIET_PRESETS, await cfg("aipro_quiet"))
        if not v:
            return False
        await db.set_setting("aipro_quiet", v)
    elif body.get("topic"):
        if body["topic"] not in TOPIC_LABELS:
            return False
        off = await _topics_off()
        on = (not (body["topic"] in off)) if body.get("on") is None else (not body["on"])
        (off.add if on else off.discard)(body["topic"])
        await db.set_setting("aipro_topics_off", json.dumps(sorted(off)))
    elif body.get("uid") is not None:
        b = await _blocked()
        uid = int(body["uid"])
        (b.discard if (body.get("blocked") is False or (body.get("blocked") is None and uid in b)) else b.add)(uid)
        await db.set_setting("aipro_blocked", json.dumps(sorted(b)))
    else:
        return False
    return True


# ═══════════════════════ ابزارهای چت (برای رهگشا) ═══════════════════════
TOOL_PERMISSIONS = {
    "create_watch_rule": ALL_ROLES, "list_watch_rules": ALL_ROLES, "delete_watch_rule": ALL_ROLES,
    "my_proactive_settings": ALL_ROLES, "proactive_admin_settings": BOSS, "proactive_scan": BOSS,
}
CATEGORY = ("proactive", "💬 پیام‌دادنِ خودکارِ رهگشا و قانون‌های «اگر فلان شد بهم بگو»",
            ["create_watch_rule", "list_watch_rules", "delete_watch_rule", "my_proactive_settings", "proactive_admin_settings", "proactive_scan"])

_METRIC_HELP = "؛ ".join(f"{k} = {v[0]}" for k, v in METRICS.items())
TOOL_DECLARATIONS = [
    {"name": "create_watch_rule",
     "description": ("ساخت یک «قانونِ مراقبت»: وقتی کاربر می‌گوید «اگر فلان شد بهم بگو / خبرم کن» از این استفاده کن. رهگشا هر ۱۰ دقیقه آن را به‌صورتِ دقیق "
                     "چک می‌کند و وقتی شرط برقرار شد خودش پیام می‌دهد. فقط از معیارهای زیر استفاده کن؛ اگر خواسته‌ی کاربر با هیچ‌کدام نمی‌خواند، صادقانه بگو "
                     "ممکن نیست و نزدیک‌ترین معیار را پیشنهاد بده. معیارها: " + _METRIC_HELP),
     "parameters": {"type": "object", "properties": {
         "title": {"type": "string", "description": "عنوانِ کوتاهِ قانون (همان چیزی که کاربر خواسته)"},
         "metric": {"type": "string", "description": "یکی از معیارهای بالا"},
         "target": {"type": "string", "description": "فقط برای معیارهای دارای target: نامِ مدیر/بازیکن/تورنمنت یا کلیدواژه‌ی اقدام"},
         "op": {"type": "string", "description": "یکی از: > >= < <= == !="},
         "value": {"type": "number", "description": "عددِ آستانه (برای new_logs معمولاً 1 با op >=)"},
         "repeat": {"type": "boolean", "description": "true فقط اگر کاربر گفت «همیشه / هر بار». پیش‌فرض false = یک‌بار خبر بده و تمام."},
         "cooldown_hours": {"type": "number", "description": "حداقل فاصله‌ی دو خبر (پیش‌فرض ۱۲). فقط برای repeat مهم است."},
         "notify": {"type": "string", "description": "فقط مدیر ارشد: نام/آیدیِ مدیری که باید خبر بگیرد (پیش‌فرض: خودِ کاربر)"},
     }, "required": ["title", "metric", "op", "value"]}},
    {"name": "list_watch_rules", "description": "فهرستِ قانون‌های مراقبتِ فعال (برای مدیر ارشد: همه‌ی افراد).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "delete_watch_rule", "description": "حذفِ یک قانونِ مراقبت با شناسه (از list_watch_rules).",
     "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "my_proactive_settings",
     "description": ("تنظیمِ پیام‌دادنِ خودکارِ رهگشا برای خودِ کاربر: روشن/خاموش و ساعتِ سکوت (در این بازه رهگشا خودش پیام نمی‌دهد؛ پیش‌فرض 23:45 تا 09:40). "
                     "بدونِ پارامتر فقط وضعیتِ فعلی را می‌گوید. quiet مثل «22:30-08:00» یا «off»."),
     "parameters": {"type": "object", "properties": {"enabled": {"type": "boolean"}, "quiet": {"type": "string"}}}},
    {"name": "proactive_admin_settings",
     "description": ("فقط مدیر ارشد: تنظیماتِ کلیِ پیام‌دادنِ خودکار برای همه. بدونِ پارامتر وضعیت را می‌گوید. enabled=کلیدِ کلی؛ max_per_day؛ min_gap_hours؛ "
                     "min_importance (۱ تا ۵، هرچه بیشتر ساکت‌تر)؛ default_quiet؛ topic+topic_enabled برای خاموش/روشنِ یک دسته (کلیدها در خروجی هست)؛ "
                     "person+person_enabled برای خاموش/روشن‌کردنِ رهگشا برای یک مدیر."),
     "parameters": {"type": "object", "properties": {
         "enabled": {"type": "boolean"}, "max_per_day": {"type": "integer"}, "min_gap_hours": {"type": "integer"},
         "min_importance": {"type": "integer"}, "default_quiet": {"type": "string"}, "llm_gate": {"type": "boolean"},
         "topic": {"type": "string"}, "topic_enabled": {"type": "boolean"},
         "person": {"type": "string"}, "person_enabled": {"type": "boolean"}}}},
    {"name": "proactive_scan",
     "description": "فقط مدیر ارشد: ببین رهگشا در حال حاضر برای یک نفر (یا خودت) چه موضوع‌هایی پیدا می‌کند، بدونِ فرستادنِ پیام. برای اعتماد و عیب‌یابی.",
     "parameters": {"type": "object", "properties": {"person": {"type": "string", "description": "نام/آیدی؛ خالی = خودت"}}}},
]


async def _admin_by(identifier):
    import ai_tools_ext
    return await ai_tools_ext._find_admin(identifier)


async def dispatch_tool(name, args, caller_id, caller_role, ctx):
    import ai_persona
    await _ensure_tables()
    args = args or {}
    is_boss = caller_role == ROLE_PISHVA

    if name == "create_watch_rule":
        metric = str(args.get("metric") or "").strip()
        if metric not in METRICS:
            return "❌ معیارِ نامعتبر. معیارهای مجاز: " + "، ".join(METRICS)
        op = str(args.get("op") or "").strip()
        if op not in OPS:
            return "❌ عملگر نامعتبر؛ یکی از > >= < <= == !="
        try:
            value = float(args.get("value"))
        except (TypeError, ValueError):
            return "❌ مقدارِ عددیِ آستانه لازم است."
        target = str(args.get("target") or "").strip()
        if METRICS[metric][1] and not target:
            return f"❌ این معیار به target نیاز دارد ({METRICS[metric][0]})."
        notify = caller_id
        if args.get("notify") and is_boss:
            a = await _admin_by(args["notify"])
            if not a:
                return f"❌ مدیری با مشخصات «{args['notify']}» پیدا نشد."
            notify = int(a["telegram_id"])
        n = (await _rows("SELECT COUNT(*) AS n FROM aipro_rules WHERE owner_id=? AND active=1", (caller_id,)))[0]["n"]
        if n >= 20:
            return "❌ حداکثر ۲۰ قانونِ فعال داری؛ یکی را پاک کن."
        # مقدارِ اولیه برای معیار رویدادمحور: از «الان» به بعد را بشمار
        cursor = 0
        if metric == "new_logs":
            cursor = await _count("SELECT COALESCE(MAX(id),0) FROM action_logs")
        title = str(args.get("title") or metric)[:120]
        rid = await _exec(
            "INSERT INTO aipro_rules(owner_id,notify_id,title,metric,target,op,value,cooldown_h,repeat,active,created_at,cursor) VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
            (caller_id, notify, title, metric, target, op, value, float(args.get("cooldown_hours") or 12), 1 if args.get("repeat") else 0,
             _now_teh().isoformat(), cursor))
        meta = METRICS[metric]
        cur_val, desc = await meta[2](target, {"cursor": cursor, "notify_id": notify})
        who = "" if notify == caller_id else " (برای دیگری)"
        return (f"✅ قانونِ #{rid} ساخته شد{who}: «{title}» ← {meta[0]} {op} {value:g}"
                f"{' با هدفِ «' + target + '»' if target else ''}. "
                f"{'هر بار که شرط برقرار شد' if args.get('repeat') else 'یک‌بار که شرط برقرار شد'} خودم پیام می‌دم. وضعیتِ فعلی: "
                f"{'نامشخص' if cur_val is None else cur_val} {desc}.")

    if name == "list_watch_rules":
        rs = await _rows("SELECT * FROM aipro_rules WHERE active=1 " + ("" if is_boss else "AND owner_id=? ") + "ORDER BY id DESC LIMIT 40",
                         () if is_boss else (caller_id,))
        if not rs:
            return "هیچ قانونِ فعالی نیست."
        return "\n".join(f"#{r['id']} «{r['title']}» — {METRICS.get(r['metric'], ('?',))[0]} {r['op']} {r['value']:g}"
                         f"{' ('+r['target']+')' if r['target'] else ''}{' — تکراری' if r['repeat'] else ''}"
                         f"{'' if r['owner_id'] == caller_id else ' — مالک: ' + str(r['owner_id'])}" for r in rs)

    if name == "delete_watch_rule":
        try:
            rid = int(args.get("id"))
        except (TypeError, ValueError):
            return "❌ شناسه نامعتبر."
        r = await _rows("SELECT owner_id FROM aipro_rules WHERE id=?", (rid,))
        if not r or (r[0]["owner_id"] != caller_id and not is_boss):
            return "❌ این قانون پیدا نشد یا مالِ تو نیست."
        await _exec("DELETE FROM aipro_rules WHERE id=?", (rid,))
        return f"✅ قانونِ #{rid} حذف شد."

    if name == "my_proactive_settings":
        prefs = await ai_persona.get_prefs(caller_id)
        changed = []
        if args.get("enabled") is not None:
            await ai_persona.set_pref(ctx, caller_id, "pro_on", "1" if args["enabled"] else "0")
            changed.append("روشن" if args["enabled"] else "خاموش")
        if args.get("quiet"):
            q = normalize_quiet(args["quiet"])
            if not q:
                return "❌ ساعتِ سکوت را به‌شکل «22:30-08:00» یا «off» بگو."
            await ai_persona.set_pref(ctx, caller_id, "pro_quiet", q)
            changed.append(f"ساعتِ سکوت {q}")
        prefs = await ai_persona.get_prefs(caller_id)
        quiet = prefs.get("pro_quiet") or await cfg("aipro_quiet")
        return (("✅ " + "، ".join(changed) + " شد.\n") if changed else "") + \
            f"وضعیت: پیامِ خودکار {'روشن' if prefs.get('pro_on', '1') == '1' else 'خاموش'} · ساعتِ سکوت: {quiet if quiet != 'off' else 'ندارد'}"

    if name == "proactive_admin_settings":
        changes = []
        if args.get("enabled") is not None:
            await db.set_setting("aipro_on", "1" if args["enabled"] else "0"); changes.append("کلیدِ کلی")
        if args.get("max_per_day"):
            await db.set_setting("aipro_max_day", str(max(1, min(10, int(args["max_per_day"]))))); changes.append("سقفِ روزانه")
        if args.get("min_gap_hours"):
            await db.set_setting("aipro_gap_h", str(max(1, min(48, int(args["min_gap_hours"]))))); changes.append("فاصله‌ی حداقلی")
        if args.get("min_importance"):
            await db.set_setting("aipro_min_imp", str(max(1, min(5, int(args["min_importance"]))))); changes.append("آستانه‌ی اهمیت")
        if args.get("default_quiet"):
            q = normalize_quiet(args["default_quiet"])
            if not q:
                return "❌ ساعتِ سکوت را به‌شکل «23:45-09:40» یا «off» بگو."
            await db.set_setting("aipro_quiet", q); changes.append("ساعتِ سکوتِ پیش‌فرض")
        if args.get("llm_gate") is not None:
            await db.set_setting("aipro_llm", "1" if args["llm_gate"] else "0"); changes.append("قاضیِ مدل")
        if args.get("topic"):
            t = str(args["topic"]).strip()
            if t not in TOPIC_LABELS:
                return "❌ دسته‌ی نامعتبر. دسته‌ها: " + "، ".join(f"{k} ({v})" for k, v in TOPIC_LABELS.items())
            await admin_apply({"topic": t, "on": bool(args.get("topic_enabled", True))}); changes.append(f"دسته‌ی {TOPIC_LABELS[t]}")
        if args.get("person"):
            a = await _admin_by(args["person"])
            if not a:
                return f"❌ مدیری با مشخصات «{args['person']}» پیدا نشد."
            await admin_apply({"uid": a["telegram_id"], "blocked": not bool(args.get("person_enabled", False))}); changes.append(f"فرد: {a['full_name']}")
        s = await admin_state()
        off = [t["key"] for t in s["topics"] if not t["on"]]
        blocked = [p["name"] for p in s["people"] if p["blocked"]]
        return (("✅ تغییر کرد: " + "، ".join(changes) + "\n") if changes else "") + (
            f"پیامِ خودکار: {'روشن' if s['on'] else 'خاموش'} · سقفِ روزانه: {s['max_day']} · فاصله‌ی حداقلی: {s['gap_h']} ساعت · آستانه‌ی اهمیت: {s['min_imp']} از ۵ · "
            f"ساعتِ سکوتِ پیش‌فرض: {s['quiet']} · قاضیِ مدل: {'روشن' if s['llm'] else 'خاموش'}\n"
            f"دسته‌های خاموش: {', '.join(off) or 'هیچ'} · افرادِ خاموش: {', '.join(blocked) or 'هیچ'}\n"
            "کلیدِ دسته‌ها: " + "، ".join(f"{t['key']} ({t['label']})" for t in s["topics"]))

    if name == "proactive_scan":
        uid, role = caller_id, caller_role
        if args.get("person"):
            a = await _admin_by(args["person"])
            if not a:
                return f"❌ مدیری با مشخصات «{args['person']}» پیدا نشد."
            uid, role = int(a["telegram_id"]), a["role"]
        fs = await collect(uid, role, include_all=True)
        ok, why = await eligibility(uid)
        head = f"اسکن برای {uid}: " + ("الان می‌تواند پیام بگیرد." if ok else f"الان پیام نمی‌گیرد ({why}).")
        if not fs:
            return head + "\nچیزِ قابل‌گزارشی پیدا نشد."
        return head + "\n" + "\n".join(f"- [{f['imp']}/5] {TOPIC_LABELS.get(f['topic'], f['topic'])}: {f['facts']}" for f in fs[:12])
    return None


# ═══════════════════════ تلگرام: دکمه‌های پیام + /proactive ═══════════════════════
async def _is_boss_tg(update):
    from helpers import get_user_role
    u = update.effective_user
    return bool(u and (await get_user_role(u.id)) == ROLE_PISHVA)


def _on(v):
    return "✅" if v else "⬜"


async def _panel_text_kb():
    from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
    s = await admin_state()
    text = ("💬 <b>پیام‌دادنِ خودکارِ رهگشا</b>\n\n"
            f"• کلیدِ کلی: {'✅ روشن' if s['on'] else '⏸ خاموش'}\n• سقفِ روزانه برای هر نفر: {s['max_day']}\n"
            f"• فاصله‌ی حداقلی: {s['gap_h']} ساعت\n• آستانه‌ی اهمیت: {s['min_imp']} از ۵ (بیشتر = ساکت‌تر)\n"
            f"• ساعتِ سکوتِ پیش‌فرض: {s['quiet'] if s['quiet'] != 'off' else 'ندارد'}\n• قاضیِ هوش مصنوعی: {'روشن' if s['llm'] else 'خاموش'}\n\n"
            "هر مدیر برای خودش هم می‌تواند خاموش/روشن و ساعتِ سکوت را در «تنظیمات رهگشا» عوض کند.")
    kb = [[B(f"{_on(s['on'])} کلیدِ کلی", callback_data="aipro:a:on"), B(f"{_on(s['llm'])} قاضیِ مدل", callback_data="aipro:a:llm")],
          [B(f"📅 سقف روزانه: {s['max_day']}", callback_data="aipro:a:max_day"), B(f"⏱ فاصله: {s['gap_h']}س", callback_data="aipro:a:gap_h")],
          [B(f"🎯 آستانه: {s['min_imp']}", callback_data="aipro:a:min_imp"), B(f"🌙 سکوت: {s['quiet']}", callback_data="aipro:a:quiet")],
          [B("🗂 دسته‌ها (کدام موضوع‌ها)", callback_data="aipro:a:topics"), B("👥 افراد", callback_data="aipro:a:people")],
          [B("🔍 اسکنِ من (بدونِ ارسال)", callback_data="aipro:a:scan")]]
    return text, M(kb)


async def cmd_proactive(update, ctx):
    if not await _is_boss_tg(update):
        return
    await _ensure_tables()
    t, kb = await _panel_text_kb()
    await update.effective_message.reply_text(t, reply_markup=kb, parse_mode="HTML")


async def on_cb(update, ctx):
    from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
    from helpers import get_user_role
    q = update.callback_query
    uid = q.from_user.id
    parts = q.data.split(":")
    act = parts[1] if len(parts) > 1 else ""
    await _ensure_tables()
    role = await get_user_role(uid)
    if not role:
        await q.answer("⛔", show_alert=True)
        return
    if act == "a":
        if role != ROLE_PISHVA:
            await q.answer("فقط مدیر ارشد", show_alert=True)
            return
        sub = parts[2]
        await q.answer()
        if sub in ("on", "llm", "max_day", "gap_h", "min_imp", "quiet"):
            await admin_apply({"key": sub})
        elif sub == "tp":
            await admin_apply({"topic": parts[3]})
            sub = "topics"
        elif sub == "pp":
            await admin_apply({"uid": int(parts[3])})
            sub = "people"
        if sub == "topics":
            s = await admin_state()
            kb = [[B(f"{_on(t['on'])} {t['label']}", callback_data=f"aipro:a:tp:{t['key']}")] for t in s["topics"]] + [[B("⬅️ بازگشت", callback_data="aipro:a:back")]]
            await q.edit_message_text("🗂 هر موضوع را روشن/خاموش کن:", reply_markup=M(kb))
        elif sub == "people":
            s = await admin_state()
            kb = [[B(f"{_on(not p['blocked'])} {p['name']}", callback_data=f"aipro:a:pp:{p['id']}")] for p in s["people"] if p["active"]] + [[B("⬅️ بازگشت", callback_data="aipro:a:back")]]
            await q.edit_message_text("👥 ✅ یعنی رهگشا می‌تواند برایش پیام بدهد:", reply_markup=M(kb))
        elif sub == "scan":
            txt = await dispatch_tool("proactive_scan", {}, uid, role, ctx)
            await q.message.reply_text(txt[:3800])
        else:
            t, kb = await _panel_text_kb()
            await q.edit_message_text(t, reply_markup=kb, parse_mode="HTML")
        return
    if act == "s":
        await q.answer()
        await q.message.reply_text("⚙️ تنظیماتِ شخصی: از «🤖 رهگشا» ← ⚙️ تنظیمات، یا همین‌جا به رهگشا بگو: «پیام خودکار رو خاموش کن» یا «ساعت سکوتم ۲۲:۳۰ تا ۸ باشه».")
        return
    if act == "rules":
        await q.answer()
        txt = await dispatch_tool("list_watch_rules", {}, uid, role, ctx)
        await q.message.reply_text("🔔 قانون‌های من:\n" + txt + "\n\nبرای حذف به رهگشا بگو: «قانون شماره N رو پاک کن».")
        return
    log_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    r = await _rows("SELECT * FROM aipro_log WHERE id=? AND user_id=?", (log_id, uid))
    if not r:
        await q.answer("این پیام قدیمی شده.", show_alert=True)
        return
    r = r[0]
    if act == "r":
        await q.answer()
        ctx.user_data["ai_mode"] = True
        try:
            import ai_shared
            sid = await ai_shared.get_active_session(uid)
            if sid:
                ctx.user_data["ai_session_id"] = sid
        except Exception:
            pass
        await q.message.reply_text("✍️ بنویس؛ گوشم با توئه.")
    elif act == "f":
        good = parts[3] == "1"
        await _exec("UPDATE aipro_log SET feedback=? WHERE id=?", (1 if good else 0, log_id))
        extra = ""
        if not good:
            bad = await _rows("SELECT COUNT(*) AS n FROM aipro_log WHERE user_id=? AND topic=? AND feedback=0 AND at>=?",
                              (uid, r["topic"], (_now_teh() - timedelta(days=60)).isoformat()))
            if bad and bad[0]["n"] >= 2:
                st = await _state(uid)
                st["muted"][r["topic"]] = (_now_teh() + timedelta(days=30)).isoformat()
                await _save_state(uid, muted=st["muted"])
                extra = f"\nدیگه ۳۰ روز از «{TOPIC_LABELS.get(r['topic'], r['topic'])}» برات پیام نمی‌دم."
        await q.answer(("ممنون 👍" if good else "حله، دقیق‌تر می‌شم 👎") + extra, show_alert=bool(extra))
    elif act == "z":
        until = _now_teh().replace(hour=9, minute=40, second=0, microsecond=0)
        if until <= _now_teh():
            until += timedelta(days=1)
        await _save_state(uid, snooze=until)
        await q.answer("باشه، تا فردا صبح پیام نمی‌دم 🔕", show_alert=True)


def register(app):
    from telegram.ext import CallbackQueryHandler, CommandHandler
    app.add_handler(CommandHandler("proactive", cmd_proactive), group=-49)
    app.add_handler(CallbackQueryHandler(on_cb, pattern=r"^aipro:"), group=-49)
