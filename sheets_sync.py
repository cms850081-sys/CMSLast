"""
sheets_sync.py — شیتِ متصل = «نمای زنده و خوانا» از مسابقات (یک‌طرفه: ربات ← گوگل‌شیت).

چطور کار می‌کند
──────────────
۱) تشخیصِ تغییر در سطحِ دیتابیس: روی جدول‌های مربوط به مسابقات (بازیکن، بازی، تیم، ایلو، مدیر، رهگشا …) تریگرِ سبک می‌نشیند
   که فقط «این جدول تغییر کرد» را در صفِ `_sheet_outbox` علامت می‌زند (هیچ داده‌ای در صف نمی‌ماند). چون از سطحِ دیتابیس است،
   تغییر از هرجا بیاید (تلگرام، هاب، رهگشا، …) دیده می‌شود و هیچ کدِ دیگری از پروژه تغییر نکرده است.
۲) ربات هرگز منتظرِ گوگل نمی‌ماند: کارگرِ پس‌زمینه هر چند ثانیه، تب‌هایی که به جدول‌های تغییرکرده وابسته‌اند را از رویِ
   «وضعیتِ فعلیِ دیتابیس» از نو می‌سازد (sheet_views.py) و در شیت می‌نویسد. اگر محتوای یک تب عوض نشده باشد (hash)، چیزی
   در گوگل نوشته نمی‌شود. اگر اینترنت/گوگل قطع باشد، تغییرها در صف می‌مانند و بعداً خودکار اعمال می‌شوند.
۳) شیت یک گزارشِ زیبا و قابل‌خواندن برای انسان است: رده‌بندی، بازیکنان، بازی‌ها، تیم‌ها، روند ایلو، مدیران، رهگشا … با
   نام‌ها (نه شناسه‌ها)، تاریخ شمسی، رنگِ وضعیت‌ها و فونت/اندازه‌ی مخصوصِ عنوان/هدر/بدنه. «عکسِ کاملِ خام» در بکاپ است.
۴) هر سرور شیتِ خودش را دارد. شیتِ سرور ۱ علاوه بر دیتای خودش، تبِ «سرورها» را هم دارد.
۵) همه‌چیز از تلگرام: /sheets (فقط مدیر ارشد) — کلیدِ سرویس‌اکانتِ گوگل و آی‌دیِ هر شیت را همان‌جا می‌فرستی.
   هیچ متغیری در Railway لازم نیست؛ همه‌چیز در دیتابیسِ ربات ذخیره می‌شود و از همان پنل قابلِ تغییر است.

امنیت: توکن/کلید/رمز هرگز وارد شیت نمی‌شود.
"""
import asyncio
import base64
import json
import logging
import math
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
import sheet_views as views
import srvctx
import turso_db
from config import PISHVA_ID

logger = logging.getLogger(__name__)

TEHRAN = pytz.timezone("Asia/Tehran")
TICK_SECONDS = 3                 # کارگر هر ۳ ثانیه بیدار می‌شود؛ فاصله‌ی واقعیِ به‌روزرسانی از تنظیماتِ «سرعت» می‌آید
_SECRET_COL = re.compile(r"token|secret|password|passwd|private_key|api_key|apikey|auth_key|credential", re.I)
_SECRET_KEY_SQL = ("lower(NEW.\"key\") NOT LIKE '%token%' AND lower(NEW.\"key\") NOT LIKE '%secret%' "
                   "AND lower(NEW.\"key\") NOT LIKE '%password%' AND lower(NEW.\"key\") NOT LIKE '%api%key%' "
                   "AND lower(NEW.\"key\") NOT LIKE '%private%'")
_EXCLUDED_EXACT = {"push_config", "principal_push_subscriptions"}
_SKIP_COLS = {"backups": {"file_data"}}   # سابقه‌ی بکاپ‌ها وارد شیت می‌شود، ولی نه خودِ فایل
_EXCLUDED_PREFIX = ("sheet_", "_sheet", "sqlite_", "_litestream", "_cf_")

