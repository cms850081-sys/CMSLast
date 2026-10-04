# -*- coding: utf-8 -*-
"""
hub_weather.py — بخشِ «آب‌وهوا»ی پنل هاب (سرپل‌ذهاب).

مسیرها (هر دو فقط برای مدیرِ واردشده‌ی هاب؛ هویت از initData تلگرام):
  GET  /hub/api/weather     پیش‌بینیِ کامل: الان، ۲۴ ساعت، ۵ روزِ آینده، کیفیتِ هوا، توصیه‌ها
  POST /hub/api/weather/ai  تحلیلِ هوش مصنوعی (با کش و فاصله‌ی زمانی بینِ درخواست‌ها)

داده از open-meteo (بدونِ کلید). نتیجه ۱۰ دقیقه در حافظه کش می‌شود و اگر شبکه قطع بود،
نسخه‌ی قبلی (تا ۶ ساعت) برگردانده می‌شود، پس پنل هیچ‌وقت خالی نمی‌ماند.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from aiohttp import web

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()

TEHRAN = timezone(timedelta(hours=3, minutes=30))
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
CACHE_TTL = 600            # ۱۰ دقیقه
CACHE_STALE_MAX = 6 * 3600
AI_TTL = 1800              # ۳۰ دقیقه
AI_COOLDOWN = 12           # ثانیه، برای هر کاربر

_cache = {"data": None, "at": 0.0}
_lock = None
_ai_cache = {"key": None, "text": "", "at": 0.0}
_ai_last = {}              # uid -> زمانِ آخرین درخواست

# کدِ WMO → (برچسبِ فارسی، نوعِ صحنه برای انیمیشن)
_WMO = {
    0: ("آسمانِ صاف", "clear"), 1: ("تقریباً صاف", "clear"), 2: ("کمی ابری", "partly"),
    3: ("ابری", "cloud"), 45: ("مه‌آلود", "fog"), 48: ("مهِ یخ‌زده", "fog"),
    51: ("نم‌نمِ باران", "drizzle"), 53: ("نم‌نمِ باران", "drizzle"), 55: ("نم‌نمِ شدید", "drizzle"),
    56: ("نم‌نمِ یخ‌زده", "drizzle"), 57: ("نم‌نمِ یخ‌زده", "drizzle"),
    61: ("باران ملایم", "rain"), 63: ("بارانی", "rain"), 65: ("باران شدید", "rain"),
    66: ("بارانِ یخ‌زده", "rain"), 67: ("بارانِ یخ‌زده", "rain"),
    71: ("برف ملایم", "snow"), 73: ("برفی", "snow"), 75: ("برفِ سنگین", "snow"), 77: ("دانه‌های برف", "snow"),
    80: ("رگبار", "rain"), 81: ("رگبار", "rain"), 82: ("رگبارِ شدید", "rain"),
    85: ("رگبارِ برف", "snow"), 86: ("رگبارِ برفِ سنگین", "snow"),
    95: ("رعدوبرق", "storm"), 96: ("رعدوبرق و تگرگ", "storm"), 99: ("رعدوبرق و تگرگِ شدید", "storm"),
}


def _wmo(code):
    return _WMO.get(code, ("نامشخص", "cloud"))


def _num(v, nd=0):
    if v is None:
        return None
    try:
        return round(float(v), nd) if nd else int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _aqi_level(aqi):
    if aqi is None:
        return None, None
    for hi, lvl, label in ((50, 0, "پاک"), (100, 1, "قابل‌قبول"), (150, 2, "ناسالم برای گروه‌های حساس"),
                           (200, 3, "ناسالم"), (300, 4, "بسیار ناسالم")):
        if aqi <= hi:
            return lvl, label
    return 5, "خطرناک"


def _hhmm(iso):
    try:
        return str(iso)[11:16]
    except Exception:
        return None


# ─── دریافت و تبدیل ─────────────────────────────────────────────────
async def _fetch_raw():
    import auth
    lat, lon = auth.SARPOL_LAT, auth.SARPOL_LON
    fparams = {
        "latitude": lat, "longitude": lon, "timezone": "Asia/Tehran", "forecast_days": 6,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,is_day,precipitation,weather_code,"
                   "cloud_cover,pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "precipitation_sum,sunrise,sunset,uv_index_max,wind_speed_10m_max",
    }
    aparams = {"latitude": lat, "longitude": lon, "timezone": "Asia/Tehran",
               "current": "us_aqi,pm10,pm2_5,dust,ozone"}
    async with httpx.AsyncClient(timeout=8.0) as c:
        f, a = await asyncio.gather(c.get(FORECAST_URL, params=fparams), c.get(AIR_URL, params=aparams),
                                    return_exceptions=True)
    if isinstance(f, Exception):
        raise f
    f.raise_for_status()
    air = None
    if not isinstance(a, Exception):
        try:
            a.raise_for_status()
            air = a.json().get("current") or None
        except Exception as e:  # کیفیتِ هوا اختیاری‌ست
            logger.warning("weather: air quality failed: %r", e)
    else:
        logger.warning("weather: air quality failed: %r", a)
    return f.json(), air


def _build(fj, air):
    cur = fj.get("current") or {}
    code = cur.get("weather_code")
    label, kind = _wmo(code)
    now = {
        "temp": _num(cur.get("temperature_2m")), "feels": _num(cur.get("apparent_temperature")),
        "hum": _num(cur.get("relative_humidity_2m")), "is_day": int(cur.get("is_day", 1) or 0),
        "precip": _num(cur.get("precipitation"), 1), "code": code, "label": label, "kind": kind,
        "cloud": _num(cur.get("cloud_cover")), "pressure": _num(cur.get("pressure_msl")),
        "wind": _num(cur.get("wind_speed_10m")), "wind_dir": _num(cur.get("wind_direction_10m")),
        "gust": _num(cur.get("wind_gusts_10m")),
    }
    hr = fj.get("hourly") or {}
    hours = []
    for i, t in enumerate((hr.get("time") or [])[:24]):
        c = (hr.get("weather_code") or [None] * 99)[i]
        hours.append({
            "h": _hhmm(t), "temp": _num((hr.get("temperature_2m") or [None] * 99)[i]),
            "pop": _num((hr.get("precipitation_probability") or [None] * 99)[i]) or 0,
            "code": c, "kind": _wmo(c)[1], "day": int((hr.get("is_day") or [1] * 99)[i] or 0),
        })
    d = fj.get("daily") or {}
    days = []
    for i, dt in enumerate(d.get("time") or []):
        def g(k, _i=i):
            arr = d.get(k) or []
            return arr[_i] if _i < len(arr) else None
        c = g("weather_code")
        days.append({
            "date": dt, "code": c, "label": _wmo(c)[0], "kind": _wmo(c)[1],
            "tmax": _num(g("temperature_2m_max")), "tmin": _num(g("temperature_2m_min")),
            "pop": _num(g("precipitation_probability_max")) or 0, "rain": _num(g("precipitation_sum"), 1) or 0,
            "uv": _num(g("uv_index_max"), 1), "wind": _num(g("wind_speed_10m_max")),
            "sunrise": _hhmm(g("sunrise")), "sunset": _hhmm(g("sunset")),
        })
    air_out = None
    if air and air.get("us_aqi") is not None:
        lvl, lab = _aqi_level(_num(air.get("us_aqi")))
        air_out = {"aqi": _num(air.get("us_aqi")), "pm25": _num(air.get("pm2_5"), 1), "pm10": _num(air.get("pm10"), 1),
                   "dust": _num(air.get("dust")), "ozone": _num(air.get("ozone")), "level": lvl, "label": lab}
    try:
        import auth
        moon = auth.moon_phase_emoji()
    except Exception:
        moon = "🌙"
    out = {"ok": True, "city": "سرپل‌ذهاب", "now": now, "hours": hours, "days": days, "air": air_out, "moon": moon,
           "updated": datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M")}
    out["advice"] = _advice(out)
    out["headline"] = _headline(out)
    return out


# ─── توصیه‌های قاعده‌محور (بدونِ هوش مصنوعی، فوری و قابل‌اعتماد) ─────────
def _advice(w):
    n, today = w["now"], (w["days"] or [{}])[0]
    feels = n["feels"] if n["feels"] is not None else n["temp"]
    out = []
    if feels is not None:
        if feels <= 0:
            t = "کاپشنِ ضخیم، کلاه و دستکش لازمه؛ سرما جدیه."
        elif feels <= 8:
            t = "کاپشنِ گرم بپوش؛ هوا سرده."
        elif feels <= 15:
            t = "یه کاپشنِ سبک یا ژاکت کافیه."
        elif feels <= 22:
            t = "لباسِ ملایم و لایه‌ای؛ هوا مطبوعه."
        elif feels <= 30:
            t = "لباسِ سبک مناسبه."
        elif feels <= 36:
            t = "لباسِ نخیِ روشن بپوش و آب همراهت باشه."
        else:
            t = "هوا داغه؛ ظهر تا جای ممکن بیرون نرو و آب زیاد بخور."
        out.append({"k": "wear", "icon": "👕", "title": "پوشش", "text": t})
    pops = [h["pop"] for h in w["hours"][:12]] or [today.get("pop", 0)]
    mp = max(pops + [0])
    if mp >= 60:
        out.append({"k": "rain", "icon": "☔", "title": "چتر", "text": f"احتمالِ بارش تا {mp}٪؛ حتماً چتر یا بارانی بردار."})
    elif mp >= 30:
        out.append({"k": "rain", "icon": "🌂", "title": "چتر", "text": f"احتمالِ بارش {mp}٪ هست؛ یه چترِ کوچک توی کیف بد نیست."})
    else:
        out.append({"k": "rain", "icon": "🌤️", "title": "چتر", "text": "چتر لازم نیست؛ بارشی در راه نیست."})
    air = w.get("air")
    if air and ((air["aqi"] or 0) > 100 or (air["dust"] or 0) > 100):
        out.append({"k": "air", "icon": "😷", "title": "کیفیتِ هوا",
                    "text": "هوا آلوده یا گرد‌وغباریه؛ ماسک بزن و فعالیتِ سنگینِ بیرون رو کم کن."})
    elif air:
        out.append({"k": "air", "icon": "🍃", "title": "کیفیتِ هوا", "text": "هوا برای نفس‌کشیدن و بازیِ بیرون مشکلی نداره."})
    if (today.get("uv") or 0) >= 6:
        out.append({"k": "uv", "icon": "🧴", "title": "آفتاب", "text": f"شاخصِ UV امروز {today['uv']:g} هست؛ کلاه و ضدآفتاب فراموش نشه."})
    if (n["gust"] or 0) >= 45:
        out.append({"k": "wind", "icon": "💨", "title": "باد", "text": f"وزشِ باد تا {n['gust']} کیلومتر بر ساعت؛ مراقبِ وسایلِ سبک باش."})
    ok_out = (feels is not None and 10 <= feels <= 31 and mp < 30 and (n["gust"] or 0) < 40
              and not (air and (air["aqi"] or 0) > 100))
    out.append({"k": "yard", "icon": "🏃" if ok_out else "🏠", "title": "حیاطِ مدرسه / بیرون",
                "text": "شرایط برای فعالیتِ بیرون عالیه." if ok_out else "امروز فعالیتِ داخلی (مثلاً شطرنج!) انتخابِ بهتریه."})
    return out


def _headline(w):
    n = w["now"]
    if n["temp"] is None:
        return "اطلاعاتِ هوا در دسترس نیست."
    mp = max([h["pop"] for h in w["hours"][:12]] or [0])
    rain = "بارشی در راه نیست." if mp < 30 else f"احتمالِ بارش {mp}٪ هست."
    return f"الان {n['temp']}° و {n['label']}؛ {rain}"


async def _get_weather():
    """نسخه‌ی کش‌دار؛ هم‌زمان چند درخواست فقط یک بار به شبکه می‌روند."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    now = time.monotonic()
    if _cache["data"] and now - _cache["at"] < CACHE_TTL:
        return _cache["data"]
    async with _lock:
        now = time.monotonic()
        if _cache["data"] and now - _cache["at"] < CACHE_TTL:
            return _cache["data"]
        if httpx is None:
            raise RuntimeError("httpx not installed")
        try:
            fj, air = await _fetch_raw()
            data = _build(fj, air)
            _cache.update(data=data, at=time.monotonic())
            return data
        except Exception as e:
            logger.warning("weather: fetch failed: %r", e)
            if _cache["data"] and now - _cache["at"] < CACHE_STALE_MAX:
                return dict(_cache["data"], stale=True)
            raise


