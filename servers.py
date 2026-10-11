"""
servers.py — چندسروری (دنیاهای ایزوله) برای CMSLast.

  • سرور ۱ = «سرور اصلی» (همین پروژه‌ی فعال؛ مدیر ارشد + مدیر مسابقات + مدیر امنیتی).
  • سرور ۲..۶ = «مجموعه‌ها» (مدرسه‌ها). هر کدام دیتابیسِ کاملاً جدا دارند (حتی ساعتِ کاری، لاگ، بازیکن، تنظیمات…).
    در آن‌ها «مدیر مجموعه» نقشِ بالاست و مدیر مسابقات/امنیتی را خودش استخدام می‌کند.

این ماژول فقط «رجیستری» را نگه می‌دارد و آن هم در دیتابیسِ سرور اصلی (جدول‌های servers / server_members /
server_requests). هیچ دیتای عملیاتیِ یک مجموعه در رجیستری نیست.

جداسازی: ContextVarِ srvctx تعیین می‌کند هر درخواست روی کدام دیتابیس اجرا شود (turso_db._get_client).
اگر دیتابیسِ یک سرور بارگذاری نشده باشد، هیچ‌وقت به دیتابیسِ اصلی برنمی‌گردیم.

دیتابیسِ هر سرور از کجا می‌آید (به ترتیبِ اولویت):
  ۱) متغیرهای محیطیِ از-پیش-ساخته: SERVER{N}_TURSO_URL و SERVER{N}_TURSO_TOKEN   (N = ۲..۶)
  ۲) ساختِ خودکار با Turso Platform API:  TURSO_API_TOKEN  +  TURSO_ORG  (+ TURSO_GROUP، پیش‌فرض default)
حذفِ سرور هرگز دیتابیس را پاک نمی‌کند (فقط علامتِ «حذف‌شده»)، پس همیشه قابلِ بازیابی است.
"""
import json
import logging
import os
import secrets
import time
from datetime import datetime

import turso_db
import srvctx
from srvctx import MAIN_ID, MAX_SERVERS
from config import DB_PATH, PISHVA_ID

logger = logging.getLogger(__name__)

# ─── وضعیت‌ها ───────────────────────────────────────────────
ST_ACTIVE = "active"        # روشن
ST_OFF = "off"              # خاموش (موقت، با دکمه)
ST_SUSPENDED = "suspended"  # تعلیق (با دلیل و در صورت نیاز تاریخ)
ST_DELETED = "deleted"      # حذف‌شده (قابل بازیابی از بخش «بازیابی»)
STATUS_FA = {ST_ACTIVE: "🟢 روشن", ST_OFF: "⚫ خاموش", ST_SUSPENDED: "🟠 تعلیق", ST_DELETED: "🗑 حذف‌شده"}

# ─── قابلیت‌های قابل‌کنترل برای هر سرور (کلید، عنوان فارسی) ──────────────
# مدیر ارشد می‌تواند هر کدام را برای «همه‌ی سرورها» یا «یک سرور» روشن/خاموش کند.
# اجرا: در روتر (bot.py) بر اساسِ پیشوندِ callback_data و کلمه‌های کلیدی.
FEATURES = [
    ("matches",   "مسابقات و ثبت نتیجه"),
    ("scan",      "ثبت مسابقه با عکس"),
    ("tournaments", "تورنمنت‌ها"),
    ("players",   "مدیریت بازیکنان و کلاس‌ها"),
    ("teams",     "تیم‌ها"),
    ("comms",     "مخابرات"),
    ("tasks",     "وظایف"),
    ("calendar",  "تقویم"),
    ("help",      "مرکز راهنما"),
    ("dashboard", "داشبورد"),
    ("ai",        "رهگشا (دستیار هوشمند)"),
    ("chess",     "شطرنج زنده"),
    ("hub",       "پنل هاب (CMS)"),
    ("hire",      "استخدام مدیر (لینک دعوت)"),
    ("workhours", "ساعت کاری"),
    ("elo",       "امتیاز Elo و رتبه‌بندی"),
    ("lottery",   "قرعه‌کشی"),
]
FEATURE_KEYS = [k for k, _ in FEATURES]
FEATURE_FA = dict(FEATURES)

