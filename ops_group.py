"""
ops_group.py — «گروه فرماندهی»: یک سوپرگروهِ تاپیک‌دار که ربات + رهگشا کنترل کاملش را دارند.

هر بخشِ ربات یک تاپیک دارد و رویدادها خودکار به تاپیکِ درستشان می‌روند:
  امنیت (پنلِ زنده) · مدیریت بازیکن (ورودیِ دستوری) · اعلانات · درخواست‌های دسترسی (با دکمه)
  غریبه‌ها (یک کارتِ زنده برای هر نفر) · مسابقات (گزارشِ روزانه) · رهگشا (ورودیِ چت)
  لاگ‌ها (هر اقدام یک پیام + هشتگ) · سرورها · مدیران

اصول:
• فقط «سرورِ اصلی» (srvctx.is_main) — هیچ نشتی بین سرورها/مجموعه‌ها.
• اگر گروه تنظیم نشده یا ارسال شکست بخورد، همه‌چیز مثلِ قبل (پیوی) کار می‌کند؛ این ماژول فقط «اضافه» است.
• تاپیک‌های بدون ورودی «بسته» می‌شوند (فقط ادمین‌های گروه می‌نویسند؛ دکمه‌ها برای همه کار می‌کنند ولی هر هندلر
  نقشِ کلیک‌کننده را خودش چک می‌کند). فقط «مدیریت بازیکن» و «رهگشا» ورودی دارند.
• مسیردهیِ رویداد→تاپیک قابل‌ویرایش است (رهگشا با ابزار ops_route_set).
"""
import asyncio
import html
import json
import logging
import re
import time
from datetime import datetime, timedelta

import jdatetime
import turso_db
import database as db
import srvctx
from config import DB_PATH, PISHVA_ID, ROLE_PISHVA
from helpers import TEHRAN_TZ

logger = logging.getLogger(__name__)

_BOT = None

# ═══════════════════════ پیش‌فرض‌ها ═══════════════════════
# mode: none = فقط‌خواندنی (تاپیکِ بسته) | commands = دستورات | ai = چت با رهگشا
DEFAULT_TOPICS = {
    "security":  {"emoji": "🛡️", "title": "امنیت سیستم",       "mode": "none",     "desc": "پنل زنده‌ی امنیت + خلاصه‌ی هر بررسیِ امنیتیِ رهگشا"},
    "players":   {"emoji": "♟️", "title": "مدیریت بازیکن",     "mode": "commands", "desc": "اخطار و یادداشتِ بازیکنان. دستورات: /phelp"},
    "announce":  {"emoji": "📣", "title": "اعلانات",            "mode": "none",     "desc": "نفرات برتر، وضعیت‌ها، پیشنهادات و هر اعلان"},
    "access":    {"emoji": "📥", "title": "درخواست‌های دسترسی", "mode": "none",     "desc": "درخواست‌ها با دکمه‌های تأیید/رد/صف/بلاک"},
    "strangers": {"emoji": "👤", "title": "غریبه‌ها",           "mode": "none",     "desc": "کارتِ زنده‌ی هر غریبه — حتی با یک استارت"},
    "matches":   {"emoji": "🏆", "title": "مسابقات",            "mode": "none",     "desc": "گزارش روزانه‌ی مسابقات، پیروزمندان و بازندگان"},
    "rahgosha":  {"emoji": "🤖", "title": "رهگشا",              "mode": "ai",       "desc": "هر پیامی این‌جا بنویسید رهگشا جواب می‌دهد؛ پیشنهادهای رهگشا هم این‌جا می‌آید"},
    "logs":      {"emoji": "🗂️", "title": "لاگ‌ها",            "mode": "none",     "desc": "هر اقدام یک پیام، با هشتگ برای جست‌وجو"},
    "servers":   {"emoji": "🖥️", "title": "سرورها",            "mode": "none",     "desc": "وضعیت و مدیریتِ سرورها"},
    "admins":    {"emoji": "👥", "title": "مدیران",             "mode": "none",     "desc": "فهرستِ زنده‌ی مدیران، فعالیتشان و رویدادهای مدیریتی"},
}
DEFAULT_ROUTES = {
    "access_request": "access", "stranger": "strangers", "log": "logs", "admin_event": "admins",
    "announce": "announce", "suggestion": "announce", "champion": "announce", "status": "announce",
    "match_digest": "matches", "security": "security", "rahgosha_msg": "rahgosha", "server": "servers",
}
EVENT_HELP = {
    "access_request": "درخواستِ دسترسیِ جدید (با دکمه‌ها)", "stranger": "کارتِ زنده‌ی غریبه‌ها",
    "log": "لاگِ هر اقدام", "admin_event": "رویدادهای مدیریتیِ مدیران (افزودن/نقش/دسترسی/اخطار)",
    "announce": "اعلانِ عمومی", "suggestion": "انتقادات و پیشنهادات", "champion": "قهرمان/نفرات برتر",
    "status": "تغییر وضعیتِ سیستم", "match_digest": "گزارشِ روزانه‌ی مسابقات",
    "security": "پنل و بررسی‌های امنیتی", "rahgosha_msg": "پیشنهادها و پیام‌های خودجوشِ رهگشا",
    "server": "رویدادهای سرورها",
}
DEFAULT_MATCHES_TIME = "21:00"
MIN_GAP = 1.1          # فاصله‌ی حداقلی بین ارسال‌ها (سقفِ تلگرام ≈ ۲۰ پیام/دقیقه برای هر گروه)
MAX_PENDING = 120      # بیشتر از این، پیام‌های کم‌اهمیت (لاگ) رها می‌شوند


# ═══════════════════════ ذخیره‌سازی ═══════════════════════
_tables_ok = False


async def _ensure_tables():
    global _tables_ok
    if _tables_ok:
        return
    async with turso_db.connect(DB_PATH) as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS ops_kv (k TEXT PRIMARY KEY, v TEXT)")
        await conn.execute("CREATE TABLE IF NOT EXISTS ops_live (slot TEXT PRIMARY KEY, topic_key TEXT, "
                           "message_id INTEGER, updated_at TEXT)")
        await conn.commit()
    _tables_ok = True


async def _rows(sql, args=()):
    await _ensure_tables()
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(sql, tuple(args)) as cur:
            return list(await cur.fetchall())


async def _exec(sql, args=()):
    await _ensure_tables()
    async with turso_db.connect(DB_PATH) as conn:
        await conn.execute(sql, tuple(args))
        await conn.commit()