# دسته‌بندی و عنوانِ فارسیِ «جدول‌های خام» (برای تب‌های عکسِ کاملِ دیتابیس در بکاپ): «دسته | عنوان»
CATS = {
    "player_elo": ("۲ مسابقات", "امتیاز Elo بازیکنان"), "elo_history": ("۲ مسابقات", "تاریخچه Elo"),
    "chess_elo": ("۶ شطرنج زنده", "Elo شطرنج زنده"),
    "rahgosha_user_prefs": ("۵ رهگشا", "تنظیمات کاربران"), "rahgosha_user_memory": ("۵ رهگشا", "حافظه‌ی کاربران"),
    "rahgosha_held": ("۵ رهگشا", "نگه‌داشته‌ها"), "rahgosha_player_style": ("۵ رهگشا", "سبک بازیکنان"),
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
    "backups": ("۸ سیستم", "سابقه بکاپ‌ها"), "system_settings": ("۸ سیستم", "تنظیمات سیستم"), "blocked_users": ("۸ سیستم", "کاربران مسدود"),
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


def _vis(cols, logical=""):
    skip = _SKIP_COLS.get(logical, set())
    return [c for c in cols if not _SECRET_COL.search(c) and c not in skip]


def _trigger_sql(physical: str, logical: str, sid: int, cols):
    """تریگرِ سبک: فقط «جدولِ X تغییر کرد» را علامت می‌زند (هیچ ستونی کپی نمی‌شود)."""
    safe = re.sub(r"\W", "_", physical)
    ins = f"INSERT INTO _sheet_outbox(sid,tbl,op,ts) VALUES({sid},'{logical}','%s',datetime('now'));"
    ign = views.IGNORE_UPDATE_COLS.get(logical, set())
    cmp_cols = [c for c in cols if c not in ign]
    when_u = ("WHEN " + " OR ".join(f'OLD."{c}" IS NOT NEW."{c}"' for c in cmp_cols)) if (ign and cmp_cols) else ""
    return [
        f'DROP TRIGGER IF EXISTS _shtr_{safe}_i', f'DROP TRIGGER IF EXISTS _shtr_{safe}_u', f'DROP TRIGGER IF EXISTS _shtr_{safe}_d',
        f'CREATE TRIGGER _shtr_{safe}_i AFTER INSERT ON "{physical}" BEGIN {ins % "INSERT"} END',
        f'CREATE TRIGGER _shtr_{safe}_u AFTER UPDATE ON "{physical}" {when_u} BEGIN {ins % "UPDATE"} END',
        f'CREATE TRIGGER _shtr_{safe}_d AFTER DELETE ON "{physical}" BEGIN {ins % "DELETE"} END',
    ]


async def ensure_triggers(sids):
    """تریگرِ جدول‌های «مربوط به مسابقات» (views.WATCHED) را نصب/به‌روز می‌کند؛ تریگرهای قدیمیِ بقیه‌ی جدول‌ها را پاک می‌کند."""
    done = 0
    sigs = {r[0]: r[1] for r in (await _x("SELECT tbl, sig FROM _sheet_trig")).rows}
    for sid in sids:
        for physical, logical in await _tables_of(sid):
            try:
                safe = re.sub(r"\W", "_", physical)
                if logical not in views.WATCHED:
                    if physical in sigs:          # تریگرِ نسخه‌ی قدیمیِ لاگ → دیگر لازم نیست
                        await _pipe([(f'DROP TRIGGER IF EXISTS _shtr_{safe}_{k}', None) for k in "iud"]
                                    + [("DELETE FROM _sheet_trig WHERE tbl=?", [physical])])
                        done += 1
                    continue
                cols = await _columns(physical)
                sig = f"v3|{sid}|{','.join(cols)}"
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
    """بازسازیِ کاملِ شیتِ سرور: یک علامتِ «*» در صف می‌گذارد؛ کارگر همه‌ی تب‌ها را از نو می‌سازد."""
    await _x("INSERT INTO _sheet_outbox(sid,tbl,op,ts) VALUES(?,'*','REBUILD',datetime('now'))", [sid])
    return 1


# ───────────────────────── تنظیماتِ شیت (کلی + اختصاصیِ هر سرور) ─────────────────────────
CAT_NAMES = list(views.CATEGORIES)          # دسته‌های تب‌ها (برای روشن/خاموش‌کردن در /sheets)
HDR_COLORS = {"آبی": "1E3A8A", "سبز": "14532D", "قرمز": "7F1D1D", "خاکستری": "374151", "طلایی": "92400E", "مشکی": "111827"}
FONTS = ["Arial", "Calibri", "Roboto", "Verdana", "Georgia"]
FSIZES = [9, 10, 11, 12, 14]
CWIDTHS = {"کم": 110, "متوسط": 160, "زیاد": 240}
INTERVALS = [3, 6, 15, 30, 60]
DELAYS = [(0, "فوری"), (60, "۱ دقیقه"), (300, "۵ دقیقه"), (1800, "۳۰ دقیقه"), (3600, "۱ ساعت")]
DEFAULTS = {"on": 1, "hdr": "آبی", "font": "Arial", "fsize": 10, "cw": "متوسط", "tabcolor": 1, "opcolor": 1, "interval": 6, "delay": 0}
SCOPED = {"on"}                                  # این برای هر سرور جدا هم قابل‌تنظیم است؛ بقیه فقط کلی
_last_run = {}


async def _json_cfg(k):
    try:
        return json.loads(await _cfg_get(k) or "{}")
    except Exception:
        return {}


async def get_opts(sid=None) -> dict:
    """تنظیماتِ مؤثر: پیش‌فرض ← کلی ← اختصاصیِ سرور (فقط کلیدهای مجاز و دسته‌ها)."""
    o = dict(DEFAULTS)
    g = await _json_cfg("opts")
    o.update({k: v for k, v in g.items() if k != "cats"})
    cats = dict(g.get("cats", {}))
    if sid is not None:
        ov = await _json_cfg(f"opts_{int(sid)}")
        o.update({k: v for k, v in ov.items() if k in SCOPED})
        cats.update(ov.get("cats", {}))
    o["cats"] = cats
    return o


def _cat_on(o, cat) -> bool:
    return bool(o["cats"].get(cat, 1))


async def set_opt(scope, key, val):
    """scope: 'g' یا sid. key می‌تواند 'cat:<نام دسته>' باشد."""
    k = "opts" if scope == "g" else f"opts_{int(scope)}"
    d = await _json_cfg(k)
    if key.startswith("cat:"):
        d.setdefault("cats", {})[key[4:]] = val
    else:
        d[key] = val
    await _cfg_set(k, json.dumps(d, ensure_ascii=False))


async def reset_scope(sid):
    await _x("DELETE FROM sheet_cfg WHERE k=?", [f"opts_{int(sid)}"])


def _rgb(hexs):
    return {"red": int(hexs[0:2], 16) / 255, "green": int(hexs[2:4], 16) / 255, "blue": int(hexs[4:6], 16) / 255}


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
    """عنوانِ تبِ «جدولِ خام» در بکاپ: «دسته | عنوان»."""
    cat, fa = CATS.get(logical, ("۹ سایر", logical))
    t = f"{cat} | {fa}"
    return re.sub(r"[\[\]\*\?/\\:]", "-", t)[:95]


# ───────────────────────── ساختنِ تب‌ها و نوشتن در گوگل‌شیت ─────────────────────────
_ALIGN = {"L": "LEFT", "C": "CENTER", "R": "RIGHT"}
_MASK_LEAN = ("userEnteredValue,userEnteredFormat.backgroundColor,userEnteredFormat.horizontalAlignment,userEnteredFormat.numberFormat,"
              "userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.italic,userEnteredFormat.textFormat.foregroundColor")
_MASK_FULL = _MASK_LEAN + ",userEnteredFormat.textFormat.fontSize,userEnteredFormat.textFormat.fontFamily"
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
DEFAULT_TAB_TITLES = {"Sheet1", "Sheet 1", "برگه۱", "برگه 1", "Лист1"}


def _theme(o):
    return views.Theme(hdr=HDR_COLORS.get(o["hdr"], "1E3A8A"), font=o["font"], fsize=int(o["fsize"]),
                       cw=CWIDTHS.get(o["cw"], 160), colors=bool(o.get("opcolor", 1)))


async def _qd(sql, args=None):
    """اجرای SQL فیزیکی و برگرداندنِ ردیف‌ها به‌صورتِ dict (برای sheet_views)."""
    r = await _x(sql, args)
    cols = list(r.columns)
    return [dict(zip(cols, row)) for row in r.rows]


def _api_cell(c, T, full):
    d = {}
    v = c.v
    if v is None or v == "":
        pass
    elif isinstance(v, bool):
        d["userEnteredValue"] = {"boolValue": v}
    elif isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            pass
        else:
            d["userEnteredValue"] = {"numberValue": v}
    else:
        d["userEnteredValue"] = {"stringValue": _CTRL.sub("", str(v))[:45000]}
    f = {}
    if c.bg:
        f["backgroundColor"] = _rgb(c.bg)
    if c.al:
        f["horizontalAlignment"] = _ALIGN[c.al]
    if c.nf:
        f["numberFormat"] = {"type": "PERCENT" if c.nf.endswith("%") else "NUMBER", "pattern": c.nf}
    tf = {}
    if c.b:
        tf["bold"] = True
    if c.i:
        tf["italic"] = True
    if c.fg:
        tf["foregroundColor"] = _rgb(c.fg)
    if full:
        tf["fontSize"] = int(c.sz or T.fsize)
        tf["fontFamily"] = T.font
    if tf:
        f["textFormat"] = tf
    if f:
        d["userEnteredFormat"] = f
    return d


def _est_height(row, widths, scale, fsize, base):
    """ارتفاعِ ردیف با تخمینِ تعدادِ خط‌های متن‌های بلند (چون ستون‌ها wrap می‌شوند)."""
    lines = 1
    for ci, c in enumerate(row):
        if isinstance(c.v, str) and c.v:
            cpl = max(4, int(widths[ci] * scale / (fsize * 0.72)))
            lines = max(lines, sum(max(1, math.ceil(len(part) / cpl)) for part in c.v.split("\n")))
    return base if lines == 1 else lines * (fsize + 8) + 8


def _grid_requests(gid, grid, T, o, color):
    """همه‌ی درخواست‌های لازم برای نوشتنِ یک Grid در یک تب (به‌ترتیب: پاک‌کردن ← ابعاد/ظاهرِ پایه ← داده ← ادغام ← ارتفاع/عرض)."""
    scale = T.cw / 160.0
    props = {"sheetId": gid, "rightToLeft": True,
             "gridProperties": {"rowCount": max(len(grid.rows) + 5, 20), "columnCount": grid.ncols, "frozenRowCount": grid.freeze_rows,
                                "frozenColumnCount": grid.freeze_cols, "hideGridlines": True}}
    if o.get("tabcolor"):
        props["tabColor"] = _rgb(color)
    reqs = [
        {"unmergeCells": {"range": {"sheetId": gid}}},
        {"updateCells": {"range": {"sheetId": gid}, "fields": "userEnteredValue,userEnteredFormat"}},
        {"updateSheetProperties": {"properties": props, "fields": "rightToLeft,tabColor,gridProperties.rowCount,gridProperties.columnCount,"
                                   "gridProperties.frozenRowCount,gridProperties.frozenColumnCount,gridProperties.hideGridlines"}},
        {"repeatCell": {"range": {"sheetId": gid},
                        "cell": {"userEnteredFormat": {"textFormat": {"fontFamily": T.font, "fontSize": T.fsize}, "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"}},
                        "fields": "userEnteredFormat.textFormat.fontFamily,userEnteredFormat.textFormat.fontSize,"
                                  "userEnteredFormat.verticalAlignment,userEnteredFormat.wrapStrategy"}},
    ]
    runs, cur, cur_full, start = [], [], None, 0
    for r, row in enumerate(grid.rows):
        full = any(c.sz for c in row)
        if cur and full != cur_full:
            runs.append((start, cur_full, cur))
            cur = []
        if not cur:
            start, cur_full = r, full
        cur.append({"values": [_api_cell(c, T, full) for c in row]})
    if cur:
        runs.append((start, cur_full, cur))
    for st, full, rws in runs:
        for k in range(0, len(rws), 250):
            reqs.append({"updateCells": {"start": {"sheetId": gid, "rowIndex": st + k, "columnIndex": 0}, "rows": rws[k:k + 250],
                                         "fields": _MASK_FULL if full else _MASK_LEAN}})
    for r0, c0, r1, c1 in grid.merges:
        reqs.append({"mergeCells": {"range": {"sheetId": gid, "startRowIndex": r0, "endRowIndex": r1 + 1,
                                              "startColumnIndex": c0, "endColumnIndex": c1 + 1}, "mergeType": "MERGE_ALL"}})
    base = max(26, T.fsize + 16)
    hs = [grid.heights.get(r) or (_est_height(row, grid.widths, scale, T.fsize, base) if r >= grid.body_start else base)
          for r, row in enumerate(grid.rows)]
    i = 0
    while i < len(hs):
        j = i
        while j + 1 < len(hs) and hs[j + 1] == hs[i]:
            j += 1
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": gid, "dimension": "ROWS", "startIndex": i, "endIndex": j + 1},
                                                   "properties": {"pixelSize": int(hs[i])}, "fields": "pixelSize"}})
        i = j + 1
    for ci, w in enumerate(grid.widths):
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": ci, "endIndex": ci + 1},
                                                   "properties": {"pixelSize": max(40, min(700, int(w * scale)))}, "fields": "pixelSize"}})
    return reqs


