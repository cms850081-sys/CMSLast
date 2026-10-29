"""
sheets_sync.py — آینه‌ی کاملِ دیتابیس در گوگل‌شیت («مرکز دادهٔ آفلاین»)، یک‌طرفه: ربات ← شیت.

چطور کار می‌کند
──────────────
۱) ثبتِ تغییرات در سطحِ دیتابیس: روی هر جدول سه تریگر (درج/ویرایش/حذف) می‌نشیند که هر تغییر را در جدولِ صفِ
   `_sheet_outbox` ثبت می‌کند. چون از سطحِ دیتابیس می‌گیرد، هیچ داده‌ای جا نمی‌افتد (تلگرام، هاب، رهگشا، هرجا) و
   هیچ کدِ دیگری از پروژه تغییر نکرده است. جدول‌هایی که بعداً اضافه شوند هم خودکار پوشش داده می‌شوند.
۲) ربات هرگز منتظرِ گوگل نمی‌ماند: فقط در دیتابیس می‌نویسد؛ یک کارگرِ پس‌زمینه هر چند ثانیه صف را دسته‌ای به
   شیت می‌فرستد. اگر اینترنت/گوگل قطع باشد صف می‌ماند و بعداً خودکار می‌رود.
۳) ردیف‌ها ثابت‌اند: هر تغییر یک ردیفِ جدید می‌شود (نوع: SNAPSHOT / INSERT / UPDATE / DELETE) و ردیفِ قبلی دست نمی‌خورد.
۴) هر سرور شیتِ خودش را دارد. شیتِ سرور ۱ علاوه بر دیتای خودش، رجیستریِ سرورها (تنظیمات/وضعیت/قابلیت‌ها…) را هم دارد.
۵) همه‌چیز از تلگرام: /sheets (فقط مدیر ارشد) — کلیدِ سرویس‌اکانتِ گوگل و آی‌دیِ هر شیت را همان‌جا می‌فرستی.
   هیچ متغیری در Railway لازم نیست؛ همه‌چیز در دیتابیسِ ربات ذخیره می‌شود و از همان پنل قابلِ تغییر است.

امنیت: توکن/کلید/رمز هرگز وارد شیت نمی‌شود (ستون‌ها و ردیف‌های حساس حذف می‌شوند).
"""
import asyncio
import base64
import json
import logging
import re
import time
import zlib
from datetime import datetime

