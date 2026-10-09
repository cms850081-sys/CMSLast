import jdatetime
import pytz
import asyncio
from datetime import datetime, timedelta
from telegram.error import BadRequest
from config import BAR_LENGTH, PISHVA_ID
import database as db
import logging
import json

logger = logging.getLogger(__name__)
TEHRAN_TZ = pytz.timezone("Asia/Tehran")

# ─── Date/Time ───────────────────────────────────────────────
def now_shamsi() -> str:
    now = datetime.now(TEHRAN_TZ)
    jd = jdatetime.datetime.fromgregorian(datetime=now)
    return jd.strftime("%Y/%m/%d — %H:%M:%S")

def today_shamsi() -> str:
    now = datetime.now(TEHRAN_TZ)
    jd = jdatetime.datetime.fromgregorian(datetime=now)
    return jd.strftime("%Y/%m/%d")

def today_gregorian() -> str:
    return datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d")

_WEEKDAY_FA = {
    5: "شنبه", 6: "یکشنبه", 0: "دوشنبه", 1: "سه‌شنبه",
    2: "چهارشنبه", 3: "پنجشنبه", 4: "جمعه",
}

def weekday_fa(dt: datetime = None) -> str:
    """اسم روز هفته به فارسی (شنبه تا جمعه)، بر اساس دیتتایم تهران."""
    dt = dt or datetime.now(TEHRAN_TZ)
    return _WEEKDAY_FA[dt.weekday()]

def days_ago_gregorian(n: int) -> str:
    """تاریخِ میلادیِ n روز قبل (بر پایه‌ی وقتِ تهران)، فرمت 'YYYY-MM-DD'.
    n=0 یعنی امروز."""
    dt = datetime.now(TEHRAN_TZ) - timedelta(days=max(0, n))
    return dt.strftime("%Y-%m-%d")