async def _send_requests(sheet_id, reqs, max_bytes=2_500_000, max_n=300):
    """درخواست‌ها را به‌ترتیب و در چند batchUpdate می‌فرستد (سقفِ حجم/تعداد)، با مکثِ کوتاه برای سقفِ نرخِ گوگل."""
    batch, size = [], 0
    for rq in reqs:
        n = len(json.dumps(rq, ensure_ascii=False))
        if batch and (size + n > max_bytes or len(batch) >= max_n):
            await _g("POST", f"{API}/{sheet_id}:batchUpdate", json={"requests": batch})
            batch, size = [], 0
            await asyncio.sleep(0.8)
        batch.append(rq)
        size += n
    if batch:
        await _g("POST", f"{API}/{sheet_id}:batchUpdate", json={"requests": batch})


async def _tabs_state(sid):
    """وضعیتِ تب‌ها: کلید «v:<tab>» → {h: hash آخرین محتوای نوشته‌شده، gid}. ردیف‌های بدونِ «v:» مالِ نسخه‌ی قدیمیِ لاگ‌اند."""
    out = {}
    for tbl, cols, gid in (await _x("SELECT tbl, cols, gid FROM sheet_tabs WHERE sid=?", [sid])).rows:
        h = ""
        try:
            j = json.loads(cols or "")
            if isinstance(j, dict):
                h = j.get("h", "")
        except Exception:
            pass
        out[tbl] = {"h": h, "gid": gid, "legacy": not str(tbl).startswith("v:")}
    return out