import httpx
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (ApplicationHandlerStop, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import servers
import srvctx
import turso_db
from config import PISHVA_ID

logger = logging.getLogger(__name__)

TEHRAN = pytz.timezone("Asia/Tehran")
BATCH = 400                      # حداکثر رویداد در هر ارسال برای هر سرور
TICK_SECONDS = 6
_SECRET_COL = re.compile(r"token|secret|password|passwd|private_key|api_key|apikey|auth_key|credential", re.I)
_SECRET_KEY_SQL = ("lower(NEW.\"key\") NOT LIKE '%token%' AND lower(NEW.\"key\") NOT LIKE '%secret%' "
                   "AND lower(NEW.\"key\") NOT LIKE '%password%' AND lower(NEW.\"key\") NOT LIKE '%api%key%' "
                   "AND lower(NEW.\"key\") NOT LIKE '%private%'")
_EXCLUDED_EXACT = {"push_config", "principal_push_subscriptions", "backups"}
_EXCLUDED_PREFIX = ("sheet_", "_sheet", "sqlite_", "_litestream", "_cf_")

# دسته‌بندی و عنوانِ فارسیِ تب‌ها: «دسته | عنوان»
CATS = {
    "servers": ("۰ تنظیمات سرورها", "سرورها"), "server_members": ("۰ تنظیمات سرورها", "اعضای سرورها"),
    "server_requests": ("۰ تنظیمات سرورها", "درخواست‌های سرور"),
    "players": ("۱ بازیکنان", "بازیکنان"), "classes": ("۱ بازیکنان", "کلاس‌ها"),
    "teams": ("۱ بازیکنان", "تیم‌ها"), "team_members": ("۱ بازیکنان", "اعضای تیم‌ها"),
    "tournaments": ("۲ مسابقات", "تورنمنت‌ها"), "matches": ("۲ مسابقات", "بازی‌ها و نتایج"),
    "team_matches": ("۲ مسابقات", "مسابقات تیمی"), "team_match_boards": ("۲ مسابقات", "میزهای مسابقه تیمی"),
    "match_scan_requests": ("۲ مسابقات", "ثبت با عکس"), "calendar_days": ("۲ مسابقات", "تقویم"),
    "admins": ("۳ مدیران", "مدیران"), "warnings_log": ("۳ مدیران", "اخطارها"),
    "kick_requests": ("۳ مدیران", "درخواست اخراج"), "access_requests": ("۳ مدیران", "درخواست دسترسی"),
    "tasks": ("۳ مدیران", "وظایف"), "action_logs": ("۳ مدیران", "لاگ اقدامات"),
    "messages": ("۴ پیام و اعلان", "پیام‌ها"), "announcements": ("۴ پیام و اعلان", "اطلاعیه‌ها"),
    "news": ("۴ پیام و اعلان", "اخبار"), "feedback": ("۴ پیام و اعلان", "بازخوردها"),
    "ai_chat_sessions": ("۵ رهگشا", "چت‌ها"), "ai_chat_messages": ("۵ رهگشا", "پیام‌های چت"),
    "ai_memory": ("۵ رهگشا", "حافظه"),
    "chess_requests": ("۶ شطرنج زنده", "درخواست‌ها"), "chess_games": ("۶ شطرنج زنده", "بازی‌ها"),
    "chess_chat": ("۶ شطرنج زنده", "چت بازی"),
    "principal_access_log": ("۷ مدیر مدرسه", "ورودها"), "principal_blocked_devices": ("۷ مدیر مدرسه", "دستگاه‌های مسدود"),
    "principal_notifications": ("۷ مدیر مدرسه", "اعلان‌ها"),
    "system_settings": ("۸ سیستم", "تنظیمات سیستم"), "blocked_users": ("۸ سیستم", "کاربران مسدود"),
    "stranger_log": ("۸ سیستم", "افراد ناشناس"),
}


# ───────────────────────── زیرساخت دیتابیس ─────────────────────────
def _raw():
    return turso_db._main_client()


async def _x(sql, args=None):
    """اجرای SQL فیزیکی روی دیتابیسِ اصلی (بدون بازنویسیِ چندسرور)."""
    with srvctx.use(srvctx.MAIN_ID):
        return await _raw().execute(sql, args)


async def _pipe(stmts):
    with srvctx.use(srvctx.MAIN_ID):
        return await _raw().execute_pipeline(stmts)


async def ensure_tables():
    await _pipe([
        ("CREATE TABLE IF NOT EXISTS sheet_cfg (k TEXT PRIMARY KEY, v TEXT)", None),
        ("CREATE TABLE IF NOT EXISTS sheet_links (sid INTEGER PRIMARY KEY, sheet_id TEXT, active INTEGER DEFAULT 1, "
         "linked_at TEXT, last_sync TEXT, last_error TEXT DEFAULT '', sent_total INTEGER DEFAULT 0)", None),
        ("CREATE TABLE IF NOT EXISTS sheet_tabs (sid INTEGER, tbl TEXT, cols TEXT, gid INTEGER, PRIMARY KEY(sid, tbl))", None),
        ("CREATE TABLE IF NOT EXISTS _sheet_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, sid INTEGER, tbl TEXT, "
         "op TEXT, ts TEXT, row_json TEXT, sent INTEGER DEFAULT 0)", None),
        ("CREATE INDEX IF NOT EXISTS _sheet_outbox_pending ON _sheet_outbox(sid, sent, id)", None),
        ("CREATE TABLE IF NOT EXISTS _sheet_trig (tbl TEXT PRIMARY KEY, sig TEXT)", None),
    ])


async def _cfg_get(k, default=None):
    r = await _x("SELECT v FROM sheet_cfg WHERE k=?", [k])
    return r.rows[0][0] if r.rows else default


async def _cfg_set(k, v):
    await _x("INSERT INTO sheet_cfg(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", [k, v])


def _physical_to_logical(name: str):
    m = re.match(r"^s(\d+)_(.+)$", name)
    return (int(m.group(1)), m.group(2)) if m else (srvctx.MAIN_ID, name)


def _is_excluded(logical: str) -> bool:
    l = logical.lower()
    return l in _EXCLUDED_EXACT or l.startswith(_EXCLUDED_PREFIX)


async def _physical_tables():
    r = await _x("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    return [row[0] for row in r.rows]


async def _tables_of(sid: int):
    out = []
    for p in await _physical_tables():
        s, logical = _physical_to_logical(p)
        if s == int(sid) and not _is_excluded(logical):
            out.append((p, logical))
    return out


async def _columns(physical: str):
    r = await _x(f'PRAGMA table_info("{physical}")')
    return [row[1] for row in r.rows]       # (cid, name, type, ...)


def _json_expr(cols, prefix: str):
    """عبارتِ json_object برای ستون‌ها (ستون‌های حساس حذف؛ BLOB → متن)؛ برای >۵۰ ستون چندتکه و json_patch."""
    cols = [c for c in cols if not _SECRET_COL.search(c)]
    parts = []
    for i in range(0, len(cols), 50):
        args = ", ".join(
            f"'{c}', CASE WHEN typeof({prefix}\"{c}\")='blob' THEN '[blob]' ELSE {prefix}\"{c}\" END"
            for c in cols[i:i + 50])
        parts.append(f"json_object({args})")
    if not parts:
        return "json_object()"
    expr = parts[0]
    for p in parts[1:]:
        expr = f"json_patch({expr}, {p})"
    return expr


def _when(logical: str) -> str:
    return f"WHEN {_SECRET_KEY_SQL}" if logical == "system_settings" else ""


def _trigger_sql(physical: str, logical: str, sid: int, cols):
    safe = re.sub(r"\W", "_", physical)
    new_j, old_j = _json_expr(cols, "NEW."), _json_expr(cols, "OLD.")
    when_new = _when(logical)
    when_old = _when(logical).replace("NEW.", "OLD.")
    ins = (f"INSERT INTO _sheet_outbox(sid,tbl,op,ts,row_json) VALUES({sid},'{logical}','%s',datetime('now'),%s);")
    return [
        f'DROP TRIGGER IF EXISTS _shtr_{safe}_i', f'DROP TRIGGER IF EXISTS _shtr_{safe}_u', f'DROP TRIGGER IF EXISTS _shtr_{safe}_d',
        f'CREATE TRIGGER _shtr_{safe}_i AFTER INSERT ON "{physical}" {when_new} BEGIN {ins % ("INSERT", new_j)} END',
        f'CREATE TRIGGER _shtr_{safe}_u AFTER UPDATE ON "{physical}" {when_new} BEGIN {ins % ("UPDATE", new_j)} END',
        f'CREATE TRIGGER _shtr_{safe}_d AFTER DELETE ON "{physical}" {when_old} BEGIN {ins % ("DELETE", old_j)} END',
    ]


async def ensure_triggers(sids):
    """تریگرِ همه‌ی جدول‌های سرورهای داده‌شده را نصب/به‌روز می‌کند (فقط اگر ستون‌ها عوض شده باشند)."""
    done = 0
    sigs = {r[0]: r[1] for r in (await _x("SELECT tbl, sig FROM _sheet_trig")).rows}
    for sid in sids:
        for physical, logical in await _tables_of(sid):
            try:
                cols = await _columns(physical)
                sig = f"{sid}|{','.join(cols)}"
                if sigs.get(physical) == sig:
                    continue
                await _pipe([(q, None) for q in _trigger_sql(physical, logical, sid, cols)] + [
                    ("INSERT INTO _sheet_trig(tbl,sig) VALUES(?,?) ON CONFLICT(tbl) DO UPDATE SET sig=excluded.sig", [physical, sig])])
                done += 1
            except Exception:
                logger.exception("sheets: trigger install failed for %s", physical)
    return done


async def drop_triggers(sid: int):
    for physical, _l in await _tables_of(sid):
        safe = re.sub(r"\W", "_", physical)
        try:
            await _pipe([(f'DROP TRIGGER IF EXISTS _shtr_{safe}_{k}', None) for k in "iud"]
                        + [("DELETE FROM _sheet_trig WHERE tbl=?", [physical])])
        except Exception:
            logger.exception("sheets: drop trigger failed for %s", physical)


async def queue_snapshot(sid: int) -> int:
    """همه‌ی دیتای فعلیِ سرور را (سمتِ دیتابیس) به صف اضافه می‌کند؛ کارگر کم‌کم می‌فرستد."""
    n = 0
    for physical, logical in await _tables_of(sid):
        try:
            cols = await _columns(physical)
            where = f"WHERE {_SECRET_KEY_SQL.replace('NEW.', '')}" if logical == "system_settings" else ""
            r = await _x(f'INSERT INTO _sheet_outbox(sid,tbl,op,ts,row_json) SELECT {sid},\'{logical}\',\'SNAPSHOT\','
                         f'datetime(\'now\'),{_json_expr(cols, "")} FROM "{physical}" {where}')
            n += 1
        except Exception:
            logger.exception("sheets: snapshot failed for %s", physical)
    return n


# ───────────────────────── Google API ─────────────────────────
_tok = {"v": None, "exp": 0.0}
_http = None


def _client():
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=60)
    return _http


def _b64(b: bytes) -> bytes:
    return base64.urlsafe_b64encode(b).rstrip(b"=")


async def _sa():
    raw = await _cfg_get("sa_json")
    return json.loads(raw) if raw else None


async def _access_token(force=False):
    if not force and _tok["v"] and time.time() < _tok["exp"] - 120:
        return _tok["v"]
    sa = await _sa()
    if not sa:
        raise RuntimeError("کلید گوگل ثبت نشده")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    now = int(time.time())
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claim = _b64(json.dumps({"iss": sa["client_email"], "scope": "https://www.googleapis.com/auth/spreadsheets",
                             "aud": "https://oauth2.googleapis.com/token", "iat": now, "exp": now + 3600}).encode())
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = key.sign(head + b"." + claim, padding.PKCS1v15(), hashes.SHA256())
    jwt = (head + b"." + claim + b"." + _b64(sig)).decode()
    r = await _client().post("https://oauth2.googleapis.com/token",
                             data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt})
    if r.status_code != 200:
        raise RuntimeError(f"گوگل توکن نداد ({r.status_code}): {r.text[:200]}")
    d = r.json()
    _tok["v"], _tok["exp"] = d["access_token"], time.time() + int(d.get("expires_in", 3600))
    return _tok["v"]