# پیشوندِ callback_data ← کلیدِ قابلیت (برای دروازه‌ی روتر)
CALLBACK_FEATURE = [
    ("menu_matches", "matches"), ("match_", "matches"), ("mhist_", "matches"), ("back_matches", "matches"),
    ("mscan_", "scan"), ("scan_", "scan"),
    ("menu_tournament", "tournaments"), ("tourn_", "tournaments"), ("thistory_", "tournaments"),
    ("menu_players", "players"), ("player_", "players"), ("class_", "players"), ("ptop_", "players"), ("bulk_", "players"),
    ("teams_", "teams"),
    ("menu_comms", "comms"), ("comms_", "comms"),
    ("menu_tasks", "tasks"), ("task_", "tasks"),
    ("menu_calendar", "calendar"), ("cal_", "calendar"), ("calset_", "calendar"),
    ("menu_help", "help"), ("help_", "help"),
    ("dashboard_", "dashboard"),
    ("ai_", "ai"), ("aip_", "ai"), ("aim_", "ai"),
    ("chess_", "chess"), ("chesshist_", "chess"), ("chessgames_", "chess"),
    ("wh_", "workhours"),
    ("elo_", "elo"),
    ("lottery_", "lottery"), ("adv_", "lottery"),
]

_MAIN_SCOPE = None  # (برای خوانایی)

# ─── کشِ درون‌حافظه‌ی رجیستری (تک‌پردازه‌ایم؛ نوشتن‌ها همین‌جا انجام می‌شوند) ───
_SERVERS = {}        # sid -> dict
_MEMBERS = {}        # telegram_id -> (sid, state)   state: 'member' | 'pending'
_LOADED = False


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _main():
    """هر عملِ رجیستری باید روی دیتابیسِ اصلی اجرا شود، از هر سروری که صدا زده شود."""
    return srvctx.use(MAIN_ID)


# ═════════════════════════ جدول‌ها و بارگذاری ═════════════════════════
async def ensure_tables():
    with _main():
        async with turso_db.connect(DB_PATH) as db:
            await db.executescript("""
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY,
                name TEXT,
                school TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                owner_tid INTEGER,
                db_name TEXT DEFAULT '',
                db_url TEXT DEFAULT '',
                db_token TEXT DEFAULT '',
                features TEXT DEFAULT '{}',
                invite_code TEXT DEFAULT '',
                suspend_reason TEXT DEFAULT '',
                suspend_until TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT,
                deleted_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS server_members (
                telegram_id INTEGER PRIMARY KEY,
                server_id INTEGER,
                state TEXT DEFAULT 'member',
                joined_at TEXT
            );
            CREATE TABLE IF NOT EXISTS server_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                username TEXT,
                full_name TEXT,
                school TEXT,
                need_date TEXT,
                phone TEXT,
                grade_field TEXT,
                status TEXT DEFAULT 'pending',
                server_id INTEGER,
                created_at TEXT,
                decided_at TEXT
            );
            CREATE TABLE IF NOT EXISTS server_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """)
            await db.commit()


async def load():
    """هنگامِ بالا آمدنِ ربات: رجیستری را می‌خواند و کلاینتِ دیتابیسِ همه‌ی سرورها را می‌سازد."""
    global _LOADED
    await ensure_tables()
    servers, members = {}, {}
    with _main():
        async with turso_db.connect(DB_PATH) as db:
            async with db.execute("SELECT * FROM servers") as cur:
                for r in await cur.fetchall():
                    d = {k: r[k] for k in r.keys()}
                    d["features"] = _loads(d.get("features"))
                    servers[int(d["id"])] = d
            async with db.execute("SELECT telegram_id, server_id, state FROM server_members") as cur:
                for r in await cur.fetchall():
                    members[int(r["telegram_id"])] = (int(r["server_id"]), r["state"] or "member")
    _SERVERS.clear(); _SERVERS.update(servers)
    _MEMBERS.clear(); _MEMBERS.update(members)
    for sid, s in servers.items():
        if s.get("db_url") and s.get("db_token"):
            turso_db.register_server_client(sid, s["db_url"], s["db_token"])
    await load_global_features()
    _LOADED = True
    logger.info("servers: %d سرورِ ثبت‌شده، %d عضو", len(servers), len(members))