def date_label_fa(date_str: str) -> str:
    """از یه تاریخِ میلادیِ 'YYYY-MM-DD'، برچسبِ خوانای فارسی (شمسی + روزِ
    هفته) می‌سازه. اگه فرمت نامعتبر بود، خودِ ورودی رو برمی‌گردونه."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return date_str
    jd = jdatetime.datetime.fromgregorian(datetime=dt)
    return f"{jd.strftime('%Y/%m/%d')} ({weekday_fa(dt)})"

def parse_admin_undo_date(text: str):
    """از یه متنِ آزاد، یه تاریخِ میلادیِ معتبر به فرمت 'YYYY-MM-DD' استخراج
    می‌کنه. فرمت‌های قابلِ قبول:
      • یه عددِ ساده (مثلاً «17») => همون تعداد روزِ قبل از امروز
      • تاریخِ شمسی، مثلاً «1403/06/20» یا «1403-06-20»
      • تاریخِ میلادی، مثلاً «2026-09-10»
    اگه چیزی قابلِ‌فهم نبود، None برمی‌گردونه."""
    import re
    text = normalize_digits((text or "").strip())
    if not text:
        return None
    if re.fullmatch(r"\d{1,4}", text):
        return days_ago_gregorian(int(text))
    m = re.fullmatch(r"(\d{3,4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    if y > 1500:
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return None
    try:
        return jdatetime.date(y, mo, d).togregorian().strftime("%Y-%m-%d")
    except ValueError:
        return None

def now_context_for_ai() -> str:
    """
    یه بلوک آماده برای تزریق به پرامپت سیستمی دستیار هوشمند: لحظه‌ی دقیق
    الان (میلادی و شمسی + روز هفته) به‌وقت تهران. هر بار که صدا زده بشه
    تازه محاسبه می‌شه — یعنی دستیار همیشه، حتی وسط یه گفت‌وگوی طولانی،
    به لحظه‌ی واقعیِ همین الان دسترسی داره، نه یه زمان قدیمی/کش‌شده.
    این خروجی پایه‌ی هر محاسبه‌ی زمانیِ دستیار (یادآور، زمان‌بندی اقدام) است.
    """
    now = datetime.now(TEHRAN_TZ)
    jd = jdatetime.datetime.fromgregorian(datetime=now)
    return (
        f"لحظه‌ی دقیق الان (وقت رسمی تهران، Asia/Tehran):\n"
        f"- میلادی: {now.strftime('%Y-%m-%d %H:%M:%S')} ({weekday_fa(now)})\n"
        f"- شمسی: {jd.strftime('%Y/%m/%d %H:%M:%S')}\n"
        f"این لحظه، مرجع مطلق توئه برای هر محاسبه‌ی زمانی (مثل «۱۵ دقیقه دیگه»، "
        f"«فردا ساعت ۳ ظهر»، «چند روز دیگه»). همیشه از همین لحظه محاسبه کن، نه از "
        f"حافظه یا حدس."
    )

# ─── Progress Bars ───────────────────────────────────────────
def progress_bar(percent: float, length: int = BAR_LENGTH) -> str:
    percent = max(0, min(100, percent))
    filled = int(length * percent / 100)
    half = length - filled
    bar = "█" * filled + ("▒" if half > 0 else "") + "░" * max(0, half - 1)
    return f"[{bar}] {int(percent)}٪"

def warning_bar_player(warnings: int, max_w: int = 3) -> str:
    icons = ["🟢", "🟠", "🔴"]
    result = []
    for i in range(max_w):
        result.append(icons[i] if i < warnings else "░")
    return "[ " + " ".join(result) + " ]"

def warning_bar_admin(warnings: int, max_w: int = 5) -> str:
    filled = "💀" * min(warnings, max_w)
    empty = "░" * max(0, max_w - warnings)
    return f"⚠️ [{filled}{empty}]"

def power_bar(wins: int, losses: int, draws: int) -> str:
    total = wins + losses + draws
    if total == 0:
        return progress_bar(0)
    score = (wins + draws * 0.5) / total * 100
    return progress_bar(score)

def rank_bar(wins: int, total: int) -> str:
    if total == 0:
        return progress_bar(0)
    return progress_bar(wins / total * 100)

# ─── Box Headers ─────────────────────────────────────────────
def box(title: str) -> str:
    border = "═" * (len(title) + 4)
    return f"╔{border}╗\n║  {title}  ║\n╚{border}╝"

def separator(label: str = "") -> str:
    if label:
        return f"╼╼╼╼╼╼ {label} ╾╾╾╾╾╾"
    return "╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼"

def escape_md_legacy(text: str) -> str:
    """فرار دادن کاراکترهای خاص Markdown قدیمی تلگرام (_ * ` [) توی متن‌های دینامیک
    (نام‌ها، دلایل، توضیح لاگ‌ها) که کاربر/ادمین وارد کرده، تا با تعداد فرد
    این کاراکترها (مثلاً یه زیرخط توی 'ai_access') تلگرام خطای
    'can't find end of the entity' ندهد."""
    if not text:
        return text
    for ch in ("\\", "_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def log_line(time_str: str, name: str, action: str) -> str:
    return f"⏱️ `{time_str}` | 👤 {escape_md_legacy(name)} ╼ {escape_md_legacy(action)} 📌"


# ─── جستجوی لاگ: تبدیل ارقام و بازه‌ی ساعت ──────────────────────
_DIGIT_MAP = {}
for _f, _a, _e in zip("۰۱۲۳۴۵۶۷۸۹", "٠١٢٣٤٥٦٧٨٩", "0123456789"):
    _DIGIT_MAP[_f] = _e
    _DIGIT_MAP[_a] = _e


def normalize_digits(text: str) -> str:
    """ارقام فارسی/عربی توی متن ورودی کاربر رو به ارقام انگلیسی تبدیل می‌کنه
    (مثلاً «۲۲ تا ۲۴» → «22 تا 24») تا جستجو/پارس کردن بازه درست کار کنه."""
    if not text:
        return text
    return "".join(_DIGIT_MAP.get(ch, ch) for ch in text)


def parse_hour_range(text: str):
    """از یه متن آزاد مثل «۲۲ تا ۲۴» یا «22-24» یا فقط «22»، بازه‌ی ساعت
    (from, to) بین ۰ تا ۲۳ استخراج می‌کنه. اگه چیزی پیدا نشه (None, None)."""
    import re
    text = normalize_digits((text or "").strip())
    if not text or text == "-":
        return None, None
    nums = re.findall(r"\d+", text)
    if not nums:
        return None, None
    a = int(nums[0])
    b = int(nums[1]) if len(nums) > 1 else a
    a = min(max(a, 0), 23)
    b = min(max(b, 0), 23)
    return a, b


# ─── لاگ اقدامات: برچسب فارسی + اموجی برای هر نوع اقدام ─────────
ACTION_LOG_LABELS = {
    # ── ردیابیِ کامل (audit.py): هر کلیک/دستور/درخواست ──
    "trail_start": ("▶️", "استارت ربات"),
    "trail_command": ("⌨️", "دستور"),
    "trail_button": ("🔘", "کلیک روی دکمه"),
    "trail_message": ("💬", "پیام متنی"),
    "trail_media": ("📎", "ارسال فایل/رسانه"),
    "trail_other": ("🔹", "رویداد تلگرام"),
    "trail_hub_open": ("📱", "بازکردن پنل هاب"),
    "trail_hub_view": ("👁️", "مشاهده در هاب"),
    "trail_hub_write": ("✍️", "تغییر در هاب"),
    "trail_hub_denied": ("⛔", "دسترسی ردشده به هاب"),
    "trail_web_view": ("🖥️", "مشاهده در پنل وب ادمین"),
    "trail_web_write": ("🛠️", "تغییر در پنل وب ادمین"),
    "trail_principal_view": ("🏫", "مشاهده در پنل مدیر مدرسه"),
    "trail_principal_write": ("🏫", "تغییر در پنل مدیر مدرسه"),
    "create_match":              ("♟️", "ثبت مسابقه جدید"),
    "match_result":               ("🏆", "ثبت نتیجه مسابقه"),
    "delete_match":               ("🗑️", "حذف مسابقه"),
    "eliminate_player":           ("❌", "حذف بازیکن از مسابقه"),
    "create_player":              ("👤", "ثبت بازیکن جدید"),
    "bulk_create_player":         ("👥", "ثبت گروهی بازیکن"),
    "create_class":               ("🏫", "ثبت کلاس جدید"),
    "player_warning":             ("⚠️", "اخطار به بازیکن"),
    "kick_player":                ("🚫", "اخراج بازیکن"),
    "suspend_player":             ("⏸️", "تعلیق بازیکن"),
    "revive_player":              ("♻️", "احیای بازیکن"),
    "create_tournament":          ("🏁", "ایجاد تورنمنت"),
    "edit_tournament":            ("✏️", "ویرایش تورنمنت"),
    "end_tournament":             ("🔚", "پایان تورنمنت"),
    "pause_tournament":           ("⏯️", "تعویق تورنمنت"),
    "delete_tournament":          ("🗑️", "حذف تورنمنت"),
    "set_default_tournament":     ("⭐", "تنظیم تورنمنت پیش‌فرض"),
    "adv_lottery":                ("🎲", "قرعه‌کشی پیشرفته"),
    "create_team":                ("🤝", "ثبت تیم جدید"),
    "delete_team":                ("🗑️", "حذف تیم"),
    "assign_task":                ("📋", "اعطای وظیفه"),
    "admin_warning":              ("⚠️", "اخطار به مدیر"),
    "admin_clear_warnings":       ("✅", "پاک‌کردن اخطارهای مدیر"),
    "kick_admin":                 ("🚷", "اخراج مدیر"),
    "kick_admin_keyword":         ("🚷", "اخراج مدیر"),
    "set_admin_keyword":          ("👮", "تنظیم مدیر"),
    "flood_toggle":               ("🌊", "روشن/خاموش‌کردن ضدِ فلود"),
    "admin_role_change":          ("🔄", "تغییر نقش توسط خود مدیر"),
    "override_strike":            ("🔁", "تغییر تعداد اخطار مدیر"),
    "toggle_perm":                ("🔐", "تغییر دسترسی مدیر"),
    "identity_change":            ("🪪", "تغییر نام مدیر ارشد"),
    "admin_identity_change":      ("🪪", "تغییر نام مدیر"),
    "login":                      ("🔑", "ورود مدیر ارشد"),
    "set_status":                 ("🚦", "تغییر وضعیت سیستم"),
    "toggle_setting":             ("⚙️", "تغییر تنظیمات"),
    "backup":                     ("💾", "تهیه بکاپ"),
    "backup_now":                 ("💾", "بکاپ اضطراری"),
    "auto_backup":                ("🗄️", "بکاپ خودکار"),
    "restore":                    ("📥", "بازگردانی بکاپ"),
    "repair_on":                  ("🔧", "فعال‌سازی حالت تعمیر"),
    "repair_off":                 ("✅", "غیرفعال‌سازی حالت تعمیر"),
    "dbstatus_on":                ("🟢", "فعال‌سازی دستی دیتابیس"),
    "dbstatus_off":               ("🔴", "غیرفعال‌سازی دستی دیتابیس"),
    "new_year_reset":             ("🎓", "ریست سال تحصیلی"),
    "set_group":                  ("📡", "تنظیم گروه اعلانات"),
    "set_channel":                ("🆔", "تنظیم کانال اعلانات"),
    "broadcast_toggle":           ("📢", "تغییر تنظیم پخش خودکار"),
    "set_chess_ai_broadcast_text": ("🤖", "تنظیم متن اعلان هوش مصنوعی"),
    "workhour_start":             ("🟢", "آغاز ساعت کاری"),
    "workhour_end":               ("🔴", "پایان ساعت کاری"),
    "queue_request":              ("⏳", "صف انتظار درخواست"),
    "approve_from_queue":         ("✅", "تایید از صف انتظار"),
    "release_from_queue":         ("↩️", "خروج از صف انتظار"),
    "toggle_ai_online":           ("🤖", "تغییر وضعیت هوش مصنوعی"),
    "panic":                      ("🚨", "فرمان اضطراری PANIC"),
    "unpanic":                    ("✅", "بازگشت از PANIC"),
    "freeze_all":                 ("🧊", "فعال‌سازی APS"),
    "block_user":                 ("⛔", "بلاک کاربر"),
    "unblock_user":               ("✅", "آنبلاک کاربر"),
    "weekly_champion":            ("🏅", "قهرمان هفته"),
    "undo_admin_actions":         ("↩️", "خنثی‌سازی اقدامات ادمین"),
    "redo_admin_actions":         ("↪️", "بازگردانیِ دوبارهٔ اقدامات ادمین"),
}


def action_log_label(action_type: str):
    return ACTION_LOG_LABELS.get(action_type, ("📌", action_type or "اقدام نامشخص"))


def format_log_entry(time_str: str, name: str, action_type: str, description: str) -> str:
    """یه بلوکِ مرتب و با جزییات برای یک ردیف از لاگ اقدامات می‌سازه:
    اموجی + عنوان فارسیِ اقدام، جزییات (توضیح ثبت‌شده)، ثبت‌کننده و زمان."""
    emoji, label = action_log_label(action_type)
    desc = escape_md_legacy(description) if description else ""
    head = f"{emoji} *{label}*" + (f" — {desc}" if desc else "")
    tail = f"    👤 {escape_md_legacy(name)}   ⏱ `{time_str}`"
    return head + "\n" + tail

# ─── Safe Telegram senders ────────────────────────────────────
# نکته: توی این پروژه در ده‌ها جای مختلف parse_mode="Markdown" با متن‌های
# دینامیک (نام کاربر، توضیح گزارش، متن فیدبک و ...) استفاده شده بدون اینکه
# escape بشن. کافیه همچین متنی یه زیرخط/ستاره/بک‌تیک فرد داشته باشه تا
# تلگرام با خطای "Can't parse entities: can't find end of the entity" کل
# ارسال پیام رو رد کنه — و چون خیلی از این نقطه‌ها try/except نداشتن،
# این خطا مستقیم می‌رفت بالا و توسط global_error_handler قاپیده می‌شد
# (دقیقاً همون خطایی که موقع دیدن گزارش‌ها/لاگ‌ها می‌گرفتی).
# به‌جای escape کردن دستی همه‌ی اون نقطه‌ها (ریسک بالا، جای خطای زیاد)،
# این توابع کمکی رو ساختیم: اول با Markdown امتحان می‌کنن، اگه تلگرام به
# خاطر پارس نشدن Markdown رد کرد، خودکار بدون parse_mode دوباره می‌فرستن —
# یعنی دیگه هیچ پیامی به خاطر یه کاراکتر خاص گم/بی‌جواب نمی‌مونه.
def _is_entity_parse_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "can't parse entities" in msg or "can't find end of the entity" in msg

async def safe_send_message(bot, chat_id, text: str, reply_markup=None, parse_mode="Markdown"):
    """جایگزین امن bot.send_message: اگه پارس Markdown خطا بده، متن خام می‌فرسته."""
    try:
        return await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
    except BadRequest as e:
        if parse_mode and _is_entity_parse_error(e):
            logger.warning(f"Markdown parse failed for chat {chat_id}, resending as plain text: {e}")
            return await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        raise

# حداکثر طول کپشنِ مجاز روی عکس/فایل/ویدیو/صوت در تلگرام (برخلاف متن پیام
# ساده که تا ۴۰۹۶ کاراکتر جواب می‌ده، کپشن رسانه فقط تا ۱۰۲۴ کاراکتره).
TELEGRAM_CAPTION_LIMIT = 1024

async def safe_send_media(bot, chat_id, file_type: str, file_id: str, caption: str,
                           parse_mode="HTML", reply_markup=None):
    """جایگزین امن ارسال عکس/فایل/ویدیو/صوت با کپشن.

    قبلاً این مسیر مستقیم bot.send_photo/... رو صدا می‌زد و هر خطایی
    (مثل «Can't parse entities» به خاطر یه کاراکتر خاص توی متن، یا کپشنِ
    بلندتر از ۱۰۲۴ کاراکتر) با یه except خالی قورت داده می‌شد — یعنی نه
    فایل می‌رفت، نه متن، و هیچ خطایی هم لاگ نمی‌شد. این تابع سه‌تا مشکل رو
    حل می‌کنه:
    ۱) اگه پارس فرمت (HTML/Markdown) خطا بده → بدون parse_mode دوباره
       امتحان می‌کنه (متن خام، ولی حداقل می‌ره).
    ۲) اگه کپشن از سقف ۱۰۲۴ کاراکتر تلگرام بلندتر باشه → رسانه رو بدون
       کپشن می‌فرسته و متن کامل رو به‌عنوان یه پیام جدا بعدش می‌فرسته،
       به‌جای اینکه کلاً ارسال شکست بخوره.
    ۳) خطاهای واقعی رو لاگ می‌کنه به‌جای اینکه بی‌صدا نادیده بگیره.
    """
    senders = {
        "photo": bot.send_photo,
        "document": bot.send_document,
        "video": bot.send_video,
        "audio": bot.send_audio,
    }
    send = senders.get(file_type)
    if not send:
        return await safe_send_message(bot, chat_id, caption, reply_markup=reply_markup, parse_mode=parse_mode)

    media_kwarg = {"photo": "photo", "document": "document", "video": "video", "audio": "audio"}[file_type]
    long_caption = len(caption) > TELEGRAM_CAPTION_LIMIT

    try:
        if long_caption:
            await send(chat_id=chat_id, **{media_kwarg: file_id})
            return await safe_send_message(bot, chat_id, caption, reply_markup=reply_markup, parse_mode=parse_mode)
        return await send(chat_id=chat_id, **{media_kwarg: file_id}, caption=caption,
                           parse_mode=parse_mode, reply_markup=reply_markup)
    except BadRequest as e:
        if parse_mode and _is_entity_parse_error(e):
            logger.warning(f"Parse failed sending {file_type} to {chat_id}, resending without parse_mode: {e}")
            try:
                if long_caption:
                    return await safe_send_message(bot, chat_id, caption, reply_markup=reply_markup, parse_mode=None)
                return await send(chat_id=chat_id, **{media_kwarg: file_id}, caption=caption,
                                   reply_markup=reply_markup)
            except Exception as e2:
                logger.warning(f"Retry without parse_mode also failed sending {file_type} to {chat_id}: {e2}")
                return None
        logger.warning(f"Failed to send {file_type} to {chat_id}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error sending {file_type} to {chat_id}: {e}")
        return None

async def safe_reply_text(message, text: str, reply_markup=None, parse_mode="Markdown"):
    """جایگزین امن message.reply_text."""
    try:
        return await message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except BadRequest as e:
        if parse_mode and _is_entity_parse_error(e):
            logger.warning(f"Markdown parse failed on reply, resending as plain text: {e}")
            return await message.reply_text(text, reply_markup=reply_markup)
        raise

def _is_not_modified_error(exc: Exception) -> bool:
    return "message is not modified" in str(exc).lower()


def _is_unrecoverable_edit_error(exc: Exception) -> bool:
    """خطاهایی که یعنی «دیگه اصلاً نمی‌شه این پیام رو ویرایش کرد»:
    پیام حذف شده، خیلی قدیمیه، یا خودِ کوئری منقضی شده. توی این حالت‌ها
    تنها راه اینه که یه پیام تازه بفرستیم، نه اینکه بترکونیم و کاربر
    مجبور بشه /start بزنه."""
    msg = str(exc).lower()
    return (
        "message to edit not found" in msg
        or "message can't be edited" in msg
        or "query is too old" in msg
        or "message to be edited not found" in msg
    )


async def safe_edit_message_text(query, text: str, reply_markup=None, parse_mode="Markdown"):
    """جایگزین امن query.edit_message_text.

    سه تا حالت خطا رو پوشش می‌ده (که هر سه‌تاشون قبلاً باعث می‌شدن دکمه‌های
    برگشت/منو کرش کنن و کاربر مجبور بشه /start بزنه):
    ۱) خطای پارس Markdown → بدون parse_mode دوباره امتحان می‌کنه.
    ۲) «Message is not modified» (مثلاً دوبار پشت‌سرهم زدن دکمه‌ی برگشت) →
       بی‌خطره، فقط نادیده می‌گیریم، نیازی به هیچ اقدامی نیست.
    ۳) پیام دیگه قابل ویرایش نیست (حذف‌شده/خیلی قدیمی/کوئری منقضی) →
       به‌جای کرش‌کردن، همون محتوا رو به‌عنوان پیام تازه می‌فرستیم تا
       کاربر حداقل یه منوی کار-کن جلوش داشته باشه.
    """
    try:
        return await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except BadRequest as e:
        if _is_not_modified_error(e):
            return None
        if parse_mode and _is_entity_parse_error(e):
            logger.warning(f"Markdown parse failed on edit, resending as plain text: {e}")
            try:
                return await query.edit_message_text(text, reply_markup=reply_markup)
            except BadRequest as e2:
                if _is_not_modified_error(e2):
                    return None
                if not _is_unrecoverable_edit_error(e2):
                    raise
        elif not _is_unrecoverable_edit_error(e):
            raise
        # پیام قابل ویرایش نبود — به‌جاش یه پیام جدید می‌فرستیم تا کاربر گیر نکنه
        logger.warning(f"Could not edit message, sending fresh one instead: {e}")
        try:
            return await query.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        except BadRequest:
            return await query.message.reply_text(text, reply_markup=reply_markup)

# ─── Notification sender ─────────────────────────────────────
async def send_notification(bot, user_id: int, text: str, reply_markup=None):
    notif_on = await db.get_setting("notifications_enabled", "1")
    if notif_on != "1":
        return
    try:
        await safe_send_message(bot, user_id, text, reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"Failed to notify {user_id}: {e}")

async def broadcast_to_admins(bot, text: str, exclude_id: int = None, reply_markup=None, parse_mode="Markdown"):
    notif_on = await db.get_setting("notifications_enabled", "1")
    if notif_on != "1":
        return
    admins = await db.get_active_admins()
    group_id = await db.get_setting("announcement_group_id", "")

    for admin in admins:
        tid = admin["telegram_id"]
        if tid == exclude_id:
            continue
        try:
            perms = json.loads(admin["permissions"])
            if not perms.get("notifications", True):
                continue
        except Exception:
            pass
        try:
            await safe_send_message(bot, tid, text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            logger.warning(f"Broadcast failed for {tid}: {e}")

    if group_id:
        try:
            await safe_send_message(bot, int(group_id), text)
        except Exception as e:
            logger.warning(f"Group broadcast failed: {e}")

async def notify_pishva(bot, text: str, reply_markup=None):
    await send_notification(bot, PISHVA_ID, text, reply_markup)

# ─── System Status Gate ──────────────────────────────────────
async def check_status_gate(query, action_name: str = "") -> bool:
    """قبلاً ۴ تا db.get_setting پشتِ‌سرِهم (نه موازی) صدا زده می‌شد — یعنی
    روی هر منویِ محافظت‌شده (بازیکنان/مسابقات/مخابرات و ...)، حتی با کش
    گرم، ۴ برابرِ لازم کار می‌کرد؛ با کشِ سرد، ۴ رفت‌وبرگشتِ کاملِ شبکه‌ای
    پشتِ‌سرِهم. مدیر ارشد هم قبل از چکِ رد/قبول، مجبور بود منتظرِ همین ۴ تا
    بمونه با این‌که نتیجه‌ش براش فرقی نداشت. حالا: مدیر ارشد فوری رد می‌شه،
    و بقیه‌ی تنظیمات هم‌زمان (نه پشتِ‌سرِهم) خونده می‌شن."""
    user_id = query.from_user.id
    if user_id == PISHVA_ID:
        return False

    status, bot_active, update_mode, working_hours = await asyncio.gather(
        db.get_setting("system_status", "normal"),
        db.get_setting("bot_active_for_admins", "1"),
        db.get_setting("bot_update_mode", "0"),
        db.get_setting("working_hours_active", "0"),
    )

    # Update mode blocks everyone except pishva
    if update_mode == "1":
        await query.answer("🔄 ربات در حال آپدیت است. لطفاً منتظر بمانید.", show_alert=True)
        return True

    # Bot inactive for admins
    if bot_active != "1":
        await query.answer("💤 ربات توسط مدیر ارشد خاموش شده است.", show_alert=True)
        return True

    # Working hours check — if working hours system is on and not active, block
    if working_hours == "0":
        wh_system = await db.get_setting("working_hours_system_enabled", "0")
        if wh_system == "1":
            await query.answer(
                "🕐⛔ ساعت کاری موقتاً بسته شده است!\n"
                "🙏 لطفاً منتظر بمانید تا مدیر ارشد دوباره ساعت کاری رو باز کنه. ⏳✨",
                show_alert=True
            )
            return True

    if status == "aps":
        await query.answer(
            "🪽 وضعیت APS در حال اجرا است؛ دسترسی به این بخش محدود شده.\n"
            "لطفاً تا برقراری امنیت شکیبا باشید یا با مدیر ارشد در ارتباط باشید.",
            show_alert=True
        )
        return True

    if status == "danger":
        await query.answer(
            "🔴 سیستم در وضعیت خطرناک قرار دارد؛ تمام عملیات ادمین‌ها متوقف شده است.",
            show_alert=True
        )
        return True

    if status == "bad":
        blocked = ["match_delete", "match_edit", "warning", "ban_player", "msg_admin", "view_players"]
        for b in blocked:
            if b in action_name:
                await query.answer(
                    f"🟡 سیستم در وضعیت احتیاطی است؛ این عملیات موقتاً غیرفعال شده.",
                    show_alert=True
                )
                return True

    return False

# ─── Role / Permission Checkers ──────────────────────────────
async def get_user_role(user_id: int) -> str:
    if user_id == PISHVA_ID:
        return "pishva"
    admin = await db.get_admin(user_id)
    if admin and admin["is_active"]:
        return admin["role"]
    return ""

async def pishva_display() -> str:
    return await db.get_setting("pishva_display_name", "مدیر ارشد")

async def admin_display(admin) -> str:
    if admin and admin["display_name"]:
        return admin["display_name"]
    return admin["full_name"] if admin else "نامشخص"

# ─── Date validator ──────────────────────────────────────────
def validate_date(date_str: str) -> bool:
    try:
        parts = date_str.replace("/", "-").split("-")
        return len(parts) == 3
    except Exception:
        return False

# ─── Smart Lottery ───────────────────────────────────────────
import random

async def smart_lottery(players: list) -> tuple:
    if len(players) < 2:
        return None, None, False
    player_ids = [p["id"] for p in players]
    random.shuffle(player_ids)
    for i in range(len(player_ids)):
        for j in range(i + 1, len(player_ids)):
            if not await db.have_played_before(player_ids[i], player_ids[j]):
                p1 = next(p for p in players if p["id"] == player_ids[i])
                p2 = next(p for p in players if p["id"] == player_ids[j])
                return p1, p2, False
    random.shuffle(player_ids)
    p1 = next(p for p in players if p["id"] == player_ids[0])
    p2 = next(p for p in players if p["id"] == player_ids[1])
    return p1, p2, True

# ─── Player rank label ───────────────────────────────────────
def get_rank_label(wins: int, total: int) -> str:
    if total == 0:
        return "🔰 تازه‌کار"
    pct = wins / total * 100
    if pct >= 80:
        return "👑 افسانه‌ای"
    elif pct >= 65:
        return "💎 الماس"
    elif pct >= 50:
        return "🥇 طلا"
    elif pct >= 35:
        return "🥈 نقره"
    elif pct >= 20:
        return "🥉 برنز"
    else:
        return "🔰 مبتدی"


# ─── Permission Gate ─────────────────────────────────────────
async def check_perm(query, perm: str, default: bool = True) -> bool:
    """
    اگر کاربر مدیر ارشد باشه: همیشه False (یعنی pass).
    اگر ادمین فعال باشه: permission رو از دیتابیس چک میکنه.
    اگر اصلاً ادمین نباشه: بلاک می‌کنه (True برمیگردونه).
    مقدار True برمیگردونه یعنی «بلاک شو».

    FIX: قبلاً اگه user اصلاً ادمین نبود، default=True بود و pass می‌شد.
         حالا non-admin همیشه بلاک می‌شه.
    """
    uid = query.from_user.id
    if uid == PISHVA_ID:
        return False

    admin = await db.get_admin(uid)

    # ── کاربر اصلاً ادمین نیست یا غیرفعاله ──────────────────
    if not admin or not admin["is_active"]:
        await query.answer(
            "⛔ شما مجوز دسترسی به این بخش را ندارید.\n"
            "این عملیات فقط برای مدیران ثبت‌شده مجاز است.",
            show_alert=True
        )
        return True

    # ── ادمین فعال: بررسی permission خاص ────────────────────
    import json
    try:
        perms = json.loads(admin["permissions"])
        allowed = perms.get(perm, default)
    except Exception:
        allowed = default

    if not allowed:
        await query.answer("⛔ شما اجازه انجام این عملیات را ندارید.", show_alert=True)
        return True

    return False
