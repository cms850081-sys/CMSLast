"""
hub.py — «پنل من»: مینی‌اپِ تلگرامیِ مشترکِ مدیران و مدیر ارشد.

برخلافِ admin_panel.py (رمزعبور در مرورگر) و principal_panel.py (کلید در
URL)، این یک Telegram Mini App واقعی‌ست: از همان دکمه‌ی کنارِ چتِ ربات
(«پنل من» — menu button) باز می‌شود و احرازِ هویتش initData ِ امضاشده‌ی
تلگرام است (همان الگوریتمِ game_server._verify_init_data، برای شطرنجِ
زنده) — نه رمز، نه کلید در URL.

دسترسی: initData باید معتبر باشد (۴۰۱ اگر نه) و کاربر باید یا مدیرِ
فعال در جدولِ admins باشد یا خودِ PISHVA_ID باشد (۴۰۳ اگر نه). PISHVA_ID
ردیفی در admins ندارد — در کل پروژه با ثابتِ config.PISHVA_ID شناخته
می‌شود، برای همین این ماژول همه‌جا این حالت را جدا مدیریت می‌کند.

نوشتن روی دیتابیس فقط در یک مسیر: PUT /hub/api/profile (هرکس فقط
پروفایلِ خودش را می‌نویسد). بقیه‌ی مسیرها فقط SELECT هستند.

سوارشدن روی سرور: دقیقاً مثلِ admin_panel/principal_panel — نه سرورِ
جدا، بلکه register_hub_routes(app) از game_server.start_game_server
صدا زده می‌شود.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime
from urllib.parse import parse_qsl

from aiohttp import web

import database as db
import elo
import hub_caps
from config import (BOT_TOKEN, PISHVA_ID, ROLE_TOURNAMENT_MANAGER,
                    ROLE_SECURITY_MANAGER)

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

HUB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hub")
INIT_DATA_MAX_AGE = 24 * 3600  # ثانیه، هم‌راستا با game_server.py

# نقش‌هایی که اجازه‌ی ورود به هاب را دارند. مدیر ارشد (PISHVA_ID) جدا و با
# آی‌دی چک می‌شود (ردیفی در admins ندارد)، پس اینجا فقط دو نقشِ دیگر است.
# هر مقدارِ ناشناخته‌ای در ستونِ role → ۴۰۳ (لیستِ سفید، نه لیستِ سیاه).
HUB_ALLOWED_ROLES = frozenset({ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER})


# ─── احرازِ هویتِ initData تلگرام ───────────────────────────────────
def _verify_init_data(init_data: str):
    """همان الگوریتمِ رسمیِ WebApp تلگرام؛ عیناً هم‌راستا با
    game_server._verify_init_data تا رفتارِ دو مینی‌اپ یکی باشد."""
    if not init_data or not BOT_TOKEN:
        return None
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    recv_hash = pairs.pop("hash", None)
    if not recv_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, recv_hash):
        return None
    auth_date = int(pairs.get("auth_date", "0"))
    if time.time() - auth_date > INIT_DATA_MAX_AGE:
        return None
    user_raw = pairs.get("user")
    if not user_raw:
        return None
    try:
        return json.loads(user_raw)
    except Exception:
        return None


def _err(status_cls, code, **extra):
    payload = {"ok": False, "error": code}
    payload.update(extra)
    return status_cls(text=json.dumps(payload, ensure_ascii=False),
                       content_type="application/json")


async def _gate_or_raise():
    """اگر یکی از حالت‌های قفل (تعمیر/آپدیت/خطرناک/APS/خاموشیِ ربات/ساعتِ کاری)
    فعال باشد، مدیرِ غیرِ ارشد ۴۰۳ با دلیلِ دقیق می‌گیرد؛ مدیر ارشد هرگز."""
    reason = await hub_caps.gate_reason()
    if reason:
        title, text = hub_caps.GATE_MESSAGES[reason]
        raise _err(web.HTTPForbidden, "locked", reason=reason, title=title, message=text)


async def _identify(request):
    """initData را از هدرِ X-Tg-Init-Data می‌خواند و کاربر را برمی‌گرداند.
    فقط تشخیصِ هویت — چک نمی‌کند که ادمین است یا نه (آن کارِ _require_admin
    است). خروجی: dict خامِ user تلگرام، یا None."""
    init_data = request.headers.get("X-Tg-Init-Data", "")
    return _verify_init_data(init_data)


async def _require_admin(request):
    """۴۰۱ اگر initData نامعتبر/خالی باشد؛ ۴۰۳ اگر کاربرِ معتبرِ تلگرام
    باشد ولی نه پیشوا، نه مدیرِ فعالِ یکی از نقش‌های مجاز (مسئول مسابقات /
    مسئول انتظامات)، یا در لیست بلاک باشد.
    برمی‌گرداند: (is_pishva, admin_row_or_None, tg_user).

    هویت فقط از initData امضاشده‌ی تلگرام (HMAC با توکنِ ربات) می‌آید؛ آی‌دی
    یا یوزرنیمِ ربات به‌تنهایی هیچ دسترسی‌ای نمی‌دهد."""
    user = await _identify(request)
    if not user or "id" not in user:
        raise _err(web.HTTPUnauthorized, "unauthorized")
    try:
        uid = int(user["id"])
    except (TypeError, ValueError):
        raise _err(web.HTTPUnauthorized, "unauthorized")
    if uid == PISHVA_ID:
        # اسمِ تلگرامِ مدیر ارشد هم مثلِ بقیه‌ی مدیرها همین‌جا هم‌گام می‌شه
        # (قبلاً این بخش کلاً برای پیشوا رد می‌شد، برای همین با تغییرِ
        # اسم/عکسِ تلگرام، وب‌اپ هیچ‌وقت پروفایلِ او را آپدیت نمی‌کرد؛
        # عکس چون هرلحظه زنده از تلگرام proxy می‌شود مشکلی نداشت، فقط اسم).
        try:
            tg_name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
            await asyncio.gather(db.sync_pishva_identity(tg_name), hub_caps.prewarm_auth_settings())
        except Exception:
            logger.exception("hub: sync_pishva_identity failed for %s", uid)
        return True, None, user
    # سه خوانشِ مستقل هم‌زمان (کشِ سرد = یک موج، نه ۴-۵ موجِ پشتِ‌سرِهم). ترتیبِ «بررسی» همان قبلی ست.
    blocked, admin, _ = await asyncio.gather(
        db.get_blocked_user(uid), db.get_admin(uid), hub_caps.prewarm_auth_settings())
    if blocked:
        raise _err(web.HTTPForbidden, "forbidden")
    if not admin or not admin["is_active"] or admin["role"] not in HUB_ALLOWED_ROLES:
        raise _err(web.HTTPForbidden, "forbidden")
    if not await db.can_use_hub(uid):
        raise _err(web.HTTPForbidden, "forbidden")
    await _gate_or_raise()
    # اسمِ تلگرامِ مدیر ممکنه بعد از ثبت‌نام عوض شده باشه؛ همین‌جا هم‌گامش کن.
    try:
        tg_name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
        await db.sync_admin_identity(uid, user.get("username"), tg_name)
        admin = await db.get_admin(uid) or admin
    except Exception:
        logger.exception("hub: sync_admin_identity failed for %s", uid)
    return False, admin, user


async def _require_cap(request, *anyof):
    """مثلِ _require_admin ولی علاوه بر آن، حداقل یکی از قابلیت‌های anyof را می‌خواهد
    (۴۰۳ با error=no_cap در غیرِ این صورت). خروجی: (is_pishva, admin, user, caps, feats)."""
    is_pishva, admin, user = await _require_admin(request)
    caps, feats = await hub_caps.compute_caps(is_pishva, admin)
    if anyof and not any(c in caps for c in anyof):
        raise _err(web.HTTPForbidden, "no_cap", message="این قابلیت برای شما فعال نیست.")
    return is_pishva, admin, user, caps, feats


def _json(data):
    return web.json_response(data, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


# ─── کمکی‌های نمایش/تبدیل ───────────────────────────────────────
def _role_label(role: str) -> str:
    return {
        "pishva": "مدیر ارشد",
        "tournament_manager": "مسئول مسابقات",
        "security_manager": "مسئول انتظامات",
    }.get(role, "مدیر")


def _avatar_path(telegram_id) -> str:
    # یک کوئری‌استرینگِ زمانی (هم‌بازه با TTLِ ۶۰ثانیه‌ایِ خودِ کشِ آواتار
    # در game_server) اضافه شده. بدونِ این، URL همیشه دقیقاً یکی بود
    # («/api/avatar/123») و وب‌ویوِ تلگرام (که کشِ عکس‌هاش معمولاً خیلی
    # تهاجمی‌تر از هدرِ استانداردِ Cache-Control عمل می‌کنه) همون اولین
    # عکسی که دیده بود رو برای همیشه نشون می‌داد، حتی بعد از عوض‌شدنِ
    # واقعیِ عکسِ پروفایلِ تلگرام. حالا هر ۶۰ ثانیه URL عوض می‌شه، پس
    # مرورگر/وب‌ویو مجبور می‌شه یک درخواستِ واقعاً تازه بزنه.
    if not telegram_id:
        return None
    bucket = int(time.time() // 60)
    return f"/api/avatar/{telegram_id}?t={bucket}"


def _admin_details(admin) -> list:
    try:
        raw = json.loads(admin["details"] or "[]")
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [{"k": str(d["k"])[:20], "v": str(d["v"])[:60]}
            for d in raw[:5] if isinstance(d, dict) and d.get("k") and d.get("v")]


async def _me_public(is_pishva, admin):
    """شکلِ عمومیِ «خودِ من» — چه پیشوا باشم چه مدیرِ معمولی — دقیقاً
    فیلدهایی که profileBody در hub.js انتظار دارد."""
    if is_pishva:
        p = await db.get_pishva_profile_fields()
        my = await db.get_admin_match_stats(PISHVA_ID)
        return {
            "id": PISHVA_ID, "name": p["name"], "role": "pishva", "role_label": "مدیر ارشد",
            "title": p["title"], "city": p["city"], "bio": p["bio"], "details": p["details"],
            "avatar": _avatar_path(PISHVA_ID), "joined_at": None, "last_active": None, "my": my,
        }
    my = await db.get_admin_match_stats(admin["telegram_id"])
    return {
        "id": admin["telegram_id"], "name": admin["display_name"] or admin["full_name"] or "مدیر",
        "role": admin["role"], "role_label": _role_label(admin["role"]),
        "title": admin["title"] or "", "city": admin["city"] or "", "bio": admin["bio"] or "",
        "details": _admin_details(admin),
        "avatar": _avatar_path(admin["telegram_id"]),
        "joined_at": admin["joined_at"], "last_active": admin["last_active"], "my": my,
    }


def _teammate_public(admin) -> dict:
    return {
        "id": admin["telegram_id"], "name": admin["display_name"] or admin["full_name"] or "مدیر",
        "role": admin["role"], "role_label": _role_label(admin["role"]),
        "title": admin["title"] or "", "city": admin["city"] or "", "bio": admin["bio"] or "",
        "details": _admin_details(admin),
        "avatar": _avatar_path(admin["telegram_id"]),
        "joined_at": admin["joined_at"], "last_active": admin["last_active"],
    }


async def _pishva_teammate_public():
    p = await db.get_pishva_profile_fields()
    return {
        "id": PISHVA_ID, "name": p["name"], "role": "pishva", "role_label": "مدیر ارشد",
        "title": p["title"], "city": p["city"], "bio": p["bio"], "details": p["details"],
        "avatar": _avatar_path(PISHVA_ID), "joined_at": None, "last_active": None,
    }


# ─── بوت‌استرپ (خانه، آیکونِ تبِ پروفایل، تیمِ مدیران) ────────────────
@routes.get("/hub/api/bootstrap")
async def hub_bootstrap(request):
    is_pishva, admin, user, caps, feats = await _require_cap(request)
    now_iso = datetime.now().isoformat()
    has_match_caps = any(c in caps for c in ("match_create", "match_edit", "match_delete", "predictions"))
    # «برترین‌ها» دیگر بر پایه‌ی Elo نیست؛ بر پایه‌ی قانونِ اصلیِ rankings.py است
    # (همان منبعِ مشترکِ ربات و تبِ «نفرات برتر»)، پس شرطش هم دیدنِ بازیکنان است.
    want_top = "players_view" in caps

    async def _none():
        return None

    async def _standings():
        import rankings
        return await rankings.build_standings()

    # همه‌ی خوانش‌های مستقل «هم‌زمان» (قبلاً ~۱۰ خوانشِ پشتِ‌سرِهم؛ با تأخیرِ
    # شبکه‌ی Turso هر کدام چند ده تا چند صد میلی‌ثانیه بود).
    (all_players, tours, m_summary, trend, me, active_admins,
     standings, pishva_mate, sys_vals) = await asyncio.gather(
        db.get_all_players(),
        db.get_tournaments_with_counts() if has_match_caps else _none(),
        db.get_hub_matches_summary() if has_match_caps else _none(),
        db.get_hub_trend(7) if has_match_caps else _none(),
        _me_public(is_pishva, admin),
        db.get_active_admins(),
        _standings() if want_top else _none(),
        _pishva_teammate_public() if not is_pishva else _none(),
        db.get_settings_with_defaults(
            {"system_status": "normal", "repair_mode": "0", "bot_update_mode": "0"}) if is_pishva else _none(),
    )

    active_players = [p for p in all_players if (p["status"] or "active") == "active"]
    elite_n = sum(1 for p in active_players if p["is_elite"])
    special_n = sum(1 for p in active_players if p["is_special"])

    if has_match_caps:
        active_tours = sum(1 for t in tours if t["status"] == "active")
    else:
        tours, active_tours = [], 0
        m_summary = {"pending": 0, "oldest_pending_days": 0, "done_today": 0}
        trend = {"days": [], "mix": {"white": 0, "black": 0, "draw": 0}}

    top = _top_players(standings) if (want_top and standings) else []

    # تیمِ مدیران: پیشوا همیشه اول (اگر خودِ بیننده پیشوا نیست)، بعد بقیه‌ی
    # مدیرانِ فعال بجز خودِ بیننده.
    team = []
    if not is_pishva:
        team.append(pishva_mate)
    for a in active_admins:
        if is_pishva or a["telegram_id"] != admin["telegram_id"]:
            team.append(_teammate_public(a))

    system = None
    if is_pishva:
        system = {"status": sys_vals["system_status"],
                  "repair": sys_vals["repair_mode"] == "1",
                  "update": sys_vals["bot_update_mode"] == "1"}

    return _json({
        "now": now_iso,
        "caps": sorted(caps),
        "features": feats,
        "system": system,
        "me": me,
        "team": team,
        "summary": {
            "players": {"active": len(active_players), "elite": elite_n, "special": special_n},
            "tournaments": {"active": active_tours, "total": len(tours)},
            "matches": m_summary,
        },
        "top": top,
        "trend": trend,
        "tournaments": tours,
    })


def _top_players(standings, limit=None):
    """نفرات برتر کارتِ «برترین‌ها»ی خانه، دقیقاً بر پایه‌ی قانونِ اصلیِ rankings.py:
      ۱) امتیاز (برد=۱، مساوی=۰٫۵) ← ۲) فقط در برابریِ امتیاز: ویژه > برتر > عادی
      ← ۳) اخطار (ویژه‌ها معاف) ← تعدادِ مسابقات ← سختیِ حریف.
    هیچ ترتیبِ جداگانه‌ای اینجا ساخته نمی‌شود؛ فقط خروجیِ rankings.build_standings خوانده می‌شود،
    تا ربات، تبِ «نفرات برتر» و کارتِ خانه همیشه یک لیستِ واحد را نشان بدهند."""
    import rankings
    n = limit or rankings.TOP_OVERALL_N
    out = []
    for r in (standings.get("overall") or [])[:n]:
        out.append({
            "id": r["id"], "name": r["full_name"], "cls": r["class_name"], "pos": r["pos"],
            "score": r["score"], "games": r["games"],
            "w": r["wins"], "d": r["draws"], "l": r["losses"],
            "elite": bool(r["is_elite"]), "special": bool(r["is_special"]),
        })
    return out


# ─── بازیکنان ────────────────────────────────────────────────────
@routes.get("/hub/api/players")
async def hub_players(request):
    _p, _a, _u, caps, _f = await _require_cap(request, "players_view", "match_create", "match_edit", "player_register", "match_scan")
    async def _elo_map():
        await elo.ensure_elo_table()
        return await db.get_all_player_elo()
    all_players, elo_map = await asyncio.gather(db.get_all_players(), _elo_map())
    cols = ["id", "name", "cls", "elo", "w", "d", "l", "warn", "elite", "special", "status", "games"]
    show_elo = "elo" in caps
    rows = []
    for p in all_players:
        e = elo_map.get(p["id"])
        rating = (e["rating"] if e else elo.ELO_DEFAULT) if show_elo else None
        w, d, l = p["wins"] or 0, p["draws"] or 0, p["losses"] or 0
        rows.append([
            p["id"], p["full_name"], p["class_name"] or "", round(rating) if rating is not None else None,
            w, d, l, p["warnings"] or 0,
            1 if p["is_elite"] else 0, 1 if p["is_special"] else 0,
            p["status"] or "active", w + d + l,
        ])
    return _json({"cols": cols, "rows": rows})


@routes.get("/hub/api/player/{id}")
async def hub_player_detail(request):
    await _require_cap(request, "players_view")
    try:
        pid = int(request.match_info["id"])
    except (TypeError, ValueError):
        raise web.HTTPBadRequest()
    detail = await db.get_player_hub_detail(pid)
    rank = detail["rank_row"]["rank"] if detail["rank_row"] else None
    e = await elo.get_player_elo(pid)
    matches = []
    for m in detail["matches"]:
        matches.append({
            "wid": m["white_player_id"], "w": m["white_name"] or "؟", "b": m["black_name"] or "؟",
            "res": m["result"], "t": m["t_name"], "date": (m["match_date"] or "")[:10],
        })
    return _json({
        "elo": {"peak": round(e["peak_rating"])} if e["games_played"] else None,
        "rank": rank,
        "matches": matches,
    })


# ─── مسابقات ─────────────────────────────────────────────────────
@routes.get("/hub/api/tournament/{id}")
async def hub_tournament_detail(request):
    await _require_cap(request, "match_create", "match_edit", "match_delete", "predictions")
    try:
        tid = int(request.match_info["id"])
    except (TypeError, ValueError):
        raise web.HTTPBadRequest()
    data = await db.get_tournament_standings(tid)
    standings = []
    for name, s in data["standings"]:
        standings.append({
            "name": name, "p": s["played"], "w": s["win"], "d": s["draw"], "l": s["loss"], "pts": s["points"],
        })
    rows = await db.get_tournament_matches_named(tid)
    matches = [{
        "id": r["id"], "wid": r["white_player_id"], "bid": r["black_player_id"],
        "w": r["white_name"] or "؟", "b": r["black_name"] or "؟",
        "res": r["result"], "date": (r["match_date"] or "")[:10],
    } for r in rows]
    return _json({"standings": standings, "matches": matches})


# ─── پروفایل (تنها مسیرِ نوشتنی) ────────────────────────────────────
@routes.post("/hub/api/profile")
async def hub_update_profile(request):
    is_pishva, admin, user = await _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    display_name = body.get("display_name", "")
    title = body.get("title", "")
    city = body.get("city", "")
    bio = body.get("bio", "")
    details = body.get("details", [])
    if not isinstance(details, list):
        details = []

    if is_pishva:
        p = await db.update_pishva_profile(display_name, title, city, bio, details)
    else:
        p = await db.update_admin_profile(admin["telegram_id"], display_name, title, city, bio, details)

    return _json({"profile": p})


# ─── دکمه‌ی «پنل من» کنارِ چت (menu button) ──────────────────────────
_BOT = None  # رفرنسِ ربات؛ در اولین sync_menu_buttons ست می‌شود


def _hub_button():
    from config import HUB_URL, HUB_BUTTON_TEXT
    if not HUB_URL:
        return None
    try:
        from telegram import MenuButtonWebApp, WebAppInfo
    except ImportError:
        logger.warning("MenuButtonWebApp not available in this python-telegram-bot version.")
        return None
    return MenuButtonWebApp(text=HUB_BUTTON_TEXT, web_app=WebAppInfo(url=HUB_URL))


async def sync_menu_button_for(bot, telegram_id: int):
    """دکمه‌ی «CMS» را برای *یک* نفر همین حالا هم‌گام می‌کند: اگر پیشوا یا
    مدیرِ فعالِ نقشِ مجاز است دکمه می‌گیرد، وگرنه دکمه‌ی پیش‌فرض برمی‌گردد
    (مثلاً بعد از اخراج). نیازی به صبر برای job ساعتی نیست."""
    if bot is None or not telegram_id:
        return
    try:
        from telegram import MenuButtonDefault
    except ImportError:
        return
    allowed = telegram_id == PISHVA_ID
    if not allowed:
        a = await db.get_admin(telegram_id)
        allowed = bool(a and a["is_active"] and a["role"] in HUB_ALLOWED_ROLES)
        if allowed:
            allowed = await db.can_use_hub(telegram_id)
    try:
        if allowed:
            btn = _hub_button()
            if btn is None:
                return
            await bot.set_chat_menu_button(chat_id=telegram_id, menu_button=btn)
        else:
            await bot.set_chat_menu_button(chat_id=telegram_id, menu_button=MenuButtonDefault())
    except Exception:
        logger.warning("Could not sync hub menu button for %s", telegram_id, exc_info=True)


def schedule_menu_sync(telegram_id):
    """غیرمسدودکننده؛ از database.py بعد از افزودن/اخراج/تغییرِ نقشِ مدیر
    صدا زده می‌شود. اگر حلقه‌ی asyncio یا ربات هنوز آماده نباشد، بی‌صدا رد
    می‌شود (job ساعتی جبران می‌کند)."""
    if _BOT is None or not telegram_id:
        return
    try:
        asyncio.get_running_loop().create_task(sync_menu_button_for(_BOT, int(telegram_id)))
    except RuntimeError:
        pass


async def sync_menu_buttons(bot):
    """دکمه‌ی منویِ چتِ خودِ پیشوا + همه‌ی مدیرانِ فعالِ نقشِ مجاز را روی
    «پنل من» می‌گذارد. کاربرانِ دیگر دکمه‌ی پیش‌فرض را می‌بینند — و حتی اگر
    لینک را از جایی پیدا کنند، بدونِ initData امضاشده و ردیف در admins فقط
    ۴۰۱/۴۰۳ می‌گیرند. اگر WEBAPP_URL تنظیم نشده باشد کاری نمی‌کند."""
    global _BOT
    _BOT = bot
    button = _hub_button()
    if button is None:
        logger.info("HUB_URL/WEBAPP_URL/RAILWAY_PUBLIC_DOMAIN not set (or no MenuButtonWebApp); skipping hub menu-button sync.")
        return
    logger.info("Hub menu button URL: %s", button.web_app.url)

    chat_ids = [PISHVA_ID]
    try:
        admins = await db.get_active_admins()
        for a in admins:
            if a["role"] in HUB_ALLOWED_ROLES and a["telegram_id"] and await db.can_use_hub(a["telegram_id"]):
                chat_ids.append(a["telegram_id"])
    except Exception:
        logger.exception("Could not load active admins for hub menu-button sync.")

    ok, fail = 0, 0
    for cid in chat_ids:
        try:
            await bot.set_chat_menu_button(chat_id=cid, menu_button=button)
            ok += 1
        except Exception:
            fail += 1
    logger.info("Hub menu button synced for %s chats (%s failed).", ok, fail)


async def sync_menu_buttons_job(context):
    """پوششِ سازگار با job_queue برای sync_menu_buttons (اجرای دوره‌ای)."""
    await sync_menu_buttons(context.bot)


# ─── سرو کردنِ فایل‌های استاتیکِ هاب (index.html/hub.js/hub.css/clock.js) ──
# عیناً هم‌الگوی static_files در game_server.py برای /webapp — کشِ طولانی‌
# مدت فقط برای درخواست‌های نسخه‌دار (?v=...)، محافظت در برابرِ path
# traversal، و fallback به index.html برای مسیرِ ریشه.
def _asset_version() -> str:
    """نسخه‌ی فایل‌های هاب = بزرگ‌ترین زمانِ تغییرِ آن‌ها. قبلاً placeholderِ __V__
    هرگز جایگزین نمی‌شد، پس کشِ «immutable»ِ فایل‌های ?v=... برای همیشه روی یک نسخه
    می‌ماند و بعد از هر آپدیت گوشی‌ها فایلِ قدیمی را می‌گرفتند."""
    latest = 0
    try:
        for fn in os.listdir(HUB_DIR):
            if fn.endswith((".js", ".css", ".html")):
                latest = max(latest, int(os.path.getmtime(os.path.join(HUB_DIR, fn))))
    except OSError:
        pass
    return str(latest or 1)


def _hub_index_response():
    path = os.path.join(HUB_DIR, "index.html")
    if not os.path.isfile(path):
        raise web.HTTPNotFound()
    with open(path, "r", encoding="utf-8") as f:
        page = f.read().replace("__V__", _asset_version())
    resp = web.Response(text=page, content_type="text/html", charset="utf-8")
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@routes.get("/hub")
async def hub_root(request):
    return _hub_index_response()


@routes.get("/hub/{tail:.*}")
async def hub_static_files(request):
    tail = request.match_info["tail"] or "index.html"
    if tail.startswith("api/"):
        raise web.HTTPNotFound()  # از routeهای بالا رد شده، یعنی مسیرِ api نامعتبر است
    if tail == "index.html" or tail == "":
        return _hub_index_response()
    path = os.path.normpath(os.path.join(HUB_DIR, tail))
    if not path.startswith(HUB_DIR):
        raise web.HTTPForbidden()
    if os.path.isdir(path):
        return _hub_index_response()
    if not os.path.isfile(path):
        raise web.HTTPNotFound()
    resp = web.FileResponse(path)
    if "v" in request.query:
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


# ─── ثبتِ مسیرها روی اپِ اصلی ───────────────────────────────────────
def register_hub_routes(app: web.Application):
    # مسیرهای مدیریتیِ نقش‌محور (hub_api) باید *قبل* از مسیرِ کاچ‌آلِ
    # /hub/{tail:.*} ثبت شوند، وگرنه aiohttp آن‌ها را به فایل‌های استاتیک می‌فرستد.
    try:
        import hub_api
        app.add_routes(hub_api.routes)
    except Exception:
        logger.exception("hub_api routes could not be registered")
    try:
        import hub_weather
        app.add_routes(hub_weather.routes)
    except Exception:
        logger.exception("hub_weather routes could not be registered")
    try:
        import hub_brief
        app.add_routes(hub_brief.routes)
    except Exception:
        logger.exception("hub_brief routes could not be registered")
    app.add_routes(routes)
    logger.info("Hub (پنل من) routes registered.")
