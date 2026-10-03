"""
hub_api.py — API مدیریتیِ نقش‌محورِ «پنل من».

هر مسیر:
  ۱) هویت را از initData امضاشده‌ی تلگرام می‌گیرد (hub._require_admin)،
  ۲) دروازه‌ی وضعیتِ سیستم را اعمال می‌کند (تعمیر/آپدیت/خطرناک/APS/خاموشی)،
  ۳) قابلیتِ لازم را *در سرور* چک می‌کند (hub_caps) — نه فقط مخفی‌کردنِ دکمه،
  ۴) همان منطقِ دیتابیسی و همان لاگ/اعلانِ رباتِ تلگرام را اجرا می‌کند.

همه‌ی نوشتن‌ها POST هستند و هر مسیر یک کارِ مشخص دارد؛ خروجی همیشه JSON.
"""

import asyncio
import html
import json
import logging
import re
from datetime import date, datetime, timedelta

from aiohttp import web

import database as db
import elo
import hub
import hub_caps
import hub_calendar as hcal
from config import PISHVA_ID, ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER
from helpers import now_shamsi, today_gregorian

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()

MATCH_CAPS = ("match_create", "match_edit", "match_delete", "predictions")
PLAYER_PICK_CAPS = ("players_view", "player_register", "match_create", "match_edit", "match_scan", "predictions", "teams")
# در وضعیتِ «بد» ربات این عملیات را برای مدیرانِ غیرِ ارشد می‌بندد (helpers.check_status_gate)
BAD_STATUS_BLOCKED = {"match_edit", "match_delete", "player_warn", "player_kick"}
ROLE_LABELS = {"pishva": "مدیر ارشد", ROLE_TOURNAMENT_MANAGER: "مسئول مسابقات",
               ROLE_SECURITY_MANAGER: "مسئول انتظامات"}


# ─── زیرساخت ─────────────────────────────────────────────────────
class Ctx:
    __slots__ = ("uid", "is_pishva", "admin", "caps", "feats", "user")

    def __init__(self, uid, is_pishva, admin, caps, feats, user):
        self.uid, self.is_pishva, self.admin = uid, is_pishva, admin
        self.caps, self.feats, self.user = caps, feats, user

    @property
    def name(self):
        if self.is_pishva:
            return "مدیر ارشد"
        return (self.admin["display_name"] or self.admin["full_name"] or "مدیر") if self.admin else "مدیر"


def _fail(code, message, status=400, **extra):
    cls = {400: web.HTTPBadRequest, 403: web.HTTPForbidden, 404: web.HTTPNotFound,
           409: web.HTTPConflict}.get(status, web.HTTPBadRequest)
    return hub._err(cls, code, message=message, **extra)


async def _ctx(request, *anyof, write=False):
    """احراز هویت + دروازه + چکِ قابلیت. anyof خالی = فقط ورود به هاب."""
    is_pishva, admin, user, caps, feats = await hub._require_cap(request, *anyof)
    uid = int(user["id"])
    if write and not is_pishva and anyof and set(anyof) & BAD_STATUS_BLOCKED:
        if (await hub_caps.system_status()) == "bad":
            raise _fail("bad_status", "🟡 سیستم در وضعیتِ احتیاطی است؛ این عملیات موقتاً غیرفعال شده.", 403)
    return Ctx(uid, is_pishva, admin, caps, feats, user)


async def _pishva_ctx(request):
    """فقط مدیر ارشد؛ کاربران دیگر ۴۰۳."""
    c = await _ctx(request)
    if not c.is_pishva:
        raise _fail("pishva_only", "این بخش فقط برای مدیر ارشد است.", 403)
    return c


async def _body(request):
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


def _int(v, name="id"):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise _fail("bad_request", f"مقدار «{name}» نامعتبر است.")


def _pid(request):
    return _int(request.match_info["id"])


def _text(v, lo, hi, label):
    t = re.sub(r"\s+", " ", str(v or "")).strip()
    if len(t) < lo:
        raise _fail("too_short", f"{label} خیلی کوتاه است." if lo > 1 else f"{label} را وارد کنید.")
    if len(t) > hi:
        raise _fail("too_long", f"{label} نباید بیشتر از {hi} حرف باشد.")
    return t


def _dt(v):
    return str(v or "")[:16].replace("T", " ")


def _bot():
    return hub._BOT


async def _send(uid, text, markup=None, parse_mode=None):
    bot = _bot()
    if bot is None or not uid:
        return False
    try:
        await bot.send_message(chat_id=uid, text=text, reply_markup=markup, parse_mode=parse_mode)
        return True
    except Exception:
        logger.warning("hub_api: could not notify %s", uid, exc_info=True)
        return False


def _md(s):
    """فرار از کاراکترهای Markdown قدیمیِ تلگرام برای متنِ کاربر."""
    return re.sub(r"([_*`\[])", r"\\\1", str(s or ""))


async def _destructive(uid, action):
    bot = _bot()
    if bot is None:
        return
    try:
        from anomaly_alerts import record_destructive_action
        await record_destructive_action(bot, uid, action)
    except Exception:
        logger.warning("hub_api: record_destructive_action failed", exc_info=True)


_bg = set()


async def _safe(coro):
    try:
        await coro
    except Exception:
        logger.warning("hub_api: background task failed", exc_info=True)


def _spawn(coro):
    """اجرای کار در پس‌زمینه بدونِ معطل‌کردنِ پاسخِ API (رفرنس نگه داشته می‌شود تا GC نکُشدش)."""
    t = asyncio.create_task(_safe(coro))
    _bg.add(t)
    t.add_done_callback(_bg.discard)
    return t


async def _resync_menu_buttons():
    try:
        await hub.sync_menu_buttons(_bot())
    except Exception:
        logger.exception("hub menu-button resync failed")


async def _broadcast(text, parse_mode="Markdown"):
    bot = _bot()
    if bot is None:
        return
    try:
        from helpers import broadcast_to_admins
        await broadcast_to_admins(bot, text, parse_mode=parse_mode)
    except Exception:
        logger.warning("hub_api: broadcast failed", exc_info=True)


def _broadcast_bg(text, parse_mode="Markdown"):
    """ارسالِ اعلان به همه‌ی مدیران در پس‌زمینه — جوابِ دکمه منتظرِ ارسالِ N پیام نمی‌ماند."""
    _spawn(_broadcast(text, parse_mode=parse_mode))


async def _recalc_elo():
    try:
        await elo.ensure_elo_table()
        await elo.recalculate_all_elo()
    except Exception:
        logger.warning("hub_api: Elo recalculation failed", exc_info=True)


def _ok(**kw):
    kw["ok"] = True
    return hub._json(kw)


# ─── کلاس‌ها ─────────────────────────────────────────────────────
@routes.get("/hub/api/classes")
async def api_classes(request):
    await _ctx(request, "players_view", "player_register", "classes", "match_create")
    classes = await db.get_all_classes()
    players = await db.get_all_players()
    counts = {}
    for p in players:
        counts[p["class_id"]] = counts.get(p["class_id"], 0) + 1
    return hub._json({"classes": [
        {"id": c["id"], "name": c["name"], "count": counts.get(c["id"], 0),
         "style": (c["button_style"] if "button_style" in c.keys() else None) or "none"}
        for c in classes]})


@routes.post("/hub/api/class/create")
async def api_class_create(request):
    c = await _ctx(request, "classes", write=True)
    name = _text((await _body(request)).get("name"), 1, 30, "نامِ کلاس")
    if any(x["name"] == name for x in await db.get_all_classes()):
        raise _fail("duplicate", "کلاسی با این نام از قبل هست.", 409)
    await db.create_class(name)
    await db.log_action(c.uid, "create_class", f"ثبت کلاس: {name}")
    return _ok()


@routes.post("/hub/api/class/{id}/rename")
async def api_class_rename(request):
    c = await _ctx(request, "classes", write=True)
    cid = _pid(request)
    name = _text((await _body(request)).get("name"), 1, 30, "نامِ کلاس")
    cls = await db.get_class(cid)
    if not cls:
        raise _fail("not_found", "کلاس پیدا نشد.", 404)
    if any(x["name"] == name and x["id"] != cid for x in await db.get_all_classes()):
        raise _fail("duplicate", "کلاسی با این نام از قبل هست.", 409)
    await db.rename_class(cid, name)
    await db.log_action(c.uid, "rename_class", f"تغییر نام کلاس: {cls['name']} ← {name}")
    return _ok()


@routes.post("/hub/api/class/{id}/style")
async def api_class_style(request):
    await _ctx(request, "classes", write=True)
    cid = _pid(request)
    style = str((await _body(request)).get("style") or "")
    if not await db.get_class(cid):
        raise _fail("not_found", "کلاس پیدا نشد.", 404)
    if not await db.set_class_button_style(cid, style):
        raise _fail("bad_request", "رنگِ نامعتبر.")
    return _ok()


@routes.post("/hub/api/class/{id}/delete")
async def api_class_delete(request):
    c = await _ctx(request, "classes", write=True)
    cid = _pid(request)
    cls = await db.get_class(cid)
    if not cls:
        raise _fail("not_found", "کلاس پیدا نشد.", 404)
    if not await db.delete_class(cid):
        raise _fail("not_empty", "این کلاس هنوز بازیکن دارد؛ اول بازیکنانش را به کلاسِ دیگر ببرید.", 409)
    await db.log_action(c.uid, "delete_class", f"حذف کلاس: {cls['name']}")
    return _ok()


