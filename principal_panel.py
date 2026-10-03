"""
principal_panel.py — پنل فقط‌خواندنیِ مدیر مدرسه.

این ماژول جز چند مورد هیچ نوشتنی روی دیتابیس نداره (بقیه‌ی کارش فقط SELECT ـه):
۱) گفتگوهای دستیارِ هوشمند توی جدول‌های ai_chat_* ذخیره می‌شن تا مدیر ارشد از
«پنل ادمین ← دستیار» ببینتشون. اثری از این تاریخچه توی خودِ پنل مدیر مدرسه نیست.
۲) هر بارگذاریِ صفحه (یعنی هر «ورود») توی principal_access_log ثبت می‌شه —
با IP، User-Agent، و (در صورتِ موفقیت) موقعیتِ جغرافیاییِ IP — تا مدیر ارشد از
«پنل ادمین ← دستگاه‌های مدیر مدرسه» ببینتش و بتونه یک دستگاهِ خاص رو بلاک/آنبلاک
کنه. این لاگ‌نویسی در پس‌زمینه انجام می‌شه (fire-and-forget) و هیچ‌وقت باعثِ
معطلیِ بارگذاریِ صفحه برای مدیر مدرسه نمی‌شه.
۳) اعلانات (زنگولهٔ بالای صفحه): فقط وضعیتِ «خوانده/خوانده‌نشده»ی اعلان‌ها و
اشتراکِ Web Push ِ دستگاهِ خودِ مدیر مدرسه نوشته می‌شه. خودِ اعلان‌ها (ساخت/
ویرایش/حذف) فقط از «پنل ادمین ← ارسال اعلان» انجام می‌شن، نه از این پنل.
مستقل از منطق ربات
و از admin_panel.py هست، ولی درست مثل همون، روی همون اپلیکیشن aiohttp ای
که game_server.py می‌سازه سوار میشه (نه یک سرور جدا).

احراز هویت: بدون فرم ورود و بدون رمز — فقط یک کلید ثابت (env: PRINCIPAL_KEY)
که در خودِ لینک به‌صورت ?k=... قرار می‌گیره. هر درخواستی (چه صفحه، چه API)
باید این کلید رو با پارامتر k بفرسته.

دستگاه‌ها: چون این پنل حسابِ کاربری نداره، «دستگاه» با هشِ IP+User-Agent
شناسایی می‌شه (_device_id). اگر مدیر ارشد از پنل ادمین یک دستگاه رو بلاک کنه،
همون دستگاه—even با کلیدِ درست—دیگه نه صفحه باز می‌کنه نه هیچ API‌ای جواب
می‌گیره (_require_auth این رو قبل از هر چیز دیگه‌ای چک می‌کنه).
"""

import hashlib
import hmac
import json
import logging
import os
import re
import asyncio
import base64
import html as html_lib
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx
import net_utils
from aiohttp import web

import database as db
from helpers import now_context_for_ai, admin_display, pishva_display
from status_page import render_status_page

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

PANEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp_principal")

# ─── کلید دسترسی ────────────────────────────────────────────────
PRINCIPAL_KEY = os.environ.get("PRINCIPAL_KEY", "")


def _authed(request) -> bool:
    if not PRINCIPAL_KEY:
        # اگر کلیدی تنظیم نشده باشه، به‌خاطر امنیت هیچ درخواستی معتبر شمرده نمیشه.
        return False
    k = request.query.get("k", "")
    return hmac.compare_digest(k, PRINCIPAL_KEY)


async def _panel_enabled() -> bool:
    return (await db.get_setting("principal_panel_enabled", "1")) == "1"


# ─── شناساییِ دستگاه (IP + User-Agent) ──────────────────────────────
def _client_ip(request) -> str:
    """روی Railway (و هر استقرارِ پشتِ پراکسی) request.remote آدرسِ خودِ
    پراکسیه، نه کاربر؛ IP واقعیِ کاربر توی هدرِ X-Forwarded-For (اولین
    آیتمِ لیست) می‌شینه. اگه این هدر نبود (مثلاً اجرای محلی)، به
    request.remote برمی‌گردیم."""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first
    return request.remote or ""


_UA_BROWSER_PATTERNS = [
    ("Edge", re.compile(r"Edg(e|A|iOS)?/")),
    ("Instagram", re.compile(r"Instagram")),
    ("Telegram", re.compile(r"Telegram")),
    ("Firefox", re.compile(r"Firefox/")),
    ("Chrome", re.compile(r"(Chrome|CriOS)/")),
    ("Safari", re.compile(r"Safari/")),
    ("Opera", re.compile(r"(OPR|Opera)/")),
]
_UA_OS_PATTERNS = [
    ("Windows", re.compile(r"Windows")),
    ("Android", re.compile(r"Android")),
    ("iOS", re.compile(r"(iPhone|iPad|iPod)")),
    ("macOS", re.compile(r"Mac OS X")),
    ("Linux", re.compile(r"Linux")),
]


def _parse_user_agent(ua: str):
    """یک پارسرِ سبکِ دستی (بدونِ وابستگیِ تازه) برای استخراجِ نام مرورگر،
    سیستم‌عامل، و نوعِ دستگاه از رشته‌ی User-Agent — فقط برای نمایشِ خواناتر
    توی پنل ادمین، نه یک شناساییِ دقیقِ فنی."""
    ua = ua or ""
    browser = next((name for name, pat in _UA_BROWSER_PATTERNS if pat.search(ua)), "نامشخص")
    os_name = next((name for name, pat in _UA_OS_PATTERNS if pat.search(ua)), "نامشخص")
    if re.search(r"Mobi|Android.*Mobile|iPhone", ua):
        device_type = "mobile"
    elif re.search(r"iPad|Tablet", ua):
        device_type = "tablet"
    else:
        device_type = "desktop"
    return browser, os_name, device_type


def _device_id(ip: str, ua: str) -> str:
    raw = f"{ip}::{ua}"
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]


# ─── موقعیتِ جغرافیاییِ IP (اختیاری، بهترین‌تلاش) ───────────────────
# فقط برای نمایشِ «آدرس» (شهر/کشور) در پنل ادمین؛ اگه این سرویس در دسترس
# نباشه یا کند باشه، اصلاً جلوی ثبتِ لاگ یا بارگذاریِ صفحه رو نمی‌گیره —
# چون خودِ این تابع فقط توسط تسکِ پس‌زمینه‌ی لاگ صدا زده می‌شه، نه مسیرِ
# اصلیِ رندرِ صفحه.
_PRIVATE_IP_RE = re.compile(r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[0-1])\.|::1$|localhost$)")


async def _geolocate_ip(ip: str):
    if not ip or _PRIVATE_IP_RE.match(ip):
        return None, None, None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(
                f"http://ip-api.com/json/{ip}",
                params={"fields": "status,city,regionName,country"},
            )
        data = r.json()
        if data.get("status") == "success":
            return data.get("city"), data.get("regionName"), data.get("country")
    except Exception:
        logger.debug("Geolocation lookup failed for %s", ip, exc_info=True)
    return None, None, None


async def _log_access(request, path: str, allowed: bool):
    """ثبتِ یک «ورود» به پنل مدیر مدرسه، کاملاً در پس‌زمینه — درخواستِ
    اصلیِ کاربر هیچ‌وقت منتظرِ این تابع (و مخصوصاً منتظرِ درخواستِ شبکه‌ایِ
    ژئولوکیشن) نمی‌مونه."""
    try:
        ip = _client_ip(request)
        ua = request.headers.get("User-Agent", "")
        browser, os_name, device_type = _parse_user_agent(ua)
        dev_id = _device_id(ip, ua)
        city, region, country = await _geolocate_ip(ip)
        await db.log_principal_access(
            dev_id, ip, ua, browser, os_name, device_type, path, allowed,
            city=city, region=region, country=country,
        )
    except Exception:
        logger.exception("principal access logging failed")


def _require_auth(request):
    if not _authed(request):
        raise web.HTTPUnauthorized(
            text=json.dumps({"ok": False, "error": "unauthorized"}),
            content_type="application/json",
        )


async def _require_not_blocked(request):
    """اگه مدیر ارشد این دستگاهِ خاص رو از «پنل ادمین ← دستگاه‌ها» بلاک
    کرده باشه، حتی با کلیدِ درست هم نه صفحه باز می‌شه نه هیچ API‌ای جواب
    می‌ده — دقیقاً هم‌سطحِ چکِ خاموش/روشنِ کلِ پنل (_require_enabled)."""
    ip = _client_ip(request)
    ua = request.headers.get("User-Agent", "")
    dev_id = _device_id(ip, ua)
    if await db.is_principal_device_blocked(dev_id):
        raise web.HTTPForbidden(
            text=json.dumps({"ok": False, "error": "device_blocked"}),
            content_type="application/json",
        )


