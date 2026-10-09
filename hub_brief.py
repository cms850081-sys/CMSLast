"""
hub_brief.py — «خلاصه در پنل»: داده‌ی صفحه‌ی خلاصه‌ی روزِ مدیر ارشد در «پنل من» (هاب).

دکمه‌ی «🖥️ خلاصه در پنل» زیر پیام صبحگاهی ربات، هاب را روی تبِ خلاصه باز می‌کند
(?tab=brief). این ماژول فقط دو مسیر دارد و هر دو فقط برای مدیر ارشد (initData امضاشده):

  GET  /hub/api/brief      ← داده‌ی ساخت‌یافته‌ی خلاصه (مسابقه‌ها، هوا، برنامه‌ی درسی، …)
  POST /hub/api/brief/ai   ← «نکته‌های هوشمند» رهگشا (۳ تا ۵ جمله‌ی کوتاه)؛ اختیاری و ناهمزمان:
                              اگر هوش مصنوعی در دسترس نباشد صفحه بدونِ آن کامل نمایش داده می‌شود.

هیچ‌چیز روی دیتابیس نمی‌نویسد. داده‌ها از همان توابعِ morning_brief.py می‌آیند تا پیام تلگرام
و صفحه‌ی پنل همیشه یک عدد را نشان بدهند (نه دو منبعِ مختلف).
"""
import asyncio
import json
import logging
import time
from datetime import datetime

from aiohttp import web

import database as db
import hub
import morning_brief as mb
from helpers import TEHRAN_TZ, today_shamsi, weekday_fa

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

AI_COOLDOWN = 15            # ثانیه بین دو درخواست
AI_CACHE_TTL = 10 * 60      # ثانیه
_ai = {"text": None, "at": 0.0, "last_req": 0.0, "lock": None}


def _j(data, status=200):
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


async def _pishva_only(request):
    is_pishva, _admin, _user = await hub._require_admin(request)
    if not is_pishva:
        raise hub._err(web.HTTPForbidden, "pishva_only")


def _part_of_day(hour: int) -> str:
    return ("بامداد" if hour < 5 else "صبح" if hour < 11 else "ظهر" if hour < 15
            else "عصر" if hour < 19 else "شب")


def _v(row, key, default=None):
    try:
        v = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if v is None else v


def _weather_payload(w):
    if not w:
        return None
    n = w.get("now") or {}
    t0 = (w.get("days") or [{}])[0]
    air = w.get("air") or None
    return {
        "city": w.get("city") or "سرپل‌ذهاب",
        "now": {k: n.get(k) for k in ("temp", "feels", "hum", "is_day", "label", "kind", "wind", "precip")},
        "today": {k: t0.get(k) for k in ("tmin", "tmax", "pop", "uv", "sunrise", "sunset", "rain", "wind")},
        "air": ({k: air.get(k) for k in ("aqi", "level", "label", "pm25")} if air else None),
        "advice": [{"title": a.get("title"), "text": a.get("text") or a.get("short")}
                   for a in (w.get("advice") or [])[:4]],
        "moon": w.get("moon"),
        "stale": bool(w.get("stale")),
    }


async def _timetable_payload():
    try:
        from school_timetable import lessons_for
        day, rows = await lessons_for()
    except Exception:
        logger.exception("hub_brief: timetable failed")
        return {"day": weekday_fa(), "holiday": False, "rows": [], "note": "برنامه در دسترس نیست."}
    if day == "جمعه":
        return {"day": day, "holiday": True, "rows": [], "note": "امروز جمعه است؛ مدرسه تعطیل است."}
    return {"day": day, "holiday": False,
            "rows": [{"cls": c, "lessons": list(ls)} for c, ls in rows],
            "note": "" if rows else f"برای {day} برنامه‌ای در سیستم ثبت نشده."}