class GoogleError(Exception):
    def __init__(self, status, msg):
        super().__init__(f"{status}: {msg}")
        self.status, self.msg = status, msg


async def _g(method, url, **kw):
    for attempt in (0, 1):
        tok = await _access_token(force=attempt == 1)
        r = await _client().request(method, url, headers={"Authorization": f"Bearer {tok}"}, **kw)
        if r.status_code == 401 and attempt == 0:
            continue
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text[:200])
            except Exception:
                msg = r.text[:200]
            raise GoogleError(r.status_code, msg)
        return r.json()


API = "https://sheets.googleapis.com/v4/spreadsheets"


async def _meta(sheet_id):
    d = await _g("GET", f"{API}/{sheet_id}", params={"fields": "properties.title,sheets.properties(sheetId,title)"})
    return d


def _tab_title(logical: str) -> str:
    cat, fa = CATS.get(logical, ("۹ سایر", logical))
    t = f"{cat} | {fa}"
    return re.sub(r"[\[\]\*\?/\\:]", "-", t)[:95]


def _cell(v):
    if v is None:
        return {}
    if isinstance(v, bool):
        return {"userEnteredValue": {"boolValue": v}}
    if isinstance(v, (int, float)):
        return {"userEnteredValue": {"numberValue": v}}
    s = str(v)
    return {"userEnteredValue": {"stringValue": s[:45000]}}