async def _save_tab_state(sid, key, h, gid):
    await _x("INSERT INTO sheet_tabs(sid,tbl,cols,gid) VALUES(?,?,?,?) ON CONFLICT(sid,tbl) DO UPDATE SET cols=excluded.cols, gid=excluded.gid",
             [sid, "v:" + key, json.dumps({"h": h}), gid])


async def render_server(sid, sheet_id, o, dirty, all_dirty):
    """تب‌های وابسته به جدول‌های تغییرکرده (یا همه) را از نو می‌سازد و فقط اگر محتوا عوض شده باشد در شیت می‌نویسد. تعدادِ تب‌های نوشته‌شده را برمی‌گرداند."""
    T = _theme(o)
    tabs = [t for t in views.TABS if _cat_on(o, t.cat) and (not t.main_only or int(sid) == srvctx.MAIN_ID)]
    state = await _tabs_state(sid)
    meta = await _meta(sheet_id)
    by_title = {sh["properties"]["title"]: sh["properties"]["sheetId"] for sh in meta.get("sheets", [])}
    gids = set(by_title.values())

    # ۱) تب‌هایی که باید وجود داشته باشند
    add, gid_of, created = [], {}, False
    for t in tabs:
        st = state.get("v:" + t.key)
        gid = st["gid"] if st and st["gid"] in gids else by_title.get(t.title)
        if gid is None:
            gid = zlib.crc32(f"{sid}:{t.key}".encode()) % 2_000_000_000
            add.append({"addSheet": {"properties": {"sheetId": gid, "title": t.title, "rightToLeft": True,
                                                    "gridProperties": {"frozenRowCount": 4}}}})
            created = True
        gid_of[t.key] = gid
    if add:
        add += [{"updateSheetProperties": {"properties": {"sheetId": gid_of[t.key], "index": i}, "fields": "index"}} for i, t in enumerate(tabs)]
        await _send_requests(sheet_id, add)

    # ۲) مهاجرت از نسخه‌ی قدیمیِ لاگ: تب‌های قدیمی پنهان می‌شوند (پاک نمی‌شوند)، تبِ خالیِ پیش‌فرض هم پنهان می‌شود
    legacy = [st["gid"] for st in state.values() if st["legacy"] and st["gid"] in gids]
    if created:
        legacy += [g for title, g in by_title.items() if title in DEFAULT_TAB_TITLES and g not in gid_of.values()]
    for g in dict.fromkeys(legacy):
        try:
            await _g("POST", f"{API}/{sheet_id}:batchUpdate", json={"requests": [
                {"updateSheetProperties": {"properties": {"sheetId": g, "hidden": True}, "fields": "hidden"}}]})
        except GoogleError as e:
            logger.info("sheets: hide legacy tab skipped: %s", e)
    if any(st["legacy"] for st in state.values()):
        await _x("DELETE FROM sheet_tabs WHERE sid=? AND tbl NOT LIKE 'v:%'", [sid])

    # ۳) کدام تب‌ها باید بازسازی شوند؟
    targets = [t for t in tabs if all_dirty or not state.get("v:" + t.key) or state["v:" + t.key]["gid"] != gid_of[t.key]
               or (t.deps & dirty)]
    if not targets:
        return 0
    now = datetime.now(TEHRAN)
    ctx = views.Ctx(sid, _qd, await _physical_tables(), T, views.fa_date(now.strftime("%Y-%m-%d %H:%M"), True),
                    now.strftime("%Y-%m-%d"), _srv_name(sid))
    written, first_err = 0, None
    for t in targets:
        try:
            grid = await t.build(ctx)
            h = grid.digest(T)
            st = state.get("v:" + t.key)
            if st and st["h"] == h and st["gid"] == gid_of[t.key]:
                continue                                   # محتوا عوض نشده → چیزی در گوگل نوشته نمی‌شود
            await _send_requests(sheet_id, _grid_requests(gid_of[t.key], grid, T, o, t.color))
            await _save_tab_state(sid, t.key, h, gid_of[t.key])
            written += 1
            await asyncio.sleep(0.6)
        except GoogleError:
            raise
        except Exception as e:
            logger.exception("sheets: tab %s failed (sid=%s)", t.key, sid)
            first_err = first_err or f"{t.title}: {e}"
    if first_err:
        await _x("UPDATE sheet_links SET last_error=? WHERE sid=?", [first_err[:300], sid])
    return written