def _loads(v):
    try:
        d = json.loads(v or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


# ═════════════════════════ خواندنِ هم‌زمان (sync) از کش ═════════════════════════
def get(sid: int):
    if int(sid) == MAIN_ID:
        return {"id": MAIN_ID, "name": "سرور اصلی", "status": ST_ACTIVE, "owner_tid": PISHVA_ID,
                "features": {}, "school": "", "invite_code": ""}
    return _SERVERS.get(int(sid))


def all_servers(include_deleted: bool = True):
    out = [get(MAIN_ID)]
    for sid in sorted(_SERVERS):
        s = _SERVERS[sid]
        if include_deleted or s.get("status") != ST_DELETED:
            out.append(s)
    return out


def sub_servers(include_deleted: bool = False):
    return [s for s in all_servers(include_deleted) if int(s["id"]) != MAIN_ID]


def active_ids():
    """سرورهایی که کارهای زمان‌بندی‌شده باید برایشان اجرا شوند (اصلی + مجموعه‌های روشن)."""
    return [MAIN_ID] + [sid for sid in sorted(_SERVERS)
                        if _SERVERS[sid].get("status") == ST_ACTIVE and turso_db.has_server_client(sid)]


def owner_tid(sid: int = None):
    sid = srvctx.current() if sid is None else int(sid)
    if sid == MAIN_ID:
        return PISHVA_ID
    s = _SERVERS.get(sid)
    return int(s["owner_tid"]) if s and s.get("owner_tid") else None


def is_owner(uid: int, sid: int = None) -> bool:
    """آیا uid «مدیر مجموعه»ی سرورِ جاری است؟ (برای سرور اصلی همیشه False؛ آنجا مدیر ارشد داریم.)"""
    sid = srvctx.current() if sid is None else int(sid)
    if sid == MAIN_ID:
        return False
    o = owner_tid(sid)
    return bool(o) and int(uid) == int(o)


def is_boss(uid: int) -> bool:
    """«مدیر ارشدِ این سرور»: در سرور اصلی = مدیر ارشد؛ در مجموعه‌ها = مدیر مجموعه (برای دروازه‌های هم‌ترازِ مدیر ارشد)."""
    uid = int(uid)
    if srvctx.current() == MAIN_ID:
        return uid == PISHVA_ID
    return is_owner(uid)


def boss_chat(sid: int = None) -> int:
    """گیرنده‌ی اعلان‌های «مدیر ارشد» در سرورِ جاری: مدیر ارشد (اصلی) یا مدیر مجموعه."""
    return owner_tid(sid) or PISHVA_ID


def member_server(uid: int) -> int:
    """کاربر به کدام سرور تعلق دارد؟ مدیر ارشد و ناشناس‌ها → اصلی."""
    if int(uid) == PISHVA_ID:
        return MAIN_ID
    m = _MEMBERS.get(int(uid))
    if not m:
        return MAIN_ID
    sid = m[0]
    return sid if sid in _SERVERS else MAIN_ID


def member_state(uid: int):
    m = _MEMBERS.get(int(uid))
    return m[1] if m else None


def feature_on(sid: int, key: str) -> bool:
    if int(sid) == MAIN_ID:
        return True
    s = _SERVERS.get(int(sid))
    if not s:
        return False
    g = _GLOBAL_FEATURES.get(key, True)
    if not g:
        return False
    return bool((s.get("features") or {}).get(key, True))


_GLOBAL_FEATURES = {}   # «همه‌ی سرورها»: کلید → bool (در server_meta ذخیره می‌شود)
META_CACHE = {}         # کلید → مقدار (مثلاً ai_global_prompt) از جدول server_meta


def feature_of_callback(data: str):
    for prefix, key in CALLBACK_FEATURE:
        if data.startswith(prefix):
            return key
    return None


def gate_message(sid: int):
    """اگر سرور در دسترس نیست پیامِ مناسب برای اعضایش؛ وگرنه None."""
    if int(sid) == MAIN_ID:
        return None
    s = _SERVERS.get(int(sid))
    if not s:
        return "⛔ این سرور وجود ندارد."
    st = s.get("status")
    if st == ST_ACTIVE:
        return None
    if st == ST_OFF:
        return "⚫ این سرور در حال حاضر خاموش است.\nبرای اطلاعات بیشتر با مدیر ارشد تماس بگیرید."
    if st == ST_SUSPENDED:
        why = (s.get("suspend_reason") or "").strip()
        until = (s.get("suspend_until") or "").strip()
        t = "🟠 این سرور موقتاً تعلیق شده است."
        if why:
            t += f"\n📝 دلیل: {why}"
        if until:
            t += f"\n⏳ تا: {until}"
        return t + "\nبرای اطلاعات بیشتر با مدیر ارشد تماس بگیرید."
    return "🗑 این سرور حذف شده است."


# ═════════════════════════ نوشتن در رجیستری ═════════════════════════
async def _exec(sql, args=()):
    with _main():
        async with turso_db.connect(DB_PATH) as db:
            cur = await db.execute(sql, tuple(args))
            await db.commit()
            return cur


async def set_member(uid: int, sid: int, state: str = "member"):
    uid, sid = int(uid), int(sid)
    if uid == PISHVA_ID:
        return
    await _exec("INSERT OR REPLACE INTO server_members(telegram_id, server_id, state, joined_at) VALUES (?,?,?,?)",
                (uid, sid, state, _now()))
    _MEMBERS[uid] = (sid, state)


async def remove_member(uid: int):
    uid = int(uid)
    await _exec("DELETE FROM server_members WHERE telegram_id=?", (uid,))
    _MEMBERS.pop(uid, None)


def member_ids(sid: int):
    return [u for u, (s, _st) in _MEMBERS.items() if s == int(sid)]


async def save_server(sid: int, **fields):
    s = _SERVERS.get(int(sid))
    if not s:
        return
    fields["updated_at"] = _now()
    cols, vals = [], []
    for k, v in fields.items():
        s[k] = v
        cols.append(f"{k}=?")
        vals.append(json.dumps(v, ensure_ascii=False) if k == "features" else v)
    vals.append(int(sid))
    await _exec(f"UPDATE servers SET {', '.join(cols)} WHERE id=?", vals)


async def set_status(sid: int, status: str, reason: str = "", until: str = ""):
    extra = {}
    if status == ST_DELETED:
        extra["deleted_at"] = _now()
    if status == ST_ACTIVE:
        extra.update(suspend_reason="", suspend_until="")
    elif status == ST_SUSPENDED:
        extra.update(suspend_reason=reason or "", suspend_until=until or "")
    await save_server(sid, status=status, **extra)


async def rename(sid: int, name: str):
    await save_server(sid, name=name.strip()[:60])


async def set_feature(sid: int, key: str, value: bool):
    s = _SERVERS.get(int(sid))
    if not s or key not in FEATURE_KEYS:
        return
    f = dict(s.get("features") or {})
    f[key] = bool(value)
    await save_server(sid, features=f)


async def set_global_feature(key: str, value: bool):
    if key not in FEATURE_KEYS:
        return
    _GLOBAL_FEATURES[key] = bool(value)
    await _exec("INSERT OR REPLACE INTO server_meta(key, value) VALUES (?,?)",
                ("global_features", json.dumps(_GLOBAL_FEATURES)))


async def load_global_features():
    with _main():
        async with turso_db.connect(DB_PATH) as db:
            async with db.execute("SELECT key, value FROM server_meta") as cur:
                rows = await cur.fetchall()
    _GLOBAL_FEATURES.clear()
    META_CACHE.clear()
    for r in rows:
        META_CACHE[r[0]] = r[1]
        if r[0] == "global_features":
            _GLOBAL_FEATURES.update({k: bool(v) for k, v in _loads(r[1]).items()})


def meta(key: str, default: str = "") -> str:
    return META_CACHE.get(key, default)


def next_free_id():
    for sid in range(2, MAX_SERVERS + 1):
        if sid not in _SERVERS:
            return sid
    return None


def live_count() -> int:
    """تعدادِ سرورهایی که ظرفیت را اشغال کرده‌اند (حذف‌شده‌ها هم تا بازیابی/پاک‌سازی جا نگه می‌دارند)."""
    return len(_SERVERS)


# ═════════════════════════ دیتابیسِ هر سرور ═════════════════════════
def _env_slot(sid: int):
    url = os.environ.get(f"SERVER{sid}_TURSO_URL", "").strip()
    tok = os.environ.get(f"SERVER{sid}_TURSO_TOKEN", "").strip()
    return (url, tok) if url and tok else (None, None)


async def provision_db(sid: int, label: str):
    """(db_name, url, token) برای سرور sid؛ یا خطای فارسیِ روشن."""
    url, tok = _env_slot(sid)
    if url:
        return f"env-slot-{sid}", url, tok

    api = os.environ.get("TURSO_API_TOKEN", "").strip()
    org = os.environ.get("TURSO_ORG", "").strip()
    if not (api and org):
        raise RuntimeError(
            "برای ساختِ دیتابیسِ سرورِ جدید یکی از این دو باید تنظیم شود:\n"
            f"۱) در Railway: SERVER{sid}_TURSO_URL و SERVER{sid}_TURSO_TOKEN (دیتابیسِ از-پیش-ساخته)\n"
            "۲) یا TURSO_API_TOKEN و TURSO_ORG (ساختِ خودکار)")
    import httpx
    group = os.environ.get("TURSO_GROUP", "").strip() or "default"
    name = f"cms-s{sid}-{secrets.token_hex(3)}"
    headers = {"Authorization": f"Bearer {api}"}
    async with httpx.AsyncClient(timeout=40.0) as cl:
        r = await cl.post(f"https://api.turso.tech/v1/organizations/{org}/databases",
                          json={"name": name, "group": group}, headers=headers)
        if r.status_code >= 300:
            raise RuntimeError(f"Turso: ساختِ دیتابیس ناموفق بود ({r.status_code}): {r.text[:200]}")
        host = (r.json().get("database") or {}).get("Hostname") or (r.json().get("database") or {}).get("hostname")
        if not host:
            raise RuntimeError("Turso: آدرسِ دیتابیسِ ساخته‌شده در پاسخ نبود.")
        t = await cl.post(f"https://api.turso.tech/v1/organizations/{org}/databases/{name}/auth/tokens",
                          params={"expiration": "never", "authorization": "full-access"}, headers=headers)
        if t.status_code >= 300:
            raise RuntimeError(f"Turso: ساختِ توکن ناموفق بود ({t.status_code}): {t.text[:200]}")
        jwt = t.json().get("jwt")
        if not jwt:
            raise RuntimeError("Turso: توکن در پاسخ نبود.")
    return name, f"libsql://{host}", jwt


async def create_server(name: str, school: str, owner_tid_: int, username: str, full_name: str):
    """سرورِ جدید می‌سازد: دیتابیس ← جدول‌ها ← مدیر مجموعه ← ثبت در رجیستری. شناسه‌ی سرور را برمی‌گرداند."""
    import database as db
    from config import ROLE_TOURNAMENT_MANAGER
    if int(owner_tid_) in _MEMBERS and _MEMBERS[int(owner_tid_)][0] != MAIN_ID and \
            _MEMBERS[int(owner_tid_)][1] == "member":
        raise RuntimeError("این فرد هم‌اکنون عضوِ یک سرورِ دیگر است (هر نفر فقط یک سرور).")
    sid = next_free_id()
    if sid is None:
        raise RuntimeError(f"ظرفیتِ سرورها پر است (حداکثر {MAX_SERVERS} سرور، با سرور اصلی).")

    db_name, url, token = await provision_db(sid, name)
    # ثبتِ اولیه (تا کلاینت ساخته شود) و ساختِ جدول‌ها داخلِ سرورِ جدید
    await _exec("INSERT INTO servers(id,name,school,status,owner_tid,db_name,db_url,db_token,features,invite_code,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, name.strip()[:60], school, ST_ACTIVE, int(owner_tid_), db_name, url, token, "{}",
                 secrets.token_urlsafe(9), _now(), _now()))
    _SERVERS[sid] = {"id": sid, "name": name.strip()[:60], "school": school, "status": ST_ACTIVE,
                     "owner_tid": int(owner_tid_), "db_name": db_name, "db_url": url, "db_token": token,
                     "features": {}, "invite_code": "", "suspend_reason": "", "suspend_until": "", "note": ""}
    try:
        turso_db.register_server_client(sid, url, token)
        with srvctx.use(sid):
            await db.init_db()
            await db.set_setting("server_name", name.strip()[:60])
            await db.set_setting("pishva_display_name", (full_name or "").strip()[:40] or "مدیر مجموعه")
            await db.create_admin(int(owner_tid_), username or "", full_name or "", ROLE_TOURNAMENT_MANAGER)
    except Exception:
        # برگشت: سرورِ نیمه‌کاره نماند
        _SERVERS.pop(sid, None)
        turso_db.drop_server_client(sid)
        await _exec("DELETE FROM servers WHERE id=?", (sid,))
        raise
    await set_member(int(owner_tid_), sid, "member")
    with _main():
        async with turso_db.connect(DB_PATH) as c:
            async with c.execute("SELECT invite_code FROM servers WHERE id=?", (sid,)) as cur:
                r = await cur.fetchone()
    _SERVERS[sid]["invite_code"] = r[0] if r else ""
    return sid