_cfg_cache = {"t": 0.0, "v": None}


def invalidate():
    _cfg_cache["t"] = 0.0


async def load_cfg(force=False) -> dict:
    if not force and _cfg_cache["v"] is not None and time.monotonic() - _cfg_cache["t"] < 20:
        return _cfg_cache["v"]
    kv = {r["k"]: r["v"] for r in await _rows("SELECT k, v FROM ops_kv")}

    def _j(k, default):
        try:
            return json.loads(kv[k]) if k in kv else default
        except Exception:
            return default

    gid = kv.get("group_id")
    cfg = {
        "group_id": int(gid) if gid else None,
        "topics": _j("topics", {k: dict(v, thread_id=None) for k, v in DEFAULT_TOPICS.items()}),
        "routes": {**DEFAULT_ROUTES, **_j("routes", {})},
        "matches_time": kv.get("matches_time") or DEFAULT_MATCHES_TIME,
        "last_digest": kv.get("last_digest") or "",
        "log_trail": kv.get("log_trail") == "1",
    }
    _cfg_cache["v"], _cfg_cache["t"] = cfg, time.monotonic()
    return cfg


async def save_kv(k: str, v):
    await _exec("INSERT OR REPLACE INTO ops_kv(k, v) VALUES (?,?)", (k, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)))
    invalidate()


async def save_topics(topics: dict):
    await save_kv("topics", topics)


def _main_ok() -> bool:
    try:
        return srvctx.is_main()
    except Exception:
        return True


async def is_ops_chat(chat_id) -> bool:
    """برای گاردِ پنل‌ها: آیا این چت همان گروهِ فرماندهی است؟"""
    try:
        cfg = await load_cfg()
        return bool(cfg["group_id"]) and int(chat_id) == cfg["group_id"]
    except Exception:
        return False


def topic_of(cfg: dict, thread_id):
    if not thread_id:
        return None
    for k, t in cfg["topics"].items():
        if t.get("thread_id") and int(t["thread_id"]) == int(thread_id):
            return k
    return None


# ═══════════════════════ ابزارکِ قالب‌بندی ═══════════════════════
def h(x) -> str:
    return html.escape(str(x if x is not None else ""), quote=False)


def tag(s) -> str:
    """هشتگِ فارسی/انگلیسیِ امن: فاصله→_ ، حذفِ علامت‌ها."""
    s = re.sub(r"[^\w\u0600-\u06FF]+", "_", str(s or "").strip(), flags=re.UNICODE).strip("_")
    return s or "نامشخص"


def now_teh() -> datetime:
    return datetime.now(TEHRAN_TZ).replace(tzinfo=None)


def jfmt(dt: datetime, fmt="%Y/%m/%d %H:%M") -> str:
    try:
        return jdatetime.datetime.fromgregorian(datetime=dt).strftime(fmt)
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M")


_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(n) -> str:
    return str(n).translate(_FA_DIGITS)


def bar(pct: float, width=10) -> str:
    pct = max(0.0, min(100.0, float(pct)))
    full = int(round(pct / 100 * width))
    return "▰" * full + "▱" * (width - full)


_SPARK = "▁▂▃▄▅▆▇█"


def spark(values) -> str:
    if not values:
        return "—"
    mx = max(values) or 1
    return "".join(_SPARK[min(7, int(v / mx * 7.999))] if v else "▁" for v in values)


# ═══════════════════════ ارسال (با کنترلِ نرخ) ═══════════════════════
def _NOPREVIEW():
    from telegram import LinkPreviewOptions
    return LinkPreviewOptions(is_disabled=True)


# دو صفِ جدا: لاگ‌ها (کم‌اهمیت، قابل‌رها) پشتِ هم نمی‌مانند تا درخواستِ دسترسی/ادیتِ پنل را معطل کنند.
_locks = {False: asyncio.Lock(), True: asyncio.Lock()}
_last = {False: 0.0, True: 0.0}
_pending = 0


async def _paced(factory, droppable=False):
    global _pending
    from telegram.error import RetryAfter
    if droppable and _pending > MAX_PENDING:
        return None
    _pending += 1
    try:
        async with _locks[bool(droppable)]:
            gap = MIN_GAP - (time.monotonic() - _last[bool(droppable)])
            if gap > 0:
                await asyncio.sleep(gap)
            for _ in range(3):
                try:
                    r = await factory()
                    _last[bool(droppable)] = time.monotonic()
                    return r
                except RetryAfter as e:
                    await asyncio.sleep(float(getattr(e, "retry_after", 3)) + 0.5)
            return None
    finally:
        _pending -= 1


async def post(topic_key: str, text: str, kb=None, parse_mode="HTML", droppable=False, _retry=True):
    """ارسال به یک تاپیک. → پیام | None (اگر گروه نیست/شکست خورد)."""
    if not _main_ok() or _BOT is None:
        return None
    cfg = await load_cfg()
    t = cfg["topics"].get(topic_key)
    if not cfg["group_id"] or not t or not t.get("thread_id"):
        return None
    try:
        return await _paced(lambda: _BOT.send_message(
            chat_id=cfg["group_id"], message_thread_id=int(t["thread_id"]), text=text[:4096], reply_markup=kb,
            parse_mode=parse_mode, link_preview_options=_NOPREVIEW(), disable_notification=True), droppable=droppable)
    except Exception as e:
        msg = str(e).lower()
        if _retry and "thread" in msg and ("not found" in msg or "invalid" in msg):
            logger.warning("ops: topic %s thread missing, recreating", topic_key)
            t["thread_id"] = None
            await save_topics(cfg["topics"])
            await ensure_topics(_BOT, only=topic_key)
            return await post(topic_key, text, kb, parse_mode, droppable, _retry=False)
        logger.warning("ops: post to %s failed: %s", topic_key, e)
        return None


async def post_event(event: str, text: str, kb=None, parse_mode="HTML", droppable=False):
    cfg = await load_cfg()
    key = cfg["routes"].get(event)
    if not key:
        return None
    return await post(key, text, kb, parse_mode, droppable)


