import random
import time
import asyncio
import logging
from datetime import datetime, timezone, timedelta

_perf_logger = logging.getLogger("perf")
logger = logging.getLogger(__name__)

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

import database as db
import keyboards as kb
from helpers import safe_edit_message_text, now_shamsi, box, separator, pishva_display, notify_pishva
from config import (PISHVA_ID, PISHVA_PASSWORD, ROLE_PISHVA,
                     ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER,
                     ST_ROLE_SELECT, ST_PISHVA_PASSWORD, ST_ADMIN_USERNAME,
                     ST_ADMIN_FULLNAME, ST_ACCESS_REQUEST_MSG)

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None


# ─── زمان و خوش‌آمدگویی هوشمند و خودمونی (ساعت واقعی ایران) ────
IRAN_TZ = timezone(timedelta(hours=3, minutes=30))


# ─── فاز واقعی ماه بر اساس چرخه‌ی سینودیکی (۲۹.530588861 روزه) ─
_MOON_REF_NEW = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)  # ماه نوی مرجع
_SYNODIC_MONTH = 29.530588861  # طول متوسط یک چرخه‌ی کامل ماه (روز)


def moon_phase_emoji() -> str:
    """اموجی فاز فعلی ماه، محاسبه‌شده از زمان (نه موقعیت جغرافیایی).

    phase = (روزهای گذشته از ماه نوی مرجع mod طول چرخه) / طول چرخه
    عددی بین ۰ تا ۱ که ۰/۱ = ماه نو، ۰.۲۵ = تربیع اول،
    ۰.۵ = بدر کامل، ۰.۷۵ = تربیع آخر.
    """
    now = datetime.now(timezone.utc)
    days_since_new = (now - _MOON_REF_NEW).total_seconds() / 86400.0
    phase = (days_since_new % _SYNODIC_MONTH) / _SYNODIC_MONTH

    if phase < 0.0625 or phase >= 0.9375:
        return "🌑"  # ماه نو
    elif phase < 0.1875:
        return "🌒"  # هلال رو به رشد
    elif phase < 0.3125:
        return "🌓"  # تربیع اول
    elif phase < 0.4375:
        return "🌔"  # محدب رو به رشد
    elif phase < 0.5625:
        return "🌕"  # بدر کامل
    elif phase < 0.6875:
        return "🌖"  # محدب رو به کاهش
    elif phase < 0.8125:
        return "🌗"  # تربیع آخر
    else:
        return "🌘"  # هلال رو به کاهش


_GREETINGS = {
    "late_night": [  # 23:00 - 4:00
        "{moon} شب به‌خیر، *{name} عزیز*! الان که خیلی دیروقته، تا صبح بیداری؟",
        "{moon} *{name} عزیز*، این‌موقع شب هنوز بیداری؟ استراحتم خوبه ها 😄",
    ],
    "dawn": [  # 4:00 - 7:00
        "🌄 سحر بخیر، *{name} عزیز*! امروز عجب سحرخیز شدی‌ها",
        "🌄 *{name} عزیز*، هنوز آفتاب نزده و تو بیداری، دمت گرم!",
    ],
    "morning": [  # 7:00 - 11:00
        "☀️ صبح بخیر، *{name} عزیز*! روزت پرانرژی باشه",
        "☀️ *{name} عزیز* صبح بخیر، وقت شروع یه روز خوبه",
    ],
    "noon": [  # 11:00 - 14:00
        "🌞 ظهر بخیر، *{name} عزیز*! ناهار یادت نره",
        "🌞 *{name} عزیز* ظهر بخیر، وسط روزی و بازم سرحالی",
    ],
    "afternoon": [  # 14:00 - 17:00
        "🌤️ عصر بخیر، *{name} عزیز*",
        "🌤️ *{name} عزیز*، عصر شیرینی داشته باشی",
    ],
    "evening": [  # 17:00 - 19:00
        "🌇 غروب بخیر، *{name} عزیز*",
        "🌇 *{name} عزیز*، وقت یه چای عصرونه‌ست",
    ],
    "night": [  # 19:00 - 23:00
        "{moon} شب بخیر، *{name} عزیز*",
        "{moon} *{name} عزیز*، شب خوبی داشته باشی",
    ],
}


def time_greeting(name: str) -> str:
    """خوش‌آمدگویی خودمونی و متنوع بر اساس ساعت روز — اسم همیشه بولد."""
    hour = datetime.now(IRAN_TZ).hour
    if hour >= 23 or hour < 4:
        bucket = "late_night"
    elif hour < 7:
        bucket = "dawn"
    elif hour < 11:
        bucket = "morning"
    elif hour < 14:
        bucket = "noon"
    elif hour < 17:
        bucket = "afternoon"
    elif hour < 19:
        bucket = "evening"
    else:
        bucket = "night"

    template = random.choice(_GREETINGS[bucket])
    if bucket in ("late_night", "night"):
        return template.format(name=name, moon=moon_phase_emoji())
    return template.format(name=name)


