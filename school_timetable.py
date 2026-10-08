"""
school_timetable.py — برنامه‌ی هفتگی مدرسه (۱۴۰۵-۱۴۰۶) برای «خلاصه صبحگاهی»

ساختار داده:
    TIMETABLE[«نام کلاس»][«روز هفته»] = [درس۱, درس۲, ...]   (به ترتیب زنگ‌ها)

هر درس یا یک رشته‌ی ساده است («نگارش»)، یا یک Alt برای درس‌های یک‌هفته‌درمیون:
    Alt("دینی", "بیکار")  ← یک هفته دینی، هفته‌ی بعد بیکار

تشخیص «این هفته کدوم‌ه؟» با یک تاریخ مرجع انجام می‌شه (setting: mb_tt_ref_date):
    هفته‌ی مرجع → گزینه‌ی اول ،  هفته‌ی بعدش → گزینه‌ی دوم ،  و همین‌طور یک‌درمیون.
    معادله:  idx = ((تاریخ_امروز − تاریخ_مرجع).days // 7) % 2   → 0 یعنی گزینه‌ی اول

برای پرکردن بقیه‌ی برنامه فقط کافیه به همین دیکشنری اضافه کنی؛ هیچ جای دیگه‌ای نیاز به تغییر نیست.
"""
from datetime import date, datetime, timedelta

import database as db
from helpers import TEHRAN_TZ, weekday_fa


class Alt:
    """درسِ یک‌هفته‌درمیون: a در هفته‌های مرجع، b در هفته‌های بعدی."""
    def __init__(self, a: str, b: str):
        self.a, self.b = a, b


# برنامه‌ی «دهم انسانی» از روی جدول ۱۴۰۵-۱۴۰۶ (ترتیب زنگ‌ها از راست به چپ).
# خانه‌های دوتکه‌ی جدول (بالا/پایین) یک‌هفته‌درمیون‌اند: سطر بالا = Alt.a ، سطر پایین = Alt.b
# برای اضافه‌کردن کلاس‌های دیگر، فقط یک کلید جدید مثل «دهم تجربی ۱» به همین دیکشنری اضافه کن.
TIMETABLE = {
    "دهم انسانی": {
        "شنبه":      ["آمادگی", "نگارش", "تربیت بدنی", Alt("دینی", "بیکار")],
        "یکشنبه":    [Alt("انگلیسی", "آمادگی"), "دینی", "تفکر", "فارسی"],
        "دوشنبه":    [Alt("علوم و فنون", "فارسی"), "انگلیسی", "اقتصاد", "جغرافیا"],
        "سه‌شنبه":   ["عربی", "ریاضی و آمار", "علوم و فنون", Alt("عربی", "تاریخ")],
        "چهارشنبه":  ["جامعه‌شناسی", "ریاضی و آمار", "تاریخ", "منطق"],
    },
}

REF_KEY = "mb_tt_ref_date"


def week_saturday(d: date) -> date:
    """شنبه‌ی همان هفته (شنبه = شروع هفته؛ در پایتون Saturday.weekday() == 5)."""
    return d - timedelta(days=(d.weekday() - 5) % 7)


async def set_alt_this_week(first_option_now: bool):
    """first_option_now=True یعنی «این هفته گزینه‌ی اول (مثلاً دینی) داریم»."""
    sat = week_saturday(datetime.now(TEHRAN_TZ).date())
    ref = sat if first_option_now else sat + timedelta(days=7)
    await db.set_setting(REF_KEY, ref.isoformat())


async def _ref_date():
    raw = (await db.get_setting(REF_KEY, "")).strip()
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


async def lessons_for(dt: datetime = None):
    """[(نام کلاس, [متن هر زنگ])] برای روز dt (پیش‌فرض: امروز تهران)."""
    dt = dt or datetime.now(TEHRAN_TZ)
    day = weekday_fa(dt)
    ref = await _ref_date()
    out = []
    for cls, days in TIMETABLE.items():
        slots = days.get(day)
        if not slots:
            continue
        lines = []
        for s in slots:
            if isinstance(s, Alt):
                if ref is None:
                    lines.append(f"{s.a} / {s.b} (یک‌هفته‌درمیون)")
                else:
                    idx = ((dt.date() - ref).days // 7) % 2
                    lines.append(s.a if idx == 0 else s.b)
            else:
                lines.append(s)
        out.append((cls, lines))
    return day, out


async def render_today(dt: datetime = None) -> str:
    day, rows = await lessons_for(dt)
    if day == "جمعه":
        return "امروز جمعه است؛ مدرسه تعطیل است."
    if not rows:
        return f"برای {day} برنامه‌ای در سیستم ثبت نشده."
    return "\n".join(f"• {cls}: " + "، ".join(lines) for cls, lines in rows)