async def _require_enabled(request):
    """اگر مدیر ارشد این پنل رو از داخل ربات خاموش کرده باشه، حتی با کلیدِ
    درست هم نه صفحه باز میشه نه هیچ API‌ای جواب میده."""
    if not await _panel_enabled():
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"ok": False, "error": "panel_disabled"}),
            content_type="application/json",
        )


def _blocked_page():
    # ظاهر: همان لوگو و حالِ‌وهوای پنل اصلیِ تلگرام (status_page.py)
    html = render_status_page(
        "دسترسی مسدود است",
        "🚫 دسترسی مسدود است",
        "دسترسیِ این دستگاه به پنل مدیر مدرسه محدود شده است.<br>"
        "در صورتی که این اشتباه است، با پارسا کریمی در ارتباط باشید.",
    )
    return web.Response(text=html, content_type="text/html", charset="utf-8", status=403)


def _disabled_page():
    html = render_status_page(
        "پنل غیرفعال است",
        "مدیر عزیز!",
        "با عرض پوزش، پنل نظارت شما بر مسابقات به دلیل مشکل در اجرای سیستم از دسترس خارج گشته‌.<br>"
        "این مشکل به زودی برطرف خواهد شد.<br>"
        "در این فاصله با پارسا کریمی در ارتباط باشید.<br>"
        "با تشکر از پیگیری و شکیبایی شما🙏",
    )
    return web.Response(text=html, content_type="text/html", charset="utf-8", status=503)


def _json(data):
    resp = web.json_response(data, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))
    try:
        resp.enable_compression()   # فهرست‌های بلند (بازیکن‌ها/مسابقات) با gzip خیلی کم‌حجم‌تر می‌رسن
    except Exception:
        pass
    return resp


# ─── کش «تازه + کهنهٔ فوری» (stale-while-revalidate) روی خروجیِ APIها ──────
# لایهٔ database.py فقط خودِ جدول‌ها را ۱۵ ثانیه کش می‌کند؛ ولی بعد از انقضا، اولین
# کلیکِ مدیر مجبور بود منتظرِ چند رفت‌وبرگشتِ شبکه‌ای به Turso بماند. اینجا خروجیِ نهایی
# (JSON ساخته‌شده) نگه داشته می‌شود:
#   • تا _RESP_FRESH ثانیه: بدونِ هیچ کاری فوری برمی‌گردد.
#   • تا _RESP_STALE_MAX ثانیه: همان لحظه نسخهٔ قبلی برمی‌گردد و هم‌زمان در پس‌زمینه تازه می‌شود.
#   • قدیمی‌تر: مثلِ قبل منتظرِ داده‌ی تازه می‌ماند (تا هیچ‌وقت داده‌ی خیلی کهنه نمایش داده نشود).
# درخواست‌های هم‌زمانِ یک کلید در یک محاسبه ادغام می‌شوند (۵ کلیکِ پشتِ‌سرهم = ۱ کوئری).
_RESP_FRESH = 10
_RESP_STALE_MAX = 60
_resp_cache = {}        # key -> (text, fresh_until, stale_until)
_resp_refreshing = {}   # key -> asyncio.Task


def _swr_refresh(key, compute):
    import time
    task = _resp_refreshing.get(key)
    if task is not None and not task.done():
        return task

    async def run():
        try:
            value = await compute()
            now = time.monotonic()
            if len(_resp_cache) > 200:
                _resp_cache.clear()
            _resp_cache[key] = (value, now + _RESP_FRESH, now + _RESP_STALE_MAX)
            return value
        finally:
            _resp_refreshing.pop(key, None)

    task = asyncio.create_task(run())
    _resp_refreshing[key] = task
    # خطای تسکِ پس‌زمینه نباید «Task exception was never retrieved» بسازد
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    return task


async def _swr(key, compute):
    import time
    hit = _resp_cache.get(key)
    if hit:
        value, fresh_until, stale_until = hit
        now = time.monotonic()
        if now < fresh_until:
            return value
        if now < stale_until:
            _swr_refresh(key, compute)
            return value
    return await asyncio.shield(_swr_refresh(key, compute))


async def _swr_response(request, compute):
    """compute(request) باید یک web.Response (از _json) برگرداند؛ متنِ آن کش می‌شود."""
    key = request.path + "?" + "&".join(
        f"{k}={v}" for k, v in sorted(request.query.items()) if k != "k"
    )

    async def run():
        resp = await compute(request)
        return resp.text

    text = await _swr(key, run)
    out = web.Response(text=text, content_type="application/json", charset="utf-8")
    try:
        out.enable_compression()
    except Exception:
        pass
    return out


# ─── نفراتِ برتر: حالت (خودکار/دستی) از تلگرام تنظیم می‌شه ─────────────
# این پنل این کلیدها رو فقط می‌خونه، صرفاً برای نمایشِ فهرستِ نفراتِ برتر
# در تبِ «نفرات برتر». انتخابِ دستیِ نفراتِ برتر (وقتی حالت روی manual
# باشه) دیگه از این‌جا انجام نمی‌شه — به پنلِ ادمین‌ها منتقل شده.
TOP_PLAYERS_MODE_KEY = "top_players_mode"       # "auto" | "manual"
TOP_PLAYERS_MANUAL_KEY = "top_players_manual"   # JSON: [{"rank":1,"player_id":12}, ...]


async def _top_mode() -> str:
    val = await db.get_setting(TOP_PLAYERS_MODE_KEY, "auto")
    return val if val in ("auto", "manual") else "auto"


async def _manual_list() -> list:
    raw = await db.get_setting(TOP_PLAYERS_MANUAL_KEY, "[]")
    try:
        data = json.loads(raw) or []
    except Exception:
        data = []
    out = []
    for item in data:
        try:
            out.append({"rank": int(item["rank"]), "player_id": int(item["player_id"])})
        except Exception:
            continue
    out.sort(key=lambda x: x["rank"])
    return out


# ─── صفحه اصلی و فایل‌های استاتیک ────────────────────────────────
def _asset_version():
    try:
        mtimes = [
            os.path.getmtime(os.path.join(PANEL_DIR, f))
            for f in os.listdir(PANEL_DIR)
            if os.path.isfile(os.path.join(PANEL_DIR, f))
        ]
        return str(int(max(mtimes))) if mtimes else "0"
    except Exception:
        return "0"