# ─── آب‌وهوای واقعی سرپل‌ذهاب (بدون نیاز به کلید API) ──────────
SARPOL_LAT = 34.4597
SARPOL_LON = 45.8646

# هر کد آب‌وهوا چند برداشتِ کوتاه و متفاوت داره تا هربار یه‌شکل نباشه
_WEATHER_MOOD = {
    0: ["صاف و آفتابی", "بی‌ابر و روشن"],
    1: ["تقریباً صاف", "کمی ابری"],
    2: ["نیمه‌ابری", "ترکیبی از آفتاب و ابر"],
    3: ["ابری و دلگیر", "یکدست ابری"],
    45: ["مه‌آلود", "با دید کم به‌خاطر مه"],
    48: ["مه یخ‌زده و سرد"],
    51: ["نم‌نم بارونی"],
    53: ["بارونیِ ملایم"],
    55: ["بارونیِ نسبتاً شدید"],
    56: ["بارونِ یخ‌زده‌ی سبک، لغزنده"],
    57: ["بارونِ یخ‌زده، پرخطر"],
    61: ["بارونیِ سبک"],
    63: ["بارونیِ متوسط"],
    65: ["بارونیِ شدید"],
    66: ["بارونِ یخ‌زده، جاده‌ها لغزنده"],
    67: ["بارونِ یخ‌زده‌ی شدید"],
    71: ["برفیِ سبک"],
    73: ["برفیِ متوسط"],
    75: ["برفیِ سنگین"],
    77: ["با دانه‌های ریز برف"],
    80: ["با رگبار ناگهانی"],
    81: ["با رگبارهای پیاپی"],
    82: ["با رگبار شدید"],
    85: ["با رگبار برف سبک"],
    86: ["با رگبار برف شدید"],
    95: ["طوفانی و رعدوبرقی"],
    96: ["طوفانی همراه با تگرگ سبک"],
    99: ["طوفانی و پرتگرگ"],
}

# FIX: چندتا از موردهای بالا (کدهای ۰ و ۲) توی روز به «آفتاب» اشاره می‌کنن؛
# اگه شب باشه (is_day=0) و همون متن‌ها به‌طورِ تصادفی انتخاب بشن، جمله‌ای
# مثلِ «امشب آفتابی به‌نظر می‌رسه» بی‌معنیه (شب که آفتاب نیست). برای این
# کدها یه نسخه‌ی شبانه‌ی بدونِ اشاره به آفتاب داریم؛ get_weather_line شب‌ها
# از این استفاده می‌کنه، نه از _WEATHER_MOOD.
_WEATHER_MOOD_NIGHT = {
    0: ["صاف و بی‌ابر", "آسمونِ روشن و بی‌ابر"],
    2: ["نیمه‌ابری", "کمی ابری"],
}

_STORM_CODES = {95, 96, 99}
_SNOW_CODES = {71, 73, 75, 77, 85, 86}
_ICE_CODES = {56, 57, 66, 67}
_PRECIP_CODES = {51, 53, 55, 61, 63, 65, 80, 81, 82} | _ICE_CODES | _STORM_CODES
_FOG_CODES = {45, 48}

# FIX: کشِ سراسریِ خطِ آب‌وهوا — به get_weather_line نگاه کنید.
_weather_cache = None  # (line, fetched_at_monotonic) | None
_WEATHER_CACHE_TTL = 900  # ۱۵ دقیقه

# FIX: سقفِ «بیش‌ازحد‌کهنه» برای get_weather_line_nowait. کشِ معمولی بعد از
# ۱۵ دقیقه منقضی می‌شه، ولی نسخه‌ی nowait تا همین چند خط پایین‌تر، وقتی کش
# منقضی بود، بازم همون مقدارِ کهنه رو (هرچقدر هم که کهنه باشه) فوری برمی‌گرداند
# و فقط یک رفرش در پس‌زمینه می‌فرستد — یعنی اگه یه مدت طولانی (مثلاً کل شب)
# هیچ‌کس پنل رو باز نکرده باشه، اولین درخواستِ بعدی (مثلاً ظهرِ روز بعد) همون
# خطِ شب‌ونصفه‌شب رو نشون می‌داد («امشب...» به‌جای «امروز...»، یا برعکس).
# با این سقف، اگه کش از این‌قدر قدیمی‌تر شده باشه دیگه نشونش نمی‌دیم (رشته‌ی
# خالی برمی‌گردونیم) تا اطلاعاتِ گمراه‌کننده نمایش داده نشه؛ رفرشِ پس‌زمینه
# بازم فرستاده می‌شه تا دفعه‌ی بعد تازه باشه.
_WEATHER_STALE_HARD_LIMIT = 2700  # ۴۵ دقیقه


def _weather_emoji(code: int, is_day: int) -> str:
    if code in (0, 1):
        return "☀️" if is_day else "🌕"
    if code == 2:
        return "⛅" if is_day else "☁️"
    if code == 3:
        return "☁️"
    if code in _FOG_CODES:
        return "🌫️"
    if code in _STORM_CODES:
        return "⛈️"
    if code in _SNOW_CODES:
        return "❄️"
    if code in _ICE_CODES:
        return "🌧️❄️"
    if code in _PRECIP_CODES:
        return "🌧️"
    return "🌡️"