def _tehran(ts: str) -> str:
    try:
        dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=pytz.utc)
        return dt.astimezone(TEHRAN).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts


FIXED = ["شناسه رویداد", "زمان (تهران)", "نوع"]


async def _tabs_state(sid):
    r = await _x("SELECT tbl, cols, gid FROM sheet_tabs WHERE sid=?", [sid])
    return {row[0]: {"cols": json.loads(row[1] or "[]"), "gid": row[2]} for row in r.rows}


async def _ensure_tabs(sid, sheet_id, needs: dict):
    """needs: {logical: [cols...]} — تب‌های جاافتاده را (به ترتیبِ دسته‌بندی) می‌سازد و ستون‌های جدید را به هدر اضافه می‌کند."""
    state = await _tabs_state(sid)
    meta = await _meta(sheet_id)
    by_title = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
    reqs, new_state = [], {}
    for logical in sorted(needs, key=lambda t: _tab_title(t)):
        want_cols = needs[logical]
        title = _tab_title(logical)
        st = state.get(logical)
        if st and st["gid"] in by_title.values():
            cur = st["cols"]
            extra = [c for c in want_cols if c not in cur]
            if extra:
                allc = cur + extra
                reqs.append({"updateCells": {"start": {"sheetId": st["gid"], "rowIndex": 0, "columnIndex": 0},
                                             "rows": [{"values": [_cell(h) for h in FIXED + allc]}],
                                             "fields": "userEnteredValue"}})
                new_state[logical] = (allc, st["gid"])
            continue
        gid = by_title.get(title)
        if gid is None:
            gid = zlib.crc32(f"{sid}:{logical}".encode()) % 2_000_000_000
            reqs.append({"addSheet": {"properties": {"sheetId": gid, "title": title, "rightToLeft": True,
                                                     "gridProperties": {"frozenRowCount": 1}}}})
        cols = list(dict.fromkeys(want_cols))
        reqs.append({"updateCells": {"start": {"sheetId": gid, "rowIndex": 0, "columnIndex": 0},
                                     "rows": [{"values": [_cell(h) for h in FIXED + cols]}], "fields": "userEnteredValue"}})
        new_state[logical] = (cols, gid)
    if reqs:
        await _g("POST", f"{API}/{sheet_id}:batchUpdate", json={"requests": reqs})
        for logical, (cols, gid) in new_state.items():
            await _x("INSERT INTO sheet_tabs(sid,tbl,cols,gid) VALUES(?,?,?,?) ON CONFLICT(sid,tbl) DO UPDATE SET cols=excluded.cols, gid=excluded.gid",
                     [sid, logical, json.dumps(cols, ensure_ascii=False), gid])