async def live(slot: str, topic_key: str, text: str, kb=None, parse_mode="HTML"):
    """پیامِ زنده: اگر قبلاً ساخته شده ادیت می‌شود، وگرنه می‌سازد. → message_id | None."""
    if not _main_ok() or _BOT is None:
        return None
    cfg = await load_cfg()
    if not cfg["group_id"]:
        return None
    rows = await _rows("SELECT message_id FROM ops_live WHERE slot=?", (slot,))
    if rows:
        mid = int(rows[0]["message_id"])
        try:
            await _paced(lambda: _BOT.edit_message_text(chat_id=cfg["group_id"], message_id=mid, text=text[:4096],
                                                        reply_markup=kb, parse_mode=parse_mode,
                                                        link_preview_options=_NOPREVIEW()))
            return mid
        except Exception as e:
            if "not modified" in str(e).lower():
                return mid
            logger.info("ops: live %s edit failed (%s) -> new message", slot, e)
    m = await post(topic_key, text, kb, parse_mode)
    if m is None:
        return None
    await _exec("INSERT OR REPLACE INTO ops_live(slot, topic_key, message_id, updated_at) VALUES (?,?,?,?)",
                (slot, topic_key, m.message_id, now_teh().isoformat()))
    return m.message_id


# ═══════════════════════ تاپیک‌ها ═══════════════════════
async def _create_one(bot, gid, key, t) -> bool:
    try:
        ft = await bot.create_forum_topic(chat_id=gid, name=f"{t['emoji']} {t['title']}")
        t["thread_id"] = ft.message_thread_id
    except Exception as e:
        logger.warning("ops: create topic %s failed: %s", key, e)
        return False
    try:
        intro = await bot.send_message(chat_id=gid, message_thread_id=t["thread_id"],
                                       text=f"{t['emoji']} <b>{h(t['title'])}</b>\n{h(t.get('desc',''))}", parse_mode="HTML")
        try:
            await bot.pin_chat_message(chat_id=gid, message_id=intro.message_id, disable_notification=True)
        except Exception:
            pass
        if t.get("mode") == "none":
            await bot.close_forum_topic(chat_id=gid, message_thread_id=t["thread_id"])
    except Exception:
        logger.info("ops: topic %s intro/close failed", key, exc_info=True)
    return True


async def ensure_topics(bot, only=None) -> dict:
    """تاپیک‌های ساخته‌نشده را می‌سازد. → {key: ok}"""
    cfg = await load_cfg(force=True)
    gid = cfg["group_id"]
    out = {}
    if not gid:
        return out
    for key, t in cfg["topics"].items():
        if only and key != only:
            continue
        if t.get("thread_id"):
            out[key] = True
            continue
        out[key] = await _create_one(bot, gid, key, t)
        await asyncio.sleep(0.6)
    await save_topics(cfg["topics"])
    return out


async def create_topic(bot, key, title, emoji="📌", desc="", mode="none"):
    cfg = await load_cfg(force=True)
    if not cfg["group_id"]:
        return False, "گروه هنوز وصل نشده."
    key = tag(key).lower()
    if key in cfg["topics"]:
        return False, f"تاپیکی با کلید «{key}» از قبل هست."
    if mode not in ("none", "commands", "ai"):
        return False, "mode باید none | commands | ai باشد."
    t = {"emoji": emoji or "📌", "title": title, "mode": mode, "desc": desc, "thread_id": None}
    if not await _create_one(bot, cfg["group_id"], key, t):
        return False, "ساختنِ تاپیک در تلگرام شکست خورد (دسترسیِ «مدیریت تاپیک‌ها» را به ربات بدهید)."
    cfg["topics"][key] = t
    await save_topics(cfg["topics"])
    return True, f"✅ تاپیکِ «{emoji} {title}» ساخته شد (کلید: {key})."


async def edit_topic(bot, key, title=None, emoji=None, desc=None, mode=None):
    cfg = await load_cfg(force=True)
    t = cfg["topics"].get(key)
    if not t:
        return False, f"تاپیکی با کلید «{key}» نیست."
    if mode is not None and mode not in ("none", "commands", "ai"):
        return False, "mode باید none | commands | ai باشد."
    if title:
        t["title"] = title
    if emoji:
        t["emoji"] = emoji
    if desc is not None:
        t["desc"] = desc
    gid = cfg["group_id"]
    try:
        if t.get("thread_id") and (title or emoji):
            await bot.edit_forum_topic(chat_id=gid, message_thread_id=int(t["thread_id"]), name=f"{t['emoji']} {t['title']}")
        if mode is not None and mode != t.get("mode"):
            t["mode"] = mode
            if t.get("thread_id"):
                if mode == "none":
                    await bot.close_forum_topic(chat_id=gid, message_thread_id=int(t["thread_id"]))
                else:
                    await bot.reopen_forum_topic(chat_id=gid, message_thread_id=int(t["thread_id"]))
    except Exception as e:
        return False, f"تلگرام اجازه نداد: {e}"
    await save_topics(cfg["topics"])
    return True, f"✅ تاپیکِ «{key}» ویرایش شد."


async def delete_topic(bot, key):
    cfg = await load_cfg(force=True)
    t = cfg["topics"].get(key)
    if not t:
        return False, f"تاپیکی با کلید «{key}» نیست."
    used = [e for e, k in cfg["routes"].items() if k == key]
    if used:
        return False, f"این تاپیک هنوز مقصدِ رویدادهای {', '.join(used)} است؛ اول مسیرشان را عوض کنید."
    try:
        if t.get("thread_id"):
            await bot.delete_forum_topic(chat_id=cfg["group_id"], message_thread_id=int(t["thread_id"]))
    except Exception as e:
        return False, f"تلگرام اجازه نداد: {e}"
    cfg["topics"].pop(key, None)
    await save_topics(cfg["topics"])
    return True, f"🗑️ تاپیکِ «{key}» حذف شد."


async def set_route(event: str, topic_key: str):
    cfg = await load_cfg(force=True)
    if event not in EVENT_HELP:
        return False, "رویداد نامعتبر. معتبر: " + "، ".join(EVENT_HELP)
    if topic_key not in cfg["topics"]:
        return False, "تاپیک نامعتبر. معتبر: " + "، ".join(cfg["topics"])
    routes = {k: v for k, v in cfg["routes"].items()}
    routes[event] = topic_key
    await save_kv("routes", routes)
    return True, f"✅ رویدادِ «{event}» از این به بعد به تاپیکِ «{topic_key}» می‌رود."