def _json(data, status=200):
    import json
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


# ─── مسیرها ──────────────────────────────────────────────────────────
@routes.get("/hub/api/weather")
async def api_weather(request):
    import hub
    await hub._require_admin(request)
    try:
        return _json(await _get_weather())
    except Exception:
        return _json({"ok": False, "error": "weather_unavailable", "message": "الان دریافتِ آب‌وهوا ممکن نشد."}, 503)


def _ai_prompt(w):
    n, t = w["now"], (w["days"] or [{}])[0]
    air = w.get("air") or {}
    days = "؛ ".join(f"{d['date'][5:]}: {d['tmin']} تا {d['tmax']}°، {d['label']}، بارش {d['pop']}٪" for d in w["days"][1:6])
    return (
        "تو دستیارِ هواشناسِ مهربان و خودمونیِ یه مدرسه‌ی شطرنج در سرپل‌ذهاب هستی. "
        "بر اساسِ داده‌ی زیر، یه تحلیلِ کوتاه و کاربردی به فارسیِ محاوره‌ای بنویس (حداکثر ۶ خط، بدون تیتر و بدون ستاره‌ی مارک‌داون). "
        "اولین خط خلاصه‌ی امروز باشه، بعد توصیه‌ی عملی (پوشش، رفت‌وآمد، حیاط یا فعالیتِ داخلی)، و آخرش یه جمله درباره‌ی روندِ چند روزِ آینده. "
        "عدد از خودت نساز؛ فقط از داده‌ی زیر استفاده کن.\n\n"
        f"الان: {n['temp']}° (حسِ واقعی {n['feels']}°)، {n['label']}، رطوبت {n['hum']}٪، باد {n['wind']} و وزش تا {n['gust']} km/h.\n"
        f"امروز: {t.get('tmin')} تا {t.get('tmax')}°، احتمالِ بارش {t.get('pop')}٪، UV {t.get('uv')}، غروب {t.get('sunset')}.\n"
        f"کیفیتِ هوا: AQI {air.get('aqi')} ({air.get('label')})، گرد‌وغبار {air.get('dust')}.\n"
        f"پنج روزِ بعد: {days}."
    )