async def create_all_tabs(sid, sheet_id):
    needs = {}
    for physical, logical in await _tables_of(sid):
        needs[logical] = [c for c in await _columns(physical) if not _SECRET_COL.search(c)]
    await _ensure_tabs(sid, sheet_id, needs)


async def _send_batch(sid, sheet_id, rows):
    """rows: [(id, tbl, op, ts, row_json)] → یک batchUpdate با appendCells برای همه‌ی تب‌ها."""
    events = []
    needs = {}
    for (eid, tbl, op, ts, rj) in rows:
        try:
            data = json.loads(rj) if rj else {}
        except Exception:
            data = {"raw": str(rj)[:1000]}
        events.append((eid, tbl, op, ts, data))
        needs.setdefault(tbl, [])
        for k in data:
            if k not in needs[tbl]:
                needs[tbl].append(k)
    await _ensure_tabs(sid, sheet_id, needs)
    state = await _tabs_state(sid)
    per_tab = {}
    for eid, tbl, op, ts, data in events:
        st = state.get(tbl)
        if not st:
            continue
        vals = [eid, _tehran(ts), op] + [data.get(c) for c in st["cols"]]
        per_tab.setdefault(st["gid"], []).append({"values": [_cell(v) for v in vals]})
    reqs = [{"appendCells": {"sheetId": gid, "rows": rws, "fields": "userEnteredValue"}} for gid, rws in per_tab.items()]
    if reqs:
        await _g("POST", f"{API}/{sheet_id}:batchUpdate", json={"requests": reqs})


_busy = asyncio.Lock()
_backoff_until = 0.0