# ─── بازیکنان ────────────────────────────────────────────────────
@routes.post("/hub/api/player/create")
async def api_player_create(request):
    c = await _ctx(request, "player_register", write=True)
    b = await _body(request)
    name = _text(b.get("name"), 3, 60, "نام و نام‌خانوادگی")
    cid = _int(b.get("class_id"), "کلاس")
    cls = await db.get_class(cid)
    if not cls:
        raise _fail("not_found", "کلاس پیدا نشد.", 404)
    for p in await db.get_all_players():
        if p["class_id"] == cid and hub_norm(p["full_name"]) == hub_norm(name):
            raise _fail("duplicate", "بازیکنی با همین نام در همین کلاس از قبل ثبت شده.", 409)
    pid = await db.create_player(name, cid)
    await db.log_action(c.uid, "create_player", f"ثبت بازیکن: {name}", pid)
    team_note = None
    tid = b.get("team_id")
    if tid and c.feats["team_mode"] and "teams" in c.caps:
        team = await db.get_team(_int(tid, "تیم"))
        if team and team["status"] == "active":
            await db.add_team_member(team["id"], pid)
            team_note = team["name"]
    return _ok(id=pid, name=name, cls=cls["name"], team=team_note)


def hub_norm(s):
    return re.sub(r"\s+", " ", str(s or "").replace("ي", "ی").replace("ك", "ک")).strip().lower()


@routes.get("/hub/api/player/{id}/panel")
async def api_player_panel(request):
    c = await _ctx(request, "players_view")
    pid = _pid(request)
    want_elo = "elo" in c.caps
    want_matches = any(x in c.caps for x in MATCH_CAPS)
    want_teams = "teams" in c.caps

    async def _none():
        return None

    async def _rank():
        try:
            return (await db.get_player_hub_detail(pid, 1))["rank_row"]["rank"]
        except Exception:
            return None

    # قبلاً ۷-۸ کوئری پشتِ‌سرِهم بود؛ همه‌ی خوانش‌های مستقل هم‌زمان، پس زمان ≈ کندترین یکی (نه جمعِ همه).
    p, log, pend, e, rank, ehist, hist, teams = await asyncio.gather(
        db.get_player(pid), db.get_warnings_log("player", pid, 30), db.get_pending_kick_request_for_player(pid),
        elo.get_player_elo(pid) if want_elo else _none(),
        _rank() if want_elo else _none(),
        elo.get_player_elo_history(pid, 10) if want_elo else _none(),
        db.get_player_match_history(pid) if want_matches else _none(),
        db.get_player_teams(pid) if want_teams else _none())
    if not p:
        raise _fail("not_found", "بازیکن پیدا نشد.", 404)
    w, d, l = p["wins"] or 0, p["draws"] or 0, p["losses"] or 0
    out = {
        "id": p["id"], "name": p["full_name"], "class_id": p["class_id"], "cls": p["class_name"] or "",
        "status": p["status"] or "active", "warnings": p["warnings"] or 0,
        "w": w, "d": d, "l": l, "elite": bool(p["is_elite"]), "special": bool(p["is_special"]),
        "notes": p["notes"] or "",
    }
    # ─ انضباطی (همه‌ی نقش‌های دارای «مشاهده‌ی بازیکنان») ─
    out["warn_log"] = [{"reason": r["reason"], "by": r["issuer_name"] or ("مدیر ارشد" if r["issued_by"] == PISHVA_ID else "؟"),
                        "at": _dt(r["issued_at"])} for r in log]
    out["kick_pending"] = bool(pend)
    # ─ Elo ─
    if want_elo:
        out["elo"] = {"rating": round(e["rating"]), "peak": round(e["peak_rating"]),
                      "games": e.get("games_played", 0), "title": elo.get_elo_title(e["rating"]), "rank": rank,
                      "history": [{"change": round(h["change"] or 0), "new": round(h["new_rating"] or 0),
                                   "opp": h["opponent_name"] or "؟", "res": h["result"],
                                   "at": str(h["recorded_at"])[:10]} for h in ehist]}
    # ─ مسابقات ─
    if want_matches:
        beat, lost = {}, {}
        for m in hist:
            mine = "white" if m["white_player_id"] == pid else "black"
            opp = m["black_name"] if mine == "white" else m["white_name"]
            if m["result"] == mine:
                beat[opp] = beat.get(opp, 0) + 1
            elif m["result"] in ("white", "black"):
                lost[opp] = lost.get(opp, 0) + 1
        out["best_opp"] = max(beat, key=beat.get) if beat else None
        out["hard_opp"] = max(lost, key=lost.get) if lost else None
        out["last_matches"] = [{
            "id": m["id"], "wid": m["white_player_id"], "w": m["white_name"] or "؟", "b": m["black_name"] or "؟",
            "res": m["result"], "date": (m["match_date"] or "")[:10]} for m in list(hist)[:12]]
    if want_teams:
        out["teams"] = [{"id": t["id"], "name": t["name"]} for t in teams]
    out["can"] = {
        "edit": "player_register" in c.caps, "warn": "player_warn" in c.caps,
        "kick": "player_kick" in c.caps, "kick_direct": bool(c.feats["direct_kick"]),
        "delete": "player_delete" in c.caps,
        "elite": "player_register" in c.caps or c.is_pishva, "special": c.is_pishva,
        "predict": "predictions" in c.caps,
    }
    return hub._json(out)


@routes.post("/hub/api/player/{id}/edit")
async def api_player_edit(request):
    c = await _ctx(request, "player_register", write=True)
    pid = _pid(request)
    p = await db.get_player(pid)
    if not p:
        raise _fail("not_found", "بازیکن پیدا نشد.", 404)
    b = await _body(request)
    changes, notes = {}, []
    if "name" in b:
        n = _text(b["name"], 3, 60, "نام و نام‌خانوادگی")
        if n != p["full_name"]:
            changes["full_name"] = n
            notes.append(f"نام: {p['full_name']} ← {n}")
    if "class_id" in b:
        cid = _int(b["class_id"], "کلاس")
        cls = await db.get_class(cid)
        if not cls:
            raise _fail("not_found", "کلاس پیدا نشد.", 404)
        if cid != p["class_id"]:
            changes["class_id"] = cid
            notes.append(f"کلاس ← {cls['name']}")
    if "notes" in b:
        nt = str(b["notes"] or "").strip()[:400]
        if nt != (p["notes"] or ""):
            changes["notes"] = nt
            notes.append("یادداشت")
    if changes:
        await db.update_player(pid, **changes)
        await db.log_action(c.uid, "edit_player", f"ویرایش بازیکن {p['full_name']}: " + "، ".join(notes), pid)
    return _ok()


@routes.post("/hub/api/player/{id}/flags")
async def api_player_flags(request):
    c = await _ctx(request, "player_register", write=True)
    pid = _pid(request)
    p = await db.get_player(pid)
    if not p:
        raise _fail("not_found", "بازیکن پیدا نشد.", 404)
    b = await _body(request)
    upd = {}
    if "elite" in b:
        upd["is_elite"] = 1 if b["elite"] else 0
    if "special" in b:
        if not c.is_pishva:
            raise _fail("pishva_only", "«ویژه» را فقط مدیر ارشد تعیین می‌کند.", 403)
        upd["is_special"] = 1 if b["special"] else 0
    if upd:
        await db.update_player(pid, **upd)
        await db.log_action(c.uid, "player_flags", f"تغییر برچسبِ {p['full_name']}: {upd}", pid)
    return _ok()


@routes.post("/hub/api/player/{id}/warn")
async def api_player_warn(request):
    c = await _ctx(request, "player_warn", write=True)
    pid = _pid(request)
    p = await db.get_player(pid)
    if not p:
        raise _fail("not_found", "بازیکن پیدا نشد.", 404)
    reason = _text((await _body(request)).get("reason"), 3, 300, "دلیلِ اخطار")
    await db.add_player_warning(pid, reason, c.uid)
    upd = await db.get_player(pid)
    await db.log_action(c.uid, "player_warning", f"اخطار به {p['full_name']}: {reason}", pid)
    if (upd["warnings"] or 0) >= 3 and not c.is_pishva:
        _spawn(_send(PISHVA_ID,
                    f"🔴 بازیکن *{_md(p['full_name'])}* به {upd['warnings']} اخطار رسید!\n"
                    f"📋 دلیل: {_md(reason)}\n⏱️ `{now_shamsi()}`", parse_mode="Markdown"))
    return _ok(warnings=upd["warnings"] or 0)


@routes.post("/hub/api/player/{id}/kick")
async def api_player_kick(request):
    c = await _ctx(request, "player_kick", write=True)
    pid = _pid(request)
    p = await db.get_player(pid)
    if not p:
        raise _fail("not_found", "بازیکن پیدا نشد.", 404)
    b = await _body(request)
    if (p["is_elite"] or p["is_special"]) and not b.get("confirm"):
        raise _fail("needs_confirm", "این بازیکن برتر/ویژه است. برای ادامه تأیید کنید.", 409)
    if (p["status"] or "active") == "kicked":
        raise _fail("already", "این بازیکن از قبل اخراج شده.", 409)
    if c.is_pishva or c.feats["direct_kick"]:
        await db.update_player(pid, status="kicked")
        await db.log_action(c.uid, "kick_player", f"اخراج: {p['full_name']}", pid)
        _spawn(_destructive(c.uid, "kick_player"))
        return _ok(mode="direct")
    if await db.get_pending_kick_request_for_player(pid):
        raise _fail("already", "برای این بازیکن قبلاً درخواستِ اخراج ثبت شده و منتظرِ تأیید است.", 409)
    req_id = await db.create_kick_request(c.uid, pid)

    async def _notify():
        import keyboards as kb
        from helpers import box
        await _send(PISHVA_ID,
                    f"{box('🚫 درخواست اخراج بازیکن')}\n\n👤 ادمینِ درخواست‌دهنده: *{_md(c.name)}*\n"
                    f"♟️ بازیکن: *{_md(p['full_name'])}*\n⏱️ `{now_shamsi()}`\n\n📌 تایید یا رد کنید:",
                    markup=kb.kb_kick_request(req_id), parse_mode="Markdown")
    _spawn(_notify())   # پس‌زمینه: جوابِ دکمه منتظرِ تلگرام نمی‌ماند؛ خطاها لاگ می‌شوند
    await db.log_action(c.uid, "request_kick_player", f"درخواست اخراج: {p['full_name']}", pid)
    return _ok(mode="request")


