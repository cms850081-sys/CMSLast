"""
📅 تقویم مدرسه
- هر ماه شمسی به‌صورت گرید ۷ ستونی از دکمه‌های شیشه‌ای (۲۸ تا ۳۱ دکمه بسته به ماه).
- امروز: آبی (style=primary) — روزهای ایونت‌دار: سبز (style=success) — تعطیل: قرمز (style=danger).
- همه‌ی نقش‌ها می‌توانند تقویم را ببینند و با زدن روی یک روز جزئیاتش را (اگر ثبت شده) در یک
  Alert کوچک ببینند. فقط مدیر ارشد (Pishva) می‌تواند از همان روی همان روز، ایونت/تعطیلی ثبت،
  ویرایش یا حذف کند.
"""
import jdatetime
import json
from datetime import datetime
from telegram.ext import ConversationHandler

import database as db
import keyboards as kb
from servers import is_boss as _boss, boss_chat as _boss_chat
from config import PISHVA_ID, ST_CALENDAR_TITLE
from helpers import safe_edit_message_text, box, TEHRAN_TZ


async def _can_edit_calendar(uid: int) -> bool:
    """مدیر ارشد همیشه دسترسی داره؛ سایر مدیرها فقط اگه مدیر ارشد از پنل
    دسترسی‌های مدیر، مجوز 'calendar_edit' رو براشون فعال کرده باشه."""
    if _boss(uid):
        return True
    admin = await db.get_admin(uid)
    if not admin or not admin["is_active"]:
        return False
    try:
        perms = json.loads(admin["permissions"])
    except Exception:
        perms = {}
    return bool(perms.get("calendar_edit", False))


# ─── محاسبات تقویم شمسی ─────────────────────────────────────────
# نگاشتِ weekday میلادیِ پایتون (دوشنبه=۰ ... یکشنبه=۶) به موقعیتِ همان روز
# در هفته‌ی فارسی (شنبه=۰ ... جمعه=۶) — دقیقاً همان نگاشتی که helpers.py برای
# weekday_fa استفاده می‌کند، برای هماهنگی با بقیه‌ی ربات.
_WEEKDAY_POS = {5: 0, 6: 1, 0: 2, 1: 3, 2: 4, 3: 5, 4: 6}


def jalali_days_in_month(year: int, month: int) -> int:
    """تعداد روزهای یک ماه شمسی را بدون وابستگی به APIِ خاصی از jdatetime پیدا می‌کند:
    امتحان می‌کند ۳۱، بعد ۳۰، بعد ۲۹ روز معتبر است یا نه (کبیسه/غیرکبیسه خودش حل می‌شود)."""
    for day in (31, 30, 29):
        try:
            jdatetime.date(year, month, day)
            return day
        except ValueError:
            continue
    return 28


def jalali_first_weekday_pos(year: int, month: int) -> int:
    """موقعیتِ روزِ اولِ ماه در هفته‌ی فارسی (شنبه=۰ ... جمعه=۶)."""
    g = jdatetime.date(year, month, 1).togregorian()
    return _WEEKDAY_POS[g.weekday()]


PERSIAN_WEEKDAY_NAMES = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]


def _weekday_name(jdate: str) -> str:
    """اسم روز هفته (فارسی) برای یک تاریخ شمسیِ دلخواه، نه فقط امروز."""
    y, m, d = map(int, jdate.split("/"))
    g = jdatetime.date(y, m, d).togregorian()
    return PERSIAN_WEEKDAY_NAMES[_WEEKDAY_POS[g.weekday()]]


def _today_ymd():
    now = datetime.now(TEHRAN_TZ)
    jd = jdatetime.datetime.fromgregorian(datetime=now)
    return (jd.year, jd.month, jd.day)


def _clock_str() -> str:
    return datetime.now(TEHRAN_TZ).strftime("%H:%M")


# ─── نمایش تقویم ─────────────────────────────────────────────────
def _month_header_text(extra_line: str = "") -> str:
    text = box('📅 تقویم مدرسه') + "\n\n"
    text += f"🕒 ساعت الان: {_clock_str()}\n\n"
    if extra_line:
        text += extra_line + "\n\n"
    text += (
        "🔵 امروز   🟢 ایونت   🔴 تعطیل/جمعه‌پنجشنبه\n"
        "روی هر روز بزنید تا جزئیاتش را ببینید."
    )
    return text


async def build_calendar_content(year: int = None, month: int = None, extra_line: str = ""):
    """(text, markup) یک ماهِ تقویم رو برمی‌گردونه — هم مسیرِ دکمه (callback query)
    و هم مسیرِ کلیدواژه (پیام متنی مثل «تقویم»/«امروز»/«ایونت»/«تعطیلی») از همین
    یه‌جا محتوا می‌گیرن تا دقیقاً یک چیز نشون داده بشه."""
    ty, tm, td = _today_ymd()
    year = year or ty
    month = month or tm
    days_map = await db.get_calendar_month(year, month)
    text = _month_header_text(extra_line)
    markup = kb.kb_calendar(year, month, days_map, (ty, tm, td))
    return text, markup


async def content_default():
    """کلمات «تقویم»/«تاریخ»/«ساعت»/«وقت» → همین تقویمِ ماهِ جاری، با ساعتِ الان."""
    text, markup = await build_calendar_content()
    return text, markup, None


async def content_today():
    """کلمه‌ی «امروز» → همون تقویم، با یه خط تاکیدشده‌ی بالا برای امروز."""
    ty, tm, td = _today_ymd()
    jdate = f"{ty:04d}/{tm:02d}/{td:02d}"
    extra = f"📌 امروز: {_weekday_name(jdate)} {jdate}"
    text, markup = await build_calendar_content(ty, tm, extra)
    return text, markup, None