_busy = asyncio.Lock()
_backoff_until = 0.0


async def tick(context=None, force=False):
    """هر ۳ ثانیه بیدار می‌شود؛ طبقِ تنظیماتِ هر سرور (روشن/خاموش، سرعت، تأخیر، بخش‌ها) تب‌های تغییرکرده را بازسازی می‌کند."""
    global _backoff_until
    if _busy.locked() or (time.time() < _backoff_until and not force):
        return
    async with _busy:
        try:
            links = (await _x("SELECT sid, sheet_id FROM sheet_links WHERE active=1")).rows
            if not links or not await _cfg_get("sa_json"):
                return
            for sid, sheet_id in links:
                o = await get_opts(sid)
                if not o["on"]:
                    continue
                if not force and time.time() - _last_run.get(sid, 0) < int(o["interval"]):
                    continue
                _last_run[sid] = time.time()
                q, args = "SELECT tbl, MAX(id) FROM _sheet_outbox WHERE sid=? AND sent=0", [sid]
                if int(o["delay"]) > 0 and not force:
                    q += " AND ts <= datetime('now', ?)"
                    args.append(f"-{int(o['delay'])} seconds")
                rows = (await _x(q + " GROUP BY tbl", args)).rows
                if not rows and not force:
                    continue
                dirty = {r[0] for r in rows}
                hi = max((int(r[1]) for r in rows), default=0)
                try:
                    n = await render_server(sid, sheet_id, o, dirty, force or "*" in dirty)
                    if hi:
                        await _x("DELETE FROM _sheet_outbox WHERE sid=? AND id<=?", [sid, hi])
                    await _x("UPDATE sheet_links SET last_sync=?, sent_total=sent_total+? WHERE sid=?",
                             [datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M:%S"), n, sid])
                    await _x("UPDATE sheet_links SET last_error='' WHERE sid=? AND last_error NOT LIKE '%:%'", [sid])
                except GoogleError as e:
                    await _x("UPDATE sheet_links SET last_error=? WHERE sid=?", [str(e)[:300], sid])
                    logger.warning("sheets: sid %s update failed: %s", sid, e)
                    if e.status == 429:
                        _backoff_until = time.time() + 65
                        return
                except Exception as e:
                    await _x("UPDATE sheet_links SET last_error=? WHERE sid=?", [str(e)[:300], sid])
                    logger.warning("sheets: sid %s update failed: %s", sid, e)
        except Exception:
            logger.warning("sheets: tick failed", exc_info=True)


async def maintenance(context=None):
    """ساعتی: تریگر جدول‌های جدید/ستون‌های جدید + بازسازیِ کاملِ شیت (اگر چیزی عوض نشده باشد، چیزی در گوگل نوشته نمی‌شود)."""
    try:
        links = [r[0] for r in (await _x("SELECT sid FROM sheet_links WHERE active=1")).rows]
        if links:
            await ensure_triggers(links)
            for sid in links:
                await queue_snapshot(sid)
        await _x("DELETE FROM _sheet_outbox WHERE sent=1")
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
    lines = ["📊 <b>شیت‌ها — نمای زنده‌ی مسابقات</b>", ""]
    lines.append(f"🔑 کلید گوگل: {'✅ ' + sa['client_email'] if sa else '❌ ثبت نشده'}")
    lines.append("")
    kb = [[InlineKeyboardButton("🔑 ارسال/تغییر کلید گوگل", callback_data="sh:key"),
           InlineKeyboardButton("⚙️ تنظیمات شیت", callback_data="sh:cfg")]]
    for s in servers.all_servers(include_deleted=False):
        sid = int(s["id"])
        ln = links.get(sid)
        if ln:
            st = ("⏸ متوقف" if not ln[2] else "🟢 فعال") + (f" · تغییرِ منتظر: {pend.get(sid, 0)}" if pend.get(sid) else "")
        else:
            st = "⚪ وصل نیست"
        lines.append(f"• {_srv_name(sid)}: {st}")
        kb.append([InlineKeyboardButton(f"{'📎' if ln else '➕'} {_srv_name(sid)}", callback_data=f"sh:srv:{sid}")])
    lines.append("")
    lines.append("ℹ️ ایمیلِ بالا را در هر شیت Share کن (Editor)، بعد آی‌دی یا لینکِ شیت را برای همان سرور بفرست.\nشیت یک گزارشِ زیبا و زنده از مسابقات است؛ عکسِ کاملِ خامِ دیتابیس در بکاپ است.")
    return "\n".join(lines), InlineKeyboardMarkup(kb)


async def _srv_text_kb(sid):
    r = (await _x("SELECT sheet_id, active, linked_at, last_sync, last_error, sent_total FROM sheet_links WHERE sid=?", [sid])).rows
    pend = (await _x("SELECT COUNT(*) FROM _sheet_outbox WHERE sid=? AND sent=0", [sid])).rows[0][0]
    lines = [f"📊 <b>{_srv_name(sid)}</b>", ""]
    kb = []
    if r:
        sheet_id, active, linked, last, err, total = r[0]
        lines += [f"شیت: <code>{sheet_id}</code>", f"وضعیت: {'🟢 فعال' if active else '⏸ متوقف'}",
                  f"وصل‌شده: {linked or '—'}", f"آخرین به‌روزرسانی: {last or '—'}", f"تعداد بازنویسیِ تب‌ها: {total}",
                  f"تغییرِ منتظرِ ثبت: {pend}"]
        if err:
            lines.append(f"⚠️ آخرین خطا: {err[:200]}")
        kb += [[InlineKeyboardButton("🧪 تست اتصال", callback_data=f"sh:test:{sid}"),
                InlineKeyboardButton("🔄 به‌روزرسانی الان", callback_data=f"sh:sync:{sid}")],
               [InlineKeyboardButton("▶️ ادامه" if not active else "⏸ توقف", callback_data=f"sh:tog:{sid}"),
                InlineKeyboardButton("✏️ تغییر شیت", callback_data=f"sh:set:{sid}")],
               [InlineKeyboardButton("♻️ بازسازی کامل شیت", callback_data=f"sh:resnap:{sid}")],
               [InlineKeyboardButton("🗑 قطع اتصال", callback_data=f"sh:del:{sid}")]]
    else:
        lines.append("هنوز شیتی وصل نشده.")
        kb.append([InlineKeyboardButton("➕ وصل کردن شیت", callback_data=f"sh:set:{sid}")])
    kb.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="sh:menu")])
    return "\n".join(lines), InlineKeyboardMarkup(kb)