@routes.post("/hub/api/player/{id}/status")
async def api_player_status(request):
    c = await _ctx(request, "player_kick", write=True)
    pid = _pid(request)
    p = await db.get_player(pid)
    if not p:
        raise _fail("not_found", "بازیکن پیدا نشد.", 404)
    st = str((await _body(request)).get("status") or "")
    if st == "suspended":
        await db.update_player(pid, status="suspended")
        await db.log_action(c.uid, "suspend_player", f"تعلیق: {p['full_name']}", pid)
        _spawn(_destructive(c.uid, "suspend_player"))
    elif st == "active":
        await db.update_player(pid, status="active", warnings=0)
        await db.log_action(c.uid, "revive_player", f"احیا: {p['full_name']}", pid)
    else:
        raise _fail("bad_request", "وضعیتِ نامعتبر.")
    return _ok()


@routes.post("/hub/api/player/{id}/delete")
async def api_player_delete(request):
    c = await _ctx(request, "player_delete", write=True)
    if not c.is_pishva:   # حذفِ کامل فقط مدیر ارشد (دفاعِ دوم، علاوه بر نبودنِ قابلیت در caps مدیران)
        raise _fail("pishva_only", "حذفِ کاملِ بازیکن فقط برای مدیر ارشد است.", 403)
    pid = _pid(request)
    p = await db.get_player(pid)
    if not p:
        raise _fail("not_found", "بازیکن قبلاً حذف شده.", 404)
    if not (await _body(request)).get("confirm"):
        raise _fail("needs_confirm", "حذف کامل غیرقابل‌بازگشت است؛ تأیید لازم است.", 409)
    had_games = (p["wins"] or 0) + (p["draws"] or 0) + (p["losses"] or 0) > 0
    name = p["full_name"]
    await db.delete_player_hard(pid)
    await db.log_action(c.uid, "delete_player_hard", f"حذف کامل: {name}", pid)
    await _destructive(c.uid, "delete_player_hard")
    if had_games:
        # مسابقه‌های این بازیکن پاک شدند؛ آمار و Elo حریف‌ها باید دوباره با matches هم‌خوان شود.
        try:
            await db.recalculate_all_player_stats()
        except Exception:
            logger.warning("hub_api: stats recalculation failed", exc_info=True)
        await _recalc_elo()
    return _ok()


# ─── مسابقه‌ها ───────────────────────────────────────────────────
def _match_row(m):
    return {"id": m["id"], "wid": m["white_player_id"], "bid": m["black_player_id"],
            "w": m["white_name"] or "؟", "b": m["black_name"] or "؟", "res": m["result"],
            "reason": m["draw_reason"] or "", "date": (m["match_date"] or "")[:10],
            "tid": m["tournament_id"], "t": (m["t_name"] if "t_name" in m.keys() else None) or ""}


@routes.get("/hub/api/matches")
async def api_matches(request):
    await _ctx(request, *MATCH_CAPS)
    q = request.query
    scope = q.get("scope", "pending")
    if scope not in ("pending", "done", "all"):
        scope = "pending"
    tid = None
    if q.get("tournament"):
        tid = _int(q["tournament"], "مسابقه")
    rows = await db.get_hub_matches(scope, 80, (q.get("q") or "").strip()[:40], tid)
    return hub._json({"matches": [_match_row(m) for m in rows]})


def _valid_date(v):
    s = str(v or "").strip()[:10]
    try:
        date.fromisoformat(s)
        return s
    except ValueError:
        raise _fail("bad_date", "تاریخ نامعتبر است.")


@routes.post("/hub/api/match/create")
async def api_match_create(request):
    c = await _ctx(request, "match_create", write=True)
    b = await _body(request)
    wid, bid = _int(b.get("white_id"), "سفید"), _int(b.get("black_id"), "سیاه")
    if wid == bid:
        raise _fail("same_player", "سفید و سیاه نمی‌توانند یک نفر باشند.")
    wp, bp = await db.get_player(wid), await db.get_player(bid)
    if not wp or not bp:
        raise _fail("not_found", "بازیکن پیدا نشد.", 404)
    for p in (wp, bp):
        if (p["status"] or "active") != "active":
            raise _fail("inactive", f"«{p['full_name']}» فعال نیست.")
    md = _valid_date(b.get("date") or today_gregorian())
    if b.get("tournament_id"):
        t = await db.get_tournament(_int(b["tournament_id"], "مسابقه"))
        if not t:
            raise _fail("not_found", "مسابقه/تورنمنت پیدا نشد.", 404)
        tid = t["id"]
    else:
        dt = await db.get_default_tournament()
        tid = dt["id"] if dt else None
    mid = await db.create_match(wid, bid, md, tid, c.uid)
    await db.log_action(c.uid, "create_match", f"{wp['full_name']} vs {bp['full_name']}", mid)
    return _ok(id=mid)


# ─── ثبتِ نتیجه با عکس ─────────────────────────────────────────────
# مدیر ارشد: ثبتِ مستقیم. مدیر مسابقات: فقط «درخواست» ساخته می‌شود و بعد از تأییدِ مدیر ارشد ثبت می‌شود.
import match_scan_service as scan_svc
SCAN_COMMIT_CHUNK = scan_svc.COMMIT_CHUNK


@routes.post("/hub/api/match/scan")
async def api_match_scan(request):
    c = await _ctx(request, "match_scan")
    b = await _body(request)
    import match_vision as mv
    try:
        raw, mime = mv.decode_image(b.get("image"), b.get("mime") or "")
    except ValueError as e:
        raise _fail("bad_image", str(e), 400)
    try:
        review = await scan_svc.scan_sheet(raw, mime, c.uid)
    except scan_svc.ScanError as e:
        # busy/ai_*/nothing_found: کد و پیام به کلاینت می‌رسد (وضعیتِ HTTP مثل قبل)
        cls = {429: web.HTTPTooManyRequests, 502: web.HTTPBadGateway, 503: web.HTTPServiceUnavailable,
               422: web.HTTPUnprocessableEntity}.get(e.status, web.HTTPBadRequest)
        raise hub._err(cls, e.code, message=e.message)
    review["direct"] = not await hub_caps.scan_needs_approval(c.is_pishva, c.admin)   # false = برای تأییدِ مدیر ارشد می‌رود
    return hub._json(review)


@routes.post("/hub/api/match/scan/commit")
async def api_match_scan_commit(request):
    c = await _ctx(request, "match_scan", write=True)
    b = await _body(request)
    items = b.get("items")
    # مدیر ارشد: ثبتِ مستقیم در دسته‌های ≤۲۰؛ مدیر مسابقات: یک درخواستِ کامل (سقف MAX_ITEMS در clean_items)
    needs_approval = await hub_caps.scan_needs_approval(c.is_pishva, c.admin)
    if not needs_approval and isinstance(items, list) and len(items) > SCAN_COMMIT_CHUNK:
        raise _fail("too_many", f"در هر مرحله حداکثر {SCAN_COMMIT_CHUNK} مورد.")
    try:
        md = scan_svc.valid_date(b.get("date") or today_gregorian())
        tid = await scan_svc.resolve_tournament(_int(b["tournament_id"], "مسابقه") if b.get("tournament_id") else None)
        clean = scan_svc.clean_items(items)
    except ValueError as e:
        raise _fail("bad_request", str(e))

    if needs_approval:
        # ── نیازمندِ تأیید: هیچ‌چیز ثبت نمی‌شود؛ فقط درخواست برای مدیر ارشد ──
        req_id = await scan_svc.submit_for_approval(c.uid, clean, md, tid)
        await db.log_action(c.uid, "scan_request", f"درخواستِ ثبت با عکس: {len(clean)} مسابقه", req_id)

        async def _notify():
            import match_scan_bot as msb
            await msb.notify_pishva_new_request(_bot(), req_id, c.name)
        _spawn(_notify())
        return _ok(mode="request", request_id=req_id, count=len(clean))

    created, failed = await scan_svc.commit_items(clean, md, tid, c.uid)
    if created:
        await db.log_action(c.uid, "scan_commit", f"ثبت با عکس: {created} مسابقه" + ("" if c.is_pishva else " (مستقیم)"))
        if not c.is_pishva:
            # مدیرِ دارای دسترسیِ مستقیم: فقط خبر به مدیر ارشد (تأیید لازم نیست)
            import match_scan_bot as msb
            _spawn(msb.notify_pishva_direct_commit(_bot(), c.name, created))
    return hub._json({"ok": True, "mode": "direct", "created": created, "failed": failed})


@routes.post("/hub/api/scan-request/{id}/{act}")
async def api_scan_request_decide(request):
    c = await _pishva_ctx(request)
    act = request.match_info["act"]
    if act not in ("approve", "reject"):
        raise web.HTTPNotFound()
    rid = _pid(request)
    if act == "approve":
        ok, created, failed, req = await scan_svc.approve_scan_request(rid, c.uid)
        if not ok:
            raise _fail("done", "این درخواست قبلاً بررسی شده.", 409)
        msg = f"✅ درخواستِ ثبت با عکسِ شما توسط مدیر ارشد تایید شد: {created} مسابقه ثبت شد."
        if failed:
            msg += f"\n⚠️ {len(failed)} ردیف ثبت نشد."
        await _send(req["admin_id"], msg)
        return hub._json({"ok": True, "created": created, "failed": failed})
    ok, req = await scan_svc.reject_scan_request(rid, c.uid)
    if not ok:
        raise _fail("done", "این درخواست قبلاً بررسی شده.", 409)
    await _send(req["admin_id"], "❌ درخواستِ ثبت با عکسِ شما توسط مدیر ارشد رد شد؛ هیچ مسابقه‌ای ثبت نشد.")
    return _ok()