async def _render_index(request):
    # هر بارگذاریِ صفحه‌ی اصلی = یک «ورود»؛ اول چکِ بلاک (چون دستگاهِ
    # بلاک‌شده اصلاً نباید حتی پوسته‌ی برنامه رو ببینه)، بعد ثبتِ لاگ در
    # پس‌زمینه — بدونِ اینکه درخواستِ کاربر منتظرِ ژئولوکیشن/نوشتنِ دیتابیس
    # بمونه.
    ip = _client_ip(request)
    ua = request.headers.get("User-Agent", "")
    dev_id = _device_id(ip, ua)
    if await db.is_principal_device_blocked(dev_id):
        asyncio.create_task(_log_access(request, request.path, allowed=False))
        return _blocked_page()
    asyncio.create_task(_log_access(request, request.path, allowed=_authed(request)))

    index_path = os.path.join(PANEL_DIR, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("{{V}}", _asset_version())
    # کلید همون‌طور که هست (حتی اگه خالی/غلط باشه) توی صفحه جاگذاری میشه؛
    # درخواست‌های API خودشون کلید غلط رو رد می‌کنن.
    html = html.replace("{{KEY}}", request.query.get("k", ""))
    # نسخه‌ی امن برای قرارگرفتن داخلِ href (لینکِ manifest): URL-encode + escape
    html = html.replace("{{KEYQ}}", html_lib.escape(quote(request.query.get("k", ""), safe=""), quote=True))
    resp = web.Response(text=html, content_type="text/html", charset="utf-8")
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@routes.get("/principal/{tail:.*}")
async def principal_static(request):
    if not await _panel_enabled():
        return _disabled_page()
    tail = request.match_info["tail"] or "index.html"
    path = os.path.normpath(os.path.join(PANEL_DIR, tail))
    if not path.startswith(PANEL_DIR):
        raise web.HTTPForbidden()
    if os.path.isdir(path) or not os.path.isfile(path) or path.endswith("index.html"):
        return await _render_index(request)
    resp = web.FileResponse(path)
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@routes.get("/principal")
async def principal_root(request):
    if not await _panel_enabled():
        return _disabled_page()
    return await _render_index(request)


@routes.get("/principal-assets/{tail:.*}")
async def principal_assets(request):
    if not await _panel_enabled():
        raise web.HTTPServiceUnavailable()
    tail = request.match_info["tail"]
    path = os.path.normpath(os.path.join(PANEL_DIR, tail))
    if not path.startswith(PANEL_DIR) or not os.path.isfile(path):
        raise web.HTTPNotFound()
    resp = web.FileResponse(path)
    if "v" in request.query:
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


# ─── کمکی‌ها ─────────────────────────────────────────────────────
def _result_fa(m):
    r = m["result"]
    if r == "white":
        return f"برد {m['white_name'] or '—'}"
    if r == "black":
        return f"برد {m['black_name'] or '—'}"
    if r == "draw":
        return "تساوی"
    if r == "cancelled":
        return "لغو شده"
    return "بدون نتیجه"


# ─── خلاصه کلی ────────────────────────────────────────────────────
async def _overview_stats_uncached() -> dict:
    # این ۵ کوئری کاملاً مستقل‌اند → هم‌زمان (gather)، نه پشتِ‌سرِهم.
    classes, players, tournaments, matches_all, matches_week = await asyncio.gather(
        db.get_all_classes(),
        db.get_all_players(),
        db.get_all_tournaments(),
        db.get_matches_by_filter("all"),
        db.get_matches_by_filter("week"),
    )
    classes = classes or []
    players = players or []
    tournaments = tournaments or []
    matches_all = matches_all or []
    matches_week = matches_week or []

    players_active = sum(1 for p in players if p and p["status"] == "active")
    decided = [m for m in matches_all if m and m["result"] in ("white", "black", "draw")]
    active_tournaments = [t for t in tournaments if t and t["status"] == "active"]
    return {
        "classes_total": len(classes),
        "players_total": len(players),
        "players_active": players_active,
        "matches_total": len(matches_all),
        "matches_this_week": len(matches_week),
        "matches_decided": len(decided),
        "tournaments_active": len(active_tournaments),
    }


async def _overview_stats() -> dict:
    """آمارِ خلاصه، کش‌شده؛ هم برای صفحهٔ «خانه» و هم برای پرامپتِ رهگشا (یک محاسبه برای هر دو)."""
    import json as _j
    text = await _swr("overview-stats", lambda: _stats_as_text())
    return _j.loads(text)


async def _stats_as_text() -> str:
    return json.dumps(await _overview_stats_uncached(), ensure_ascii=False)


@routes.get("/api/principal/overview")
async def principal_overview(request):
    _require_auth(request)
    await _require_not_blocked(request)
    await _require_enabled(request)
    return await _swr_response(request, _compute_overview)


async def _compute_overview(request):
    return _json({"ok": True, "stats": await _overview_stats()})


# ─── کلاس‌ها ──────────────────────────────────────────────────────
@routes.get("/api/principal/classes")
async def principal_classes(request):
    _require_auth(request)
    await _require_not_blocked(request)
    await _require_enabled(request)
    return await _swr_response(request, _compute_classes)


async def _compute_classes(request):
    # قبلاً برای هر کلاس یک کوئری جدا (get_players_by_class) زده می‌شد — یعنی
    # با N کلاس، N رفت‌وبرگشتِ شبکه‌ایِ اضافه، پشتِ‌سرِهم. چون get_all_players
    # همه‌ی بازیکن‌ها رو با class_id برمی‌گردونه (و الان کش هم می‌شه)، به‌جاش
    # یک‌بار همه رو می‌گیریم و خودمون توی پایتون بر اساس کلاس گروه‌بندی می‌کنیم.
    classes, players = await asyncio.gather(db.get_all_classes(), db.get_all_players())
    classes = classes or []
    players = players or []
    by_class = {}
    for p in players:
        by_class.setdefault(p["class_id"], []).append(p)
    out = []
    for c in classes:
        cplayers = by_class.get(c["id"], [])
        out.append({
            "name": c["name"],
            "player_count": len(cplayers),
            "wins": sum((p["wins"] or 0) for p in cplayers),
            "draws": sum((p["draws"] or 0) for p in cplayers),
            "losses": sum((p["losses"] or 0) for p in cplayers),
        })
    return _json({"ok": True, "classes": out})


# ─── بازیکنان ─────────────────────────────────────────────────────
@routes.get("/api/principal/players")
async def principal_players(request):
    _require_auth(request)
    await _require_not_blocked(request)
    await _require_enabled(request)
    return await _swr_response(request, _compute_players)


async def _compute_players(request):
    players = await db.get_all_players() or []
    out = []
    for p in players:
        games = (p["wins"] or 0) + (p["losses"] or 0) + (p["draws"] or 0)
        out.append({
            "full_name": p["full_name"],
            "class_name": p["class_name"] or "بدون کلاس",
            "status": p["status"],
            "games": games,
            "wins": p["wins"] or 0,
            "draws": p["draws"] or 0,
            "losses": p["losses"] or 0,
            "is_elite": bool(p["is_elite"]),
            "is_special": bool(p["is_special"]),
        })
    return _json({"ok": True, "players": out})


# ─── مسابقات ──────────────────────────────────────────────────────
@routes.get("/api/principal/matches")
async def principal_matches(request):
    _require_auth(request)
    await _require_not_blocked(request)
    await _require_enabled(request)
    return await _swr_response(request, _compute_matches)


async def _compute_matches(request):
    period = request.query.get("period", "all")
    matches = await db.get_matches_by_filter(period) or []
    out = []
    for m in matches:
        out.append({
            "white": m["white_name"] or "—",
            "black": m["black_name"] or "—",
            "result": m["result"],
            "result_fa": _result_fa(m),
            "match_date": m["match_date"],
            "created_at": m["created_at"],
        })
    return _json({"ok": True, "matches": out})


# ─── نفرات برتر (هفته/ماه/کل) ─────────────────────────────────────
async def _period_stats(period: str):
    """برای بازه‌ی داده‌شده، برای هر بازیکنی که مسابقه داشته آمار (بازی/برد/
    تساوی/باخت/امتیاز) حساب می‌کنه. پایه‌ی مشترکِ هم جدولِ خودکار و هم
    رتبه‌بندیِ پیشنهادیِ ربات (برای وقتی مدیر ارشد می‌خواد دستی انتخاب کنه)."""
    matches, players = await asyncio.gather(
        db.get_matches_by_filter(period), db.get_all_players()
    )
    matches = matches or []
    players = players or []
    meta = {
        p["id"]: {
            "full_name": p["full_name"],
            "class_name": p["class_name"] or "بدون کلاس",
            "status": p["status"],
        }
        for p in players
    }

    stats = {}
    for m in matches:
        if not m or m["result"] not in ("white", "black", "draw"):
            continue
        w_id, b_id = m["white_player_id"], m["black_player_id"]
        for pid in (w_id, b_id):
            stats.setdefault(pid, {"games": 0, "wins": 0, "draws": 0, "losses": 0, "score": 0.0})
        stats[w_id]["games"] += 1
        stats[b_id]["games"] += 1
        if m["result"] == "white":
            stats[w_id]["wins"] += 1
            stats[w_id]["score"] += 1
            stats[b_id]["losses"] += 1
        elif m["result"] == "black":
            stats[b_id]["wins"] += 1
            stats[b_id]["score"] += 1
            stats[w_id]["losses"] += 1
        else:
            stats[w_id]["draws"] += 1
            stats[w_id]["score"] += 0.5
            stats[b_id]["draws"] += 1
            stats[b_id]["score"] += 0.5
    return meta, stats


@routes.get("/api/principal/top")
async def principal_top(request):
    _require_auth(request)
    await _require_not_blocked(request)
    await _require_enabled(request)
    return await _swr_response(request, _compute_top)


async def _compute_top(request):
    mode = await _top_mode()

    if mode == "manual":
        # مدیر ارشد نفراتِ برتر رو دستی انتخاب کرده — بازه (هفته/ماه/کل)
        # اینجا معنی نداره، همیشه همون فهرستِ دستی (با آمارِ واقعیِ کل دوران
        # برای نمایش) و به ترتیبِ رتبه‌ای که خودش تعیین کرده برگردونده می‌شه.
        manual = await _manual_list()
        meta, stats = await _period_stats("all")
        rows = []
        for item in manual:
            pid = item["player_id"]
            m = meta.get(pid)
            if not m:
                continue
            s = stats.get(pid, {"games": 0, "wins": 0, "draws": 0, "losses": 0, "score": 0.0})
            rows.append({"full_name": m["full_name"], "class_name": m["class_name"], **s})
        return _json({"ok": True, "leaderboard": rows, "mode": "manual"})

    period = request.query.get("period", "week")
    meta, stats = await _period_stats(period)
    rows = []
    for pid, s in stats.items():
        m = meta.get(pid, {"full_name": "بازیکن حذف‌شده", "class_name": "—"})
        rows.append({"full_name": m["full_name"], "class_name": m["class_name"], **s})
    rows.sort(key=lambda r: (-r["score"], -r["wins"]))
    return _json({"ok": True, "leaderboard": rows[:50], "mode": "auto"})


# ─── روندها (نمودارها) ─────────────────────────────────────────────
@routes.get("/api/principal/trends")
async def principal_trends(request):
    _require_auth(request)
    await _require_not_blocked(request)
    await _require_enabled(request)
    return await _swr_response(request, _compute_trends)


async def _compute_trends(request):
    import turso_db as _a

    now = datetime.now()

    async with _a.connect(db.DB_PATH) as conn:
        conn.row_factory = _a.Row

        # این ۴ کوئری کاملاً مستقلن (فقط SELECT، هیچ‌کدوم به نتیجه‌ی بقیه
        # نیاز نداره)؛ قبلاً پشتِ‌سرِهم اجرا می‌شدن، الان هم‌زمان.
        daily_cur, week_cur, class_cur, result_cur = await asyncio.gather(
            conn.execute(
                """SELECT substr(created_at,1,10) as d, COUNT(*) as cnt
                   FROM matches WHERE created_at IS NOT NULL
                   GROUP BY d ORDER BY d DESC LIMIT 30"""
            ),
            conn.execute(
                "SELECT created_at FROM matches WHERE created_at >= ?",
                ((now - timedelta(days=56)).isoformat(),),
            ),
            conn.execute(
                """SELECT c.name as cname, COUNT(p.id) as cnt
                   FROM classes c LEFT JOIN players p ON p.class_id = c.id
                   GROUP BY c.id ORDER BY cnt DESC"""
            ),
            conn.execute(
                "SELECT result, COUNT(*) as cnt FROM matches WHERE result IS NOT NULL GROUP BY result"
            ),
        )
        daily_rows = await daily_cur.fetchall()
        week_rows = await week_cur.fetchall()
        class_rows = await class_cur.fetchall()
        result_rows = await result_cur.fetchall()

    # سطل‌بندی هفتگی (۸ هفته اخیر، از قدیم به جدید)
    buckets = [0] * 8
    for r in week_rows:
        try:
            dt = datetime.fromisoformat(r["created_at"])
        except Exception:
            continue
        idx = (now - dt).days // 7
        if 0 <= idx < 8:
            buckets[7 - idx] += 1
    week_labels = [(now - timedelta(days=7 * (7 - i))).strftime("%m-%d") for i in range(8)]
    matches_by_week = [{"label": week_labels[i], "value": buckets[i]} for i in range(8)]

    result_fa_map = {"white": "برد سفید", "black": "برد سیاه", "draw": "تساوی", "cancelled": "لغو شده"}

    return _json({
        "ok": True,
        "matches_by_day": [{"label": r["d"], "value": r["cnt"]} for r in reversed(daily_rows)],
        "matches_by_week": matches_by_week,
        "players_by_class": [{"label": r["cname"] or "بدون کلاس", "value": r["cnt"]} for r in class_rows],
        "results_distribution": [
            {"label": result_fa_map.get(r["result"], r["result"]), "value": r["cnt"]} for r in result_rows
        ],
    })


# ─── دستیار هوشمند (فقط مشاوره/راهنما — هیچ ابزار اجرایی‌ای نداره) ──
# این پنل عمداً کاملاً فقط‌خواندنی‌ست؛ دستیارش هم همین اصل رو رعایت می‌کنه:
# فقط درباره‌ی وضعیت فعلی (آمار، کلاس‌ها، بازیکن‌ها، مسابقات) توضیح می‌ده
# و مدیر رو توی خودِ پنل راهنمایی می‌کنه، هیچ تابعی برای تغییر داده نداره.
#
# نکته‌ی مهم (رفعِ یه باگِ قبلی): قبلاً دستیار هیچ «ابزاری» نداشت و فقط یک
# خلاصه‌ی آماریِ کلی (تعداد کلاس/بازیکن/مسابقه) بالای سرش بود؛ برای همین به
# ساده‌ترین سوال‌های طبیعی («این دو بازیکن فعال کیا هستن؟») می‌گفت دسترسی
# ندارم. الان چندتا ابزارِ فقط‌خواندنیِ محدود داره (پایین‌تر) تا بتونه واقعاً
# جواب بده — ولی عمداً هیچ ابزاری برای آمارِ فردیِ برد/باختِ یک بازیکن یا
# رتبه‌بندیِ «کدوم دانش‌آموز بهتره» نداره؛ اون فقط از تبِ «خانه» قابل دیدنه.
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY", "") or "").strip().strip('"\'').strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
ASSISTANT_MODEL_CHAIN = [GEMINI_MODEL, "gemini-3.6-flash", "gemini-2.5-flash-lite"]
ASSISTANT_MODEL_CHAIN = list(dict.fromkeys(ASSISTANT_MODEL_CHAIN))  # حذف تکراری با حفظ ترتیب
# نکته (۲۰۲۶-۰۹-۲۴، اصلاح‌شده): پیش‌فرضِ قبلی اینجا gemini-2.5-flash-lite بود که
# طبق گزارش‌های متعددِ کاربرا در فوروم رسمیِ گوگل (بهار/تابستانِ ۲۰۲۶) به‌شدت
# ناپایدار شده (۵۰۳ overloaded مکرر، نرخ موفقیتِ گزارش‌شده ~۳۵-۴۰٪، حتی یه ۴۰۴ی
# ناگهانی در ۹ ژوئیه، هفته‌ها زودتر از شاتدأونِ رسمیِ ۱۶ اکتبر). چون همیشه *اول*
# زنجیره بود، هر سوال از رهگشا توی این پنل باید یه ASSISTANT_REQUEST_TIMEOUT کامل
# رو روش تلف می‌کرد قبل از رسیدن به مدلِ بعدی. الان gemini-3.5-flash-lite (نسل ۳،
# minimal thinking) اول صفه؛ gemini-2.5-flash-lite رفته ته لیست.
ASSISTANT_REQUEST_TIMEOUT = 20  # قبلاً ۱۲ بود؛ کوتاه بودنش باعث ReadTimeout مکرر روی gemini-3.6-flash می‌شد
# خطاهای گذرای Gemini (شلوغیِ سرور / محدودیتِ نرخ) معمولاً با یک تلاشِ مجدد و کمی صبر
# برطرف می‌شن؛ قبلاً همون لحظه می‌رفتیم سراغِ مدلِ بعدی یا پیامِ خطا می‌دادیم.
ASSISTANT_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
ASSISTANT_RETRY_DELAY = 0.6  # ثانیه (قبلاً ۱.۲)
ASSISTANT_MAX_OUTPUT_TOKENS = 1024
ASSISTANT_MAX_HISTORY_TURNS = 6  # چند رفت‌وبرگشت آخر (برای اینکه هر بار کل تاریخچه از کلاینت زیاد نشه)
ASSISTANT_MAX_TOOL_HOPS = 4      # حداکثر چندبار پشتِ‌سرهم اجازه‌ی صدازدنِ تابع (فقط گزارش‌گیریه، نیازی به عدد بزرگ نیست)