async def _content_next(day_type: str, label: str, noun: str):
    ty, tm, td = _today_ymd()
    today_jdate = f"{ty:04d}/{tm:02d}/{td:02d}"
    row = await db.get_next_calendar_day(day_type, today_jdate)
    if not row:
        extra = f"{label} — {noun} بعدی‌ای ثبت نشده."
        text, markup = await build_calendar_content(ty, tm, extra)
        return text, markup, None
    y, m, _d = map(int, row["jdate"].split("/"))
    extra = f"{label} بعدی: {_weekday_name(row['jdate'])} {row['jdate']} — {row['title']}"
    text, markup = await build_calendar_content(y, m, extra)
    return text, markup, None


async def content_next_event():
    """کلمه‌ی «ایونت» → نزدیک‌ترین ایونتِ ثبت‌شده‌ی آینده (یا امروز)."""
    return await _content_next("event", "🟢 ایونت", "ایونت")


async def content_next_holiday():
    """کلمه‌ی «تعطیلی» → نزدیک‌ترین تعطیلیِ ثبت‌شده‌ی آینده (یا امروز)."""
    return await _content_next("holiday", "🔴 تعطیل", "تعطیلی")


async def _render(update, ctx, year: int = None, month: int = None, extra_line: str = ""):
    query = update.callback_query
    text, markup = await build_calendar_content(year, month, extra_line)
    await safe_edit_message_text(query, text, reply_markup=markup, parse_mode="Markdown")


async def calendar_open(update, ctx):
    query = update.callback_query
    await query.answer()
    await _render(update, ctx)


async def calendar_nav(update, ctx):
    query = update.callback_query
    await query.answer()
    _, _, year, month = query.data.split("_")
    await _render(update, ctx, int(year), int(month))


async def calendar_noop(update, ctx):
    await update.callback_query.answer()


# ─── زدن روی یک روز ──────────────────────────────────────────────
async def calendar_day_tap(update, ctx):
    query = update.callback_query
    jdate = query.data.replace("cal_day_", "")
    uid = query.from_user.id
    day_info = await db.get_calendar_day(jdate)

    if not await _can_edit_calendar(uid):
        # بدون مجوز ویرایش: فقط می‌بینه، نمی‌تونه تغییر بده.
        if day_info:
            label = "🔴 تعطیل" if day_info["day_type"] == "holiday" else "🟢 ایونت"
            await query.answer(f"{jdate}\n{label}\n\n{day_info['title']}", show_alert=True)
        else:
            await query.answer(f"{jdate}\nروز عادی — چیزی ثبت نشده.", show_alert=True)
        return

    await query.answer()
    ctx.user_data["cal_jdate"] = jdate
    if day_info:
        label = "🔴 تعطیل" if day_info["day_type"] == "holiday" else "🟢 ایونت"
        status_line = f"وضعیت فعلی: {label}\n📝 {day_info['title']}\n\n"
    else:
        status_line = "وضعیت فعلی: روز عادی\n\n"
    await safe_edit_message_text(
        query,
        f"📅 {jdate}\n\n{status_line}چه کاری می‌خواهید انجام دهید؟",
        reply_markup=kb.kb_calendar_day_actions(jdate, has_entry=bool(day_info)),
    )


# ─── ثبت ایونت/تعطیلی (مدیر ارشد) ────────────────────────────────
async def calendar_set_start(update, ctx):
    query = update.callback_query
    if not await _can_edit_calendar(query.from_user.id):
        await query.answer("⛔ شما اجازه‌ی ویرایش تقویم را ندارید.", show_alert=True)
        return
    await query.answer()
    day_type = "event" if query.data == "calset_event" else "holiday"
    ctx.user_data["cal_type"] = day_type
    label = "ایونت" if day_type == "event" else "تعطیلی"
    jdate = ctx.user_data.get("cal_jdate", "")
    await safe_edit_message_text(query, f"✏️ توضیح {label} برای {jdate} را بنویسید:")
    return ST_CALENDAR_TITLE


async def calendar_title_save(update, ctx):
    title = (update.message.text or "").strip()
    jdate = ctx.user_data.get("cal_jdate")
    day_type = ctx.user_data.get("cal_type")
    if not jdate or not day_type or not title:
        await update.message.reply_text("⚠️ چیزی ثبت نشد. دوباره از تقویم اقدام کنید.")
        return ConversationHandler.END

    await db.set_calendar_day(jdate, day_type, title, update.message.from_user.id)
    year, month, _ = jdate.split("/")
    label = "🟢 ایونت" if day_type == "event" else "🔴 تعطیل"
    ty, tm, td = _today_ymd()
    days_map = await db.get_calendar_month(int(year), int(month))
    await update.message.reply_text(
        f"✅ {jdate} به‌عنوان {label} ثبت شد.",
        reply_markup=kb.kb_calendar(int(year), int(month), days_map, (ty, tm, td)),
    )
    return ConversationHandler.END


async def calendar_clear(update, ctx):
    query = update.callback_query
    if not await _can_edit_calendar(query.from_user.id):
        await query.answer("⛔ شما اجازه‌ی ویرایش تقویم را ندارید.", show_alert=True)
        return
    await query.answer()
    jdate = ctx.user_data.get("cal_jdate")
    if not jdate:
        return
    await db.delete_calendar_day(jdate)
    year, month, _ = jdate.split("/")
    await _render(update, ctx, int(year), int(month))