@routes.post("/hub/api/match/{id}/edit")
async def api_match_edit(request):
    c = await _ctx(request, "match_edit", write=True)
    mid = _pid(request)
    m = await db.get_match(mid)
    if not m:
        raise _fail("not_found", "مسابقه پیدا نشد.", 404)
    b = await _body(request)
    real = ("white", "black", "draw")
    notes = []
    if "date" in b:
        nd = _valid_date(b["date"])
        if nd != (m["match_date"] or "")[:10]:
            await db.update_match(mid, match_date=nd)
            notes.append(f"تاریخ ← {nd}")
    if "tournament_id" in b and b["tournament_id"] != m["tournament_id"]:
        tid = b["tournament_id"]
        if tid is not None:
            t = await db.get_tournament(_int(tid, "مسابقه"))
            if not t:
                raise _fail("not_found", "مسابقه/تورنمنت پیدا نشد.", 404)
            tid = t["id"]
        await db.update_match(mid, tournament_id=tid)
        notes.append("تورنمنت")
    if "result" in b:
        new = b["result"] or None
        if new not in (None, "white", "black", "draw", "cancelled"):
            raise _fail("bad_request", "نتیجه‌ی نامعتبر.")
        reason = str(b.get("reason") or "").strip()[:200]
        if new == "cancelled" and not reason:
            raise _fail("reason_required", "برای لغو، دلیل را بنویسید.")
        if new == "draw" and not reason:
            reason = "توافقی"
        old = m["result"]
        if new != old:
            if new in real:
                if old in real:
                    await db.correct_match_result(mid, new, reason if new == "draw" else "", c.uid)
                    await _recalc_elo()
                else:
                    if old == "cancelled":
                        await db.update_match(mid, result=None)
                    fresh = await db.get_match(mid)
                    await db.record_match_result(mid, new, reason if new == "draw" else "", c.uid, match=fresh)
                    try:
                        await elo.ensure_elo_table()
                        await elo.update_elo_after_match(m["white_player_id"], m["black_player_id"], new, mid)
                    except Exception:
                        logger.warning("hub_api: Elo update failed", exc_info=True)
            else:
                stat = {"white": ("win", "loss"), "black": ("loss", "win"), "draw": ("draw", "draw")}
                if old in real:
                    await db.reverse_player_stats(m["white_player_id"], stat[old][0])
                    await db.reverse_player_stats(m["black_player_id"], stat[old][1])
                await db.update_match(mid, result=new, draw_reason=reason if new == "cancelled" else "",
                                      updated_by=c.uid, updated_at=datetime.now().isoformat())
                if old in real:
                    await _recalc_elo()
            notes.append({"white": "برد سفید", "black": "برد سیاه", "draw": "تساوی",
                          "cancelled": "لغو", None: "بدون نتیجه"}[new])
    if notes:
        await db.log_action(c.uid, "edit_match",
                            f"ویرایش مسابقه {m['white_name']} ⚔️ {m['black_name']}: " + "، ".join(notes), mid)
    return _ok()


@routes.post("/hub/api/match/{id}/delete")
async def api_match_delete(request):
    c = await _ctx(request, "match_delete", write=True)
    mid = _pid(request)
    m = await db.get_match(mid)
    if not m:
        raise _fail("not_found", "مسابقه قبلاً حذف شده.", 404)
    if not (await _body(request)).get("confirm"):
        raise _fail("needs_confirm", "برای حذف تأیید لازم است.", 409)
    snap = json.dumps({
        "id": m["id"], "white_player_id": m["white_player_id"], "black_player_id": m["black_player_id"],
        "result": m["result"], "draw_reason": m["draw_reason"], "match_date": m["match_date"],
        "tournament_id": m["tournament_id"], "created_by": m["created_by"], "created_at": m["created_at"],
        "updated_by": m["updated_by"], "updated_at": m["updated_at"], "is_pinned": m["is_pinned"],
    }, ensure_ascii=False)
    await db.delete_match_safely(mid)
    if m["result"] in ("white", "black", "draw"):
        await _recalc_elo()
    await db.log_action(c.uid, "delete_match", f"حذف مسابقه {mid}", mid, snapshot=snap)
    await _destructive(c.uid, "delete_match")
    return _ok()


# ─── پیش‌بینی و Elo ──────────────────────────────────────────────
@routes.get("/hub/api/predict")
async def api_predict(request):
    await _ctx(request, "predictions")
    wid, bid = _int(request.query.get("white"), "سفید"), _int(request.query.get("black"), "سیاه")
    if wid == bid:
        raise _fail("same_player", "دو بازیکنِ متفاوت انتخاب کنید.")
    # همه‌ی خوانش‌ها هم‌زمان؛ و فقط بازی‌های مستقیمِ این دو نفر (نه کلِ تاریخچه‌ی سفید)
    wp, bp, (we, be), hist = await asyncio.gather(
        db.get_player(wid), db.get_player(bid), elo.get_player_elo_pair(wid, bid), db.get_h2h_results(wid, bid))
    if not wp or not bp:
        raise _fail("not_found", "بازیکن پیدا نشد.", 404)
    pw = elo.expected_score(we["rating"], be["rating"])
    hw = hb = hd = 0
    for m in hist:
        if m["result"] == "draw":
            hd += 1
        elif (m["result"] == "white") == (m["white_player_id"] == wid):
            hw += 1
        else:
            hb += 1
    return hub._json({
        "white": {"id": wid, "name": wp["full_name"], "elo": round(we["rating"]), "title": elo.get_elo_title(we["rating"])},
        "black": {"id": bid, "name": bp["full_name"], "elo": round(be["rating"]), "title": elo.get_elo_title(be["rating"])},
        "p_white": round(pw * 100), "p_black": round((1 - pw) * 100),
        "h2h": {"white": hw, "black": hb, "draw": hd},
    })


@routes.get("/hub/api/elo/leaderboard")
async def api_elo_board(request):
    await _ctx(request, "elo")
    limit = max(5, min(_int(request.query.get("limit", 50), "limit"), 200))
    await elo.ensure_elo_table()
    rows = await elo.get_elo_leaderboard(limit)
    return hub._json({"rows": [{
        "id": r["player_id"], "name": r["full_name"], "cls": r["class_name"] or "",
        "elo": round(r["rating"]), "peak": round(r["peak_rating"]), "games": r["games_played"],
        "title": elo.get_elo_title(r["rating"])} for r in rows]})


# ═══════════════════════════════════════════════════════════════
#  مخابرات
# ═══════════════════════════════════════════════════════════════
def _admin_name(a):
    return (a["display_name"] or a["full_name"] or "مدیر") if a else "؟"


async def _name_map():
    pname, admins = await asyncio.gather(db.get_setting("pishva_display_name", "مدیر ارشد"), db.get_all_admins())
    m = {PISHVA_ID: pname}
    for a in admins:
        m[a["telegram_id"]] = _admin_name(a)
    return m


@routes.get("/hub/api/comms/overview")
async def api_comms_overview(request):
    c = await _ctx(request, "comms")
    # ۶ خوانشِ مستقل «هم‌زمان» (قبلاً پشتِ‌سرِهم، هرکدام یک رفت‌وبرگشتِ شبکه) و با LIMIT در خودِ SQL
    names, active, inbox_rows, sent_rows, ann_rows, news_rows = await asyncio.gather(
        _name_map(), db.get_active_admins(),
        db.get_messages_for(c.uid, 40), db.get_sent_messages_for(c.uid, 40),
        db.get_all_announcements(15), db.get_all_news(15))
    recipients = []
    if c.uid != PISHVA_ID:
        recipients.append({"id": PISHVA_ID, "name": names[PISHVA_ID], "role": "مدیر ارشد"})
    for a in active:
        if a["telegram_id"] != c.uid:
            recipients.append({"id": a["telegram_id"], "name": _admin_name(a), "role": ROLE_LABELS.get(a["role"], "مدیر")})
    inbox = [{"id": m["id"], "from": names.get(m["sender_id"], "؟"), "text": m["text"],
              "at": _dt(m["sent_at"]), "read": bool(m["is_read"])} for m in list(inbox_rows)[:40]]
    sent = [{"id": m["id"], "to": names.get(m["receiver_id"], "؟"), "text": m["text"],
             "at": _dt(m["sent_at"]), "read": bool(m["is_read"])} for m in list(sent_rows)[:40]]
    anns = [{"id": a["id"], "text": re.sub(r"<[^>]+>", "", str(a["text"] or ""))[:400], "at": _dt(a["sent_at"])}
            for a in list(ann_rows)[:15]]
    news = [{"id": n["id"], "text": re.sub(r"<[^>]+>", "", str(n["text"] or ""))[:400], "at": _dt(n["sent_at"])}
            for n in list(news_rows)[:15]]
    return hub._json({"recipients": recipients, "inbox": inbox, "sent": sent,
                      "announcements": anns, "news": news, "can_broadcast": c.is_pishva})