async def bind_group(bot, chat):
    """اتصالِ گروه (فقط مدیر ارشد صدا می‌زند). → (ok, پیام)"""
    if chat.type != "supergroup":
        return False, "این باید یک «سوپرگروه» باشد."
    if not getattr(chat, "is_forum", False):
        return False, "اول در تنظیماتِ گروه گزینه‌ی «Topics» را روشن کنید، بعد دوباره /bindops بزنید."
    try:
        me = await bot.get_chat_member(chat.id, bot.id)
    except Exception as e:
        return False, f"عضویتِ ربات را نتوانستم بررسی کنم: {e}"
    if me.status != "administrator" or not getattr(me, "can_manage_topics", False):
        return False, ("ربات باید «ادمین» باشد و دسترسیِ «مدیریت تاپیک‌ها» (Manage Topics) داشته باشد. "
                       "به‌علاوه پین‌کردنِ پیام و حذفِ پیام را هم بدهید؛ بعد دوباره /bindops بزنید.")
    await save_kv("group_id", str(chat.id))
    res = await ensure_topics(bot)
    bad = [k for k, ok in res.items() if not ok]
    if bad:
        return False, "گروه وصل شد ولی این تاپیک‌ها ساخته نشدند: " + "، ".join(bad)
    return True, "✅ گروهِ فرماندهی وصل شد و تاپیک‌ها ساخته شدند."


# ═══════════════════════ پنلِ امنیت ═══════════════════════
async def gather_security() -> dict:
    now_local = datetime.now()
    since24 = (now_local - timedelta(hours=24)).isoformat()
    pending = await db.get_pending_requests()
    queued = await db.get_queued_requests()
    blocked = await db.get_all_blocked()
    snap = await db.get_security_snapshot(3)

    async def _q(sql, args=()):
        async with turso_db.connect(DB_PATH) as conn:
            conn.row_factory = turso_db.Row
            async with conn.execute(sql, tuple(args)) as cur:
                return list(await cur.fetchall())

    s = (await _q("SELECT COUNT(DISTINCT telegram_id) AS u, COUNT(*) AS n FROM stranger_log WHERE ts>=?", (since24,)))[0]
    hours = await _q("SELECT substr(ts,1,13) AS hr, COUNT(*) AS n FROM stranger_log WHERE ts>=? GROUP BY hr ORDER BY hr",
                     ((now_local - timedelta(hours=12)).isoformat(),))
    by_hr = {r["hr"]: r["n"] for r in hours}
    series = [by_hr.get((now_local - timedelta(hours=11 - i)).strftime("%Y-%m-%dT%H"), 0) for i in range(12)]
    try:
        from anomaly_alerts import DESTRUCTIVE_ACTIONS
        acts = list(DESTRUCTIVE_ACTIONS)
    except Exception:
        acts = []
    destructive = 0
    if acts:
        ph = ",".join("?" * len(acts))
        since_t = (now_teh() - timedelta(hours=24)).isoformat()
        r = await _q(f"SELECT COUNT(*) AS n FROM action_logs WHERE action_type IN ({ph}) AND logged_at>=?", (*acts, since_t))
        destructive = r[0]["n"] if r else 0
    admins = await db.get_all_admins()
    active_admins = [a for a in admins if a["is_active"]]
    online = 0
    for a in active_admins:
        try:
            if a["last_active"] and (now_teh() - datetime.fromisoformat(str(a["last_active"])[:19])).total_seconds() < 900:
                online += 1
        except Exception:
            pass
    status = await db.get_setting("system_status", "normal")
    return {"pending": len(pending), "queued": len(queued), "blocked": len(blocked), "st_users": s["u"], "st_actions": s["n"],
            "series": series, "destructive": destructive, "admins": len(active_admins), "online": online,
            "top": snap["top_strangers"], "system_status": status}


def score_of(d: dict) -> int:
    sc = 100
    sc -= min(30, d["pending"] * 10)
    sc -= min(15, d["queued"] * 3)
    sc -= min(20, d["destructive"] * 5)
    sc -= min(20, max(0, d["st_users"] - 5) * 2)
    if d["system_status"] != "normal":
        sc -= 10
    return max(0, sc)


def level_of(sc: int):
    if sc >= 85:
        return "🟢", "پایدار"
    if sc >= 65:
        return "🟡", "نیازمند توجه"
    if sc >= 40:
        return "🟠", "پرخطر"
    return "🔴", "بحرانی"


def render_security(d: dict) -> str:
    sc = score_of(d)
    ic, lv = level_of(sc)
    lines = [
        "╔══════════════════════╗",
        f"   🛡️ <b>مرکز امنیت سیستم</b>",
        "╚══════════════════════╝",
        f"{ic} وضعیت کلی: <b>{lv}</b>",
        f"{bar(sc, 12)}  <b>{fa(sc)}</b>/۱۰۰",
        "",
        "┏━ 📊 ۲۴ ساعت گذشته ━━━━━",
        f"┃ 👤 غریبه‌های فعال: <b>{fa(d['st_users'])}</b>  (اقدام: {fa(d['st_actions'])})",
        f"┃ ⏱ روندِ ۱۲ ساعت: <code>{spark(d['series'])}</code>",
        f"┃ 🔥 اقدامات مخربِ مدیران: <b>{fa(d['destructive'])}</b>",
        "┣━ 🚪 دسترسی ━━━━━━━━━━",
        f"┃ 📥 درخواستِ در انتظار: <b>{fa(d['pending'])}</b>",
        f"┃ ⏳ صفِ انتظار: <b>{fa(d['queued'])}</b>",
        f"┃ 🚫 بلاک‌شده‌ها: <b>{fa(d['blocked'])}</b>",
        "┣━ 👥 مدیران ━━━━━━━━━━",
        f"┃ فعال: <b>{fa(d['admins'])}</b>  |  آنلاین الان: <b>{fa(d['online'])}</b>",
        f"┃ 🚦 وضعیتِ سیستم: <b>{h(d['system_status'])}</b>",
    ]
    if d["top"]:
        lines.append("┣━ 🕵️ پرتلاش‌ترین غریبه‌ها ━━")
        for t in d["top"]:
            nm = t.get("full_name") or t.get("username") or t["telegram_id"]
            flag = "🚫" if t.get("is_blocked") else "👁"
            lines.append(f"┃ {flag} {h(nm)} — {fa(t['action_count'])} اقدام")
    lines += ["┗━━━━━━━━━━━━━━━━━━━━━", f"🕒 آخرین به‌روزرسانی: <code>{jfmt(now_teh())}</code>"]
    return "\n".join(lines)


def _sec_kb():
    from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
    return M([[B("🔄 به‌روزرسانی", callback_data="ops:sec:refresh"), B("🤖 بررسیِ رهگشا", callback_data="ops:sec:review")]])