@routes.get("/hub/api/brief")
async def api_brief(request):
    await _pishva_only(request)
    now = datetime.now(TEHRAN_TZ)
    since, label = await mb._window()
    (matches, pending, visits, tasks, w, tt, rules, name) = await asyncio.gather(
        mb._night_matches(since), mb._pending_matches(), mb._admin_visits(), mb._pending_tasks(),
        mb._weather(), _timetable_payload(), mb.list_rules(),
        db.get_setting(mb.KEY_NAME, mb.DEFAULT_NAME))
    ids = [x for r in matches for x in (r["wid"], r["bid"])]
    players = await mb._players_info(ids)

    m_out, counts = [], {"white": 0, "black": 0, "draw": 0, "cancelled": 0, "none": 0}
    for r in matches:
        res = _v(r, "result")
        counts[res if res in counts else "none"] += 1
        m_out.append({"id": _v(r, "id"), "wn": _v(r, "wn", "؟"), "wc": _v(r, "wc", ""),
                      "bn": _v(r, "bn", "؟"), "bc": _v(r, "bc", ""), "result": res})

    by_cls = {}
    for r in pending:
        by_cls.setdefault(_v(r, "wc") or _v(r, "bc") or "بدون کلاس", []).append(
            {"w": _v(r, "wn", "؟"), "b": _v(r, "bn", "؟")})

    return _j({
        "ok": True,
        "name": (name or "").strip() or mb.DEFAULT_NAME,
        "weekday": weekday_fa(), "date": today_shamsi(),
        "hour": now.hour, "minute": now.minute, "part": _part_of_day(now.hour),
        "generated_at": now.isoformat(),
        "window": {"label": label},
        "stats": {"matches": len(m_out), **counts, "pending": len(pending),
                  "players": len(players), "tasks": len(tasks), "rules": len(rules)},
        "matches": m_out,
        "pending": [{"cls": c, "items": it} for c, it in by_cls.items()],
        "players": [{"id": _v(p, "id"), "name": _v(p, "name", "؟"), "cls": _v(p, "cname", ""),
                     "w": _v(p, "w", 0), "d": _v(p, "d", 0), "l": _v(p, "l", 0),
                     "elite": bool(_v(p, "elite", 0)), "warn": _v(p, "warn", 0)} for p in players],
        "weather": _weather_payload(w),
        "visits": [{"name": _v(v, "name", "—"), "role": _v(v, "role", ""),
                    "last_active": str(_v(v, "last_active", "") or "")[:16]} for v in visits],
        "tasks": [{"title": _v(t, "title", ""), "who": _v(t, "who", "—")} for t in tasks],
        "timetable": tt,
    })


_AI_PROMPT = (
    "تو «رهگشا»، دستیار هوشمند LUX هستی. از روی داده‌های زیر برای مدیر ارشد «نکته‌های امروز» بنویس.\n"
    "قواعد: دقیقاً ۳ تا ۵ مورد؛ هر مورد یک جمله‌ی کوتاه (حداکثر ۱۴ کلمه) و کاربردی؛ هر مورد در یک خط جدا و با «• » شروع شود؛ "
    "فارسی و گرم؛ فقط از داده‌ها استفاده کن و چیزی نساز؛ مهم‌ترین کارِ امروز را اول بگو؛ هیچ مارک‌داون، عنوان یا مقدمه‌ای ننویس.\n"
    "{rules}\n=== داده‌ها ===\n{ctx}"
)


@routes.post("/hub/api/brief/ai")
async def api_brief_ai(request):
    await _pishva_only(request)
    if _ai["lock"] is None:
        _ai["lock"] = asyncio.Lock()
    now = time.monotonic()
    if _ai["text"] and now - _ai["at"] < AI_CACHE_TTL:
        return _j({"ok": True, "text": _ai["text"], "cached": True})
    if now - _ai["last_req"] < AI_COOLDOWN:
        return _j({"ok": False, "error": "cooldown"}, 429)
    async with _ai["lock"]:
        _ai["last_req"] = time.monotonic()
        try:
            import ai_assistant
            if not await ai_assistant._is_ai_online():
                return _j({"ok": False, "error": "ai_offline"}, 503)
            ctx_text = await mb._collect_context()
            rules = await mb.list_rules()
            rules_txt = ("قانون‌های اجباری مدیر ارشد (رعایت کن):\n" + "\n".join(f"- {r['content']}" for r in rules)) if rules else ""
            prompt = _AI_PROMPT.format(rules=rules_txt, ctx=ctx_text)
            data = await ai_assistant._call_gemini([{"role": "user", "parts": [{"text": prompt}]}], None)
            text = "".join(p.get("text", "") for p in ai_assistant._extract_parts(data) if not p.get("thought")).strip()
            lines = [ln.strip().lstrip("•-* ").strip() for ln in text.replace("**", "").splitlines()]
            lines = [ln for ln in lines if ln][:5]
            if not lines:
                raise ValueError("empty")
            _ai["text"], _ai["at"] = lines, time.monotonic()
            return _j({"ok": True, "text": lines})
        except Exception as e:
            logger.warning("hub_brief: AI highlights failed: %r", e)
            return _j({"ok": False, "error": "ai_failed"}, 503)