@routes.post("/hub/api/comms/send")
async def api_comms_send(request):
    c = await _ctx(request, "comms", write=True)
    b = await _body(request)
    to = _int(b.get("to"), "گیرنده")
    text = _text(b.get("text"), 1, 1000, "متنِ پیام")
    if to == c.uid:
        raise _fail("self", "به خودتان نمی‌توانید پیام بدهید.")
    if not c.is_pishva and (await hub_caps.system_status()) == "bad":
        raise _fail("bad_status", "🟡 سیستم در وضعیتِ احتیاطی است؛ ارسالِ پیام موقتاً غیرفعال شده.", 403)
    target_ok = to == PISHVA_ID
    if not target_ok:
        a = await db.get_admin(to)
        target_ok = bool(a and a["is_active"])
    if not target_ok:
        raise _fail("not_found", "گیرنده پیدا نشد یا فعال نیست.", 404)
    msg_id = await db.send_message_db(c.uid, to, text)
    try:
        import keyboards as kb
        from helpers import box
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        sender = await db.get_setting("pishva_display_name", "مدیر ارشد") if c.is_pishva else c.name
        bot = _bot()
        if bot is not None:
            sent = await bot.send_message(
                chat_id=to,
                text=(f"{box('📨 پیام جدید')}\n\n📬 شما یک پیام جدید دارید.\n👤 از: {_md(sender)}\n"
                      f"⏱️ `{now_shamsi()}`\n\n💬 متن: _{_md(text)}_"),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ تأیید مطالعه", callback_data=f"msg_ack_{msg_id}")]]),
                parse_mode="Markdown")
            await db.set_message_notif(msg_id, sent.chat_id, sent.message_id)
    except Exception:
        logger.warning("hub_api: message notification failed", exc_info=True)
    return _ok(id=msg_id)


@routes.post("/hub/api/comms/read")
async def api_comms_read(request):
    c = await _ctx(request, "comms", write=True)
    m = await db.get_message(_int((await _body(request)).get("id")))
    if m and m["receiver_id"] == c.uid:
        await db.mark_message_read(m["id"])
    return _ok()


@routes.post("/hub/api/comms/delete-sent")
async def api_comms_delete_sent(request):
    c = await _ctx(request, "comms", write=True)
    m = await db.get_message(_int((await _body(request)).get("id")))
    if not m or m["sender_id"] != c.uid:
        raise _fail("not_found", "پیام پیدا نشد.", 404)
    await db.delete_sent_message(m["id"])
    bot = _bot()
    if bot is not None and m["notif_chat_id"] and m["notif_message_id"]:
        try:
            await bot.delete_message(chat_id=m["notif_chat_id"], message_id=m["notif_message_id"])
        except Exception:
            pass
    return _ok()


@routes.post("/hub/api/comms/announce")
async def api_comms_announce(request):
    c = await _pishva_ctx(request)
    text = _text((await _body(request)).get("text"), 1, 3000, "متنِ بیانیه")
    bot = _bot()
    if bot is None:
        raise _fail("no_bot", "ربات هنوز آماده نیست؛ چند ثانیه بعد امتحان کنید.", 400)
    from comms import _send_announcement
    await _send_announcement(bot, html.escape(text), "", "")
    await db.log_action(c.uid, "announcement", f"بیانیه از هاب: {text[:60]}")
    return _ok()


@routes.post("/hub/api/comms/news")
async def api_comms_news(request):
    c = await _pishva_ctx(request)
    text = _text((await _body(request)).get("text"), 1, 1500, "متنِ خبر")
    await db.create_news(text)
    await _broadcast(f"✨ <b>خبر فوری از سیستم</b>✨\n\n{html.escape(text)}\n\n⏱️ <code>{html.escape(now_shamsi())}</code>",
                     parse_mode="HTML")
    await db.log_action(c.uid, "news", f"خبر از هاب: {text[:60]}")
    return _ok()


# ═══════════════════════════════════════════════════════════════
#  تیم‌ها (فقط وقتی «حالت تیمی» روشن است)
# ═══════════════════════════════════════════════════════════════
async def _teams_ctx(request, write=False):
    c = await _ctx(request, "teams", write=write)
    if not c.feats["team_mode"]:
        raise _fail("team_off", "حالت تیمی خاموش است.", 403)
    return c


async def _team_or_404(tid):
    t = await db.get_team(tid)
    if not t or t["status"] != "active":
        raise _fail("not_found", "تیم پیدا نشد.", 404)
    return t


@routes.get("/hub/api/teams")
async def api_teams(request):
    await _teams_ctx(request)
    out = []
    for t in await db.get_all_teams():
        mem = await db.get_team_members(t["id"])
        st = await db.get_team_stats(t["id"])
        out.append({"id": t["id"], "name": t["name"], "slogan": t["slogan"] or "", "members": len(mem),
                    "warnings": t["warnings"] or 0, **st})
    return hub._json({"teams": out})


@routes.get("/hub/api/team/{id}")
async def api_team_detail(request):
    c = await _teams_ctx(request)
    t = await _team_or_404(_pid(request))
    mem = await db.get_team_members(t["id"])
    log = await db.get_warnings_log("team", t["id"], 20)
    return hub._json({
        "id": t["id"], "name": t["name"], "slogan": t["slogan"] or "", "warnings": t["warnings"] or 0,
        "captain_id": t["captain_id"], "created_at": _dt(t["created_at"] or ""),
        "stats": await db.get_team_stats(t["id"]),
        "members": [{"id": m["player_id"], "name": m["full_name"], "cls": m["class_name"] or "",
                     "level": m["level"] or "", "reserve": bool(m["is_reserve"]),
                     "status": m["player_status"] or "active"} for m in mem],
        "warn_log": [{"reason": r["reason"], "by": r["issuer_name"] or "مدیر ارشد", "at": _dt(r["issued_at"])} for r in log],
        "can_create": bool(c.feats["team_create"]),
    })


@routes.post("/hub/api/team/create")
async def api_team_create(request):
    c = await _teams_ctx(request, write=True)
    if not c.feats["team_create"]:
        raise _fail("no_create", "ساختِ تیم توسط مدیران در تنظیماتِ مدیر ارشد بسته است.", 403)
    b = await _body(request)
    name = _text(b.get("name"), 2, 40, "نامِ تیم")
    slogan = str(b.get("slogan") or "").strip()[:80]
    if any(hub_norm(t["name"]) == hub_norm(name) for t in await db.get_all_teams()):
        raise _fail("duplicate", "تیمی با این نام از قبل هست.", 409)
    tid = await db.create_team(name, slogan, c.name, c.uid)
    have = await db.get_players_with_team()
    added = 0
    for pid in (b.get("member_ids") or [])[:30]:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        if pid in have or not await db.get_player(pid):
            continue
        await db.add_team_member(tid, pid)
        added += 1
    await db.log_action(c.uid, "create_team", f"ثبت تیم: {name}", tid)
    return _ok(id=tid, added=added)


@routes.post("/hub/api/team/{id}/edit")
async def api_team_edit(request):
    c = await _teams_ctx(request, write=True)
    t = await _team_or_404(_pid(request))
    b = await _body(request)
    upd = {}
    if "name" in b:
        n = _text(b["name"], 2, 40, "نامِ تیم")
        if any(x["id"] != t["id"] and hub_norm(x["name"]) == hub_norm(n) for x in await db.get_all_teams()):
            raise _fail("duplicate", "تیمی با این نام از قبل هست.", 409)
        upd["name"] = n
    if "slogan" in b:
        upd["slogan"] = str(b["slogan"] or "").strip()[:80]
    if "captain_id" in b:
        cap = b["captain_id"]
        if cap is not None:
            cap = _int(cap, "کاپیتان")
            if cap not in {m["player_id"] for m in await db.get_team_members(t["id"])}:
                raise _fail("bad_request", "کاپیتان باید عضوِ همین تیم باشد.")
        upd["captain_id"] = cap
    if upd:
        await db.update_team(t["id"], **upd)
        await db.log_action(c.uid, "edit_team", f"ویرایش تیم {t['name']}", t["id"])
    return _ok()


@routes.post("/hub/api/team/{id}/add-member")
async def api_team_add_member(request):
    c = await _teams_ctx(request, write=True)
    t = await _team_or_404(_pid(request))
    pid = _int((await _body(request)).get("player_id"), "بازیکن")
    p = await db.get_player(pid)
    if not p:
        raise _fail("not_found", "بازیکن پیدا نشد.", 404)
    if pid in await db.get_players_with_team():
        raise _fail("in_team", "این بازیکن از قبل عضوِ یک تیم است.", 409)
    await db.add_team_member(t["id"], pid)
    await db.log_action(c.uid, "edit_team", f"افزودنِ {p['full_name']} به تیم {t['name']}", t["id"])
    return _ok()


@routes.post("/hub/api/team/{id}/remove-member")
async def api_team_remove_member(request):
    c = await _teams_ctx(request, write=True)
    t = await _team_or_404(_pid(request))
    pid = _int((await _body(request)).get("player_id"), "بازیکن")
    await db.remove_team_member(t["id"], pid)
    if t["captain_id"] == pid:
        await db.update_team(t["id"], captain_id=None)
    await db.log_action(c.uid, "edit_team", f"حذفِ عضو از تیم {t['name']}", t["id"])
    return _ok()


@routes.post("/hub/api/team/{id}/warn")
async def api_team_warn(request):
    c = await _teams_ctx(request, write=True)
    if "player_warn" not in c.caps:
        raise _fail("no_cap", "قابلیتِ اخطار برای شما فعال نیست.", 403)
    t = await _team_or_404(_pid(request))
    reason = _text((await _body(request)).get("reason"), 3, 300, "دلیلِ اخطار")
    await db.add_team_warning(t["id"], reason, c.uid)
    await db.log_action(c.uid, "team_warning", f"اخطار به تیم {t['name']}: {reason}", t["id"])
    return _ok()


