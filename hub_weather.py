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
# چندمدلی: open-meteo مدل‌های مستقل (ECMWF/ICON/GFS/GEM) را جدا برمی‌گرداند؛ «توافقِ مدل‌ها» و CAPE
# (ناپایداریِ جوّ) رگبارهای محلی را که بهترین‌مدل نمی‌بیند آشکار می‌کند.
ENSEMBLE_MODELS = "ecmwf_ifs025,icon_global,gfs_global,gem_global"
RAIN_MM = 0.1              # حداقلِ بارش در ساعت که «بارش» حساب شود
CAPE_WARN = 500            # J/kg: ناپایداریِ متوسط → احتمالِ رگبارِ محلی
CAPE_HIGH = 1000           # J/kg: ناپایداریِ زیاد → رگبار/رعدوبرقِ محتمل
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
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,is_day,precipitation,rain,showers,weather_code,"
                   "cloud_cover,pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "hourly": "temperature_2m,precipitation_probability,precipitation,showers,cape,weather_code,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                 "precipitation_sum,sunrise,sunset,uv_index_max,wind_speed_10m_max",
    }
    aparams = {"latitude": lat, "longitude": lon, "timezone": "Asia/Tehran",
               "current": "us_aqi,pm10,pm2_5,dust,ozone"}
    mparams = {"latitude": lat, "longitude": lon, "timezone": "Asia/Tehran", "forecast_days": 2,
               "models": ENSEMBLE_MODELS, "hourly": "precipitation,showers,cape"}
    async with httpx.AsyncClient(timeout=8.0) as c:
        f, a, m = await asyncio.gather(c.get(FORECAST_URL, params=fparams), c.get(AIR_URL, params=aparams),
                                       c.get(FORECAST_URL, params=mparams), return_exceptions=True)
    if isinstance(f, Exception):
        raise f
    f.raise_for_status()
    multi = None
    if not isinstance(m, Exception):
        try:
            m.raise_for_status()
            multi = m.json().get("hourly") or None
        except Exception as e:  # چندمدلی اختیاری‌ست؛ بدونِ آن هم پنل کار می‌کند
            logger.warning("weather: multi-model failed: %r", e)
    else:
        logger.warning("weather: multi-model failed: %r", m)
    air = None
    if not isinstance(a, Exception):
        try:
            a.raise_for_status()
            air = a.json().get("current") or None
        except Exception as e:  # کیفیتِ هوا اختیاری‌ست
            logger.warning("weather: air quality failed: %r", e)
    else:
        logger.warning("weather: air quality failed: %r", a)
    fj = f.json()
    fj["_multi"] = multi
    return fj, air


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
    times = hr.get("time") or []
    multi = fj.get("_multi") or {}
    mtimes = multi.get("time") or []
    mkeys_p = [k for k in multi if k.startswith("precipitation_")]
    mkeys_c = [k for k in multi if k.startswith("cape_")]

    def col(k, i):
        arr = hr.get(k) or []
        return arr[i] if i < len(arr) else None

    def mcol(k, t):
        try:
            arr = multi.get(k) or []
            return arr[mtimes.index(t)]
        except (ValueError, IndexError):
            return None

    # «ساعتِ جاری» به وقتِ تهران؛ لیست از همین ساعت شروع می‌شود (قبلاً از ۰۰:۰۰ امروز شروع می‌شد
    # و «۱۲ ساعتِ آینده» در عصر عملاً صبحِ امروز را نگاه می‌کرد).
    now_key = datetime.now(TEHRAN).strftime("%Y-%m-%dT%H:00")
    start = 0
    for i, t in enumerate(times):
        if str(t) >= now_key:
            start = i
            break
    hours = []
    for i in range(start, min(start + 24, len(times))):
        t = times[i]
        c = col("weather_code", i)
        pop = _num(col("precipitation_probability", i)) or 0
        mm_best = float(col("precipitation", i) or 0) + 0.0
        mm_models = [float(v) for v in (mcol(k, t) for k in mkeys_p) if v is not None]
        capes = [float(v) for v in ([col("cape", i)] + [mcol(k, t) for k in mkeys_c]) if v is not None]
        wet = sum(1 for v in mm_models + [mm_best] if v >= RAIN_MM)
        total = len(mm_models) + 1
        cape = max(capes) if capes else 0
        mm = max(mm_models + [mm_best])
        # ریسکِ نهایی: بیشینه‌ی احتمالِ مدلِ اصلی، «توافقِ مدل‌ها» و ناپایداری (رگبارِ محلی)
        agree = round(100 * wet / total) if total else 0
        risk = max(pop, agree)
        if cape >= CAPE_HIGH:
            risk = max(risk, 45)
        elif cape >= CAPE_WARN:
            risk = max(risk, 30)
        hours.append({
            "h": _hhmm(t), "temp": _num(col("temperature_2m", i)),
            "pop": min(100, risk), "pop_model": pop, "agree": agree, "mm": round(mm, 1),
            "cape": _num(cape), "code": c, "kind": _wmo(c)[1], "day": int(col("is_day", i) or 0),
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
    now["raining"] = bool((_num(cur.get("rain"), 1) or 0) > 0 or (_num(cur.get("showers"), 1) or 0) > 0
                          or (now["precip"] or 0) > 0 or kind in ("rain", "drizzle", "storm"))
    out = {"ok": True, "city": "سرپل‌ذهاب", "now": now, "hours": hours, "days": days, "air": air_out, "moon": moon,
           "updated": datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M")}
    out["nowcast"] = _nowcast(out)
    out["advice"] = _advice(out)
    out["hint"] = _hint(out, out["advice"])
    out["headline"] = _headline(out)
    return out


# ─── توصیه‌های قاعده‌محور (بدونِ هوش مصنوعی، فوری و منطقی) ────────────
# تنها منبعِ حقیقت: هم کادرِ اصلیِ پنل، هم نوارِ خانه، هم پرامپتِ تحلیلِ هوش مصنوعی از همین‌جا می‌آیند
# تا هیچ‌وقت عنوان و متن با هم تناقض نداشته باشند. هر مورد: k, ic (نامِ آیکون), level (ok/warn/bad), title, text, short
def _lvl_rank(x):
    return {"bad": 0, "warn": 1, "ok": 2}.get(x.get("level"), 2)


def _wear(w):
    n, days = w["now"], w["days"]
    hr = datetime.now(TEHRAN).hour
    day = bool(n["is_day"])
    nxt = (not day) and hr >= 12                      # عصر/شب → برنامه برای فردا
    ref = (days[1] if nxt and len(days) > 1 else (days[0] if days else {})) or {}
    tmin, tmax = ref.get("tmin"), ref.get("tmax")
    feels = n["feels"] if n["feels"] is not None else n["temp"]
    when = "فردا" if nxt else "امروز"
    if feels is None and tmin is None:
        return {"k": "wear", "ic": "jacket", "outfit": "jacket", "level": "ok", "title": "پوشش",
                "text": "اطلاعاتِ دما در دسترس نیست.", "short": "پوشش"}
    morning_window = (not day) or hr < 11            # لایه‌ای فقط وقتی معنی دارد که هنوز صبحِ خنک پیش رو/در جریان است
    # صبحِ خنک و ظهرِ گرم → لایه‌ای (رایج‌ترین موقعیتِ واقعی در سرپل‌ذهاب)
    if (tmin is not None and tmax is not None and tmax - tmin >= 10 and tmin <= 19 and tmax >= 24 and morning_window):
        return {"k": "wear", "ic": "jacket", "outfit": "jacket", "level": "ok", "title": "لایه‌ای بپوش",
                "text": f"{when} صبح {tmin}° و ظهر {tmax}° می‌شه؛ یه ژاکتِ سبک بپوش که ظهر بشه درش آورد.",
                "short": f"صبح {tmin}° · ظهر {tmax}°"}
    t = feels if (day or tmin is None) else tmin     # روز: حسِ همین لحظه؛ شب: کمینه‌ی روزِ مرجع (صبحِ رفت‌وآمد)
    if t <= 0:
        lv = ("heavy", "پوششِ خیلی گرم", "کاپشنِ ضخیم، کلاه و دستکش بپوش.", "کاپشنِ ضخیم")
    elif t <= 9:
        lv = ("coat", "کاپشنِ گرم", "کاپشنِ گرم و لباسِ زیرِ گرم بپوش.", "کاپشنِ گرم")
    elif t <= 17:
        lv = ("jacket", "ژاکت یا کاپشنِ سبک", "یه ژاکت یا کاپشنِ سبک کافیه.", "ژاکت بردار")
    elif t <= 25:
        lv = ("tee", "لباسِ سبک و راحت", "لباسِ سبک بپوش و یه لایه‌ی نازک همراهت باشه.", "لباسِ سبک")
    elif t <= 33:
        lv = ("tee", "لباسِ سبک", "لباسِ نخیِ روشن بپوش.", "لباسِ نخی")
    else:
        lv = ("tee", "لباسِ خیلی سبک", "لباسِ نخیِ روشن بپوش و آب همراهت باشه.", "لباسِ خیلی سبک")
    lead = f"{when} صبح حدودِ {t}° می‌شه؛ " if (not day) and tmin is not None else ""
    return {"k": "wear", "ic": lv[0], "outfit": lv[0], "level": "ok", "title": lv[1], "text": lead + lv[2], "short": lv[3]}


def _nowcast(w):
    """کوتاه‌مدت (۳ ساعتِ آینده): آیا بارش/رگبارِ محلی محتمل است؟ بر پایه‌ی مدل‌ها، نه رادار."""
    hs = (w.get("hours") or [])[:3]
    mp = max([h["pop"] for h in hs] or [0])
    mm = max([h.get("mm") or 0 for h in hs] or [0])
    cape = max([h.get("cape") or 0 for h in hs] or [0])
    if w["now"].get("raining"):
        level, text = "now", "الان بارش هست یا مدل‌ها بارشِ جاری نشان می‌دهند."
    elif mp >= 60 or mm >= 1:
        level, text = "likely", f"در ۳ ساعتِ آینده بارش محتمل است (تا {mp}٪)."
    elif mp >= 30 or cape >= CAPE_WARN:
        level, text = "possible", "جوّ ناپایدار است؛ رگبارِ کوتاهِ محلی ممکن است بیاید حتی اگر پیش‌بینیِ رسمی بارش نشان نمی‌دهد."
    else:
        level, text = "none", "تا ۳ ساعتِ آینده بارشِ مهمی دیده نمی‌شود."
    return {"level": level, "pop": mp, "mm": round(mm, 1), "cape": cape, "text": text}


def _advice(w):
    n = w["now"]
    days = w["days"] or [{}]
    t0 = days[0]
    day = bool(n["is_day"])
    kind = n["kind"]
    air = w.get("air")
    feels = n["feels"] if n["feels"] is not None else n["temp"]
    mp = max([h["pop"] for h in w["hours"][:12]] or [0])
    nc = w.get("nowcast") or {}
    gust = n["gust"] or 0
    items = []

    # بارش
    if kind == "storm":
        items.append({"k": "rain", "ic": "umbrella", "level": "bad", "title": "رعدوبرق", "short": "رعدوبرق",
                      "text": "رعدوبرق در راهه؛ زیرِ آسمونِ باز و کنارِ درخت‌ها نمون."})
    elif kind == "snow":
        items.append({"k": "rain", "ic": "umbrella", "level": "warn", "title": "برف", "short": "برف می‌بارد",
                      "text": "برف می‌بارد؛ کفِ زمین لغزنده‌ست، آهسته راه برو."})
    elif n.get("raining"):
        items.append({"k": "rain", "ic": "umbrella", "level": "bad", "title": "چتر", "short": "بارش هست",
                      "text": "الان بارش هست؛ چتر یا بارانی همراهت باشد."})
    elif nc.get("level") == "possible" and mp < 60:
        items.append({"k": "rain", "ic": "umbrella", "level": "warn", "title": "چتر", "short": "رگبارِ محلی ممکنه",
                      "text": "جوّ ناپایداره و رگبارِ ناگهانی ممکنه؛ یه چترِ کوچک همراه داشته باش."})
    elif mp >= 60:
        items.append({"k": "rain", "ic": "umbrella", "level": "bad", "title": "چتر", "short": "چتر ببر",
                      "text": f"احتمالِ بارش تا {mp}٪؛ چتر یا بارانی ببر."})
    elif mp >= 30:
        items.append({"k": "rain", "ic": "umbrella", "level": "warn", "title": "چتر", "short": "چترِ کوچک",
                      "text": f"احتمالِ بارش {mp}٪ هست؛ یه چترِ کوچک بد نیست."})
    else:
        items.append({"k": "rain", "ic": "umbrella", "level": "ok", "title": "چتر", "short": "بدونِ چتر",
                      "text": "بارشی در راه نیست؛ چتر لازم نیست."})

    # کیفیتِ هوا / گردوغبار
    if air:
        aqi, dust = air["aqi"] or 0, air["dust"] or 0
        if aqi > 100 or dust > 100:
            items.append({"k": "air", "ic": "mask", "level": "bad" if (aqi > 150 or dust > 200) else "warn", "title": "کیفیتِ هوا",
                          "short": "ماسک بزن", "text": "هوا آلوده یا پر از گردوغباره؛ ماسک بزن و کمتر بیرون بمون."})
        elif aqi > 50 or dust >= 50:
            items.append({"k": "air", "ic": "leaf", "level": "warn", "title": "کیفیتِ هوا", "short": "هوای متوسط",
                          "text": "کیفیتِ هوا متوسطه؛ اگه حساسیت داری ماسک بزن."})
        else:
            items.append({"k": "air", "ic": "leaf", "level": "ok", "title": "کیفیتِ هوا", "short": "هوای پاک",
                          "text": "هوا پاکه و برای نفس‌کشیدن مشکلی نداره."})

    # مه
    if kind == "fog":
        items.append({"k": "fog", "ic": "wind", "level": "warn", "title": "مه", "short": "دیدِ کم",
                      "text": "مه هست و دید کمه؛ موقعِ رد‌شدن از خیابون احتیاط کن."})

    # گرما و آفتاب (فقط روز)
    if day and feels is not None and feels >= 36:
        items.append({"k": "heat", "ic": "thermo", "level": "bad" if feels >= 42 else "warn", "title": "گرما", "short": "آب بخور",
                      "text": "هوا خیلی گرمه؛ آب زیاد بخور و ظهر تا جای ممکن بیرون نمون."})
    uv = t0.get("uv") or 0
    if day and uv >= 6:
        items.append({"k": "uv", "ic": "sun", "level": "warn", "title": "آفتاب", "short": "ضدآفتاب",
                      "text": f"شاخصِ UV امروز {uv:g} هست؛ کلاه و ضدآفتاب فراموش نشه و ظهر زیرِ آفتاب نمون."})

    # باد
    if gust >= 45:
        items.append({"k": "wind", "ic": "wind", "level": "warn", "title": "باد", "short": "باد تند",
                      "text": f"وزشِ باد تا {gust} کیلومتر بر ساعت؛ مراقبِ وسایلِ سبک باش."})

    # «بیرون مناسبه» فقط به‌عنوانِ خبرِ خوب (وقتی هیچ هشداری نیست)؛ موارد ناخوشایند قبلاً با چتر/ماسک/گرما/باد پوشش داده شده‌اند
    if day and feels is not None:
        bad_air = bool(air and ((air["aqi"] or 0) > 100 or (air["dust"] or 0) > 100))
        if 5 <= feels <= 33 and mp < 30 and gust < 40 and not bad_air and kind not in ("storm", "fog", "snow"):
            items.append({"k": "out", "ic": "tree", "level": "ok", "title": "بیرون", "short": "بیرون مناسبه",
                          "text": "برای رفت‌وآمد و بیرون‌بودن مناسبه."})

    items.sort(key=_lvl_rank)                       # اولویت: خطرها اول (sort پایدار است)
    return [_wear(w)] + items


def _hint(w, adv):
    """یک جمله‌ی کوتاه برای نوارِ خانه: مهم‌ترین هشدار (اگر هست) + پوشش."""
    wear, rest = adv[0], adv[1:]
    flagged = [x for x in rest if x["level"] in ("bad", "warn")]
    parts = ([flagged[0]["short"]] if flagged else []) + [wear["short"]]
    return " · ".join(parts)


def _headline(w):
    n = w["now"]
    if n["temp"] is None:
        return "اطلاعاتِ هوا در دسترس نیست."
    mp = max([h["pop"] for h in w["hours"][:12]] or [0])
    nc = w.get("nowcast") or {}
    if n.get("raining"):
        rain = "الان بارش هست."
    elif nc.get("level") == "possible":
        rain = "رگبارِ محلی ممکنه."
    else:
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


def report_lines(w):
    """خطوطِ متنیِ کامل برای رهگشا (و هر مصرف‌کننده‌ی متنیِ دیگر) — همه از همین یک منبع."""
    n, nc = w["now"], w.get("nowcast") or {}
    out = [f"- الان: {n['temp']}°C (حسِ {n['feels']}°)، {n['label']}، رطوبت {n['hum']}٪، "
           f"باد {n['wind']} و وزش تا {n['gust']} km/h"
           + (" — همین الان بارش هست" if n.get("raining") else "")]
    out.append(f"- کوتاه‌مدت (۳ ساعت): {nc.get('text', '')} [احتمال {nc.get('pop', 0)}٪، بارشِ بیشینه {nc.get('mm', 0)}mm، ناپایداری CAPE={nc.get('cape', 0)}]")
    wet = [h for h in w["hours"][:12] if h["pop"] >= 30 or (h.get("mm") or 0) >= 0.5]
    out.append("- ۱۲ ساعتِ آینده: " + ("؛ ".join(f"ساعت {h['h']} احتمال {h['pop']}٪" for h in wet[:6]) if wet else "بارشِ قابل‌توجهی دیده نمی‌شود"))
    for i, lab in enumerate(("امروز", "فردا", "پس‌فردا")):
        if i < len(w["days"]):
            d = w["days"][i]
            out.append(f"- {lab}: {d['tmin']} تا {d['tmax']}°C، {d['label']}، احتمال بارش {d['pop']}٪ ({d['rain']}mm)")
    out.append("- توجه: پیش‌بینیِ بارش مدلی است (رادار نیست)؛ رگبارِ کوچکِ محلی را ممکن است دیر یا اصلاً نشان ندهد. "
               "اگر کاربر گفت همین الان بارون می‌آید، حرفِ او را بپذیر و با پیش‌بینی بحث نکن.")
    return out


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
    hr = datetime.now(TEHRAN).hour
    part = "بامداد" if hr < 5 else "صبح" if hr < 11 else "ظهر" if hr < 15 else "عصر" if hr < 19 else "شب"
    days = "؛ ".join(f"{d['date'][5:]}: {d['tmin']} تا {d['tmax']}°، {d['label']}، بارش {d['pop']}٪" for d in w["days"][1:6])
    adv = " | ".join(a["text"] for a in w.get("advice", [])[:4])
    return (
        "نقش: تو یک گزارشگرِ هواشناسیِ کوتاه‌نویس هستی. مخاطبت یکی از مدیرانِ یک ربات است که در سرپل‌ذهاب زندگی می‌کند و روزها به مدرسه می‌رود.\n"
        "کارِ تو فقط توضیحِ وضعیتِ آب‌وهوا و کارهای روزمره‌ی مربوط به آن است (پوشش، چتر، ماسک، آب خوردن، رفت‌وآمد).\n"
        "قوانین سخت:\n"
        "• هرگز از «باشگاه»، «بازیکن»، «شطرنج»، «مسابقه»، «کلاس»، «مربی» یا هر چیزِ نامرتبط با آب‌وهوا حرف نزن؛ هیچ زمینه‌ای از این‌ها وجود ندارد.\n"
        "• عدد یا واقعیتِ جدید نساز؛ فقط از داده‌ی زیر استفاده کن و با «توصیه‌های محاسبه‌شده» تناقض نداشته باش.\n"
        "• اگر شب/بامداد است، درباره‌ی «فردا صبح / امروز صبح» حرف بزن، نه درباره‌ی آفتابِ همین لحظه.\n"
        "• فارسیِ محاوره‌ایِ ساده، حداکثر ۴ خط، بدون تیتر، بدون ستاره و بدون ایموجی.\n"
        "• خطِ اول: خلاصه‌ی وضعیت. خطِ دوم: مهم‌ترین توصیه‌ی عملی. خطِ آخر (اختیاری): روندِ چند روزِ آینده.\n\n"
        f"زمان: {part} (ساعتِ {hr}) به وقتِ سرپل‌ذهاب.\n"
        f"الان: {n['temp']}° (حسِ واقعی {n['feels']}°)، {n['label']}، رطوبت {n['hum']}٪، باد {n['wind']} و وزش تا {n['gust']} km/h.\n"
        f"امروز: {t.get('tmin')} تا {t.get('tmax')}°، احتمالِ بارش {t.get('pop')}٪، UV {t.get('uv')}، غروب {t.get('sunset')}.\n"
        f"کیفیتِ هوا: AQI {air.get('aqi')} ({air.get('label')})، گردوغبار {air.get('dust')}.\n"
        f"پنج روزِ بعد: {days}.\n"
        f"کوتاه‌مدت (۳ ساعت): {(w.get('nowcast') or {}).get('text', '')}\n"
        "• اگر کوتاه‌مدت گفت رگبارِ محلی ممکن است، حتماً در خطِ دوم یادآوری کن که پیش‌بینیِ رگبار قطعی نیست.\n"
        f"توصیه‌های محاسبه‌شده: {adv}"
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
    key = ("v3", w["updated"][:15], w["now"]["temp"], w["now"]["code"], (w.get("nowcast") or {}).get("level"))  # تقریباً هر ۱۰ دقیقه
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


# ─── سامانه‌ها: شبکه‌ی جوّیِ ایران و همسایه‌ها ──────────────────────────
# یک شبکه‌ی ۱ درجه روی ایران و حاشیه‌ی همسایه‌ها، در یک درخواستِ تکی (بدون تکه‌تکه‌کردن، بدون موازی).
# دلیل: تکه‌تکه‌کردنِ درخواست (چه موازی چه پشتِ‌سرهم) باعثِ ۴۲۹ از open-meteo شد. یک درخواستِ تکیِ
# کوچک‌تر، هر ۶ ساعت یک‌بار (یعنی حدودِ ۴ درخواست در روز)، هیچ سهمیه‌ای را به خطر نمی‌اندازد.
MAP_TTL = 6 * 3600
MAP_STALE_MAX = 24 * 3600
MAP_REGIONS = {
    # 15 ردیف × 23 ستون = 345 نقطه (تقریباً هم‌اندازه‌ی درخواستِ قبلیِ موفق)، ولی با فاصله‌ی ۱٫۵ درجه
    # روی محدوده‌ای خیلی وسیع‌تر (۴۳ تا ۲۲ شمالی، ۳۴ تا ۶۷ شرقی) — چون جزئیاتِ ریز لازم نیست،
    # این وسعت باعث میشه کل محدوده‌ای که توی نقشه دیده میشه رنگی باشه، نه فقط یه مربعِ کوچیکِ وسط.
    "iran": {"lat0": 43.0, "lon0": 34.0, "step": 1.5, "rows": 15, "cols": 23},
}
_map_cache = {}
_map_locks = {}


def _grid_points(spec):
    lats, lons = [], []
    for r in range(spec["rows"]):
        for c in range(spec["cols"]):
            lats.append(round(spec["lat0"] - r * spec["step"], 4))
            lons.append(round(spec["lon0"] + c * spec["step"], 4))
    return lats, lons


async def _fetch_grid(region, attempt=0):
    spec = MAP_REGIONS[region]
    lats, lons = _grid_points(spec)
    params = {
        "latitude": ",".join(str(x) for x in lats),
        "longitude": ",".join(str(x) for x in lons),
        "timezone": "Asia/Tehran",
        "daily": "precipitation_sum",
        "forecast_days": 1,
        "current": "temperature_2m,relative_humidity_2m,precipitation,cloud_cover,"
                   "pressure_msl,wind_speed_10m,wind_direction_10m",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(FORECAST_URL, params=params)
    if r.status_code == 429:
        if attempt >= 3:
            r.raise_for_status()
        wait = float(r.headers.get("Retry-After", 0)) or (2.0 * (2 ** attempt))
        await asyncio.sleep(min(wait, 20))
        return await _fetch_grid(region, attempt + 1)
    r.raise_for_status()
    items = r.json()
    if isinstance(items, dict):
        items = [items]
    if len(items) != len(lats):
        raise RuntimeError(f"grid size mismatch: got {len(items)}, expected {len(lats)}")

    def col(key, nd=0):
        out = []
        for it in items:
            v = (it.get("current") or {}).get(key)
            if v is None:
                out.append(None)
            elif nd:
                out.append(round(float(v), nd))
            else:
                out.append(int(round(float(v))))
        return out

    def dsum():
        out = []
        for it in items:
            arr = (it.get("daily") or {}).get("precipitation_sum") or []
            v = arr[0] if arr else None
            out.append(None if v is None else round(float(v), 1))
        return out

    return {
        "region": region,
        "rows": spec["rows"], "cols": spec["cols"],
        "lat0": spec["lat0"], "lon0": spec["lon0"], "step": spec["step"],
        "t": col("temperature_2m", 1), "pr": dsum(),
        "cl": col("cloud_cover"), "p": col("pressure_msl"),
        "ws": col("wind_speed_10m"), "wd": col("wind_direction_10m"),
        "h": col("relative_humidity_2m"),
        "updated": datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M"),
    }


async def _get_grid(region):
    now = time.monotonic()
    ent = _map_cache.get(region)
    if ent and ent["data"] and now - ent["at"] < MAP_TTL:
        return ent["data"]
    lock = _map_locks.setdefault(region, asyncio.Lock())
    async with lock:
        ent = _map_cache.get(region)
        now = time.monotonic()
        if ent and ent["data"] and now - ent["at"] < MAP_TTL:
            return ent["data"]
        if httpx is None:
            raise RuntimeError("httpx not installed")
        try:
            data = await _fetch_grid(region)
            _map_cache[region] = {"data": data, "at": time.monotonic()}
            return data
        except Exception as e:
            logger.exception("weather map: fetch failed (%s)", region)
            if ent and ent["data"] and now - ent["at"] < MAP_STALE_MAX:
                return dict(ent["data"], stale=True)
            raise RuntimeError(f"{type(e).__name__}: {e}") from e


@routes.get("/hub/api/weather/map")
async def api_weather_map(request):
    import hub
    await hub._require_admin(request)
    region = request.query.get("region", "iran")
    if region not in MAP_REGIONS:
        return _json({"ok": False, "error": "bad_region", "message": "منطقه‌ی نامعتبر است."}, 400)
    try:
        return _json(await _get_grid(region))
    except Exception as e:
        return _json({"ok": False, "error": "map_unavailable", "message": "داده‌ی نقشه الان دریافت نشد.",
                      "reason": str(e)[:200]}, 503)