# ───────────────────────── منوی تنظیماتِ شیت ─────────────────────────
def _onoff(v):
    return "✅" if v else "⬜"


def _delay_label(sec):
    return dict(DELAYS).get(int(sec), f"{sec} ثانیه")


def _btn(label, data):
    return [InlineKeyboardButton(label, callback_data=data)]


async def _cfg_main():
    o = await get_opts()
    text = ("⚙️ <b>تنظیمات شیت</b>\n\n"
            f"🔌 به‌روزرسانیِ شیت: {'✅ روشن' if o['on'] else '⏸ خاموش (تغییرات منتظر می‌مانند)'}\n"
            f"⏱ فاصله‌ی به‌روزرسانی: {o['interval']} ثانیه · تأخیر: {_delay_label(o['delay'])}\n"
            f"🎨 هدر: {o['hdr']} · فونت: {o['font']} {o['fsize']} · ستون: {o['cw']}")
    kb = [_btn(f"🔌 به‌روزرسانیِ شیت: {'روشن' if o['on'] else 'خاموش'}", "sh:t:g:on"),
          _btn("🗂 کدام بخش‌ها در شیت باشند", "sh:cfg:what:g"),
          _btn("🎨 ظاهر جدول‌ها (رنگ، فونت، سایز)", "sh:cfg:look"),
          _btn("⏱ سرعت و تأخیر", "sh:cfg:speed"),
          _btn("🖥 تنظیمات هر سرور", "sh:cfg:srv"),
          _btn("⬅️ بازگشت", "sh:menu")]
    return text, InlineKeyboardMarkup(kb)