@routes.post("/hub/api/team/{id}/delete")
async def api_team_delete(request):
    c = await _teams_ctx(request, write=True)
    t = await _team_or_404(_pid(request))
    if not (await _body(request)).get("confirm"):
        raise _fail("needs_confirm", "برای حذفِ تیم تأیید لازم است.", 409)
    await db.delete_team(t["id"])
    await db.log_action(c.uid, "delete_team", f"حذف تیم: {t['name']}", t["id"])
    await _destructive(c.uid, "delete_team")
    return _ok()


# ═══════════════════════════════════════════════════════════════
#  تقویم (+ تعطیلی‌های رسمیِ ۳ سالِ پیشِ رو)
# ═══════════════════════════════════════════════════════════════
def _tehran_today():
    try:
        from helpers import TEHRAN_TZ
        return datetime.now(TEHRAN_TZ).date()
    except Exception:
        return date.today()


async def _hijri_offset():
    try:
        return max(-2, min(2, int(await db.get_setting("hijri_offset", "0"))))
    except (TypeError, ValueError):
        return 0


@routes.get("/hub/api/calendar/month")
async def api_calendar_month(request):
    c = await _ctx(request, "calendar")
    today = _tehran_today()
    ty, tm, td = hcal.date_to_jalali(today)
    y = _int(request.query.get("y", ty), "سال")
    m = _int(request.query.get("m", tm), "ماه")
    if not (1300 <= y <= 1500 and 1 <= m <= 12):
        raise _fail("bad_request", "ماه/سال نامعتبر است.")
    off, custom = await asyncio.gather(_hijri_offset(), db.get_calendar_month(y, m))
    grid = hcal.month_grid(y, m, off)
    for d in grid["days"]:
        cu = custom.get(d["d"])
        d["custom"] = {"type": cu["day_type"], "title": cu["title"]} if cu else None
        d["holiday"] = bool(d["official"]) or bool(cu and cu["day_type"] == "holiday")
        d["today"] = (y, m, d["d"]) == (ty, tm, td)
    grid["can_edit"] = "calendar_edit" in c.caps
    return hub._json(grid)


@routes.get("/hub/api/calendar/holidays")
async def api_calendar_holidays(request):
    await _ctx(request, "calendar")
    today = _tehran_today()
    end = today + timedelta(days=366 * 3)
    sj = hcal.jstr(*hcal.date_to_jalali(today))
    ej = hcal.jstr(*hcal.date_to_jalali(end))
    off, cal_rows = await asyncio.gather(_hijri_offset(), db.get_calendar_range(sj, ej))
    items = hcal.upcoming_holidays(today, 3, off)
    by_j = {i["j"]: i for i in items}
    for r in cal_rows:
        if r["day_type"] != "holiday":
            continue
        if r["jdate"] in by_j:
            by_j[r["jdate"]]["titles"].append(f"{r['title']} (ثبتِ مدرسه)")
            continue
        try:
            jy, jm, jd = map(int, r["jdate"].split("/"))
            g = hcal.jalali_to_date(jy, jm, jd)
        except Exception:
            continue
        wd = hcal.weekday_fa(g)
        items.append({"j": r["jdate"], "g": g.isoformat(), "jy": jy, "jm": jm, "jd": jd,
                      "month_name": hcal.PERSIAN_MONTHS[jm - 1], "wd": wd, "wd_name": hcal.PERSIAN_WEEKDAYS[wd],
                      "titles": [f"{r['title']} (ثبتِ مدرسه)"], "on_friday": wd == 6})
    items.sort(key=lambda i: i["g"])
    return hub._json({"items": items, "from": today.isoformat(), "hijri_offset": off,
                      "note": "تعطیلی‌های قمری با محاسبه‌ی جدولی‌ست و ممکن است تا یک روز با اعلامِ رسمیِ رؤیتِ هلال فرق کند."})


@routes.post("/hub/api/calendar/day")
async def api_calendar_day(request):
    c = await _ctx(request, "calendar_edit", write=True)
    b = await _body(request)
    jd = str(b.get("jdate") or "")
    try:
        jy, jm, jdd = map(int, jd.split("/"))
        if not (1300 <= jy <= 1500 and 1 <= jm <= 12 and 1 <= jdd <= hcal.jalali_month_length(jy, jm)):
            raise ValueError
    except Exception:
        raise _fail("bad_date", "تاریخِ شمسیِ نامعتبر.")
    jd = hcal.jstr(jy, jm, jdd)
    typ = str(b.get("day_type") or "")
    if typ == "clear":
        await db.delete_calendar_day(jd)
        await db.log_action(c.uid, "calendar_edit", f"حذفِ رویداد/تعطیلیِ {jd}")
    elif typ in ("event", "holiday"):
        title = _text(b.get("title"), 1, 80, "عنوان")
        await db.set_calendar_day(jd, typ, title, c.uid)
        await db.log_action(c.uid, "calendar_edit", f"{'تعطیلی' if typ == 'holiday' else 'رویداد'} {jd}: {title}")
    else:
        raise _fail("bad_request", "نوعِ نامعتبر.")
    return _ok()


# ═══════════════════════════════════════════════════════════════
#  فقط مدیر ارشد: مدیریتِ مدیران، درخواست‌ها، تنظیمات، وضعیت، لاگ
# ═══════════════════════════════════════════════════════════════
LEGACY_PERMS = {
    "notifications": "اعلان", "news": "اخبار", "match_management": "مدیریتِ مسابقات (ربات)",
    "view_players": "مشاهده‌ی بازیکنان (ربات)", "issue_warning": "اخطار (ربات)",
    "request_ban": "درخواستِ اخراج (ربات)", "direct_ban": "اخراجِ مستقیم", "assign_task": "وظیفه",
    "report": "گزارش", "bot_active": "ربات فعال", "settings_access": "تنظیمات", "senior_admin": "ارشد",
    "edit_delete_match": "ویرایش/حذفِ مسابقه (ربات)", "communications": "مخابرات (ربات)",
    "ai_access": "دسترسیِ هوش مصنوعی", "chess_access": "شطرنجِ زنده", "hub_access": "ورود به پنل من",
    "calendar_edit": "ویرایشِ تقویم (ربات)",
}


def _admin_full(a):
    perms = hub_caps.parse_perms(a)
    eff = hub_caps.role_caps(a["role"], perms)
    over = perms.get("hub_caps") if isinstance(perms.get("hub_caps"), dict) else {}
    return {
        "id": a["telegram_id"], "name": _admin_name(a), "username": a["username"] or "",
        "role": a["role"], "role_label": ROLE_LABELS.get(a["role"], "مدیر"),
        "active": bool(a["is_active"]), "warnings": a["warnings"] or 0,
        "joined": str(a["joined_at"] or "")[:10], "last_active": _dt(a["last_active"] or ""),
        "scan_mode": hub_caps.scan_mode_override(perms) or "default",
        "caps": eff, "overrides": {k: bool(v) for k, v in over.items() if k in hub_caps.CAP_KEYS},
        "defaults": {k: (k in hub_caps.ROLE_DEFAULTS.get(a["role"], set())) for k in hub_caps.CAP_KEYS},
        "perms": {k: bool(perms.get(k, k in ("notifications", "news", "match_management", "view_players",
                                            "issue_warning", "request_ban", "report", "bot_active",
                                            "edit_delete_match", "communications", "ai_access",
                                            "chess_access", "hub_access"))) for k in LEGACY_PERMS},
    }


@routes.get("/hub/api/admin/list")
async def api_admin_list(request):
    await _pishva_ctx(request)
    admins = await db.get_all_admins()
    return hub._json({
        "admins": [_admin_full(a) for a in admins],
        "caps_meta": [{"key": k, "label": l, "group": g} for k, l, g in hub_caps.CAPS],
        "legacy_meta": [{"key": k, "label": l} for k, l in LEGACY_PERMS.items()],
        "max_warnings": 5,
    })


async def _admin_or_404(request):
    a = await db.get_admin(_pid(request))
    if not a:
        raise _fail("not_found", "مدیر پیدا نشد.", 404)
    return a


@routes.post("/hub/api/admin/{id}/caps")
async def api_admin_caps(request):
    c = await _pishva_ctx(request)
    a = await _admin_or_404(request)
    b = await _body(request)
    perms = hub_caps.parse_perms(a)
    over = dict(perms.get("hub_caps") or {}) if isinstance(perms.get("hub_caps"), dict) else {}
    if b.get("reset"):
        over = {}
    for k, v in (b.get("caps") or {}).items():
        if k in hub_caps.CAP_KEYS:
            over[k] = bool(v)
    await db.set_admin_permission(a["telegram_id"], "hub_caps", over)
    await db.log_action(c.uid, "admin_permission", f"شخصی‌سازیِ دسترسیِ پنل من برای {_admin_name(a)}", a["telegram_id"])
    fresh = await db.get_admin(a["telegram_id"])
    return _ok(admin=_admin_full(fresh))


@routes.post("/hub/api/admin/{id}/scan-mode")
async def api_admin_scan_mode(request):
    c = await _pishva_ctx(request)
    a = await _admin_or_404(request)
    mode = str((await _body(request)).get("mode") or "")
    if mode not in ("default", "direct", "approval"):
        raise _fail("bad_request", "حالتِ نامعتبر.")
    await db.set_admin_permission(a["telegram_id"], "scan_mode", None if mode == "default" else mode)
    label = {"default": "تنظیمِ کلی", "direct": "مستقیم", "approval": "با تأییدِ مدیر ارشد"}[mode]
    await db.log_action(c.uid, "admin_permission", f"حالتِ ثبت با عکس → {label} برای {_admin_name(a)}", a["telegram_id"])
    return _ok(admin=_admin_full(await db.get_admin(a["telegram_id"])))