async def refresh_security():
    d = await gather_security()
    await live("security:panel", "security", render_security(d), _sec_kb())
    return d


async def security_review(bot):
    """بررسیِ امنیتیِ رهگشا: پنل را تازه می‌کند و یک خلاصه‌ی مرتب (با LLM، با جایگزینِ قاعده‌محور) می‌فرستد."""
    d = await refresh_security()
    sc = score_of(d)
    ic, lv = level_of(sc)
    facts = (f"امتیاز {sc}/100 ({lv}); غریبه‌ی فعال ۲۴ساعت: {d['st_users']}; درخواست در انتظار: {d['pending']}; صف: {d['queued']}; "
             f"بلاک: {d['blocked']}; اقدام مخرب ۲۴ساعت: {d['destructive']}; مدیر فعال: {d['admins']} (آنلاین {d['online']}); "
             f"وضعیت سیستم: {d['system_status']}; پرتلاش‌ترین غریبه‌ها: "
             + "؛ ".join(f"{(t.get('full_name') or t.get('username') or t['telegram_id'])}={t['action_count']}" for t in d["top"]))
    summary = ""
    try:
        import ai_assistant as aia
        prompt = ("تو «رهگشا» هستی. با این داده‌های امنیتیِ یک ربات تلگرامی، یک گزارشِ کوتاه و مرتب بنویس با سه بخش دقیقاً با همین عنوان‌ها:\n"
                  "«خلاصه» (۱-۲ خط)، «ریسک‌ها» (حداکثر ۳ مورد، هرکدام یک خط)، «اقدام پیشنهادی» (حداکثر ۳ مورد).\n"
                  "فقط از داده‌ی زیر استفاده کن، چیزی از خودت نساز. فارسی، بدون مارک‌داون.\n\n" + facts)
        data = await aia._call_gemini([{"role": "user", "parts": [{"text": prompt}]}], None)
        summary = "".join(p.get("text", "") for p in aia._extract_parts(data)).strip()
    except Exception:
        logger.info("ops: security review LLM failed", exc_info=True)
    if not summary:
        risks = []
        if d["pending"]:
            risks.append(f"{fa(d['pending'])} درخواستِ دسترسی بی‌پاسخ")
        if d["destructive"]:
            risks.append(f"{fa(d['destructive'])} اقدامِ مخرب در ۲۴ ساعت")
        if d["st_users"] > 5:
            risks.append(f"افزایشِ غریبه‌های فعال ({fa(d['st_users'])})")
        summary = f"خلاصه: وضعیت {lv}.\nریسک‌ها: " + ("؛ ".join(risks) if risks else "موردِ مهمی نیست.")
    text = (f"🤖 <b>بررسیِ امنیتیِ رهگشا</b>  {ic} {h(lv)} ({fa(sc)}/۱۰۰)\n"
            f"━━━━━━━━━━━━━━━━\n{h(summary)}\n━━━━━━━━━━━━━━━━\n"
            f"#بررسی_امنیتی #امتیاز_{sc} #وضعیت_{tag(lv)} #تاریخ_{jfmt(now_teh(), '%Y_%m_%d')}")
    await post("security", text)
    return text


# ═══════════════════════ کارتِ زنده‌ی مدیران ═══════════════════════
_ROLE_FA = {"pishva": "👑 مدیر ارشد", "tournament_manager": "🏆 مدیر مسابقات", "security_manager": "🛡️ مدیر امنیتی"}


def _admins_kb():
    from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
    return M([[B("🔄 به‌روزرسانی", callback_data="ops:adm:refresh")]])