# ═════════════════════════ لینکِ دعوت (استخدامِ مدیر) ═════════════════════════
def sid_by_invite(code: str):
    for sid, s in _SERVERS.items():
        if s.get("invite_code") and s["invite_code"] == code and s.get("status") == ST_ACTIVE:
            return sid
    return None


async def regenerate_invite(sid: int):
    code = secrets.token_urlsafe(9)
    await save_server(sid, invite_code=code)
    return code


async def disable_invite(sid: int):
    await save_server(sid, invite_code="")


# ═════════════════════════ درخواستِ ثبت‌نامِ مدیر مجموعه ═════════════════════════
async def create_request(uid, username, full_name, school, need_date, phone, grade_field) -> int:
    cur = await _exec(
        "INSERT INTO server_requests(telegram_id,username,full_name,school,need_date,phone,grade_field,status,created_at)"
        " VALUES (?,?,?,?,?,?,?,'pending',?)",
        (int(uid), username, full_name, school, need_date, phone, grade_field, _now()))
    return cur.lastrowid


async def get_request(req_id: int):
    with _main():
        async with turso_db.connect(DB_PATH) as db:
            async with db.execute("SELECT * FROM server_requests WHERE id=?", (int(req_id),)) as cur:
                return await cur.fetchone()


async def pending_request_of(uid: int):
    with _main():
        async with turso_db.connect(DB_PATH) as db:
            async with db.execute("SELECT * FROM server_requests WHERE telegram_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
                                  (int(uid),)) as cur:
                return await cur.fetchone()