@routes.post("/hub/api/admin/{id}/perm")
async def api_admin_perm(request):
    c = await _pishva_ctx(request)
    a = await _admin_or_404(request)
    b = await _body(request)
    perm = str(b.get("perm") or "")
    if perm not in LEGACY_PERMS:
        raise _fail("bad_request", "دسترسیِ نامعتبر.")
    await db.set_admin_permission(a["telegram_id"], perm, bool(b.get("value")))
    await db.log_action(c.uid, "admin_permission", f"{LEGACY_PERMS[perm]} → {'روشن' if b.get('value') else 'خاموش'} برای {_admin_name(a)}", a["telegram_id"])
    return _ok(admin=_admin_full(await db.get_admin(a["telegram_id"])))


@routes.post("/hub/api/admin/{id}/role")
async def api_admin_role(request):
    c = await _pishva_ctx(request)
    a = await _admin_or_404(request)
    role = str((await _body(request)).get("role") or "")
    if role not in (ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER):
        raise _fail("bad_request", "نقشِ نامعتبر.")
    await db.set_admin_role(a["telegram_id"], role)
    await db.log_action(c.uid, "admin_role", f"تغییرِ نقشِ {_admin_name(a)} به {ROLE_LABELS[role]}", a["telegram_id"])
    await _send(a["telegram_id"], f"💼 نقشِ شما توسطِ مدیر ارشد به «{ROLE_LABELS[role]}» تغییر کرد.\n/start بزنید.")
    return _ok(admin=_admin_full(await db.get_admin(a["telegram_id"])))


@routes.post("/hub/api/admin/{id}/active")
async def api_admin_active(request):
    c = await _pishva_ctx(request)
    a = await _admin_or_404(request)
    on = bool((await _body(request)).get("active"))
    if on:
        await db.revive_admin(a["telegram_id"])
        await _send(a["telegram_id"], "✅ دسترسیِ شما دوباره فعال شد.\n/start بزنید.")
    else:
        await db.kick_admin(a["telegram_id"])
        await _send(a["telegram_id"], "🚫 دسترسیِ شما توسطِ مدیر ارشد لغو شد.")
    await db.log_action(c.uid, "admin_active", f"{'فعال‌سازی' if on else 'اخراج'} مدیر {_admin_name(a)}", a["telegram_id"])
    return _ok(admin=_admin_full(await db.get_admin(a["telegram_id"])))


@routes.post("/hub/api/admin/{id}/warn")
async def api_admin_warn(request):
    c = await _pishva_ctx(request)
    a = await _admin_or_404(request)
    reason = _text((await _body(request)).get("reason"), 3, 300, "دلیلِ اخطار")
    await db.add_admin_warning(a["telegram_id"], reason, c.uid)
    await db.log_action(c.uid, "admin_warning", f"اخطار به مدیر {_admin_name(a)}: {reason}", a["telegram_id"])
    await _send(a["telegram_id"], f"⚠️ اخطار از مدیر ارشد\n📋 دلیل: {reason}")
    return _ok(admin=_admin_full(await db.get_admin(a["telegram_id"])))


@routes.post("/hub/api/admin/{id}/clear-warnings")
async def api_admin_clear_warn(request):
    c = await _pishva_ctx(request)
    a = await _admin_or_404(request)
    await db.set_admin_warnings(a["telegram_id"], 0)
    await db.log_action(c.uid, "admin_clear_warnings", f"پاک‌کردنِ اخطارهای {_admin_name(a)}", a["telegram_id"])
    return _ok(admin=_admin_full(await db.get_admin(a["telegram_id"])))


@routes.post("/hub/api/admin/{id}/rename")
async def api_admin_rename(request):
    c = await _pishva_ctx(request)
    a = await _admin_or_404(request)
    name = _text((await _body(request)).get("name"), 2, 40, "نام")
    await db.update_admin_display_name(a["telegram_id"], name)
    await db.log_action(c.uid, "admin_rename", f"تغییرِ نامِ نمایشیِ {_admin_name(a)} به {name}", a["telegram_id"])
    return _ok()


@routes.post("/hub/api/admin/create")
async def api_admin_create(request):
    c = await _pishva_ctx(request)
    b = await _body(request)
    tid = _int(b.get("telegram_id"), "آی‌دی تلگرام")
    role = str(b.get("role") or "")
    if tid <= 0 or tid == PISHVA_ID:
        raise _fail("bad_request", "آی‌دی نامعتبر است.")
    if role not in (ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER):
        raise _fail("bad_request", "نقشِ نامعتبر.")
    name = _text(b.get("name"), 2, 40, "نام")
    existing = await db.get_admin(tid)
    if existing:
        await db.update_admin_role_active(tid, role)
    else:
        await db.create_admin(tid, str(b.get("username") or "").lstrip("@")[:40], name, role)
    await db.log_action(c.uid, "admin_create", f"افزودنِ مدیر {name} ({ROLE_LABELS[role]})", tid)
    await _send(tid, f"✅ دسترسی تأیید شد\n💼 {ROLE_LABELS[role]}\n\n/start بزنید.")
    return _ok()


# ─── درخواست‌ها ──────────────────────────────────────────────────
@routes.get("/hub/api/requests")
async def api_requests(request):
    await _pishva_ctx(request)
    names, pend, kick_rows, scan_rows = await asyncio.gather(
        _name_map(), db.get_pending_requests(), db.get_pending_kick_requests(), db.get_pending_scan_requests())
    pnames = await db.get_players_names([r["player_id"] for r in kick_rows])   # یک کوئری، نه یکی برای هر درخواست
    acc = [{"id": r["id"], "name": r["full_name"] or "؟", "username": r["username"] or "",
            "role": ROLE_LABELS.get(r["role"], r["role"]), "message": r["message"] or "",
            "at": _dt(r["requested_at"])} for r in pend]
    kicks = [{"id": r["id"], "admin": names.get(r["admin_id"], "؟"),
              "player": pnames.get(r["player_id"], "؟"), "player_id": r["player_id"],
              "at": _dt(r["requested_at"])} for r in kick_rows]
    scans = []
    for r in scan_rows:
        rows = await scan_svc.request_rows(r)
        scans.append({"id": r["id"], "admin": names.get(r["admin_id"], "؟"), "count": r["item_count"] or len(rows),
                      "date": r["match_date"] or "", "at": _dt(r["created_at"]), "rows": rows[:80]})
    return hub._json({"access": acc, "kicks": kicks, "scans": scans})


@routes.post("/hub/api/request/{id}/{act}")
async def api_request_decide(request):
    c = await _pishva_ctx(request)
    act = request.match_info["act"]
    if act not in ("approve", "reject"):
        raise web.HTTPNotFound()
    req = await db.get_access_request(_pid(request))
    if not req or req["status"] != "pending":
        raise _fail("done", "این درخواست قبلاً بررسی شده.", 409)
    if act == "approve":
        await db.update_access_request(req["id"], "approved")
        await db.create_admin(req["telegram_id"], req["username"], req["full_name"], req["role"])
        await db.close_other_open_requests(req["telegram_id"], req["id"])
        await _send(req["telegram_id"], f"✅ دسترسی تأیید شد\n💼 {ROLE_LABELS.get(req['role'], '')}\n\n/start بزنید.")
    else:
        await db.update_access_request(req["id"], "rejected")
        await _send(req["telegram_id"], "❌ درخواست دسترسی شما رد شد.\nبرای اطلاعات بیشتر با مدیر ارشد تماس بگیرید.")
    await db.log_action(c.uid, f"access_{act}", f"{'تأیید' if act == 'approve' else 'رد'} درخواستِ دسترسیِ {req['full_name']}", req["telegram_id"])
    return _ok()


@routes.post("/hub/api/kick-request/{id}/{act}")
async def api_kick_decide(request):
    c = await _pishva_ctx(request)
    act = request.match_info["act"]
    if act not in ("approve", "reject"):
        raise web.HTTPNotFound()
    r = await db.get_kick_request(_pid(request))
    if not r or r["status"] != "pending":
        raise _fail("done", "این درخواست دیگر معتبر نیست.", 409)
    p = await db.get_player(r["player_id"])
    pname = p["full_name"] if p else ""
    if act == "approve":
        await db.update_kick_request(r["id"], "approved")
        if p:
            await db.update_player(r["player_id"], status="kicked")
            await db.log_action(c.uid, "kick_player", f"تاییدِ درخواستِ اخراج: {pname}", r["player_id"])
            await _destructive(r["admin_id"], "kick_player")
        await _send(r["admin_id"], f"✅ درخواستِ اخراجِ «{pname}» توسط مدیر ارشد تایید شد.")
    else:
        await db.update_kick_request(r["id"], "rejected")
        await _send(r["admin_id"], f"❌ درخواستِ اخراجِ «{pname}» توسط مدیر ارشد رد شد.")
    return _ok()