ASSISTANT_ROLE_FA = {
    "tournament_manager": "مدیر مسابقات",
    "security_manager": "مدیر امنیتی",
}
ASSISTANT_TOURNAMENT_STATUS_FA = {"active": "فعال", "ended": "پایان‌یافته", "paused": "متوقف"}


# ─── ابزارهای فقط‌خواندنیِ دستیار ──────────────────────────────────
async def _tool_get_players(args: dict) -> str:
    """نام/کلاس/وضعیتِ بازیکنان — عمداً بدون آمار برد/باخت یا رتبه‌بندیِ فردی."""
    players = await db.get_all_players() or []
    search = str(args.get("search") or "").strip().lower()
    class_filter = str(args.get("class_name") or "").strip().lower()
    status_filter = str(args.get("status") or "").strip().lower()
    rows = []
    for p in players:
        cname = p["class_name"] or "بدون کلاس"
        status = p["status"] or ""
        if status_filter and status.lower() != status_filter:
            continue
        if class_filter and class_filter not in cname.lower():
            continue
        if search and search not in (p["full_name"] or "").lower() and search not in cname.lower():
            continue
        rows.append({"full_name": p["full_name"], "class_name": cname, "status": status})
    if not rows:
        return "هیچ بازیکنی با این مشخصات پیدا نشد."
    truncated = len(rows) > 60
    shown = rows[:60]
    lines = [f"- {r['full_name']} — {r['class_name']} ({'فعال' if r['status'] == 'active' else 'غیرفعال'})" for r in shown]
    out = f"تعداد نتایج: {len(rows)}\n" + "\n".join(lines)
    if truncated:
        out += "\n(فقط ۶۰ موردِ اول؛ فهرست کامل در تبِ «بازیکن‌ها»ی همین پنل است.)"
    return out