async def tick(context=None):
    """هر چند ثانیه: صفِ هر سرور را به شیتش می‌فرستد."""
    global _backoff_until
    if _busy.locked() or time.time() < _backoff_until:
        return
    async with _busy:
        try:
            links = (await _x("SELECT sid, sheet_id FROM sheet_links WHERE active=1")).rows
            if not links or not await _cfg_get("sa_json"):
                return
            for sid, sheet_id in links:
                r = await _x("SELECT id,tbl,op,ts,row_json FROM _sheet_outbox WHERE sid=? AND sent=0 ORDER BY id LIMIT ?", [sid, BATCH])
                rows = [tuple(x) for x in r.rows]
                if not rows:
                    continue
                try:
                    await _send_batch(sid, sheet_id, rows)
                    lo, hi = rows[0][0], rows[-1][0]
                    await _x("UPDATE _sheet_outbox SET sent=1 WHERE sid=? AND sent=0 AND id>=? AND id<=?", [sid, lo, hi])
                    await _x("UPDATE sheet_links SET last_sync=?, last_error='', sent_total=sent_total+? WHERE sid=?",
                             [datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M:%S"), len(rows), sid])
                except GoogleError as e:
                    await _x("UPDATE sheet_links SET last_error=? WHERE sid=?", [str(e)[:300], sid])
                    logger.warning("sheets: sid %s send failed: %s", sid, e)
                    if e.status == 429:
                        _backoff_until = time.time() + 65
                        return
                except Exception as e:
                    await _x("UPDATE sheet_links SET last_error=? WHERE sid=?", [str(e)[:300], sid])
                    logger.warning("sheets: sid %s send failed: %s", sid, e)
        except Exception:
            logger.warning("sheets: tick failed", exc_info=True)


async def maintenance(context=None):
    """ساعتی: تریگر جدول‌های جدید/ستون‌های جدید + پاک‌کردنِ رویدادهای ارسال‌شده‌ی قدیمی."""
    try:
        links = [r[0] for r in (await _x("SELECT sid FROM sheet_links WHERE active=1")).rows]
        if links:
            await ensure_triggers(links)
        await _x("DELETE FROM _sheet_outbox WHERE sent=1 AND ts < datetime('now','-2 days')")
    except Exception:
        logger.warning("sheets: maintenance failed", exc_info=True)


async def start(application):
    """از post_init صدا زده می‌شود."""
    await ensure_tables()
    jq = application.job_queue
    if jq:
        jq.run_repeating(tick, interval=TICK_SECONDS, first=25, name="sheets_tick")
        jq.run_repeating(maintenance, interval=3600, first=90, name="sheets_maint")
    logger.info("sheets_sync: ready")


# ───────────────────────── رابطِ تلگرام: /sheets ─────────────────────────
def _is_boss(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id == PISHVA_ID and update.effective_chat and update.effective_chat.type == "private")


def _sheet_id_from(text: str):
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", text)
    if m:
        return m.group(1)
    m = re.fullmatch(r"\s*([A-Za-z0-9_-]{25,})\s*", text)
    return m.group(1) if m else None


def _srv_name(sid):
    s = servers.get(sid) or {}
    return s.get("name") or f"سرور {sid}"


async def _menu_text_kb():
    sa = await _sa()
    links = {r[0]: r for r in (await _x("SELECT sid, sheet_id, active, last_sync, last_error, sent_total FROM sheet_links")).rows}
    pend = {r[0]: r[1] for r in (await _x("SELECT sid, COUNT(*) FROM _sheet_outbox WHERE sent=0 GROUP BY sid")).rows}
    lines = ["📊 <b>شیت‌ها — مرکز داده‌ی آفلاین</b>", ""]
    lines.append(f"🔑 کلید گوگل: {'✅ ' + sa['client_email'] if sa else '❌ ثبت نشده'}")
    lines.append("")
    kb = [[InlineKeyboardButton("🔑 ارسال/تغییر کلید گوگل", callback_data="sh:key")]]
    for s in servers.all_servers(include_deleted=False):
        sid = int(s["id"])
        ln = links.get(sid)
        if ln:
            st = ("⏸ متوقف" if not ln[2] else "🟢 فعال") + (f" · صف: {pend.get(sid, 0)}" if pend.get(sid) else "")
        else:
            st = "⚪ وصل نیست"
        lines.append(f"• {_srv_name(sid)}: {st}")
        kb.append([InlineKeyboardButton(f"{'📎' if ln else '➕'} {_srv_name(sid)}", callback_data=f"sh:srv:{sid}")])
    lines.append("")
    lines.append("ℹ️ ایمیلِ بالا را در هر شیت Share کن (Editor)، بعد آی‌دی یا لینکِ شیت را برای همان سرور بفرست.")
    return "\n".join(lines), InlineKeyboardMarkup(kb)


async def _srv_text_kb(sid):
    r = (await _x("SELECT sheet_id, active, linked_at, last_sync, last_error, sent_total FROM sheet_links WHERE sid=?", [sid])).rows
    pend = (await _x("SELECT COUNT(*) FROM _sheet_outbox WHERE sid=? AND sent=0", [sid])).rows[0][0]
    lines = [f"📊 <b>{_srv_name(sid)}</b>", ""]
    kb = []
    if r:
        sheet_id, active, linked, last, err, total = r[0]
        lines += [f"شیت: <code>{sheet_id}</code>", f"وضعیت: {'🟢 فعال' if active else '⏸ متوقف'}",
                  f"وصل‌شده: {linked or '—'}", f"آخرین ارسال: {last or '—'}", f"ردیف‌های ارسال‌شده: {total}",
                  f"در صف: {pend}"]
        if err:
            lines.append(f"⚠️ آخرین خطا: {err[:200]}")
        kb += [[InlineKeyboardButton("🧪 تست اتصال", callback_data=f"sh:test:{sid}"),
                InlineKeyboardButton("🔄 همگام‌سازی الان", callback_data=f"sh:sync:{sid}")],
               [InlineKeyboardButton("▶️ ادامه" if not active else "⏸ توقف", callback_data=f"sh:tog:{sid}"),
                InlineKeyboardButton("✏️ تغییر شیت", callback_data=f"sh:set:{sid}")],
               [InlineKeyboardButton("♻️ ریختن دوباره‌ی همه‌ی داده", callback_data=f"sh:resnap:{sid}")],
               [InlineKeyboardButton("🗑 قطع اتصال", callback_data=f"sh:del:{sid}")]]
    else:
        lines.append("هنوز شیتی وصل نشده.")
        kb.append([InlineKeyboardButton("➕ وصل کردن شیت", callback_data=f"sh:set:{sid}")])
    kb.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="sh:menu")])
    return "\n".join(lines), InlineKeyboardMarkup(kb)