# ─── تنظیمات ─────────────────────────────────────────────────────
SETTING_DEFS = [
    ("notifications_enabled", "اعلانات", "عمومی", "1"),
    ("communications_enabled", "مخابرات", "عمومی", "1"),
    ("help_enabled", "راهنما", "عمومی", "1"),
    ("ai_online", "هوش مصنوعی", "عمومی", "1"),
    ("match_registration_enabled", "ثبتِ مسابقه", "مسابقات و تیم‌ها", "1"),
    ("scan_enabled", "ثبت با عکس (برای مدیران)", "مسابقات و تیم‌ها", "1"),
    ("live_chess_enabled", "شطرنجِ زنده", "مسابقات و تیم‌ها", "1"),
    ("hub_enabled", "پنل من (هاب)", "مسابقات و تیم‌ها", "1"),
    ("team_mode_enabled", "حالتِ تیمی", "مسابقات و تیم‌ها", "0"),
    ("team_registration_enabled", "ثبت‌نام با تیم", "مسابقات و تیم‌ها", "1"),
    ("managers_can_create_teams", "ساختِ تیم توسطِ مدیر", "مسابقات و تیم‌ها", "0"),
    ("admin_login_enabled", "ورودِ ادمین", "مدیران و دسترسی", "1"),
    ("bot_active_for_admins", "ربات برای ادمین‌ها روشن", "مدیران و دسترسی", "1"),
    ("admin_dashboard_enabled", "داشبوردِ ادمین‌ها", "مدیران و دسترسی", "1"),
    ("admin_direct_kick_enabled", "اخراجِ مستقیمِ مدیران", "مدیران و دسترسی", "1"),
    ("principal_panel_enabled", "پنل مدیر مدرسه", "پنل‌ها و امنیت", "1"),
    ("admin_webpanel_enabled", "پنل وبِ ادمین", "پنل‌ها و امنیت", "1"),
    ("bug_report_to_pishva_enabled", "گزارشِ باگ", "پنل‌ها و امنیت", "1"),
]
TOGGLE_KEYS = {k for k, *_ in SETTING_DEFS}
TEXT_SETTINGS = {"announcement_group_id", "announcement_channel_id", "hijri_offset", "repair_reason"}
STATUS_LABELS = {"normal": "🟢 نرمال", "bad": "🟡 بد", "danger": "🔴 خطرناک", "aps": "🪽 APS"}


async def _settings_payload():
    # همه‌ی کلیدها با یک رفت‌وبرگشتِ شبکه (قبلاً ~۲۷ خوانش پشتِ‌سرِهم)
    defaults = {k: d for k, _l, _g, d in SETTING_DEFS}
    defaults.update({
        "top_players_mode": "auto", "scan_default_mode": "approval",
        "announcement_group_id": "", "announcement_channel_id": "",
        "hijri_offset": "0", "repair_reason": "",
        "system_status": "normal", "repair_mode": "0", "bot_update_mode": "0",
        "working_hours_system_enabled": "0", "working_hours_active": "0",
    })
    v = await db.get_settings_with_defaults(defaults)
    return {
        "items": [{"key": k, "label": l, "group": g, "on": v[k] == "1"} for k, l, g, _d in SETTING_DEFS],
        "top_players_mode": v["top_players_mode"],
        "scan_default_mode": "direct" if v["scan_default_mode"] == "direct" else "approval",
        "texts": {
            "announcement_group_id": v["announcement_group_id"],
            "announcement_channel_id": v["announcement_channel_id"],
            "hijri_offset": v["hijri_offset"],
            "repair_reason": v["repair_reason"],
        },
        "status": v["system_status"],
        "repair": v["repair_mode"] == "1",
        "update": v["bot_update_mode"] == "1",
        "hours": {"system": v["working_hours_system_enabled"] == "1",
                  "active": v["working_hours_active"] == "1"},
    }


@routes.get("/hub/api/settings")
async def api_settings(request):
    await _pishva_ctx(request)
    return hub._json(await _settings_payload())


@routes.post("/hub/api/settings/toggle")
async def api_settings_toggle(request):
    c = await _pishva_ctx(request)
    key = str((await _body(request)).get("key") or "")
    if key == "top_players_mode":
        cur = await db.get_setting(key, "auto")
        new = "manual" if cur != "manual" else "auto"
    elif key == "scan_default_mode":
        cur = await db.get_setting(key, "approval")
        new = "approval" if cur == "direct" else "direct"
    elif key in TOGGLE_KEYS:
        dflt = next(d for k, _l, _g, d in SETTING_DEFS if k == key)
        cur = await db.get_setting(key, dflt)
        new = "0" if cur == "1" else "1"
    else:
        raise _fail("bad_request", "تنظیمِ نامعتبر.")
    await db.set_setting(key, new)
    await db.log_action(c.uid, "toggle_setting", f"{key} -> {new}")
    if key == "hub_enabled":
        # چندین فراخوانیِ پشتِ‌سرِهمِ تلگرام (یکی برای هر مدیر) — نباید جوابِ دکمه را معطل کند
        _spawn(_resync_menu_buttons())
    return hub._json(await _settings_payload())


@routes.post("/hub/api/settings/text")
async def api_settings_text(request):
    c = await _pishva_ctx(request)
    b = await _body(request)
    key, val = str(b.get("key") or ""), str(b.get("value") or "").strip()
    if key not in TEXT_SETTINGS:
        raise _fail("bad_request", "تنظیمِ نامعتبر.")
    if key in ("announcement_group_id", "announcement_channel_id"):
        if val and not re.fullmatch(r"-?\d{5,20}", val):
            raise _fail("bad_request", "آی‌دیِ عددیِ گروه/کانال را وارد کنید (مثل ‎-100123456789).")
    elif key == "hijri_offset":
        if val not in ("-2", "-1", "0", "1", "2"):
            raise _fail("bad_request", "تصحیح باید بینِ ‎-۲ تا ۲ روز باشد.")
    else:
        val = val[:200]
    await db.set_setting(key, val)
    await db.log_action(c.uid, "toggle_setting", f"{key} -> {val[:40]}")
    return hub._json(await _settings_payload())


@routes.post("/hub/api/system/status")
async def api_system_status(request):
    c = await _pishva_ctx(request)
    new = str((await _body(request)).get("status") or "")
    if new not in STATUS_LABELS:
        raise _fail("bad_request", "وضعیتِ نامعتبر.")
    await db.set_setting("system_status", new)
    await db.log_action(c.uid, "set_status", f"تغییر وضعیت به: {new}")
    from helpers import box
    ts = now_shamsi()
    pname = await db.get_setting("pishva_display_name", "مدیر ارشد")
    if new == "danger":
        _broadcast_bg(f"{box('🔴 هشدار — وضعیت بحرانی')}\n\n⚠️ سیستم وارد وضعیت خطرناک شد.\n🛡️ پروتکل امنیتی فعال است.\n"
                         f"🔒 دسترسی شما موقتاً معلق شد.\n⏱️ `{ts}`\n\nمنتظر دستور {pname} باشید.")
    elif new == "aps":
        _broadcast_bg(f"{box('🪽 حالت امنیتی APS')}\n\n🔐 امنیت به سیستم APS واگذار شده.\n🔒 دسترسی همه قطع شده است.\n⏱️ `{ts}`")
    elif new == "normal":
        _broadcast_bg(f"🟢 سیستم به وضعیت نرمال بازگشت.\n✅ دسترسی شما فعال است.\n⏱️ `{ts}`")
    return hub._json(await _settings_payload())


@routes.post("/hub/api/system/repair")
async def api_system_repair(request):
    c = await _pishva_ctx(request)
    b = await _body(request)
    on = bool(b.get("on"))
    from helpers import box
    ts = now_shamsi()
    if on:
        if "reason" in b:
            await db.set_setting("repair_reason", str(b.get("reason") or "").strip()[:200])
        await asyncio.gather(db.set_setting("repair_mode", "1"), db.set_setting("bot_update_mode", "1"))
        reason = await db.get_setting("repair_reason", "")
        _broadcast_bg(f"{box('🔧 حالت تعمیر فعال شد')}\n\n🛠️ ربات در حال تعمیر و بروزرسانی است.\n⏱️ `{ts}`\n"
                         f"{'📝 دلیل: ' + _md(reason) if reason else ''}\n\nلطفاً منتظر بمانید.")
        await db.log_action(c.uid, "repair_on", "فعال‌سازی حالت تعمیر")
    else:
        await asyncio.gather(db.set_setting("repair_mode", "0"), db.set_setting("bot_update_mode", "0"))
        _broadcast_bg(f"✅ تعمیر پایان یافت. ربات آماده استفاده است.\n⏱️ `{ts}`")
        await db.log_action(c.uid, "repair_off", "غیرفعال‌سازی حالت تعمیر")
    return hub._json(await _settings_payload())


@routes.post("/hub/api/system/update")
async def api_system_update(request):
    c = await _pishva_ctx(request)
    on = bool((await _body(request)).get("on"))
    await db.set_setting("bot_update_mode", "1" if on else "0")
    if on:
        _broadcast_bg(f"🔄 ربات در حال آپدیت است. لطفاً منتظر بمانید.\n⏱️ `{now_shamsi()}`")
    await db.log_action(c.uid, "toggle_setting", f"bot_update_mode -> {'1' if on else '0'}")
    return hub._json(await _settings_payload())


# ─── لاگ اقدامات ────────────────────────────────────────────────
@routes.get("/hub/api/logs")
async def api_logs(request):
    await _pishva_ctx(request)
    q = request.query
    period = q.get("period", "today")
    if period not in ("today", "week", "month", "all"):
        period = "today"
    page = max(0, _int(q.get("page", 0), "page"))
    admin_id = _int(q["admin"], "admin") if q.get("admin") else None
    (rows, total), names = await asyncio.gather(db.get_action_logs(period, admin_id, page, 25), _name_map())
    try:
        from helpers import ACTION_LOG_LABELS as _AL
    except Exception:
        _AL = {}
    return hub._json({"total": total, "page": page, "pages": max(1, -(-total // 25)), "rows": [{
        "id": r["id"], "admin": names.get(r["admin_id"], str(r["admin_id"])),
        "type": r["action_type"], "label": (_AL.get(r["action_type"]) or ("", r["action_type"]))[1],
        "text": r["description"], "at": _dt(r["logged_at"]),
        "undone": bool(r["undone"]) if "undone" in r.keys() else False} for r in rows]})