async def refresh_admins():
    admins = await db.get_all_admins()
    since24 = (now_teh() - timedelta(hours=24)).isoformat()
    since7 = (now_teh() - timedelta(days=7)).isoformat()
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute("SELECT admin_id, SUM(logged_at>=?) AS d1, COUNT(*) AS d7 FROM action_logs "
                                "WHERE logged_at>=? AND action_type NOT LIKE 'trail\\_%' ESCAPE '\\' GROUP BY admin_id",
                                (since24, since7)) as cur:
            cnt = {r["admin_id"]: (r["d1"] or 0, r["d7"]) for r in await cur.fetchall()}
    lines = ["╔══════════════════════╗", "   👥 <b>مرکز مدیران</b>", "╚══════════════════════╝"]
    lines.append(f"👑 مدیر ارشد — ۲۴س: {fa(cnt.get(PISHVA_ID, (0, 0))[0])} اقدام | ۷روز: {fa(cnt.get(PISHVA_ID, (0, 0))[1])}")
    for a in admins:
        c = cnt.get(a["telegram_id"], (0, 0))
        ago = "—"
        try:
            if a["last_active"]:
                mins = int((now_teh() - datetime.fromisoformat(str(a["last_active"])[:19])).total_seconds() // 60)
                ago = "هم‌اکنون" if mins < 2 else (f"{fa(mins)} دقیقه پیش" if mins < 120 else f"{fa(mins // 60)} ساعت پیش")
        except Exception:
            pass
        nm = a["display_name"] or a["full_name"] or a["username"] or a["telegram_id"]
        st = "🟢" if a["is_active"] else "⚪️"
        lines.append(f"\n{st} <b>{h(nm)}</b> — {_ROLE_FA.get(a['role'], h(a['role']))}\n"
                     f"   آخرین فعالیت: {ago} | ۲۴س: {fa(c[0])} | ۷روز: {fa(c[1])} | ⚠️ {fa(a['warnings'] or 0)}")
    lines.append(f"\n🕒 <code>{jfmt(now_teh())}</code>\n"
                 f"ℹ️ ریزِ هر اقدام در تاپیکِ لاگ‌ها با هشتگِ #مدیر_نام پیدا می‌شود.")
    await live("admins:card", "admins", "\n".join(lines), _admins_kb())


# ═══════════════════════ کارتِ زنده‌ی سرورها ═══════════════════════
def _srv_kb(subs):
    from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
    rows = [[B("🔄 به‌روزرسانی", callback_data="ops:srv:refresh")]]
    import servers
    for s in subs:
        sid = s["id"]
        if s.get("status") == servers.ST_ACTIVE:
            rows.append([B(f"⏸ تعلیق «{s.get('name', sid)}»", callback_data=f"ops:srv:susp:{sid}")])
        elif s.get("status") in (servers.ST_SUSPENDED, servers.ST_OFF):
            rows.append([B(f"▶️ فعال‌سازی «{s.get('name', sid)}»", callback_data=f"ops:srv:on:{sid}")])
    return M(rows)


async def refresh_servers():
    import servers
    allv = servers.all_servers(include_deleted=False)
    lines = ["╔══════════════════════╗", "   🖥️ <b>مرکز سرورها</b>", "╚══════════════════════╝"]
    emoji = {servers.ST_ACTIVE: "🟢", servers.ST_OFF: "⚪️", servers.ST_SUSPENDED: "🟠"}
    for s in allv:
        n = 0
        try:
            n = len(servers.member_ids(s["id"])) if s["id"] != srvctx.MAIN_ID else len(await db.get_active_admins()) + 1
        except Exception:
            pass
        lines.append(f"\n{emoji.get(s.get('status'), '⚫️')} <b>{h(s.get('name', s['id']))}</b> (#{fa(s['id'])})\n"
                     f"   وضعیت: {h(s.get('status'))} | اعضا: {fa(n)}")
    lines.append(f"\n🕒 <code>{jfmt(now_teh())}</code>")
    await live("servers:card", "servers", "\n".join(lines), _srv_kb([s for s in allv if s["id"] != srvctx.MAIN_ID]))


# ═══════════════════════ هوک‌ها (از بقیه‌ی پروژه صدا زده می‌شوند) ═══════════════════════
_CAT = (("player", "بازیکنان"), ("suspend", "بازیکنان"), ("kick", "بازیکنان"), ("revive", "بازیکنان"),
        ("match", "مسابقات"), ("result", "مسابقات"), ("tourn", "تورنمنت"), ("admin", "مدیران"), ("role", "مدیران"),
        ("perm", "مدیران"), ("ai_", "رهگشا"), ("server", "سرورها"), ("block", "امنیت"), ("req", "امنیت"),
        ("security", "امنیت"), ("backup", "سیستم"), ("setting", "سیستم"), ("status", "سیستم"))
_ADMIN_EVENT_KEYS = ("admin", "role", "perm", "warn_admin", "kick_admin", "add_admin", "remove_admin")


def _category(action: str) -> str:
    a = (action or "").lower()
    for k, v in _CAT:
        if a.startswith(k) or k in a:
            return v
    return "عمومی"


async def on_log(admin_id, action_type, description, target_id=None):
    """از database.log_action صدا زده می‌شود. هر اقدام = یک پیام با هشتگ."""
    if not _main_ok() or _BOT is None:
        return
    cfg = await load_cfg()
    if not cfg["group_id"]:
        return
    a = str(action_type or "")
    if a.startswith("trail_") and not cfg["log_trail"]:
        return
    try:
        if admin_id == PISHVA_ID:
            name, role = "مدیر_ارشد", "pishva"
        else:
            adm = await db.get_admin(int(admin_id)) if admin_id else None
            name = (adm["display_name"] or adm["full_name"]) if adm else f"id{admin_id}"
            role = adm["role"] if adm else "نامشخص"
    except Exception:
        name, role = f"id{admin_id}", "نامشخص"
    n = now_teh()
    cat = _category(a)
    wd = ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه")[n.weekday()]
    text = (f"🗂 <b>{h(a)}</b>\n👤 {h(name.replace('_', ' '))} ({_ROLE_FA.get(role, h(role))})\n"
            f"📝 {h(str(description or '')[:900])}\n🕒 <code>{jfmt(n, '%Y/%m/%d %H:%M:%S')}</code>\n\n"
            f"#عمل_{tag(a)} #دسته_{tag(cat)} #مدیر_{tag(name)} #نقش_{tag(role)} "
            + (f"#هدف_{target_id} " if target_id else "")
            + f"#تاریخ_{jfmt(n, '%Y_%m_%d')} #ماه_{jfmt(n, '%Y_%m')} #ساعت_{n.strftime('%H')} #روز_{tag(wd)} #سرور_اصلی")
    await post_event("log", text, droppable=True)
    if any(k in a.lower() for k in _ADMIN_EVENT_KEYS):
        await post_event("admin_event", text, droppable=True)


_stranger_pending = {}


async def on_stranger(tid, username, full_name, action):
    """از security._track_activity: هر تعاملِ پیویِ غریبه. اولین بار فوری، بعدی‌ها با تأخیرِ ۱۵ثانیه (ادغامِ ادیت‌ها)."""
    if not _main_ok() or _BOT is None:
        return
    cfg = await load_cfg()
    if not cfg["group_id"]:
        return
    first = not await _rows("SELECT 1 FROM ops_live WHERE slot=?", (f"stranger:{tid}",))
    if first:
        await _flush_stranger(tid)
        return
    if tid in _stranger_pending:
        return
    _stranger_pending[tid] = True

    async def _later():
        await asyncio.sleep(15)
        _stranger_pending.pop(tid, None)
        try:
            await _flush_stranger(tid)
        except Exception:
            logger.info("ops: stranger flush failed", exc_info=True)
    asyncio.create_task(_later())


async def _flush_stranger(tid):
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute("SELECT username, full_name, action, ts FROM stranger_log WHERE telegram_id=? ORDER BY id DESC LIMIT 400",
                                (tid,)) as cur:
            rows = list(await cur.fetchall())
    if not rows:
        return
    total_cur = None
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute("SELECT COUNT(*) AS n, MIN(ts) AS f FROM stranger_log WHERE telegram_id=?", (tid,)) as cur:
            total_cur = (await cur.fetchone())
    last = rows[0]
    name = last["full_name"] or last["username"] or str(tid)
    blocked = bool(await db.get_blocked_user(tid))
    req = None
    try:
        for q in await db.get_pending_requests():
            if q["telegram_id"] == tid:
                req = "📥 درخواستِ دسترسی در انتظار"
        for q in await db.get_queued_requests():
            if q["telegram_id"] == tid:
                req = "⏳ در صفِ انتظار"
    except Exception:
        pass
    status = "🚫 بلاک" if blocked else (req or "👁 بدونِ دسترسی")

    def _t(ts):
        try:
            return jfmt(datetime.fromisoformat(str(ts)[:19]), "%m/%d %H:%M:%S")
        except Exception:
            return str(ts)[:16]
    acts = "\n".join(f"  ▫️ <code>{_t(r['ts'])}</code> {h(r['action'])}" for r in rows[:8])
    text = (f"👤 <b>{h(name)}</b>  {h('@' + last['username']) if last['username'] else ''}\n"
            f"🆔 <code>{tid}</code>\n📌 وضعیت: {status}\n"
            f"🔢 کلِ اقدامات: <b>{fa(total_cur['n'])}</b>\n"
            f"🟢 اولین بار: <code>{_t(total_cur['f'])}</code>  |  آخرین: <code>{_t(last['ts'])}</code>\n\n"
            f"🧾 <b>آخرین اقدامات:</b>\n{acts}\n\n#غریبه #آیدی_{tid}")
    from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
    kb = None if blocked else M([[B("🚫 بلاک", callback_data=f"ops:blk:{tid}")]])
    await live(f"stranger:{tid}", "strangers", text, kb)


async def on_status_change(key, value):
    """از database.set_setting برای کلیدهای وضعیتی."""
    labels = {"system_status": "وضعیت سیستم", "bot_update_mode": "حالت آپدیت", "bot_active_for_admins": "ربات برای مدیران",
              "working_hours_active": "ساعت کاری"}
    if key not in labels or not _main_ok():
        return
    await post_event("status", f"🚦 <b>{labels[key]}</b> تغییر کرد → <b>{h(value)}</b>\n🕒 <code>{jfmt(now_teh())}</code>\n#وضعیت #{tag(key)}")


async def on_feedback(sender_id, fb_type, title, content):
    if not _main_ok():
        return
    try:
        adm = await db.get_admin(int(sender_id))
        who = (adm["display_name"] or adm["full_name"]) if adm else ("مدیر ارشد" if sender_id == PISHVA_ID else str(sender_id))
    except Exception:
        who = str(sender_id)
    await post_event("suggestion", f"💡 <b>{h(fb_type)}</b> — {h(title)}\n👤 {h(who)}\n\n{h(str(content)[:1500])}\n\n#پیشنهاد #{tag(fb_type)}")


async def on_champion(text_markdown: str):
    plain = re.sub(r"[*_`]", "", text_markdown or "")
    await post_event("champion", f"🏆 {h(plain)}\n\n#قهرمان_هفته")


# ═══════════════════════ گزارشِ روزانه‌ی مسابقات ═══════════════════════
async def build_digest(date_g: str):
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(
                "SELECT m.id, m.result, wp.full_name AS w, bp.full_name AS b FROM matches m "
                "LEFT JOIN players wp ON wp.id=m.white_player_id LEFT JOIN players bp ON bp.id=m.black_player_id "
                "WHERE substr(m.match_date,1,10)=? ORDER BY m.id", (date_g,)) as cur:
            ms = list(await cur.fetchall())
    if not ms:
        return None
    wins, losses, lines, draws, pend = [], [], [], 0, 0
    for m in ms:
        w, b, r = m["w"] or "؟", m["b"] or "؟", m["result"]
        if r == "white":
            wins.append(w); losses.append(b); lines.append(f"✅ {h(w)}  ⚔️  {h(b)} ← <b>{h(w)}</b>")
        elif r == "black":
            wins.append(b); losses.append(w); lines.append(f"✅ {h(w)}  ⚔️  {h(b)} ← <b>{h(b)}</b>")
        elif r == "draw":
            draws += 1; lines.append(f"🤝 {h(w)}  ⚔️  {h(b)} ← مساوی")
        else:
            pend += 1; lines.append(f"⏳ {h(w)}  ⚔️  {h(b)} ← نتیجه ثبت نشده")
    try:
        jd = jdatetime.date.fromgregorian(date=datetime.strptime(date_g, "%Y-%m-%d").date()).strftime("%Y/%m/%d")
    except Exception:
        jd = date_g
    text = (f"🏆 <b>گزارش مسابقاتِ {jd}</b>\n━━━━━━━━━━━━━━\n"
            f"📊 {fa(len(ms))} مسابقه | ✅ {fa(len(wins))} برد | 🤝 {fa(draws)} مساوی | ⏳ {fa(pend)} بدون نتیجه\n\n"
            + "\n".join(lines)
            + ("\n\n🥇 <b>پیروزمندان:</b> " + "، ".join(h(x) for x in dict.fromkeys(wins)) if wins else "")
            + ("\n💔 <b>بازندگان:</b> " + "، ".join(h(x) for x in dict.fromkeys(losses)) if losses else "")
            + f"\n\n#گزارش_روزانه #تاریخ_{jd.replace('/', '_')}")
    return text


async def send_digest(date_g=None, force=False):
    date_g = date_g or now_teh().strftime("%Y-%m-%d")
    text = await build_digest(date_g)
    if not text:
        return False, "برای این روز مسابقه‌ای ثبت نشده."
    m = await post_event("match_digest", text)
    if m is None:
        return False, "ارسال به تاپیک ممکن نشد (گروه وصل است؟)."
    return True, "✅ گزارش ارسال شد."


# ═══════════════════════ جاب‌ها ═══════════════════════
async def _tick(context):
    if not _main_ok():
        return
    try:
        cfg = await load_cfg()
        if not cfg["group_id"]:
            return
        n = now_teh()
        today = n.strftime("%Y-%m-%d")
        if cfg["last_digest"] != today:
            try:
                hh, mm = (int(x) for x in cfg["matches_time"].split(":"))
            except Exception:
                hh, mm = 21, 0
            due = n.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if due <= n < due + timedelta(hours=4):      # بیشتر از ۴ ساعت دیرتر شد → امروز رد
                await save_kv("last_digest", today)
                await send_digest(today)
            elif n >= due + timedelta(hours=4):
                await save_kv("last_digest", today)
    except Exception:
        logger.warning("ops: tick failed", exc_info=True)


async def _refresh_all(context):
    if not _main_ok():
        return
    cfg = await load_cfg()
    if not cfg["group_id"]:
        return
    for fn in (refresh_security, refresh_admins, refresh_servers):
        try:
            await fn()
        except Exception:
            logger.info("ops: %s failed", fn.__name__, exc_info=True)


def start(application):
    global _BOT
    _BOT = application.bot
    jq = application.job_queue
    if jq:
        jq.run_repeating(_tick, interval=60, first=50, name="ops_tick")
        jq.run_repeating(_refresh_all, interval=600, first=90, name="ops_refresh")
    logger.info("ops_group: started")


# ═══════════════════════ هندلرهای گروه ═══════════════════════
async def _role(uid):
    import ai_assistant as aia
    return await aia.get_user_role(uid)


async def cmd_admin(update, ctx):
    from telegram.ext import ApplicationHandlerStop
    chat, m, user = update.effective_chat, update.effective_message, update.effective_user
    if not (chat and m and user) or user.id != PISHVA_ID:
        return
    cmd = (m.text or "").split()[0].lstrip("/").split("@")[0].lower()
    global _BOT
    _BOT = ctx.bot
    if cmd == "bindops":
        ok, msg = await bind_group(ctx.bot, chat)
        await m.reply_text(msg)
        if ok:
            for fn in (refresh_security, refresh_admins, refresh_servers):
                try:
                    await fn()
                except Exception:
                    pass
    elif cmd == "opsrebuild":
        cfg = await load_cfg(force=True)
        if cfg["group_id"] != chat.id:
            await m.reply_text("این گروه، گروهِ فرماندهی نیست. اول /bindops بزنید.")
        else:
            res = await ensure_topics(ctx.bot)
            await m.reply_text("✅ تاپیک‌ها بررسی/ساخته شدند: " + "، ".join(f"{k}{'✔' if v else '✖'}" for k, v in res.items()))
    raise ApplicationHandlerStop


async def on_message(update, ctx):
    from telegram.ext import ApplicationHandlerStop
    chat, m, user = update.effective_chat, update.effective_message, update.effective_user
    if not (chat and m and user):
        return
    cfg = await load_cfg()
    if not cfg["group_id"] or chat.id != cfg["group_id"]:
        return
    key = topic_of(cfg, m.message_thread_id)
    role = await _role(user.id)
    mode = (cfg["topics"].get(key) or {}).get("mode") if key else None
    if role and mode == "ai":
        await _rahgosha(update, ctx)
    elif role and mode == "commands":
        import ops_players
        await ops_players.handle(update, ctx, (m.text or "").strip(), role)
    raise ApplicationHandlerStop     # بقیه‌ی تاپیک‌ها ورودی ندارند؛ هیچ هندلرِ دیگری هم این‌جا کار نمی‌کند


async def _rahgosha(update, ctx):
    """هر پیام در تاپیکِ رهگشا → همان موتورِ چتِ پیوی. «ai_mode» فقط در همین فراخوانی روشن می‌ماند."""
    import ai_assistant as aia
    ud = ctx.user_data
    had = "ai_mode" in ud
    prev = ud.get("ai_mode")
    ud["ai_mode"] = True
    ud["_ops_topic_call"] = True
    try:
        await aia.ai_assistant_message(update, ctx)
    finally:
        ud.pop("_ops_topic_call", None)
        if had:
            ud["ai_mode"] = prev
        else:
            ud.pop("ai_mode", None)


async def on_cb(update, ctx):
    q = update.callback_query
    uid = q.from_user.id
    parts = (q.data or "").split(":")
    if uid != PISHVA_ID and not await _role(uid):
        await q.answer("⛔", show_alert=True)
        return
    if len(parts) < 3:
        await q.answer()
        return
    if parts[1] == "sec":
        await q.answer("⏳")
        if parts[2] == "refresh":
            await refresh_security()
        else:
            await security_review(ctx.bot)
        return
    if parts[1] == "adm":
        await q.answer("⏳")
        await refresh_admins()
        return
    # از اینجا به بعد فقط مدیر ارشد
    if uid != PISHVA_ID:
        await q.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
    if parts[1] == "srv":
        import servers
        if parts[2] == "refresh":
            await q.answer("⏳")
            await refresh_servers()
        elif parts[2] in ("susp", "on") and len(parts) == 4:
            sid = int(parts[3])
            word = "تعلیق" if parts[2] == "susp" else "فعال‌سازی"
            await q.answer()
            await q.message.reply_text(f"❓ مطمئنی «{word}» سرور #{sid}؟", message_thread_id=q.message.message_thread_id,
                                       reply_markup=M([[B("✅ بله", callback_data=f"ops:srvy:{parts[2]}:{sid}"),
                                                        B("❌ نه", callback_data="ops:no:0")]]))
        return
    if parts[1] == "srvy" and len(parts) == 4:
        import servers
        sid = int(parts[3])
        if sid == srvctx.MAIN_ID:
            await q.answer("سرورِ اصلی قابل تعلیق نیست.", show_alert=True)
            return
        if parts[2] == "susp":
            await servers.set_status(sid, servers.ST_SUSPENDED, "از گروه فرماندهی")
        else:
            await servers.set_status(sid, servers.ST_ACTIVE)
        await db.log_action(uid, "server_status", f"سرور #{sid} → {'تعلیق' if parts[2] == 'susp' else 'فعال'} (گروه فرماندهی)")
        await q.answer("✅")
        try:
            await q.message.delete()
        except Exception:
            pass
        await refresh_servers()
        return
    if parts[1] == "no":
        await q.answer()
        try:
            await q.message.delete()
        except Exception:
            pass
        return
    if parts[1] == "blk" and len(parts) == 3:
        tid = int(parts[2])
        await q.answer()
        await q.message.reply_text(f"❓ بلاکِ دائمِ {tid}؟", message_thread_id=q.message.message_thread_id,
                                   reply_markup=M([[B("🚫 بله، بلاک", callback_data=f"ops:blky:{tid}"), B("❌ نه", callback_data="ops:no:0")]]))
        return
    if parts[1] == "blky" and len(parts) == 3:
        tid = int(parts[2])
        async with turso_db.connect(DB_PATH) as conn:
            conn.row_factory = turso_db.Row
            async with conn.execute("SELECT username, full_name FROM stranger_log WHERE telegram_id=? ORDER BY id DESC LIMIT 1", (tid,)) as cur:
                r = await cur.fetchone()
        await db.block_user(tid, (r["username"] if r else "") or "", (r["full_name"] if r else "") or "", "بلاک از گروه فرماندهی", uid)
        await db.log_action(uid, "block_user", f"بلاکِ {tid} (گروه فرماندهی)", tid)
        await q.answer("🚫 بلاک شد")
        try:
            await q.message.delete()
        except Exception:
            pass
        await _flush_stranger(tid)
        return
    await q.answer()


def register(app):
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters
    app.add_handler(CommandHandler(["bindops", "opsrebuild"], cmd_admin, filters=filters.ChatType.SUPERGROUP), group=-96)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, on_message), group=-95)
    app.add_handler(CallbackQueryHandler(on_cb, pattern=r"^ops:"), group=-94)