async def _cfg_what(scope):
    sid = None if scope == "g" else int(scope)
    o = await get_opts(sid)
    title = "همه‌ی سرورها" if sid is None else _srv_name(sid)
    text = (f"🗂 <b>کدام بخش‌ها در شیت باشند — {title}</b>\n\nبا زدن هر مورد روشن/خاموش می‌شود (تب‌های خاموش دیگر به‌روز نمی‌شوند؛ پاک هم نمی‌شوند)."
            + ("" if sid is None else "\nاین‌جا هرچه عوض کنی فقط برای همین سرور است."))
    kb = []
    if sid is not None:
        kb.append(_btn(f"{_onoff(o['on'])} به‌روزرسانیِ شیتِ این سرور", f"sh:t:{scope}:on"))
    for i, cat in enumerate(CAT_NAMES):
        kb.append(_btn(f"{_onoff(o['cats'].get(cat, 1))} {cat}", f"sh:t:{scope}:cat{i}"))
    if sid is not None:
        kb.append(_btn("↩️ برگشت به تنظیمات کلی", f"sh:rs:{sid}"))
    kb.append(_btn("⬅️ بازگشت", "sh:cfg" if sid is None else "sh:cfg:srv"))
    return text, InlineKeyboardMarkup(kb)


async def _cfg_look():
    o = await get_opts()
    text = "🎨 <b>ظاهر جدول‌ها</b>\n\nهر دکمه را بزنی گزینه‌ی بعدی انتخاب می‌شود. تب‌ها با ظاهرِ تازه بازسازی می‌شوند؛ «اعمال» همین را همین الان انجام می‌دهد."
    kb = [_btn(f"🎨 رنگ هدر: {o['hdr']}", "sh:y:hdr"),
          _btn(f"🔤 فونت: {o['font']}", "sh:y:font"),
          _btn(f"🔠 اندازه‌ی فونت: {o['fsize']}", "sh:y:fsize"),
          _btn(f"↔️ عرض ستون: {o['cw']}", "sh:y:cw"),
          _btn(f"{_onoff(o['tabcolor'])} رنگِ تب‌ها به‌تفکیک دسته", "sh:y:tabcolor"),
          _btn(f"{_onoff(o['opcolor'])} رنگ‌بندیِ وضعیت‌ها (فعال/تعلیق، برد/باخت، مدال‌ها)", "sh:y:opcolor"),
          _btn("✨ اعمال روی همه‌ی تب‌ها", "sh:apply"),
          _btn("⬅️ بازگشت", "sh:cfg")]
    return text, InlineKeyboardMarkup(kb)


async def _cfg_speed():
    o = await get_opts()
    text = ("⏱ <b>سرعت و تأخیر</b>\n\n"
            "• فاصله‌ی به‌روزرسانی: حداقل چند ثانیه بینِ دو بازنویسیِ شیت (کمتر = زنده‌تر، ولی نزدیک‌تر به سقفِ گوگل).\n"
            "• تأخیر: تغییرات تازه‌تر از این مدت هنوز در شیت دیده نمی‌شوند.")
    kb = [_btn(f"⚡ فاصله‌ی به‌روزرسانی: {o['interval']} ثانیه", "sh:y:interval"),
          _btn(f"⏳ تأخیر: {_delay_label(o['delay'])}", "sh:y:delay"),
          _btn("⬅️ بازگشت", "sh:cfg")]
    return text, InlineKeyboardMarkup(kb)


async def _cfg_srv_list():
    kb = []
    for sv in servers.all_servers(include_deleted=False):
        sid = int(sv["id"])
        ov = await _json_cfg(f"opts_{sid}")
        kb.append(_btn(f"{'🔧' if ov else '🖥'} {_srv_name(sid)}", f"sh:cfg:what:{sid}"))
    kb.append(_btn("⬅️ بازگشت", "sh:cfg"))
    return "🖥 <b>تنظیمات هر سرور</b>\n\n🔧 یعنی تنظیماتِ اختصاصیِ جدا دارد.", InlineKeyboardMarkup(kb)


def _cycle_list(key):
    return {"hdr": list(HDR_COLORS), "font": FONTS, "fsize": FSIZES, "cw": list(CWIDTHS), "tabcolor": [1, 0], "opcolor": [1, 0],
            "interval": INTERVALS, "delay": [d for d, _l in DELAYS]}[key]