@routes.post("/hub/api/weather/ai")
async def api_weather_ai(request):
    import hub
    _, _, user = await hub._require_admin(request)
    uid = int(user["id"])
    now = time.monotonic()
    if now - _ai_last.get(uid, 0) < AI_COOLDOWN:
        return _json({"ok": False, "error": "slow_down", "message": "چند ثانیه صبر کن و دوباره بزن."}, 429)
    _ai_last[uid] = now
    try:
        w = await _get_weather()
    except Exception:
        return _json({"ok": False, "error": "weather_unavailable", "message": "الان دریافتِ آب‌وهوا ممکن نشد."}, 503)
    key = (w["updated"][:15], w["now"]["temp"], w["now"]["code"])  # تقریباً هر ۱۰ دقیقه
    if _ai_cache["key"] == key and _ai_cache["text"] and now - _ai_cache["at"] < AI_TTL:
        return _json({"ok": True, "text": _ai_cache["text"], "cached": True})
    try:
        import ai_assistant
        if not await ai_assistant._is_ai_online():
            return _json({"ok": False, "error": "ai_off", "message": "دستیارِ هوش مصنوعی فعلاً خاموشه."}, 503)
        data = await ai_assistant._call_gemini([{"role": "user", "parts": [{"text": _ai_prompt(w)}]}], None)
        text = "".join(p.get("text", "") for p in ai_assistant._extract_parts(data) if not p.get("thought")).strip()
        if not text:
            raise ValueError("empty AI reply")
        _ai_cache.update(key=key, text=text, at=now)
        return _json({"ok": True, "text": text})
    except Exception as e:
        logger.warning("weather ai failed: %r", e)
        return _json({"ok": False, "error": "ai_failed", "message": "تحلیل الان ممکن نشد؛ کمی بعد دوباره امتحان کن."}, 503)