async def get_weather_line(force: bool = False) -> str:
    """یک جمله‌ی کوتاه و خودمونی درباره‌ی آب‌وهوای سرپل‌ذهاب.
    نوعِ توصیف صرفاً بر اساس دما نیست؛ کدِ واقعیِ آب‌وهوا تعیین‌کننده‌ست.
    اگر در دسترس نبود، رشته‌ی خالی برمی‌گرداند.

    FIX: قبلاً این تابع روی *هر* بازِ شدنِ پنل خوش‌آمدگویی (یعنی /start، دستور
    «پنل»/«شروع»، و حتی دکمه‌ی 🔄 «به‌روزرسانی») یک درخواستِ HTTP واقعی به
    open-meteo.com می‌زد (با timeout تا ۴ ثانیه). یعنی همون یک پنل، صرفاً
    برای نمایشِ یک خطِ آب‌وهوا، هر بار تا ۴ ثانیه معطل می‌موند. چون آب‌وهوا
    ظرف چند دقیقه عملاً تغییری نمی‌کنه، حالا نتیجه به‌مدت ۱۵ دقیقه در حافظه
    کش می‌شه؛ فقط اولین درخواست بعد از هر بازه واقعاً به شبکه می‌ره."""
    global _weather_cache
    now = time.monotonic()
    cached = _weather_cache
    if not force and cached is not None and (now - cached[1]) < _WEATHER_CACHE_TTL:
        return cached[0]
    if httpx is None:
        logger.warning("weather: httpx نصب نیست؛ خطِ آب‌وهوا هرگز نمایش داده نمی‌شود")
        return ""
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": SARPOL_LAT,
                    "longitude": SARPOL_LON,
                    "current_weather": "true",
                }
            )
            resp.raise_for_status()
            data = resp.json()
            cw = data.get("current_weather", {})
            temp = cw.get("temperature")
            code = cw.get("weathercode")
            wind = cw.get("windspeed")
            is_day = cw.get("is_day", 1)
            if temp is None:
                logger.warning("weather: پاسخِ open-meteo بدونِ current_weather بود: %r", data)
                return cached[0] if cached is not None else ""

            emoji = _weather_emoji(code, is_day)
            mood_pool = (_WEATHER_MOOD_NIGHT.get(code) if not is_day else None) or _WEATHER_MOOD.get(code, ["نامشخص"])
            mood = random.choice(mood_pool)
            time_word = "امروز" if is_day else "امشب"

            line = f"{emoji} سرپل‌ذهاب {time_word} {mood} به‌نظر می‌رسه! (`{temp:.0f}°C`)"
            if wind is not None and wind >= 35:
                line += f" 💨 بادش هم شدیده"
            _weather_cache = (line, now)
            return line
    except Exception as e:
        logger.warning("weather: گرفتنِ آب‌وهوا ناموفق بود: %r", e)
        # اگر شبکه/سرویس در دسترس نبود، حداقل نتیجه‌ی قبلی (even if slightly
        # stale) رو نگه می‌داریم تا پنل خالی از این خط نمونه؛ اگر قبلاً هم
        # چیزی نداشتیم، رشته‌ی خالی برمی‌گرده (رفتار قبلی).
        return cached[0] if cached is not None else ""


_weather_task = None  # تسکِ رفرشِ در جریان (تا چند پنلِ هم‌زمان چندین درخواستِ تکراری نزنن)
_WEATHER_FIRST_WAIT = 2.0  # وقتی کش خالی/خیلی کهنه‌ست، حداکثر این‌قدر منتظر می‌مونیم


def _kick_weather_refresh():
    """رفرشِ آب‌وهوا رو در پس‌زمینه شروع می‌کنه (اگه از قبل در جریان نباشه) و تسکش رو برمی‌گردونه."""
    global _weather_task
    if _weather_task is None or _weather_task.done():
        _weather_task = asyncio.create_task(get_weather_line(force=True))
    return _weather_task