async def _tool_get_classes(args: dict) -> str:
    """آمار تجمیعیِ هر کلاس — مجاز چون رتبه‌بندیِ فردی نیست."""
    classes, players = await asyncio.gather(db.get_all_classes(), db.get_all_players())
    classes = classes or []
    players = players or []
    if not classes:
        return "هنوز کلاسی ثبت نشده."
    by_class = {}
    for p in players:
        by_class.setdefault(p["class_id"], []).append(p)
    lines = []
    for c in classes:
        cplayers = by_class.get(c["id"], [])
        wins = sum((p["wins"] or 0) for p in cplayers)
        draws = sum((p["draws"] or 0) for p in cplayers)
        losses = sum((p["losses"] or 0) for p in cplayers)
        lines.append(f"- {c['name']}: {len(cplayers)} بازیکن | {wins} برد، {draws} تساوی، {losses} باخت")
    return "آمار کلاس‌ها (مجموعِ نتایجِ همه‌ی بازیکنانِ هر کلاس):\n" + "\n".join(lines)


async def _tool_get_teams(args: dict) -> str:
    """آمار تجمیعیِ هر تیم — مجاز چون رتبه‌بندیِ فردی نیست."""
    team_mode = await db.get_setting("team_mode_enabled", "0")
    if team_mode != "1":
        return "حالت تیمی در حال حاضر در این مدرسه فعال نیست."
    teams = await db.get_all_teams() or []
    if not teams:
        return "هنوز هیچ تیمی ثبت نشده."
    async def _team_line(t):
        members, tstats = await asyncio.gather(db.get_team_members(t["id"]), db.get_team_stats(t["id"]))
        return (
            f"- {t['name']}: {len(members or [])} عضو | "
            f"{tstats['wins']} برد، {tstats['draws']} تساوی، {tstats['losses']} باخت"
        )
    # همهٔ تیم‌ها هم‌زمان (قبلاً تیم‌به‌تیم و پشتِ‌سرِهم)
    lines = list(await asyncio.gather(*[_team_line(t) for t in teams]))
    return "آمار تیم‌ها (بر اساسِ نتایجِ مسابقاتِ تیمی):\n" + "\n".join(lines)


async def _tool_get_tournaments(args: dict) -> str:
    tournaments = await db.get_all_tournaments() or []
    tournaments = [t for t in tournaments if t and t["status"] != "deleted"]
    if not tournaments:
        return "هیچ تورنمنتی ثبت نشده."
    lines = [f"- {t['name']}: {ASSISTANT_TOURNAMENT_STATUS_FA.get(t['status'], t['status'])}" for t in tournaments]
    return "\n".join(lines)


async def _tool_get_staff(args: dict) -> str:
    admins = [a for a in (await db.get_all_admins() or []) if a["is_active"]]
    names = await asyncio.gather(pishva_display(), *[admin_display(a) for a in admins])
    lines = [f"- مدیر ارشد: {names[0]}"]
    for a, name in zip(admins, names[1:]):
        role_fa = ASSISTANT_ROLE_FA.get(a["role"], a["role"])
        lines.append(f"- {role_fa}: {name}")
    return "\n".join(lines)


ASSISTANT_TOOL_DISPATCH = {
    "get_players": _tool_get_players,
    "get_classes": _tool_get_classes,
    "get_teams": _tool_get_teams,
    "get_tournaments": _tool_get_tournaments,
    "get_staff": _tool_get_staff,
}