async def cmd_sheets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_boss(update):
        return
    ctx.user_data.pop("sheet_wait", None)
    await ensure_tables()
    text, kb = await _menu_text_kb()
    await update.effective_message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def _show(q, text, kb):
    try:
        await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await q.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _is_boss(update):
        await q.answer("فقط مدیر ارشد", show_alert=True)
        return
    parts = q.data.split(":")
    act = parts[1]
    sid = int(parts[2]) if len(parts) > 2 else None
    await ensure_tables()
    try:
        if act == "menu":
            ctx.user_data.pop("sheet_wait", None)
            await q.answer()
            await _show(q, *(await _menu_text_kb()))
        elif act == "key":
            ctx.user_data["sheet_wait"] = {"kind": "key"}
            await q.answer()
            await q.message.reply_text(
                "🔑 فایل JSON کلیدِ سرویس‌اکانتِ گوگل را همین‌جا بفرست (به‌صورت فایل، یا متنِ کاملش را Paste کن).\n"
                "پیامت بعد از ذخیره از چت پاک می‌شود. برای انصراف /sheets بزن.")
        elif act == "srv":
            await q.answer()
            await _show(q, *(await _srv_text_kb(sid)))
        elif act == "set":
            if not await _cfg_get("sa_json"):
                await q.answer("اول کلید گوگل را بفرست", show_alert=True)
                return
            ctx.user_data["sheet_wait"] = {"kind": "sheet", "sid": sid}
            await q.answer()
            await q.message.reply_text(f"📎 لینک یا آی‌دیِ گوگل‌شیتِ «{_srv_name(sid)}» را بفرست.\n(شیت را قبلاً با ایمیل سرویس‌اکانت Share کرده باش، دسترسی Editor.) انصراف: /sheets")
        elif act == "test":
            r = (await _x("SELECT sheet_id FROM sheet_links WHERE sid=?", [sid])).rows
            try:
                m = await _meta(r[0][0])
                await q.answer(f"✅ وصل است: {m.get('properties', {}).get('title', '')}"[:190], show_alert=True)
            except Exception as e:
                await q.answer(f"❌ {e}"[:190], show_alert=True)
        elif act == "sync":
            await q.answer("در حال ارسال…")
            await tick()
            await _show(q, *(await _srv_text_kb(sid)))
        elif act == "tog":
            await _x("UPDATE sheet_links SET active=1-active WHERE sid=?", [sid])
            await q.answer("انجام شد")
            await _show(q, *(await _srv_text_kb(sid)))
        elif act == "resnap":
            await queue_snapshot(sid)
            await q.answer("همه‌ی داده دوباره در صف قرار گرفت", show_alert=True)
            await _show(q, *(await _srv_text_kb(sid)))
        elif act == "del":
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ بله، قطع کن", callback_data=f"sh:delok:{sid}"),
                                        InlineKeyboardButton("انصراف", callback_data=f"sh:srv:{sid}")]])
            await q.answer()
            await _show(q, f"اتصالِ «{_srv_name(sid)}» قطع شود؟\n(شیت و داده‌هایش دست‌نخورده می‌ماند؛ فقط ربات دیگر چیزی نمی‌فرستد.)", kb)
        elif act == "delok":
            await _x("DELETE FROM sheet_links WHERE sid=?", [sid])
            await _x("DELETE FROM sheet_tabs WHERE sid=?", [sid])
            await _x("DELETE FROM _sheet_outbox WHERE sid=?", [sid])
            await drop_triggers(sid)
            await q.answer("قطع شد")
            await _show(q, *(await _menu_text_kb()))
        else:
            await q.answer()
    except Exception as e:
        logger.exception("sheets: callback failed")
        try:
            await q.answer(f"خطا: {e}"[:190], show_alert=True)
        except Exception:
            pass


async def _try_delete(msg):
    try:
        await msg.delete()
    except Exception:
        pass