async def get_weather_line_nowait() -> str:
    """نسخه‌ی «تقریباً بدونِ معطلی» برای پنل خوش‌آمدگویی.

    FIX (ریشه‌ی «بعضی روزا آب‌وهوا نشون داده نمی‌شه»): قبلاً وقتی کش خالی بود
    (بعد از هر ری‌استارت/دیپلوی/خوابِ سرویس) یا بیش از ۴۵ دقیقه از آخرین
    دریافت گذشته بود، این تابع فقط رشته‌ی خالی برمی‌گردوند و رفرش رو «برای دفعه‌ی
    بعد» می‌فرستاد؛ یعنی اولین /start بعد از یک وقفه همیشه بدونِ آب‌وهوا بود،
    ولی وقتی چند نفر/چند بار پشتِ‌سرِهم پنل باز می‌شد (مثلاً شب) کش گرم بود و
    نشون داده می‌شد. حالا:
      • کش تازه (< ۱۵ دقیقه) → فوری برمی‌گرده.
      • کش کهنه ولی نه خیلی (< ۴۵ دقیقه) → مقدارِ قبلی فوری + رفرش در پس‌زمینه.
      • کش خالی/خیلی کهنه → حداکثر ۲ ثانیه برای رفرش صبر می‌کنه؛ اگه رسید نشون
        می‌ده، وگرنه رشته‌ی خالی (و رفرش ادامه می‌ده تا دفعه‌ی بعد آماده باشه).
    علاوه بر این، bot.py یک جابِ دوره‌ای (weather_warm_job) دارد که کش را همیشه
    گرم نگه می‌دارد، پس در حالتِ عادی مسیرِ صبر کردن اصلاً پیش نمی‌آید.
    """
    now = time.monotonic()
    cached = _weather_cache
    if cached is not None and (now - cached[1]) < _WEATHER_CACHE_TTL:
        return cached[0]
    if httpx is None:
        return ""
    task = _kick_weather_refresh()
    if cached is not None and (now - cached[1]) < _WEATHER_STALE_HARD_LIMIT:
        return cached[0]
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=_WEATHER_FIRST_WAIT)
    except Exception:
        return ""


async def weather_warm_job(context=None) -> None:
    """جابِ دوره‌ای: کشِ آب‌وهوا رو همیشه تازه نگه می‌داره (و موقعِ استارتِ ربات هم گرمش می‌کنه)."""
    try:
        await get_weather_line(force=True)
    except Exception as e:  # هرگز نباید جاب رو بترکونه
        logger.warning("weather_warm_job failed: %r", e)