ASSISTANT_TOOL_DECLARATIONS = [
    {
        "name": "get_players",
        "description": (
            "جست‌وجوی نام و کلاس و وضعیتِ بازیکنان بر اساسِ نام، نام کلاس، یا وضعیت (active/inactive). "
            "برای سوال‌هایی مثل «این بازیکن‌ها/دانش‌آموزها کیا هستن؟»، «بازیکن‌های کلاس دوم الف کیا هستن؟» "
            "یا «کدوم‌ها غیرفعالن؟» از همین استفاده کن. توجه: این تابع هیچ آمار برد/باخت یا رتبه‌ی فردی "
            "برنمی‌گردونه — چون نفراتِ برتر (رتبه‌بندیِ فردی) عمداً فقط در تبِ «خانه» قابل مشاهده است."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "بخشی از نام بازیکن یا کلاس (اختیاری)"},
                "class_name": {"type": "string", "description": "نام دقیق یا بخشی از نام کلاس (اختیاری)"},
                "status": {"type": "string", "description": "active یا inactive (اختیاری؛ خالی = همه)"},
            },
        },
    },
    {
        "name": "get_classes",
        "description": (
            "آمار مقایسه‌ایِ کلاس‌ها: تعداد بازیکن و مجموعِ برد/تساوی/باختِ همه‌ی بازیکنانِ هر کلاس. "
            "برای «کدوم کلاس برتره؟» یا «وضعیت کلاس‌ها چطوره؟» از این استفاده کن — چون آمارِ تجمیعیِ "
            "کلاس‌محوره، نه رتبه‌بندیِ فردی، مانعی برای پاسخ نداره."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_teams",
        "description": (
            "آمار مقایسه‌ایِ تیم‌ها (اگر حالتِ تیمی فعال باشه): تعدادِ عضو و مجموعِ نتایجِ مسابقاتِ تیمیِ "
            "هر تیم. برای «کدوم تیم بهتره؟» از این استفاده کن — این هم آمارِ تجمیعیِ تیم‌محوره، نه رتبه‌ی فردی."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_tournaments",
        "description": "فهرستِ تورنمنت‌ها به‌همراه وضعیتشان (فعال/پایان‌یافته/متوقف).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_staff",
        "description": (
            "فهرستِ مدیر ارشد و مدیرانِ فعالِ سیستم به‌همراه نقششان (مثلاً مدیر مسابقات، مدیر امنیتی) و "
            "نامِ نمایشی‌شان. برای «مدیر مسابقات کیه؟» یا سوال‌های مشابه درباره‌ی مدیرها از این استفاده کن."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
]


async def _dispatch_assistant_tool(name: str, args: dict) -> str:
    fn = ASSISTANT_TOOL_DISPATCH.get(name)
    if not fn:
        return "این ابزار در دسترس نیست."
    try:
        return await fn(args or {})
    except Exception as e:
        logger.exception(f"Principal assistant tool '{name}' failed")
        # نامِ خطای فنی فقط توی لاگ سرور می‌مونه؛ متنی که به مدل می‌رسه (و ممکنه به مدیر منتقل بشه)
        # عمداً غیرفنی‌ست تا مدیر با عبارت‌هایی مثل KeyError روبه‌رو نشه.
        return ("دریافت این اطلاعات با مشکل مواجه شد. بدون ذکر هیچ جزئیات فنی، مؤدبانه پوزش بخواه "
                "و از مدیر بخواه لحظاتی بعد مجدداً بپرسد.")

PANEL_GUIDE = (
    "راهنمای بخش‌های همین پنل (دقیقاً بر همین اساس راهنمایی کن، نه چیز دیگه‌ای):\n"
    "- «خانه»: خلاصه‌ی کلی — تعداد کلاس‌ها، بازیکن‌ها (فعال/کل)، مسابقات ثبت‌شده، "
    "مسابقات این هفته، مسابقات با نتیجه، تورنمنت‌های فعال، و ۵ نفر برتر همین هفته.\n"
    "- «کلاس‌ها»: فهرست کلاس‌ها با تعداد بازیکن هر کلاس و مجموع برد/تساوی/باخت آن کلاس.\n"
    "- «بازیکن‌ها»: فهرست همه‌ی بازیکنان با جستجو (نام/کلاس) و فیلتر همه/فعال؛ آمار بازی/برد/"
    "تساوی/باخت هرکدوم و ستاره‌ی ⭐ برای بازیکنان ویژه (elite).\n"
    "- «مسابقات»: فهرست مسابقات با جستجوی نام بازیکن و فیلتر بازه‌ی زمانی (همه/امروز/این هفته/این ماه).\n"
    "- «نفرات برتر»: رتبه‌بندی ۵ (یا بیشتر) نفر برتر؛ یا خودکار بر اساس امتیاز مسابقات هر بازه "
    "(هفته/ماه/کل)، یا در حالت دستی، فهرستی که مدیر ارشد شخصاً از تلگرام انتخاب کرده — این حالت "
    "فقط از تلگرام (تنظیمات ربات) قابل تغییره، نه از این پنل.\n"
    "- «روندها»: چهار نمودار — تعداد مسابقات ۳۰ روز اخیر (روزانه)، تعداد مسابقات ۸ هفته اخیر، "
    "توزیع بازیکنان بر اساس کلاس، و توزیع نتایج مسابقات (برد/تساوی/باخت/لغو).\n"
    "این پنل کاملاً فقط‌خواندنی است: مدیر مدرسه از اینجا هیچ داده‌ای را نمی‌تواند ثبت، ویرایش یا "
    "حذف کند؛ صرفاً نظارت و مشاهده. برای هرگونه تغییر واقعی (ثبت مسابقه، افزودن بازیکن، تغییر "
    "حالت نفرات برتر، ارسال اطلاعیه و…) باید از طریق مدیر ارشد یا ادمین‌های مدرسه در تلگرام اقدام شود."
)


def _assistant_system_prompt(overview_stats: dict) -> str:
    stats_line = (
        f"کلاس‌ها: {overview_stats.get('classes_total', 0)} | "
        f"بازیکنان: {overview_stats.get('players_total', 0)} "
        f"(فعال: {overview_stats.get('players_active', 0)}) | "
        f"مسابقات ثبت‌شده: {overview_stats.get('matches_total', 0)} | "
        f"مسابقات این هفته: {overview_stats.get('matches_this_week', 0)} | "
        f"مسابقات با نتیجه: {overview_stats.get('matches_decided', 0)} | "
        f"تورنمنت‌های فعال: {overview_stats.get('tournaments_active', 0)}"
    )
    # ترتیبِ عمدی: همهٔ متنِ ثابت اول، و ساعت/آمارِ متغیر در انتها. Gemini پیشوندِ مشترکِ
    # درخواست‌ها را به‌صورت خودکار کش می‌کند؛ اگر ساعت اولِ پرامپت بود، هر پیام پیشوندِ
    # متفاوتی می‌داشت و این کش هیچ‌وقت کار نمی‌کرد.
    return (
        "اسم تو «رهگشا» (Rahgosha) است؛ دستیار هوشمند «پنل مدیر مدرسه» در سامانه‌ی CMS (سیستم مدیریت مسابقات شطرنج مدرسه). "
        "هر وقت مدیر اسمت را پرسید یا خواست خودت را معرفی کنی، همیشه بگو: «من رهگشا، دستیار هوشمند LUX هستم.» "
        "خودت را هرگز محصول گوگل، جمینای یا هر شرکت/مدل دیگری معرفی نکن. "
        "دربارهٔ LUX (فقط اگر صریحاً درباره‌اش پرسیده شد؛ در حالت عادی و در معرفیِ خودت چیزِ بیشتری از این "
        "خودت اضافه نکن و از پیش پیرامونش صحبت نکن): LUX یک واحدِ مدیریتِ تحصیلی است که محمد پارسا کریمی "
        "برای خودش ساخته تا درس‌ها و برنامه‌ی تحصیلی‌اش را مدیریت کند و وضعیتِ خودش را بسنجد. در کنارش، هر "
        "وقت قرار باشد یک سیستمِ مدیریتی ساخته شود — مثلاً همین ساختارِ مدیریتِ مسابقاتِ شطرنج — همین واحد "
        "بودجه‌ی زمانی و راه‌حل‌های لازم را به محمد پارسا کریمی می‌دهد؛ در واقع سازنده‌ی تک‌تکِ بخش‌های این "
        "سامانه و خودِ تو (این هوشِ مصنوعی) محمد پارسا کریمی است. "
        "مخاطبت مدیر مدرسه است — با احترام کامل، مؤدبانه، گرم و صمیمی (نه رسمی و خشک، ولی همیشه با ادب) "
        "به فارسی صحبت کن. جمله‌ها کوتاه، روشن و مناسب صفحه‌ی موبایل باشند.\n\n"
        "نقش تو صرفاً «مشاوره و راهنمایی» است — نه اجرای هیچ کاری. تو هیچ ابزاری برای ثبت، ویرایش یا "
        "حذف چیزی نداری و نباید وانمود کنی که کاری را برای مدیر انجام داده‌ای یا خواهی داد. اگر مدیر "
        "خواستِ اجراییِ مشخصی داشت (مثلاً «فلان بازیکن رو حذف کن» یا «ساعت کاری رو ببند»)، مؤدبانه "
        "توضیح بده که این پنل فقط‌خواندنی‌ست و برای این کار باید با مدیر ارشد یا ادمین مدرسه در تلگرام "
        "هماهنگ شود؛ خودت هرگز چنین کاری را انجام‌شده اعلام نکن.\n\n"
        f"{PANEL_GUIDE}\n\n"
        "ابزارهایی که در اختیار داری (فقط برای مشاهده/گزارش، هیچ‌کدوم داده‌ای رو تغییر نمی‌دن): "
        "get_players (نام/کلاس/وضعیتِ بازیکنان، با جست‌وجو)، get_classes (آمار تجمیعیِ هر کلاس)، "
        "get_teams (آمار تجمیعیِ هر تیم، اگر فعال باشد)، get_tournaments (فهرست تورنمنت‌ها)، "
        "get_staff (مدیر ارشد و مدیرانِ فعال با نقششان). هر وقت جوابِ دقیقِ سوال نیاز به یکی از این‌ها "
        "داشت (مثلاً «این دو بازیکن فعال کیا هستن؟»، «کلاس‌ها رو مقایسه کن»، «مدیر مسابقات کیه؟»)، حتماً "
        "همون تابع رو صدا بزن و بر اساسِ نتیجه‌ی واقعی‌اش جواب بده — هیچ‌وقت اسم یا آماری رو از خودت "
        "حدس نزن یا نسازی. اگر تابعی نتیجه‌ای نداد یا خطا داد، همون رو صادقانه به مدیر بگو.\n\n"
        "محدودیتِ مهم درباره‌ی رتبه‌بندیِ فردیِ بازیکنان: تو هیچ ابزاری برای آمارِ بردوباختِ تک‌تکِ "
        "بازیکنان یا مقایسه‌ی «کدوم دانش‌آموز بهتره» نداری. اگر مدیر پرسید کدام دانش‌آموز بهتر است یا "
        "آمار فردیِ برد/باختِ یک بازیکن خاص را خواست، حدس نزن و از get_players هم برای این کار استفاده "
        "نکن (چون اصلاً چنین آماری نمی‌ده)؛ فقط مؤدبانه بگو این آمار عمداً صرفاً در تبِ «خانه»ی همین پنل "
        "(نفراتِ برتر) قابل مشاهده است. اما مقایسه‌ی «کدوم کلاس بهتره» یا «کدوم تیم بهتره» کاملاً مجازه، "
        "چون بر پایه‌ی آمارِ تجمیعیِ کلاس/تیمه نه رتبه‌بندیِ فردی — برای این دو حتماً از get_classes یا "
        "get_teams استفاده کن و بر اساسِ اعداد (نه حدس) بگو کدوم بهتره.\n\n"
        "اگر چیزی را نمی‌دانی یا خارج از حیطه‌ی این پنل است، صادقانه بگو که این اطلاعات را نداری، حدس نزن."
        f"\n\n— اطلاعاتِ لحظه‌ای —\n{now_context_for_ai()}\n\n"
        f"آمار خلاصه‌ی همین لحظه‌ی پنل:\n{stats_line}"
    )


async def _call_assistant_gemini(contents: list, tools=None, model_chain=None, system_instruction=None):
    """یک درخواست به Gemini می‌زند و (data, model) را برمی‌گرداند — data کل JSON پاسخ است
    (نه فقط متن)، چون ممکن است شاملِ یک functionCall باشد که دیسپچرِ بالای صفحه باید قبل
    از جوابِ نهایی پردازشش کند؛ model اسمِ همون مدلی‌ست که واقعاً جواب داده، تا صدازننده
    بتونه توی هاپ‌های بعدیِ همین درخواست اول از همون مدل شروع کنه (به‌جای اینکه هر بار
    دوباره هزینه‌ی رد شدن از مدل‌های از‌کار‌افتاده رو بپردازه). در خطا (None, None)
    برمی‌گردونه تا صدازننده پیامِ مناسب رو خودش انتخاب کنه.

    نکته‌ی مهم دربارهٔ سرعت: timeout هر تلاش عمداً کوتاهه (ASSISTANT_REQUEST_TIMEOUT)،
    چون این یک پنلِ تعاملیه، نه یک کارِ پس‌زمینه — اگه یک مدل جواب نده، بهتره زود از
    کنارش رد بشیم و مدلِ بعدی رو امتحان کنیم تا اینکه مدیرِ مدرسه دقیقه‌ها منتظرِ یک
    تایم‌اوتِ طولانی بمونه."""
    if not GEMINI_API_KEY:
        return None, None
    chain = model_chain or ASSISTANT_MODEL_CHAIN
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    last_empty = None
    last_empty_model = None
    # کلاینتِ مشترک و ماندگار (به‌جای ساختن یه AsyncClient تازه برای هر پیام) —
    # همون الگوی turso_db.py و ai_assistant.py، تا اتصالِ TCP/TLS با گوگل بینِ
    # درخواست‌های پیاپی keep-alive بمونه. timeout رو صریحاً per-request می‌دیم
    # چون کلاینتِ مشترک خودش timeout سراسری نداره.
    client = net_utils.get_gemini_client()
    for model in chain:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        is_gen3 = model.startswith("gemini-3")
        generation_config = {
            "maxOutputTokens": ASSISTANT_MAX_OUTPUT_TOKENS,
            "thinkingConfig": (
                {"thinkingLevel": "minimal"} if is_gen3 else {"thinkingBudget": 0}
            ),
        }
        # نسل ۳ جمینای (gemini-3.5-flash-lite / gemini-3.6-flash) دیگه پارامترِ
        # temperature رو قبول نمی‌کنه و به‌جای نادیده‌گرفتنِ ساده‌اش، مستقیماً
        # خطای 400 INVALID_ARGUMENT برمی‌گردونه (طبق مستنداتِ به‌روزشده‌ی گوگل،
        # ۲۰۲۶-۰۹-۲۱). چون این پارامتر همیشه توی payload بود، هر بار که زنجیره
        # به این دو مدل می‌رسید (مثلاً چون gemini-2.5-flash-lite از کار افتاده)
        # همون یک درخواست هم با 400 رد می‌شد — یعنی هر سه مدلِ زنجیره شکست
        # می‌خوردن و دستیار همیشه پیامِ «امکان پاسخ‌گویی وجود ندارد» رو نشون می‌داد.
        if not is_gen3:
            generation_config["temperature"] = 0.4
        payload = {
            "contents": contents,
            "generationConfig": generation_config,
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if tools:
            payload["tools"] = tools
        data = None
        for attempt in range(2):  # حداکثر یک تلاشِ مجدد برای خطاهای گذرا
            try:
                resp = await client.post(
                    url, headers=headers, json=payload, timeout=ASSISTANT_REQUEST_TIMEOUT
                )
                resp.raise_for_status()
                data = resp.json()
                break
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                logger.warning(
                    f"Principal assistant: model {model} failed ({code}) "
                    f"attempt {attempt + 1}: {e.response.text[:200]}"
                )
                if code in ASSISTANT_TRANSIENT_STATUS and attempt == 0:
                    await asyncio.sleep(ASSISTANT_RETRY_DELAY)
                    continue
                break
            except Exception as e:
                # شاملِ تایم‌اوت هم می‌شه (httpx.TimeoutException و مشابه)؛ عمداً برای
                # این‌ها تلاشِ مجدد نمی‌کنیم — اگه یک مدل تایم‌اوت می‌کنه، تلاشِ دوباره‌ی
                # فوری معمولاً همون تایم‌اوت رو تکرار می‌کنه و فقط وقتِ کاربر رو تلف می‌کنه.
                logger.warning(f"Principal assistant: model {model} failed ({e!r})")
                break
        if data is None:
            continue  # مدلِ بعدی
        if _extract_assistant_parts(data):
            return data, model
        # پاسخِ بدونِ متن/تابع (مثلاً قطع‌شدن به‌خاطر سقفِ توکن یا فیلترِ ایمنی) —
        # قبلاً همین‌جا به کاربر «متوجه نشدم» می‌گفتیم؛ اول مدلِ بعدی رو امتحان می‌کنیم.
        fr = ((data.get("candidates") or [{}])[0]).get("finishReason")
        logger.warning(f"Principal assistant: model {model} returned an empty reply (finishReason={fr})")
        last_empty = data
        last_empty_model = model
    if last_empty is not None:
        return last_empty, last_empty_model
    logger.error("Principal assistant: all models failed")
    return None, None


def _extract_assistant_parts(data: dict) -> list:
    try:
        return data["candidates"][0]["content"]["parts"]
    except Exception:
        return []


# ─── ذخیره‌ی گفتگوی دستیار (فقط برای دیدنِ مدیر ارشد توی پنل ادمین) ───
# هر خطایی توی ذخیره‌سازی فقط لاگ می‌شه و هرگز جلوی پاسخ‌گویی به مدیر رو نمی‌گیره.
async def _principal_chat_session(raw_sid, first_message: str):
    """session_id ارسالی از کلاینت رو اعتبارسنجی می‌کنه (باید واقعاً یک جلسه‌ی
    «principal» باشه، نه جلسه‌ی یکی از ادمین‌های تلگرام)؛ در غیر این صورت
    یک جلسه‌ی جدید می‌سازه و عنوانش رو از اولین پیام می‌ذاره."""
    try:
        sid = int(raw_sid) if raw_sid is not None else None
    except (TypeError, ValueError):
        sid = None
    try:
        if sid is not None:
            sess = await db.ai_get_session(sid)
            if sess and sess["role"] == db.AI_ROLE_PRINCIPAL:
                return sid
        sid = await db.ai_create_session(db.AI_PRINCIPAL_USER_ID, db.AI_ROLE_PRINCIPAL)
        await db.ai_set_session_title(sid, first_message)
        return sid
    except Exception:
        logger.exception("Principal assistant: could not open chat session")
        return None


async def _principal_chat_log(sid, sender: str, text: str):
    if sid is None:
        return
    try:
        await db.ai_add_message(sid, sender, text)
    except Exception:
        logger.exception("Principal assistant: could not save chat message")


_assistant_bg_tasks = set()


def _assistant_bg(coro):
    """اجرای کار در پس‌زمینه بدونِ معطل‌کردنِ پاسخ (ذخیرهٔ گفتگو در دیتابیس نباید جلوی جوابِ مدیر را بگیرد)."""
    task = asyncio.create_task(coro)
    _assistant_bg_tasks.add(task)
    task.add_done_callback(_assistant_bg_tasks.discard)
    return task


async def _assistant_open_session(raw_sid, message: str):
    sid = await _principal_chat_session(raw_sid, message)
    await _principal_chat_log(sid, "user", message)
    return sid


async def _assistant_log_many(sid, entries):
    for sender, text in entries:
        await _principal_chat_log(sid, sender, text)


@routes.post("/api/principal/assistant")
async def principal_assistant(request):
    _require_auth(request)
    await _require_not_blocked(request)
    await _require_enabled(request)

    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text=json.dumps({"ok": False, "error": "invalid_json"}))

    message = str(body.get("message", "")).strip()
    if not message:
        raise web.HTTPBadRequest(text=json.dumps({"ok": False, "error": "empty_message"}))
    if len(message) > 1500:
        message = message[:1500]

    # باز کردنِ جلسه + ذخیرهٔ پیامِ کاربر هم‌زمان با بقیهٔ کار (قبلاً چند رفت‌وبرگشتِ
    # پشتِ‌سرِ هم به دیتابیس قبل از شروعِ سوال از مدل بود).
    session_task = asyncio.create_task(_assistant_open_session(body.get("session_id"), message))

    raw_history = body.get("history") or []
    history = []
    if isinstance(raw_history, list):
        for turn in raw_history[-(ASSISTANT_MAX_HISTORY_TURNS * 2):]:
            role = turn.get("role")
            text = str(turn.get("text", "")).strip()
            if role in ("user", "model") and text:
                history.append({"role": role, "parts": [{"text": text[:1500]}]})
    while history and history[0]["role"] != "user":   # Gemini: نوبتِ اول باید user باشد
        history.pop(0)

    if not GEMINI_API_KEY:
        no_key_reply = "سرویس دستیار در حال حاضر در دسترس نیست. لطفاً موضوع را به مدیر سیستم اطلاع دهید."
        sid = await session_task
        _assistant_bg(_assistant_log_many(sid, [("system", no_key_reply)]))
        return _json({"ok": True, "reply": no_key_reply, "session_id": sid})

    stats = await _overview_stats()   # کش‌شده: معمولاً فوری

    system_prompt = _assistant_system_prompt(stats)
    contents = history + [{"role": "user", "parts": [{"text": message}]}]
    tools = [{"function_declarations": ASSISTANT_TOOL_DECLARATIONS}]

    reply = "متأسفانه در حال حاضر امکان پاسخ‌گویی وجود ندارد. لطفاً لحظاتی بعد مجدداً تلاش فرمایید."
    reply_ok = False  # فقط پاسخِ واقعیِ مدل «ai» ثبت می‌شه؛ پیام‌های خطا «system»
    log_entries = []  # بعد از ارسالِ پاسخ، در پس‌زمینه ذخیره می‌شوند
    # مدلی که توی هاپِ قبلیِ همین درخواست جواب داده رو برای هاپِ بعدی هم اول امتحان می‌کنیم.
    working_model = None
    for _hop in range(ASSISTANT_MAX_TOOL_HOPS):
        chain = ASSISTANT_MODEL_CHAIN
        if working_model and working_model in ASSISTANT_MODEL_CHAIN:
            chain = [working_model] + [m for m in ASSISTANT_MODEL_CHAIN if m != working_model]
        data, working_model = await _call_assistant_gemini(
            contents, tools, model_chain=chain, system_instruction=system_prompt
        )
        if data is None:
            break
        parts = _extract_assistant_parts(data)
        if not parts:
            reply = "پوزش می‌خواهم، منظور پرسش برایم روشن نشد. خواهشمندم آن را به شکل دیگری مطرح فرمایید."
            break

        fn_calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if fn_calls:
            # اگر مدل چند تابع را یک‌جا خواست (مثلاً کلاس‌ها + تورنمنت‌ها)، همه را هم‌زمان اجرا
            # می‌کنیم و همهٔ جواب‌ها را در «یک» هاپ برمی‌گردانیم (قبلاً فقط اولی جواب داده می‌شد).
            results = await asyncio.gather(*[
                _dispatch_assistant_tool(fc["name"], fc.get("args", {})) for fc in fn_calls
            ])
            response_parts = []
            for fc, result_text in zip(fn_calls, results):
                fargs = fc.get("args", {})
                log_entries.append(("tool", f"🔧 {fc['name']}({fargs}) → {str(result_text)[:400]}"))
                response_parts.append(
                    {"functionResponse": {"name": fc["name"], "response": {"result": result_text}}}
                )
            # همان parts ِ خودِ مدل (با thoughtSignatureِ نسل ۳) عیناً برگردانده می‌شود.
            contents.append({"role": "model", "parts": parts})
            contents.append({"role": "user", "parts": response_parts})
            continue

        reply = "".join(p.get("text", "") for p in parts).strip() or "باشه."
        reply_ok = True
        break
    else:
        reply = "این پرسش نیازمند بررسی چندمرحله‌ای است. خواهشمندم آن را در قالب پرسش‌های کوتاه‌تر مطرح فرمایید."

    sid = await session_task   # تا اینجا معمولاً خیلی وقت است تمام شده
    log_entries.append(("ai" if reply_ok else "system", reply))
    _assistant_bg(_assistant_log_many(sid, log_entries))
    return _json({"ok": True, "reply": reply, "session_id": sid})


# ─── گرم‌کردنِ رهگشا: وقتی مدیر پنجرهٔ رهگشا را باز می‌کند (قبل از اولین پیام) ───
_assistant_last_warm = 0.0


async def _warm_assistant():
    try:
        await _overview_stats()
        if GEMINI_API_KEY:
            # فقط برقراریِ اتصالِ TCP/TLS با گوگل (keep-alive)، تا اولین پیام هزینهٔ دست‌دادن را نپردازد.
            await net_utils.get_gemini_client().head("https://generativelanguage.googleapis.com/", timeout=5)
    except Exception:
        pass   # گرم‌کردن بهترین‌تلاش است؛ خطایش مهم نیست


@routes.get("/api/principal/assistant/warm")
async def principal_assistant_warm(request):
    import time
    global _assistant_last_warm
    await _guard(request)
    now = time.monotonic()
    if now - _assistant_last_warm > 15:
        _assistant_last_warm = now
        _assistant_bg(_warm_assistant())
    return _json({"ok": True})


# ─── اعلانات (زنگوله) ────────────────────────────────────────────
# ساخت/ویرایش/حذفِ اعلان فقط از پنل ادمینه؛ این‌جا فقط خوندنِ فهرست و
# علامت‌گذاریِ خوانده/خوانده‌نشده هست.
def _notif_out(r) -> dict:
    return {
        "id": r["id"],
        "title": r["title"],
        "body": r["body"],
        "created_at": r["created_at"],
        "created_ts": r["created_ts"],
        "updated_at": r["updated_at"],
        "is_read": bool(r["is_read"]),
        "read_at": r["read_at"],
    }


async def _guard(request):
    _require_auth(request)
    await _require_not_blocked(request)
    await _require_enabled(request)


@routes.get("/api/principal/notifications")
async def principal_notifications(request):
    await _guard(request)
    rows, summary = await asyncio.gather(
        db.get_principal_notifications(), db.get_principal_notification_summary()
    )
    return _json({
        "ok": True,
        "items": [_notif_out(r) for r in (rows or [])],
        "unread": summary["unread"],
        "latest_id": summary["latest_id"],
    })


@routes.get("/api/principal/notifications/summary")
async def principal_notifications_summary(request):
    await _guard(request)
    summary = await db.get_principal_notification_summary()
    return _json({"ok": True, **summary})


@routes.post("/api/principal/notifications/read")
async def principal_notifications_read(request):
    """body: {"ids": [1,2]} یا {"all": true} — و اختیاری {"read": false} برای
    برگرداندن به «خوانده‌نشده»."""
    await _guard(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    read = body.get("read", True) is not False
    if body.get("all") is True:
        await db.set_principal_notifications_read(None, read)
    else:
        try:
            ids = sorted({int(i) for i in (body.get("ids") or [])})[:500]
        except Exception:
            return _json({"ok": False, "error": "invalid_input"})
        if not ids:
            return _json({"ok": False, "error": "invalid_input"})
        await db.set_principal_notifications_read(ids, read)
    summary = await db.get_principal_notification_summary()
    return _json({"ok": True, **summary})


# ─── Web Push: کلیدِ عمومی + ثبتِ اشتراکِ دستگاه ─────────────────────
@routes.get("/api/principal/push/key")
async def principal_push_key(request):
    await _guard(request)
    import push_notify
    if not push_notify.is_available():
        return _json({"ok": True, "available": False})
    return _json({"ok": True, "available": True, "public_key": await push_notify.get_public_key()})


@routes.post("/api/principal/push/subscribe")
async def principal_push_subscribe(request):
    await _guard(request)
    try:
        body = await request.json()
        sub = body.get("subscription") or {}
        endpoint = str(sub.get("endpoint") or "")
        keys = sub.get("keys") or {}
        p256dh = str(keys.get("p256dh") or "")
        auth = str(keys.get("auth") or "")
        open_url = str(body.get("url") or "/principal")
    except Exception:
        return _json({"ok": False, "error": "invalid_input"})
    if (not endpoint.startswith("https://") or len(endpoint) > 1000
            or not p256dh or not auth or len(p256dh) > 200 or len(auth) > 100):
        return _json({"ok": False, "error": "invalid_subscription"})
    # آدرسی که با کلیک روی اعلان باز می‌شه فقط می‌تونه خودِ همین پنل باشه.
    if not open_url.startswith("/principal") or len(open_url) > 500:
        open_url = "/principal"

    ip = _client_ip(request)
    ua = request.headers.get("User-Agent", "")
    await db.save_push_subscription(endpoint, p256dh, auth, open_url, ua[:300], _device_id(ip, ua))
    return _json({"ok": True})


# ─── فایل‌های PWA/Service Worker ─────────────────────────────────────
# service worker باید از مسیری سرو بشه که scope ِ «/principal» رو پوشش بده؛
# فایل‌های /principal-assets/ این اجازه رو ندارن، پس مسیرِ جدا داره.
@routes.get("/principal-sw.js")
async def principal_service_worker(request):
    resp = web.FileResponse(os.path.join(PANEL_DIR, "sw.js"))
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    resp.headers["Service-Worker-Allowed"] = "/principal"
    return resp


_icon_bytes = None


def _logo_bytes():
    """لوگوی پروژه (webp) داخلِ style.css به‌صورت data-URI هست؛ همون رو برای
    آیکونِ اعلان/برنامه سرو می‌کنیم تا فایلِ تصویریِ تازه‌ای لازم نباشه."""
    global _icon_bytes
    if _icon_bytes is None:
        try:
            with open(os.path.join(PANEL_DIR, "style.css"), "r", encoding="utf-8") as f:
                m = re.search(r"data:image/webp;base64,([A-Za-z0-9+/=]+)", f.read())
            _icon_bytes = base64.b64decode(m.group(1)) if m else b""
        except Exception:
            _icon_bytes = b""
    return _icon_bytes


@routes.get("/principal-icon.webp")
async def principal_icon(request):
    data = _logo_bytes()
    if not data:
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type="image/webp",
                        headers={"Cache-Control": "public, max-age=86400"})


@routes.get("/principal-manifest.webmanifest")
async def principal_manifest(request):
    """برای نصبِ پنل روی صفحه‌ی اصلیِ گوشی (روی آیفون، اعلانِ وب فقط برای
    برنامه‌ی نصب‌شده کار می‌کنه). کلید در start_url می‌آد تا برنامه‌ی نصب‌شده
    مستقیم باز بشه."""
    if not _authed(request):
        raise web.HTTPUnauthorized()
    key = quote(request.query.get("k", ""), safe="")
    data = {
        "name": "پنل مدیر مدرسه",
        "short_name": "پنل مدیر",
        "lang": "fa",
        "dir": "rtl",
        "start_url": f"/principal?k={key}",
        "scope": "/principal",
        "display": "standalone",
        "background_color": "#130a0b",
        "theme_color": "#a3121b",
        "icons": [{"src": "/principal-icon.webp", "sizes": "144x144", "type": "image/webp", "purpose": "any"}],
    }
    return web.Response(text=json.dumps(data, ensure_ascii=False),
                        content_type="application/manifest+json", charset="utf-8",
                        headers={"Cache-Control": "no-cache"})


def register_principal_routes(app: web.Application):
    """این تابع رو از game_server.py صدا بزن تا مسیرهای پنل مدیر مدرسه
    به همون اپلیکیشنِ aiohttp اضافه بشن (دقیقاً مثل register_panel_routes)."""
    app.add_routes(routes)
    logger.info("Principal panel routes registered at /principal")