async def on_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ورودیِ بعدی بعد از زدنِ «ارسال کلید» یا «وصل کردن شیت»؛ اگر منتظرِ چیزی نیستیم هیچ‌کاری نمی‌کند."""
    wait = ctx.user_data.get("sheet_wait")
    if not wait or not _is_boss(update):
        return
    msg = update.effective_message
    await ensure_tables()
    if wait["kind"] == "key":
        raw = None
        try:
            if msg.document:
                if (msg.document.file_size or 0) > 30000:
                    await msg.reply_text("❌ فایل بزرگ است؛ باید کلیدِ JSON باشد.")
                    raise ApplicationHandlerStop
                f = await msg.document.get_file()
                raw = bytes(await f.download_as_bytearray()).decode("utf-8", "ignore")
            elif msg.text:
                raw = msg.text
        except ApplicationHandlerStop:
            raise
        except Exception:
            raw = None
        try:
            sa = json.loads(raw or "")
            assert sa.get("type") == "service_account" and sa.get("client_email") and sa.get("private_key")
        except Exception:
            await msg.reply_text("❌ این کلیدِ سرویس‌اکانتِ گوگل نیست. فایل JSON کامل را بفرست (یا /sheets برای انصراف).")
            raise ApplicationHandlerStop
        await _cfg_set("sa_json", json.dumps(sa))
        _tok["v"] = None
        await _try_delete(msg)
        try:
            await _access_token(force=True)
            note = "✅ کلید ذخیره و اعتبارسنجی شد."
        except Exception as e:
            note = f"⚠️ کلید ذخیره شد ولی گوگل نپذیرفت: {e}"
        ctx.user_data.pop("sheet_wait", None)
        await ctx.bot.send_message(update.effective_chat.id,
                                   f"{note}\n\nاین ایمیل را در هر شیت Share کن (Editor):\n<code>{sa['client_email']}</code>",
                                   parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ منوی شیت‌ها", callback_data="sh:menu")]]))
        raise ApplicationHandlerStop

    if wait["kind"] == "sheet":
        sid = int(wait["sid"])
        sheet_id = _sheet_id_from(msg.text or "")
        if not sheet_id:
            await msg.reply_text("❌ لینک یا آی‌دیِ شیت را درست بفرست (یا /sheets برای انصراف).")
            raise ApplicationHandlerStop
        await _try_delete(msg)
        status = await ctx.bot.send_message(update.effective_chat.id, "⏳ در حال اتصال…")
        try:
            m = await _meta(sheet_id)
        except GoogleError as e:
            sa = await _sa()
            hint = (f"\nشیت را با <code>{sa['client_email']}</code> (Editor) Share کن." if e.status in (403, 404) and sa else "")
            await status.edit_text(f"❌ وصل نشد: {e}{hint}", parse_mode="HTML")
            raise ApplicationHandlerStop
        except Exception as e:
            await status.edit_text(f"❌ وصل نشد: {e}")
            raise ApplicationHandlerStop
        old = (await _x("SELECT sheet_id FROM sheet_links WHERE sid=?", [sid])).rows
        await _x("INSERT INTO sheet_links(sid,sheet_id,active,linked_at) VALUES(?,?,1,?) "
                 "ON CONFLICT(sid) DO UPDATE SET sheet_id=excluded.sheet_id, active=1, last_error=''",
                 [sid, sheet_id, datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M:%S")])
        changed = not old or old[0][0] != sheet_id
        try:
            await ensure_triggers([sid])
            if changed:
                await _x("DELETE FROM sheet_tabs WHERE sid=?", [sid])
                await _x("DELETE FROM _sheet_outbox WHERE sid=? AND sent=0", [sid])
                await create_all_tabs(sid, sheet_id)
                await queue_snapshot(sid)
        except Exception as e:
            logger.exception("sheets: link setup failed")
            await status.edit_text(f"⚠️ وصل شد ولی راه‌اندازی کامل نشد: {e}")
            ctx.user_data.pop("sheet_wait", None)
            raise ApplicationHandlerStop
        pend = (await _x("SELECT COUNT(*) FROM _sheet_outbox WHERE sid=? AND sent=0", [sid])).rows[0][0]
        ctx.user_data.pop("sheet_wait", None)
        await status.edit_text(
            f"✅ «{_srv_name(sid)}» به شیت «{m.get('properties', {}).get('title', '')}» وصل شد.\n"
            f"{pend} ردیفِ موجود در صفِ ارسال است و کم‌کم در تب‌های دسته‌بندی‌شده می‌نشیند؛ از این به بعد هر تغییر همان لحظه ثبت می‌شود.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ منوی شیت‌ها", callback_data="sh:menu")]]))
        raise ApplicationHandlerStop


def register(app):
    """در bot.py صدا زده می‌شود. گروهِ -50: بعد از روتر (-100) و قبل از بقیه."""
    app.add_handler(CommandHandler(["sheets", "sheet"], cmd_sheets), group=-50)
    app.add_handler(CallbackQueryHandler(on_cb, pattern=r"^sh:"), group=-50)
    app.add_handler(MessageHandler((filters.TEXT | filters.Document.ALL) & ~filters.COMMAND, on_input), group=-50)