def _status_line(status: str) -> str:
    status_map = {
        "normal": "🟢 نرمال",
        "bad": "🟡 احتیاطی",
        "danger": "🔴 خطرناک",
        "aps": "🪽 حالت APS",
    }
    return status_map.get(status, status)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # ─── DIAG: زمان‌سنجیِ موقت برای پیدا کردنِ منشأِ کندیِ /start ───
    # این ۳ خطِ لاگ بعداً (وقتی مشکل پیدا شد) باید حذف بشن.
    _t0 = time.monotonic()
    if update.message and update.message.date:
        _delay = (datetime.now(timezone.utc) - update.message.date).total_seconds()
        _perf_logger.warning(
            "⏱️ DIAG /start: از لحظه‌ی ارسالِ پیام توی تلگرام تا رسیدنش به هندلر، %.2f ثانیه طول کشیده "
            "(اگر این عدد بزرگه یعنی مشکل از قبل از اجرای کدِ ماست: ربات خواب بوده/چند اینستنس هم‌زمان اجرا میشه/کانفلیکتِ getUpdates)",
            _delay,
        )

    uid = update.effective_user.id
    is_pishva = (uid == PISHVA_ID)
    admin = None if is_pishva else await db.get_admin(uid)
    is_admin = bool(admin and admin["is_active"])
    _perf_logger.warning("⏱️ DIAG /start: تا اینجا (بعد از db.get_admin) %.2f ثانیه از شروعِ اجرای هندلر گذشته", time.monotonic() - _t0)

    # ─── دیپ‌لینک پنل (وقتی از دکمه‌ی «🔒 پیوی» در گروه اومده) ───
    # لینک به‌صورت https://t.me/<bot>?start=panel_<action> ساخته می‌شه؛
    # تلگرام این پارامتر رو به‌عنوان context.args[0] می‌فرسته.
    args = ctx.args if ctx.args else []
    payload = args[0] if args else None

    if payload and payload.startswith("panel_") and (is_pishva or is_admin):
        action = payload[len("panel_"):]
        if is_admin:
            await db.update_admin_activity(uid)

        if action == "restart":
            if is_pishva:
                return await show_pishva_welcome(update, ctx)
            return await show_admin_welcome(update, ctx, admin)

        from keyword_commands import _panel_content, PISHVA_ONLY_ACTIONS
        if action in PISHVA_ONLY_ACTIONS and not is_pishva:
            await update.message.reply_text("⛔ این دستور فقط برای مدیر ارشد است.")
            return ConversationHandler.END

        text, markup, err = await _panel_content(action, uid, is_pishva, admin)
        if text is None:
            await update.message.reply_text(err or "❗ این پنل در دسترس نیست.")
        else:
            await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
        return ConversationHandler.END

    # ─── دیپ‌لینک شطرنج زنده (وقتی دکمه‌ی «ورود/تماشا در پیوی» در گروه زده شده) ───
    # توی گروه/سوپرگروه، تلگرام دکمه‌ی وب‌اپ رو قبول نمی‌کنه (Button_type_invalid)،
    # پس اونجا به‌جاش یک دکمه‌ی لینک به همین‌جا (پیوی) گذاشته می‌شه که دکمه‌ی
    # واقعیِ ورود/تماشا (وب‌اپ) رو همین‌جا نشون می‌ده.
    if payload and (payload.startswith("chess_enter_") or payload.startswith("chess_watch_")) and (is_pishva or is_admin):
        is_watch = payload.startswith("chess_watch_")
        token = payload[len("chess_watch_"):] if is_watch else payload[len("chess_enter_"):]
        if is_admin:
            await db.update_admin_activity(uid)

        game = await db.get_chess_game(token)
        if not game or game["status"] != "active":
            await update.message.reply_text("❗ این بازی دیگر در دسترس نیست.")
            return ConversationHandler.END

        from chess_challenge import _kb_play, _kb_spectate
        if is_watch:
            await update.message.reply_text(
                f"{box('👁 تماشای بازی شطرنج زنده')}\n\n"
                f"⚪ {game['white_name']}  در مقابل  ⚫ {game['black_name']}",
                reply_markup=_kb_spectate(token), parse_mode="Markdown"
            )
        else:
            if uid not in (game["white_id"], game["black_id"]):
                await update.message.reply_text("⛔ شما بازیکن این بازی نیستید.")
                return ConversationHandler.END
            await update.message.reply_text(
                f"{box('♟️ ورود به بازی')}\n\nبرای ادامه‌ی بازی روی دکمه‌ی زیر بزنید:",
                reply_markup=_kb_play(token), parse_mode="Markdown"
            )
        return ConversationHandler.END

    # ─── دیپ‌لینک بازپخشِ یک بازیِ تمام‌شده (وقتی دکمه‌ی «مشاهده‌ی بازی» از
    # داخلِ «لیستِ بازی‌ها» در گروه/سوپرگروه زده شده؛ همان دلیلِ chess_enter_/
    # chess_watch_ بالا — تلگرام دکمه‌ی وب‌اپ را داخلِ گروه قبول نمی‌کند).
    # فرمت: chess_replay_<chat_flag>_<token> — chat_flag همان پرچمِ نمایشِ
    # محتوای چتِ داخلِ بازی است (۱=فقط پیشوا، ۰=بقیه)، از قبل توسطِ
    # chess_games_history.py محاسبه شده و اینجا فقط منتقل می‌شود.
    if payload and payload.startswith("chess_replay_") and (is_pishva or is_admin):
        rest = payload[len("chess_replay_"):]
        chat_flag, _, token = rest.partition("_")
        if is_admin:
            await db.update_admin_activity(uid)

        game = await db.get_chess_game(token)
        if not game:
            await update.message.reply_text("❗ این بازی پیدا نشد یا حذف شده است.")
            return ConversationHandler.END

        from chess_games_history import kb_replay
        await update.message.reply_text(
            f"{box('📜 مرورِ بازی')}\n\n⚪ {game['white_name']}  در مقابل  ⚫ {game['black_name']}",
            reply_markup=kb_replay(token, chat_flag == "1"), parse_mode="Markdown"
        )
        return ConversationHandler.END

    if is_pishva:
        _r = await show_pishva_welcome(update, ctx)
        _perf_logger.warning("⏱️ DIAG /start: کلِ اجرای هندلر (پیشوا) %.2f ثانیه طول کشید", time.monotonic() - _t0)
        return _r
    if is_admin:
        await db.update_admin_activity(uid)
        _r = await show_admin_welcome(update, ctx, admin)
        _perf_logger.warning("⏱️ DIAG /start: کلِ اجرای هندلر (ادمین) %.2f ثانیه طول کشید", time.monotonic() - _t0)
        return _r

    # ⏳ اگر این شخص در صف انتظار امنیتی است، اجازه‌ی درخواست جدید نمی‌دهیم
    queued = await db.get_queued_request_by_uid(uid)
    if queued:
        await update.message.reply_text(
            "⏳ *در صف انتظار امنیتی*\n\n"
            "درخواست شما در حال بررسی توسط واحد امنیتی APS است.\n"
            "تا اطلاع ثانویه امکان ارسال درخواست جدید برای شما وجود ندارد. لطفاً صبور باشید.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    # 📥 اگه درخواستِ قبلیِ این شخص هنوز در انتظارِ بررسیِ مدیر ارشده،
    # اجازه‌ی ثبتِ درخواستِ دوم نمی‌دیم (باعثِ درخواست‌های تکراری می‌شد).
    pending_req = await db.get_pending_request_by_uid(uid)
    if pending_req:
        await update.message.reply_text(
            "📥 *درخواست شما در انتظار بررسی است*\n\n"
            "درخواست قبلی شما هنوز توسط مدیر ارشد بررسی نشده است.\n"
            "لطفاً تا اعلام نتیجه صبور باشید؛ نیازی به ارسال درخواست جدید نیست.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    # 🌊 ضدِ فلود: اگه این غریبه در بازه‌ی زمانیِ تنظیم‌شده (پنل امنیتی APS)
    # بیش از حدِ مجاز /start بزنه، خودکار وارد صف انتظار امنیتی می‌شه و
    # نیازی به ادامه‌ی مسیرِ عادی (نمایشِ انتخاب نقش) نیست.
    from security import check_stranger_flood
    if await check_stranger_flood(update, ctx, uid, update.effective_user.username, update.effective_user.full_name):
        return ConversationHandler.END

    admin_login = await db.get_setting("admin_login_enabled", "1")
    if admin_login != "1":
        await update.message.reply_text("🔒 ورود ادمین‌ها غیرفعال است.")
        return ConversationHandler.END
    text = "⚔️ *سیستم فرماندهی شطرنج*\n\n🪪 نقش خود را انتخاب کنید:"
    await update.message.reply_text(text, reply_markup=kb.kb_role_select(), parse_mode="Markdown")
    return ST_ROLE_SELECT


async def show_pishva_welcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await db.log_action(PISHVA_ID, "login", "ورود مدیر ارشد")  # خودِ تابع fire-and-forget است، شبکه‌ای بلاک نمی‌کنه

    # FIX: قبلاً pishva_display + weather + ۸ کوئری/تنظیمِ مستقلِ دیگه همه
    # پشتِ‌سرِهم await می‌شدن (۹-۱۰ رفت‌وبرگشتِ سریالی به Turso، دقیقاً همون
    # بیماریِ داشبورد) — همینه که پنل خوش‌آمدگویی چند ثانیه طول می‌کشید.
    # الان همه با هم (asyncio.gather) اجرا می‌شن، و آب‌وهوا هم دیگه هرگز
    # پنل رو معطلِ شبکه نگه نمی‌داره.
    weather, bundle = await asyncio.gather(
        get_weather_line_nowait(),
        db.get_pishva_panel_bundle(),  # FIX: کلِ پنل (حتی اگه کش تازه‌منقضی‌شده باشه) با یک رفت‌وبرگشتِ شبکه
    )
    pname = bundle["pname"]
    admins = bundle["admins"]
    pending = bundle["pending_requests"]
    pending_matches = bundle["pending_matches"]
    all_tasks = bundle["all_tasks"]
    status = bundle["status"]
    wh = bundle["wh"]
    db_stat = bundle["db_stat"]
    ai_on = bundle["ai_on"]
    greeting = time_greeting(pname)

    try:
        pending_tasks = [t for t in all_tasks if t["status"] == "pending"]
        wh_txt = "🟢 باز" if wh == "1" else "🔴 بسته"
        db_txt = "🔗 فعال" if db_stat == "1" else "⚠️ غیرفعال"
        ai_txt = "🟢 آنلاین" if ai_on == "1" else "🔴 آفلاین"

        text = (
            f"👑 *پنل مدیر ارشد*\n"
            f"{greeting}\n"
            f"🕰 `{now_shamsi()}`\n"
        )
        if weather:
            text += f"\n{weather}\n"
        text += (
            f"\n📡 {_status_line(status)} | 🕐 کاری: {wh_txt} | 🗄️ دیتابیس: {db_txt} | 🤖 AI: {ai_txt}\n"
            f"👥 ادمین: `{len(admins)}` | 📥 درخواست: `{len(pending)}` | "
            f"⏳ بی‌نتیجه: `{len(pending_matches)}` | 📋 وظایف: `{len(pending_tasks)}`"
        )
    except Exception:
        text = greeting
        if weather:
            text += "\n" + weather

    if update.message:
        sent = await update.message.reply_text(text, reply_markup=kb.kb_pishva_main(), parse_mode="Markdown")
        from keyword_commands import register_panel_owner
        await register_panel_owner(update, ctx, sent.message_id)
    else:
        await safe_edit_message_text(update.callback_query, text, reply_markup=kb.kb_pishva_main(), parse_mode="Markdown")
        from keyword_commands import register_panel_owner
        await register_panel_owner(update, ctx, update.callback_query.message.message_id)
    return ConversationHandler.END


async def show_admin_welcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE, admin):
    role_label = "🏆 مدیر مسابقات" if admin["role"] == ROLE_TOURNAMENT_MANAGER else "🛡️ مدیر امنیتی"
    _aname = admin["display_name"] or admin["full_name"]
    greeting = time_greeting(_aname)

    # همون فیکس: همه‌ی کوئری‌های مستقل با هم، نه یکی‌یکی.
    weather, bundle = await asyncio.gather(
        get_weather_line_nowait(),
        db.get_admin_panel_bundle(admin["telegram_id"]),  # FIX: کلِ پنل با یک رفت‌وبرگشتِ شبکه، حتی با کشِ سرد
    )
    pending_matches = bundle["pending_matches"]
    admin_tasks = bundle["tasks"]
    all_players = bundle["all_players"]
    status = bundle["status"]
    wh = bundle["wh"]
    ai_on = bundle["ai_on"]

    try:
        pending_tasks = [t for t in admin_tasks if t["status"] == "pending"]
        warned = [p for p in all_players if p["warnings"] > 0]
        wh_txt = "🟢 باز" if wh == "1" else "🔴 بسته"
        ai_txt = "🟢 آنلاین" if ai_on == "1" else "🔴 آفلاین (فعلا در دسترس نیست)"

        text = (
            f"{role_label}\n"
            f"{greeting}\n"
            f"🕰 `{now_shamsi()}`\n"
        )
        if weather:
            text += f"\n{weather}\n"
        text += (
            f"\n📡 {_status_line(status)} | 🕐 کاری: {wh_txt} | 🤖 AI: {ai_txt}\n"
            f"⏳ بی‌نتیجه: `{len(pending_matches)}` | ⚠️ اخطار: `{len(warned)}` | "
            f"📋 وظایف: `{len(pending_tasks)}`"
        )
    except Exception:
        text = role_label + "\n" + greeting
        if weather:
            text += "\n" + weather

    markup = kb.kb_tournament_manager_main() if admin["role"] == ROLE_TOURNAMENT_MANAGER else kb.kb_security_manager_main()
    if update.message:
        sent = await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
        from keyword_commands import register_panel_owner
        await register_panel_owner(update, ctx, sent.message_id)
    else:
        await safe_edit_message_text(update.callback_query, text, reply_markup=markup, parse_mode="Markdown")
        from keyword_commands import register_panel_owner
        await register_panel_owner(update, ctx, update.callback_query.message.message_id)
    return ConversationHandler.END


async def on_role_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "role_pishva":
        uid = query.from_user.id
        if uid == PISHVA_ID:
            return await show_pishva_welcome(update, ctx)
        await safe_edit_message_text(query, "🔐 رمز مدیر ارشد را وارد کنید:")
        ctx.user_data["pending_role"] = ROLE_PISHVA
        ctx.user_data["_audit_secret"] = True   # پیامِ بعدی (رمز) در ردیابی ثبت نمی‌شود
        return ST_PISHVA_PASSWORD
    role = ROLE_TOURNAMENT_MANAGER if data == "role_tournament" else ROLE_SECURITY_MANAGER
    ctx.user_data["pending_role"] = role
    await safe_edit_message_text(query, 
        "👤 یوزرنیم تلگرام خود را وارد کنید:\n_(باید با @ شروع شود و شامل حروف باشد)_",
        parse_mode="Markdown"
    )
    return ST_ADMIN_USERNAME


async def on_pishva_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ این حساب مجاز نیست.")
    return ConversationHandler.END


async def on_admin_username(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    if not username.startswith("@") or not any(c.isalpha() for c in username[1:]):
        await update.message.reply_text("❌ یوزرنیم باید با @ شروع شود و حاوی حروف باشد.\nدوباره وارد کنید:")
        return ST_ADMIN_USERNAME
    ctx.user_data["reg_username"] = username
    await update.message.reply_text("✍️ نام و نام‌خانوادگی خود را وارد کنید:")
    return ST_ADMIN_FULLNAME


async def on_admin_fullname(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["reg_fullname"] = update.message.text.strip()
    await update.message.reply_text(
        "📝 یک پیام برای مدیر ارشد بنویسید (یا /skip):",
        parse_mode="Markdown"
    )
    return ST_ACCESS_REQUEST_MSG


async def on_access_request_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = ""
    if update.message.text and update.message.text != "/skip":
        msg = update.message.text.strip()
    uid = update.effective_user.id
    username = ctx.user_data.get("reg_username", "")
    full_name = ctx.user_data.get("reg_fullname", "")
    role = ctx.user_data.get("pending_role", ROLE_TOURNAMENT_MANAGER)
    if await db.get_pending_request_by_uid(uid):
        await update.message.reply_text("📥 درخواست قبلی شما هنوز در انتظار بررسی است.")
        return ConversationHandler.END
    req_id = await db.create_access_request(uid, username, full_name, role, msg)
    role_label = "🏆 مدیر مسابقات" if role == ROLE_TOURNAMENT_MANAGER else "🛡️ مدیر امنیتی"
    ts = now_shamsi()
    notif = (
        "📥 *درخواست دسترسی جدید*\n\n"
        "👤 " + full_name + " | " + username + "\n"
        "💼 " + role_label + "\n"
        "📝 " + (msg or "—") + "\n"
        "⏱️ `" + ts + "`"
    )
    await notify_pishva(ctx.bot, notif, reply_markup=kb.kb_access_request(req_id))
    await update.message.reply_text("✅ درخواست ارسال شد. منتظر تأیید مدیر ارشد باشید.")
    return ConversationHandler.END


async def on_approve_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    req_id = int(query.data.split("_")[-1])
    req = await db.get_access_request(req_id)
    if not req or req["status"] != "pending":
        await query.answer("قبلاً پردازش شده.", show_alert=True)
        return
    await db.update_access_request(req_id, "approved")
    await db.create_admin(req["telegram_id"], req["username"], req["full_name"], req["role"])
    await db.close_other_open_requests(req["telegram_id"], req_id)
    role_label = "🏆 مدیر مسابقات" if req["role"] == ROLE_TOURNAMENT_MANAGER else "🛡️ مدیر امنیتی"
    try:
        await ctx.bot.send_message(
            chat_id=req["telegram_id"],
            text="✅ *دسترسی تأیید شد*\n💼 " + role_label + "\n\n/start بزنید.",
            parse_mode="Markdown"
        )
    except Exception:
        pass
    await safe_edit_message_text(query, query.message.text + "\n\n✅ *تأیید شد*", parse_mode="Markdown")
    await query.answer("✅ تأیید شد.")


async def on_reject_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    req_id = int(query.data.split("_")[-1])
    req = await db.get_access_request(req_id)
    if not req or req["status"] != "pending":
        await query.answer("قبلاً پردازش شده.", show_alert=True)
        return
    await db.update_access_request(req_id, "rejected")
    try:
        await ctx.bot.send_message(
            chat_id=req["telegram_id"],
            text="❌ درخواست دسترسی شما رد شد.\nبرای اطلاعات بیشتر با مدیر ارشد تماس بگیرید."
        )
    except Exception:
        pass
    await safe_edit_message_text(query, query.message.text + "\n\n❌ *رد شد*", parse_mode="Markdown")
    await query.answer("❌ رد شد.")


# ─── تغییر نقشِ خودِ ادمین (از داخل پنلِ خودش) ─────────────────
# دکمه‌ی «🔄 تغییر نقش» توی پنل مدیر مسابقات و مدیر امنیتی. نقش فقط بین
# این دو جابه‌جا می‌شه (مدیر ارشد نقشِ قابل‌تغییر نداره). کلیدِ callbackها
# عمداً با «rolechg_» شروع می‌شن، نه «role_»، تا با هندلرِ انتخابِ نقشِ
# ثبت‌نام (pattern="^role_") قاطی نشن.
_ROLE_LABELS = {
    ROLE_TOURNAMENT_MANAGER: "🏆 مدیر مسابقات",
    ROLE_SECURITY_MANAGER: "🛡️ مدیر امنیتی",
}


def _other_role(role: str) -> str:
    return ROLE_SECURITY_MANAGER if role == ROLE_TOURNAMENT_MANAGER else ROLE_TOURNAMENT_MANAGER


async def role_change_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid == PISHVA_ID:
        await query.answer("👑 مدیر ارشد نقشِ قابل‌تغییر ندارد.", show_alert=True)
        return
    admin = await db.get_admin(uid)
    if not (admin and admin["is_active"]):
        await query.answer("⛔ شما مدیرِ فعال نیستید.", show_alert=True)
        return
    await query.answer()
    cur = admin["role"]
    new = _other_role(cur)
    text = (
        f"{box('🔄 تغییر نقش')}\n\n"
        f"💼 نقش فعلی شما: {_ROLE_LABELS.get(cur, cur)}\n"
        f"🎯 نقش جدید: {_ROLE_LABELS[new]}\n\n"
        "با تغییر نقش، منوی پنل شما مطابق نقش جدید عوض می‌شود و مدیر ارشد هم مطلع می‌شود.\n\n"
        "آیا مطمئن هستید؟"
    )
    await safe_edit_message_text(
        query, text, reply_markup=kb.kb_role_change_confirm(new, _ROLE_LABELS[new]), parse_mode="Markdown"
    )


async def role_change_apply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid == PISHVA_ID:
        await query.answer("👑 مدیر ارشد نقشِ قابل‌تغییر ندارد.", show_alert=True)
        return
    new_role = query.data[len("rolechg_do_"):]
    if new_role not in _ROLE_LABELS:
        await query.answer("❗ نقش نامعتبر.", show_alert=True)
        return
    admin = await db.get_admin(uid)
    if not (admin and admin["is_active"]):
        await query.answer("⛔ شما مدیرِ فعال نیستید.", show_alert=True)
        return
    old_role = admin["role"]
    if old_role == new_role:
        await query.answer("ℹ️ نقش شما از قبل همین است.", show_alert=True)
        return

    await db.set_admin_role(uid, new_role)
    name = admin["display_name"] or admin["full_name"] or str(uid)
    await db.log_action(
        uid, "admin_role_change",
        f"{name}: {_ROLE_LABELS.get(old_role, old_role)} ← {_ROLE_LABELS[new_role]}", uid
    )
    try:
        await notify_pishva(
            ctx.bot,
            f"🔄 *تغییر نقش مدیر*\n\n👤 {name}\n"
            f"💼 {_ROLE_LABELS.get(old_role, old_role)}  ⬅️  {_ROLE_LABELS[new_role]}\n"
            f"⏱️ `{now_shamsi()}`"
        )
    except Exception:
        logger.warning("role change: notifying pishva failed", exc_info=True)

    await query.answer(f"✅ نقش شما به {_ROLE_LABELS[new_role]} تغییر کرد.", show_alert=True)
    fresh = await db.get_admin(uid)  # کش توسطِ set_admin_role پاک شده؛ نقشِ جدید خونده می‌شه
    await show_admin_welcome(update, ctx, fresh)