async def list_requests(status="pending", limit=20):
    with _main():
        async with turso_db.connect(DB_PATH) as db:
            async with db.execute("SELECT * FROM server_requests WHERE status=? ORDER BY id DESC LIMIT ?",
                                  (status, int(limit))) as cur:
                return await cur.fetchall()


async def decide_request(req_id: int, status: str, server_id: int = None):
    await _exec("UPDATE server_requests SET status=?, server_id=?, decided_at=? WHERE id=?",
                (status, server_id, _now(), int(req_id)))


# ═════════════════════════ قلاب‌های database.py ═════════════════════════
def on_admin_created(uid: int):
    """create_admin در یک سرورِ غیرِ اصلی صدا زده شد → عضویتِ آن فرد در رجیستری ثبت شود."""
    sid = srvctx.current()
    if sid == MAIN_ID:
        return
    import asyncio
    try:
        asyncio.get_running_loop().create_task(set_member(uid, sid, "member"))
    except RuntimeError:
        pass
    _MEMBERS[int(uid)] = (sid, "member")


def on_admin_removed(uid: int):
    """مدیرِ یک سرورِ غیرِ اصلی اخراج شد → دیگر عضوِ آن سرور نیست (به اصلی برمی‌گردد؛ ناشناس)."""
    sid = srvctx.current()
    if sid == MAIN_ID:
        return
    if is_owner(uid, sid):
        return  # مدیر مجموعه با اخراجِ عادی از رجیستری پاک نمی‌شود
    import asyncio
    _MEMBERS.pop(int(uid), None)
    try:
        asyncio.get_running_loop().create_task(remove_member(uid))
    except RuntimeError:
        pass


# ═════════════════════════ اجرا روی همه‌ی سرورها (برای جاب‌ها) ═════════════════════════
async def for_each_active(coro_fn, *args, **kw):
    """coro_fn را برای هر سرورِ فعال داخلِ زمینه‌ی همان سرور اجرا می‌کند؛ خطای یک سرور بقیه را متوقف نمی‌کند."""
    for sid in active_ids():
        try:
            with srvctx.use(sid):
                await coro_fn(*args, **kw)
        except Exception:
            logger.exception("job روی سرور %s ناموفق بود", sid)