async def _cfg_dispatch(q, ctx, parts):
    """sh:cfg[:what:<scope>|look|speed|srv] · sh:t:<scope>:<key> · sh:y:<key> · sh:apply · sh:rs:<sid>"""
    act = parts[1]
    if act == "cfg":
        sub = parts[2] if len(parts) > 2 else ""
        if sub == "what":
            r = await _cfg_what(parts[3])
        elif sub == "look":
            r = await _cfg_look()
        elif sub == "speed":
            r = await _cfg_speed()
        elif sub == "srv":
            r = await _cfg_srv_list()
        else:
            r = await _cfg_main()
        await q.answer()
        await _show(q, *r)
    elif act == "t":
        scope, key = parts[2], parts[3]
        o = await get_opts(None if scope == "g" else int(scope))
        if key.startswith("cat"):
            name = CAT_NAMES[int(key[3:])]
            await set_opt(scope, f"cat:{name}", 0 if o["cats"].get(name, 1) else 1)
        else:
            await set_opt(scope, key, 0 if o.get(key, 1) else 1)
        await q.answer("انجام شد")
        if key == "on" and scope == "g":
            await _show(q, *(await _cfg_main()))
        else:
            await _show(q, *(await _cfg_what(scope)))
    elif act == "y":
        key = parts[2]
        o = await get_opts()
        lst = _cycle_list(key)
        cur = o.get(key, DEFAULTS[key])
        nxt = lst[(lst.index(cur) + 1) % len(lst)] if cur in lst else lst[0]
        await set_opt("g", key, nxt)
        await q.answer("انجام شد")
        await _show(q, *(await (_cfg_speed() if key in ("interval", "delay") else _cfg_look())))
    elif act == "apply":
        await q.answer("در حال بازسازیِ تب‌ها…")
        sids = [r[0] for r in (await _x("SELECT sid FROM sheet_links WHERE active=1")).rows]
        for sid in sids:
            await queue_snapshot(sid)
        await tick(force=True)
        await q.message.reply_text(f"✨ ظاهرِ جدید روی شیتِ {len(sids)} سرور اعمال شد.")
    elif act == "rs":
        await reset_scope(int(parts[2]))
        await q.answer("به تنظیمات کلی برگشت")
        await _show(q, *(await _cfg_what(parts[2])))


# ───────────────────────── جابه‌جاییِ اطلاعات با بکاپ ─────────────────────────
async def backup_rows():
    """جدولِ «شیت‌ها» که داخلِ فایل بکاپ (Excel) گذاشته می‌شود: وضعیتِ اتصال و تنظیماتِ شیتِ هر سرور (بدون کلید/رمز)."""
    await ensure_tables()
    g = await get_opts()
    rows = [["سرور", "لینک شیت", "وضعیت", "آخرین به‌روزرسانی", "تعداد بازنویسی تب‌ها", "تغییر منتظر", "تنظیمات اختصاصی"]]
    links = {r[0]: r for r in (await _x("SELECT sid, sheet_id, active, last_sync, sent_total FROM sheet_links")).rows}
    pend = {r[0]: r[1] for r in (await _x("SELECT sid, COUNT(*) FROM _sheet_outbox WHERE sent=0 GROUP BY sid")).rows}
    for sv in servers.all_servers(include_deleted=False):
        sid = int(sv["id"])
        ln = links.get(sid)
        ov = await _json_cfg(f"opts_{sid}")
        rows.append([_srv_name(sid), f"https://docs.google.com/spreadsheets/d/{ln[1]}" if ln else "—",
                     ("فعال" if ln[2] else "متوقف") if ln else "وصل نیست", (ln[3] if ln else "") or "—",
                     ln[4] if ln else 0, pend.get(sid, 0), json.dumps(ov, ensure_ascii=False) if ov else "—"])
    rows.append([])
    rows.append(["تنظیمات کلی شیت", json.dumps({k: v for k, v in g.items()}, ensure_ascii=False)])
    return rows


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
    await ensure_tables()
    if act in ("cfg", "t", "y", "apply", "rs"):
        try:
            await _cfg_dispatch(q, ctx, parts)
        except Exception as e:
            logger.exception("sheets: settings callback failed")
            try:
                await q.answer(f"خطا: {e}"[:190], show_alert=True)
            except Exception:
                pass
        return
    sid = int(parts[2]) if len(parts) > 2 else None
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
            await tick(force=True)
            await _show(q, *(await _srv_text_kb(sid)))
        elif act == "tog":
            await _x("UPDATE sheet_links SET active=1-active WHERE sid=?", [sid])
            await q.answer("انجام شد")
            await _show(q, *(await _srv_text_kb(sid)))
        elif act == "resnap":
            await queue_snapshot(sid)
            await q.answer("شیت تا چند ثانیه‌ی دیگر کامل بازسازی می‌شود", show_alert=True)
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
                await _x("DELETE FROM sheet_tabs WHERE sid=?", [sid])      # شیتِ تازه → همه‌چیز از نو
            await _x("DELETE FROM _sheet_outbox WHERE sid=?", [sid])
            await queue_snapshot(sid)
        except Exception as e:
            logger.exception("sheets: link setup failed")
            await status.edit_text(f"⚠️ وصل شد ولی راه‌اندازی کامل نشد: {e}")
            ctx.user_data.pop("sheet_wait", None)
            raise ApplicationHandlerStop
        ctx.user_data.pop("sheet_wait", None)
        await status.edit_text(
            f"✅ «{_srv_name(sid)}» به شیت «{m.get('properties', {}).get('title', '')}» وصل شد.\n"
            "تب‌های رنگی و خوانا (رده‌بندی، بازیکنان، بازی‌ها، تیم‌ها، Elo، مدیران، رهگشا …) تا چند ثانیه‌ی دیگر ساخته می‌شوند و از این به بعد با هر تغییر خودکار تازه می‌شوند.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ منوی شیت‌ها", callback_data="sh:menu")]]))
        raise ApplicationHandlerStop


def register(app):
    """در bot.py صدا زده می‌شود. گروهِ -50: بعد از روتر (-100) و قبل از بقیه."""
    app.add_handler(CommandHandler(["sheets", "sheet"], cmd_sheets), group=-50)
    app.add_handler(CallbackQueryHandler(on_cb, pattern=r"^sh:"), group=-50)
    app.add_handler(MessageHandler((filters.TEXT | filters.Document.ALL) & ~filters.COMMAND, on_input), group=-50)
