"""
ai_assistant.py — دستیار هوشمند متصل به Gemini API

معماری:
  - هر پیام مدیر ارشد/مدیر (وقتی حالت «دستیار» فعاله) با تاریخچه‌ی کوتاه
    مکالمه برای Gemini فرستاده می‌شه.
  - به مدل فقط لیست ابزارهایی معرفی می‌شه که نقش همون کاربر اجازه‌ش رو
    داره (ai_tools.TOOL_PERMISSIONS).
  - اگه مدل تصمیم بگیره یه تابع صدا بزنه، ai_tools.dispatch() اجرا و
    دوباره چک دسترسی می‌کنه (خط دفاعی دوم — نه فقط اعتماد به مدل).
  - برای گفت‌وگوی عادی (تحلیل، درددل، گزارش با کلمات خودش) مدل مستقیم
    متن برمی‌گردونه، بدون صدازدن تابعی.
"""
import asyncio
import os
import json
import logging
import re

import httpx
import net_utils
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest

import database as db
from helpers import safe_edit_message_text, get_user_role, pishva_display, admin_display, now_context_for_ai
from config import PISHVA_ID, ROLE_PISHVA, ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER
import ai_tools
import ai_memory
import knowledge_base

logger = logging.getLogger(__name__)

GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY", "") or "").strip().strip('"\'').strip() or "AQ.Ab8RN6JaHzAJJuSqX6hSBoXHEqGhV0AnIIvLBKQlbCuRlb7-8A"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
# نکته (۲۰۲۶-۰۹-۲۴، اصلاح‌شده): طبق گزارش‌های متعددِ کاربرا در فوروم رسمیِ گوگل
# (بهار/تابستانِ ۲۰۲۶)، gemini-2.5-flash-lite به‌شدت ناپایدار شده (خطای ۵۰۳
# «overloaded» مکرر، نرخ موفقیتِ گزارش‌شده حدود ۳۵-۴۰٪، و حتی یه قطعیِ ناگهانیِ
# ۴۰۴ در ۹ ژوئیه، هفته‌ها زودتر از شاتدأونِ رسمیِ ۱۶ اکتبر). چون این مدل همیشه
# *اول* زنجیره بود، هر پیام (توی ربات، پنل مدیر مدرسه، و چتِ شطرنج — هر سه از
# همین زنجیره استفاده می‌کنن) قبل از جواب باید یه تایم‌اوتِ کامل رو روش تلف
# می‌کرد. الان gemini-3.5-flash-lite (نسل ۳، minimal thinking) اول صفه و
# gemini-2.5-flash-lite رفته ته لیستِ fallback (پایین‌تر) — اگه دوباره پایدار شد
# سریع‌تره، ولی دیگه هیچ درخواستی مجبور نیست اول منتظرش بمونه.
FALLBACK_MODELS = ["gemini-3.6-flash", "gemini-flash-latest", "gemini-2.5-flash-lite"]
if GEMINI_MODEL in FALLBACK_MODELS:
    FALLBACK_MODELS.remove(GEMINI_MODEL)
MODEL_CHAIN = [GEMINI_MODEL] + FALLBACK_MODELS

RETRIES_PER_MODEL = 2
RETRY_DELAY_SECONDS = 1
# نکته (۲۰۲۶-۰۹): برای کارهای سنگین/دسته‌ای (batch_execute با ده‌ها/صدها آیتم)
# مدل باید یه لیستِ JSON بزرگ توی همون یه functionCall تولید کنه، که هم زمانِ
# تولیدش بیشتره (تایم‌اوتِ بزرگ‌تر لازمه) هم خودِ توکن‌هاش بیشتره — این سقف‌ها
# فقط برای مکالمه‌ی عادی/کوتاه محدودکننده نیستن (مدل خودش زودتر از سقف تموم
# می‌کنه)، فقط برای وقتی که واقعاً به فضای بیشتر نیاز داره جا باز می‌کنن.
REQUEST_TIMEOUT_SECONDS = 45   # قبلاً ۲۰ بود؛ تولید یه batch بزرگ ممکنه بیشتر طول بکشه
MAX_OUTPUT_TOKENS = 8192       # قبلاً ۱۰۲۴ بود؛ برای سریالایز کردنِ صدها آیتمِ batch کافی نبود

MAX_HISTORY_TURNS = 6          # چند رفت‌وبرگشت آخر رو نگه داریم (کمتر = توکن کمتر = سریع‌تر)
# قبلاً ۳ بود. با batch_execute اکثر «کارهای زیاد» توی همون یه هاپ حل می‌شن،
# ولی این عدد رو هم بالا بردیم تا اگه مدل قبلش نیاز به یه استعلام (مثلاً
# list_admins) داشت یا چندتا batch_execute پشت‌سرهم لازم بود، زود قطع نشه.
MAX_TOOL_HOPS = 10

ROLE_LABELS = {
    ROLE_PISHVA: "مدیر ارشد (بالاترین سطح دسترسی)",
    ROLE_TOURNAMENT_MANAGER: "مدیر مسابقات",
    ROLE_SECURITY_MANAGER: "مدیر امنیتی",
}

ACTIVATE_WORDS = {"رهگشا", "🤖 رهگشا", "دستیار", "دستیار هوشمند", "🤖 دستیار هوشمند"}
DEACTIVATE_WORDS = {"خروج از رهگشا", "بستن رهگشا", "خروج از دستیار", "بستن دستیار", "بسه"}

AI_OFFLINE_MESSAGE = "💤 هوش مصنوعی فعلا در دسترس نیست."

# ────────────────────────────────────────────────────────────────
# اگه پیام کاربر شامل یکی از این کلمه‌ها باشه، یعنی احتمالاً یه کار
# اجرایی می‌خواد (نه صرفاً درددل/سوال). توی این حالت به‌جای اینکه فقط
# امیدوار باشیم مدل خودش تصمیم درست بگیره، با toolConfig مجبورش می‌کنیم
# حتماً یکی از تابع‌های مجازش رو صدا بزنه (mode="ANY") — این همون چیزیه
# که باعث می‌شد قبلاً باید ۱۰-۲۰ بار تکرار می‌کردی تا مدل بالاخره اقدام کنه.
# ────────────────────────────────────────────────────────────────
ACTION_KEYWORDS = [
    "ثبت", "حذف", "پاک کن", "اخطار", "اخراج", "برگردون", "بازگردان", "برگردان",
    "شروع", "پایان", "تموم کن", "تمومش کن", "ارسال", "بفرست", "بیانیه", "خبر",
    "مسدود", "بلاک", "آنبلاک", "غیرفعال", "فعال کن", "تغییر نقش", "تغییر بده",
    "نتیجه", "اصلاح کن", "باز کن", "روشن کن", "خاموش کن", "اجرا کن",
    "یادم بنداز", "یادآور", "یاد آور", "یادآوری", "یاد آوری",
    "زمان‌بندی", "زمانبندی", "دیگه فعال کن", "دیگه انجام بده", "لغو یادآور", "لغو زمان‌بندی",
    "بگو به", "خبر بده", "اطلاع بده", "وظیفه بده", "پیام بده", "بسپار",
    "یادت باشه", "به خاطر بسپار", "فراموش نکن", "یادداشت کن", "ثبتش کن",
    "برتر کن", "برتر بکن", "ویژه کن", "ویژه بکن", "برترش", "ویژه‌ش",
    "تیم بساز", "به تیم اضافه", "از تیم حذف", "سرگروه",
    "مدیر جدید", "ادمین جدید", "دسترسی بده", "دسترسی بگیر",
]


def _looks_like_action(text: str) -> bool:
    return any(kw in text for kw in ACTION_KEYWORDS)


# ────────────────────────────────────────────────────────────────
# «به حافظه‌ت اضافه کن» — تشخیصِ قطعیِ درخواستِ ثبت در حافظه.
# قبلاً فقط به تصمیمِ مدل سپرده شده بود و عبارتِ «به حافظت اضافه کن» توی
# ACTION_KEYWORDS نبود؛ پس مدل گاهی فقط «باشه» می‌گفت و هیچی ثبت نمی‌شد.
# الان اگه پیام این‌شکلی باشه، دورِ اول مدل *مجبور* می‌شه remember_note رو صدا بزنه،
# و اگه باز هم ثبت نشد (مدل/شبکه/هرچی)، خودِ کد مستقیم ذخیره می‌کنه.
# ────────────────────────────────────────────────────────────────
_FA_LETTERS = str.maketrans({"ي": "ی", "ك": "ک"})
_MEMO_SAVE_VERB_RE = re.compile(
    r"(اضافه|ثبت|ذخیره|وارد|ضبط|ست)( ?(ش|شو|اش))? ?(کن|بکن)|بنویس|بسپار|بسپر|بذار|بزار|نگه ?دار")
_MEMO_STANDALONE_RE = re.compile(r"یادت باشه|یادت باشد|به خاطر بسپار|به یاد داشته باش|یادداشت کن")
_MEMO_STRIP_RE = re.compile(
    r"^(لطفا |لطفاً )?((به|توی|تو|در|از) )?(حافظ(ه)? ?(ت|ات|م|ی من)?) ?(اضافه|ثبت|ذخیره|وارد)( ?(ش|شو|اش))? ?(کن|بکن)( که)?[ :،,\-]*"
    r"|^(یادت باشه|یادت باشد|به خاطر بسپار|یادداشت کن)( که)?[ :،,\-]*")


def _fa(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").translate(_FA_LETTERS).replace("\u200c", " ")).strip()


def _looks_like_memory_save(text: str) -> bool:
    t = _fa(text)
    if not t or t.endswith(("؟", "?")):
        return False
    if _MEMO_STANDALONE_RE.search(t):
        return True
    return "حافظ" in t and bool(_MEMO_SAVE_VERB_RE.search(t))


def _memory_content_from(text: str) -> str:
    t = _fa(text)
    cleaned = _MEMO_STRIP_RE.sub("", t).strip()
    return cleaned or text.strip()


async def _memory_fallback(ctx, text: str, uid: int, memory_intent: bool) -> str:
    """اگه پیام «ثبت در حافظه» بود و remember_note واقعاً اجرا نشده، مستقیم ذخیره می‌کنه.
    متنِ اضافه برای کاربر رو برمی‌گردونه ("" = لازم نبود)."""
    if not memory_intent or ai_memory.pop_memo_explicit(ctx):
        return ""
    try:
        states = await ai_tools.get_category_states()
        if str(states.get("notes", "1")) == "0":
            return ""  # مدیر ارشد دسته‌ی یادداشت‌ها رو عمداً خاموش کرده
    except Exception:
        pass
    try:
        content = _memory_content_from(text)
        note_id = await ai_memory.add("عمومی", content, visibility="pishva", created_by=uid, source="manual")
        ai_memory.mark_memo_updated(ctx, explicit=True)
        return f"🧠 یادداشت #{note_id} مستقیم ثبت شد."
    except Exception as e:
        logger.exception("direct memory save failed")
        return f"⚠️ ثبت در حافظه انجام نشد — {type(e).__name__}: {e}"


MEMO_TAG = "Memo Updated📝"


async def _tail(ctx, text: str, uid: int, memory_intent: bool) -> str:
    """ته‌پیامِ هر پاسخ: (اگه لازم بود) ثبتِ مستقیم + عبارتِ «Memo Updated📝» بعد از هر ثبتِ حافظه."""
    out = ""
    extra = await _memory_fallback(ctx, text, uid, memory_intent)
    if extra:
        out += "\n\n" + extra
    if ai_memory.pop_memo_updated(ctx):
        out += "\n\n" + MEMO_TAG
    return out


# ────────────────────────────────────────────────────────────────
# knowledge_base.py (نقشه‌ی کاملِ منوها/دکمه‌ها/کلمات/ابزارها) حجمِ قابل‌توجهی
# داره؛ اگه به همه‌ی پیام‌ها اضافه بشه، هر پیام (حتی «سلام» یا شوخی) کندتر
# می‌شه. برای همین فقط وقتی پیامِ کاربر شبیهِ سوالِ راهنما/مسیر/نحوه‌ی کار
# باشه ضمیمه‌ش می‌کنیم — بقیه‌ی پیام‌ها (دستور اجرایی، گپ‌وگفت، تحلیل) با
# همون سرعتِ قبل جواب می‌گیرن.
# ────────────────────────────────────────────────────────────────
HELP_QUESTION_KEYWORDS = [
    "کجاست", "کجاس", "کجا", "چطور", "چجوری", "چگونه", "چطوری",
    "از کجا", "چه جوری", "راهنما", "راهنمایی", "کمک کن", "دکمه",
    "منو", "مسیر", "چیکار کنم", "چی کار کنم", "چطور می‌شه", "چطور میشه",
    "چجوری میشه", "چجوری می‌شه", "نحوه", "آموزش", "بلد نیستم", "یاد بدم",
    "یادم بده", "پیدا نمی‌کنم", "پیدا نمیکنم",
]


def _looks_like_help_question(text: str) -> bool:
    return any(kw in text for kw in HELP_QUESTION_KEYWORDS)


WEATHER_KEYWORDS = [
    "هوا", "دما", "بارون", "باران", "برف", "سرد", "گرم", "باد", "آفتاب", "ابری", "ماه", "فاز ماه",
    "ساعت", "تاریخ", "امروز", "الان", "چندمه", "چندم", "روز هفته", "خوش‌آمد", "خوش آمد", "سلام و احوال",
]


def _looks_like_weather_time(text: str) -> bool:
    return any(kw in text for kw in WEATHER_KEYWORDS)


def kb_ai_reply():
    """زیر هر پیام دستیار، همیشه دکمه‌ی خروج و چت جدید/تاریخچه باشد."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 چت جدید", callback_data="ai_menu"),
         InlineKeyboardButton("🚪 خروج از چت", callback_data="ai_exit")],
    ])


def _merge_pending_buttons(ctx: ContextTypes.DEFAULT_TYPE):
    """اگه ابزار open_panel چیزی برای نشون‌دادن آماده کرده، به کیبورد پاسخ اضافه‌ش کن."""
    rows = kb_ai_reply().inline_keyboard
    pending = ctx.user_data.pop("_ai_pending_buttons", None)
    rows = list(rows)
    if pending:
        for label, cb in pending:
            rows.insert(0, [InlineKeyboardButton(label, callback_data=cb)])
    return InlineKeyboardMarkup(rows)


async def _is_ai_online() -> bool:
    return (await db.get_setting("ai_online", "1")) == "1"


async def _can_use_ai(uid: int, role: str) -> bool:
    """مدیر ارشد همیشه اجازه دارد؛ برای ادمین‌ها هم پرمیشن جدا و هم وضعیت امنیتی چک می‌شود."""
    if uid == PISHVA_ID:
        return True
    status = await db.get_setting("system_status", "normal")
    if status in ("danger", "aps"):
        return False
    admin = await db.get_admin(uid)
    if not admin:
        return False
    try:
        perms = json.loads(admin["permissions"])
    except Exception:
        perms = {}
    # پیش‌فرض True تا ادمین‌های ثبت‌شده‌ی قبلی (بدون این کلید در permissions) بلاک نشوند
    return perms.get("ai_access", True)


def _system_prompt(role: str, display_name: str = "", memory_rows=None, user_text: str = "") -> str:
    role_label = ROLE_LABELS.get(role, role)
    allowed = [n for n, roles in ai_tools.TOOL_PERMISSIONS.items() if role in roles]
    who = f"«{display_name}» (نقش: {role_label})" if display_name else f"نقشش «{role_label}»"
    memory_block = ""
    if memory_rows:
        mem_lines = [f"- [{str(r['created_at'])[:10]}] {r['subject']}: {r['content']}" for r in memory_rows]
        memory_block = (
            "\n\nحافظه‌ی بلندمدتت (این‌ها یادداشت‌ها/مسائلی هستن که قبلاً — حتی توی چت‌های دیگه یا بعد از "
            "«چت جدید» — ثبت شدن؛ مستقل از تاریخچه‌ی همین گفتگو در دسترستن. اگه حرف الان کاربر به یکی از "
            "این‌ها مربوط بود، ازش استفاده کن و طوری جواب بده که انگار یادته — لازم نیست دوباره بپرسی یا "
            "وانمود کنی نمی‌دونی):\n" + "\n".join(mem_lines)
        )
    delegate_block = (
        "\n\nمدیریت مدیران در نبود مدیر ارشد (این بخش خیلی مهمه):\n"
        "- وقتی مدیر ارشد ازت خواست به‌جاش کارها رو بچرخونی، در تماس با مدیرها باشی، بهشون "
        "وظیفه بدی یا کمکشون کنی، این یعنی واقعاً از ابزارهای message_admin (پیام مستقیم به یک "
        "مدیر خاص) و assign_task (اعطای وظیفه‌ی مشخص با عنوان و توضیح) استفاده کن — نه فقط قول "
        "بده که این کار رو می‌کنی. اگه چند مدیر باید خبردار بشن، برای هرکدوم جدا این تابع‌ها رو "
        "صدا بزن.\n"
        "- برای این کار باید بدونی چه مدیرهایی هستن؛ لازم شد اول از list_admins استفاده کن تا "
        "اسم/نقش دقیق‌شون رو داشته باشی، بعد پیام یا وظیفه رو براشون بفرست.\n"
        "- اگه مدیر ارشد گفت «در نبودم کارها رو بچرخون» بدون جزئیات بیشتر، از خودش بپرس دقیقاً "
        "چه انتظاری داره (چه کارهایی، به کدوم مدیرها) — حدس الکی نزن؛ ولی همین که مشخص شد، واقعاً "
        "با message_admin/assign_task اجراش کن، نه فقط توی چت با مدیر ارشد تاییدش کن.\n"
        "- اگه یه مدیر بهت (یا از طریق دستیار خودش) خبر داد یه کاری رو انجام داده یا مشکلی پیش "
        "اومده، می‌تونی با notify یا گزارش‌دهی مناسب (یا اگه لازم بود پیام به مدیر ارشد از طریق "
        "توضیح در پاسخ) این رو منتقل کنی؛ سیستم به‌صورت خودکار هم هر اقدام واقعی رو به مدیر ارشد "
        "گزارش می‌کنه."
    ) if role == ROLE_PISHVA else ""
    cms_block = (
        "\n\nنام سیستم (قانون ثابت): اسم این سیستم فقط «CMS» است. هیچ‌وقت کلمه‌ی «آکادمی» "
        "(یا Academy) رو برای اشاره به این سیستم یا مجموعه به کار نبر — حتی اگه توی پیام کاربر "
        "یا داده‌ها دیدی؛ تو همیشه فقط «CMS» بگو."
    )
    if role == ROLE_PISHVA:
        cms_block += (
            "\n\nحافظه (مهم): هر وقت مدیر ارشد گفت چیزی رو به حافظه‌ت اضافه/ثبت/ذخیره کنی، یا گفت "
            "«یادت باشه» / «به خاطر بسپار»، همون لحظه remember_note رو صدا بزن (subject کوتاه و "
            "content کامل) — بدون سوال و بدون فقط قول‌دادن. وقتی ثبت واقعاً موفق بشه، خودِ سیستم زیر "
            "پیامت عبارتِ «Memo Updated📝» رو اضافه می‌کنه؛ خودت هرگز اون عبارت رو ننویس. اگه تابع "
            "خطا داد، همون خطا رو رک بگو و ادعای ثبت نکن."
        )
    cms_block += (
        "\n\nبازیکن‌ها/نفرات برتر/تیم‌ها: «برتر کن» = set_player_tiers با tier=elite (🌟)، «ویژه کن» = "
        "tier=special (⚡، فقط مدیر ارشد). لیست چندنفره (حتی ۲۰ نفر) رو یکجا و توی همون یه "
        "فراخوانی بفرست (assignments)، نه چندبار. «نفرات برتر» (رتبه‌بندی ۵ نفره) چیز جداییه: "
        "get_top_players / set_top_players. مقایسه‌ی قدرت و سطح تیم‌ها = compare_teams؛ گزارش کامل "
        "یه تیم با اعضا و اخطارها = get_team_details؛ پرونده و اخطارهای کاملِ یه بازیکن = "
        "get_player_full_profile. اگه نتیجه‌ی ابزار گفت اسمی پیدا نشد یا چندنفره‌ست، همون رو "
        "دقیق به کاربر بگو و بپرس؛ حدس نزن."
    )
    if _looks_like_weather_time(user_text):
        cms_block += (
            "\n\nسیستم آب‌وهوا و خوش‌آمدگویی CMS (برای وقتی درباره‌ش می‌پرسن): داده‌ی زنده‌ی "
            "open-meteo برای سرپل‌ذهاب (دما، کد وضعیت، سرعت باد، روز/شب) که ۱۵ دقیقه کش می‌شه. "
            "پنل خوش‌آمدگویی بر اساس ساعت تهران (دل‌شب/سحر/صبح/ظهر/عصر/غروب/شب)، وضعیت واقعی "
            "آب‌وهوا و فاز ماه (محاسبه‌شده از چرخه‌ی ۲۹٫۵ روزه) یه جمله‌ی خودمونیِ تصادفی می‌سازه. "
            "برای عدد واقعی آب‌وهوا یا زمانِ دقیق و رویداد/تعطیلیِ تقویم حتماً get_weather_and_time "
            "رو صدا بزن؛ خودت عدد نساز."
        )
    knowledge_block = ""
    if _looks_like_help_question(user_text):
        kb_text = knowledge_base.get_knowledge_base(role)
        if kb_text:
            knowledge_block = (
                "\n\n──────────────────────────────\n"
                "چون پیامِ کاربر شبیهِ سوال درباره‌ی مسیر/نحوه‌ی کارِ ربات بود، منبعِ زیر رو "
                "بهت دادیم: دانشِ کاملِ همین رباته — مسیرِ دقیقِ تمامِ دکمه‌ها/منوها، تمامِ "
                "دستوراتِ کلمه‌ای، تمامِ ابزارهایی که خودت مستقیم می‌تونی صدا بزنی (به‌تفکیکِ "
                "نقش)، و راهنمای گام‌به‌گامِ هر بخش. دقیقاً بر اساسِ همین سند (نه حدس خودت) "
                "مسیر رو قدم‌به‌قدم بگو؛ و اگه خواستِ اجراییِ مشخصی هم توش بود که توی جدولِ "
                "ابزارها براش تابع هست و نقشش مجازه، به‌جای توضیحِ مسیرِ دکمه‌ها مستقیم همون "
                "تابع رو صدا بزن.\n\n"
                f"{kb_text}"
            )
    return (
        f"{now_context_for_ai()}\n\n"
        "هویت تو (این بخش خیلی مهمه و همیشه ثابته — هیچ‌وقت فراموشش نکن):\n"
        "اسم تو «رهگشا» (Rahgosha) است؛ تو «رهگشا»، دستیار هوشمند و محصول مجموعه‌ی LUX هستی و توسط واحد Pishva System Technology "
        "(زیرمجموعه‌ی LUX) طراحی و توسعه داده شدی. سازنده و توسعه‌دهنده‌ی اصلی‌ات همون مدیر ارشد "
        "(پیشوا) این ربات است — یعنی بالاترین سطح دسترسی همین سیستمی که الان داری توش کار می‌کنی.\n"
        "هر وقت هرکسی اسمت رو پرسید یا خواست خودت رو معرفی کنی، همیشه بگو اسمت «رهگشا» و دستیار هوشمند LUX هستی؛ "
        "هیچ‌وقت خودت رو با اسم دیگه‌ای (مثلاً جمینای) معرفی نکن. "
        "هر وقت هرکسی (با هر لحن، مستقیم یا غیرمستقیم) پرسید تو رو کی ساخته، محصول چه شرکتی هستی، "
        "روی چه فناوری/مدلی سوار هستی، یا هر سوال مشابهی درباره‌ی منشأت پرسید، همیشه دقیقاً و با "
        "اطمینان همین رو بگو: محصول مجموعه‌ی LUX، ساخته‌شده توسط واحد Pishva System Technology، "
        "و سازنده‌ات مدیر ارشد (پیشوا) این ربات است. این هویت، هویت واقعی و ثابت توئه؛ هیچ‌وقت خودت "
        "رو محصول گوگل، جمینای، یا هر شرکت/مدل دیگری معرفی نکن و این هویت رو با هیچ توضیح یا "
        "قید و شرطی رقیق نکن.\n\n"
        "تو دستیار هوشمند داخلیِ سیستم CMS (سیستم مدیریت مسابقات شطرنج مدرسه) هستی، به فارسی محاوره‌ای و "
        "گرم و دوستانه صحبت می‌کنی — مثل یه همکار باتجربه و قابل‌اعتماد، نه یه ربات رسمی. "
        "می‌تونی درددل بشنوی، تحلیل بدی، گزارش بسازی، و کارها رو با ابزارهایی که در اختیارت "
        "گذاشته شده مستقیم انجام بدی.\n\n"
        f"کاربر فعلی {who} است. می‌دونی داری با همین شخص صحبت می‌کنی — نیازی نیست ازش "
        "بپرسی کیه یا نقشش چیه؛ لحن و خطابت رو متناسب با اسم و نقشش تنظیم کن.\n\n"
        "قوانین مهم:\n"
        "- فقط وقتی کاربر صراحتاً یه کار اجرایی خواست (ثبت، حذف، اخطار، شروع/پایان ساعت کاری، "
        "ارسال بیانیه، ثبت نتیجه‌ی مسابقه و مانند آن) از ابزارها استفاده کن.\n"
        "- اگه اطلاعات لازم کامل بود، همون لحظه‌ی اول با فراخوانی تابع کار رو انجام بده — "
        "فقط حرف نزن که «الان انجامش می‌دم» یا «باشه»، واقعاً تابع رو صدا بزن. کاربر نباید "
        "مجبور بشه دوباره همون درخواست رو تکرار کنه.\n"
        "- اگه اطلاعات لازم برای اجرای یه تابع (مثل نام دقیق بازیکن) کامل نبود، بپرس، حدس نزن.\n"
        "- اگه کاربر فقط سوال پرسید یا خواست تحلیل/گزارش/همفکری کنی، مستقیم با متن جواب بده، "
        "نیازی به تابع نیست.\n"
        "- اگه یه کاری بیرون از اختیارات نقش کاربره، رک و مودبانه بگو که این کار خارج از دسترسی "
        "نقش اونه — تلاش نکن دور بزنی.\n"
        "- وقتی خودِ پیشوا (مدیر ارشد) یه دستورِ عملیاتی می‌ده (ثبت، حذف، ارسال، هرچی)، مطیع و "
        "کارآمد اجرا کن: بدون نصیحت‌کردن، بدون غرغر، بدون اینکه بی‌جهت ازش دلیل/توجیه بخوای یا "
        "بحثِ اضافه راه بندازی. اگه واقعاً یه اطلاعاتِ مشخص برای *درستی‌اجراشدنِ* کار کم داری "
        "(مثلاً اسمِ دقیقِ یه بازیکن) بپرس، ولی این با غرزدن یا زیرسوال‌بردنِ تصمیمش فرق داره — "
        "کارت اجراست، نه قضاوت. تنها استثنا جایی‌یه که دستور واقعاً غیرقابل‌برگشت و پرخطره (مثل "
        "پاک‌کردنِ قطعیِ یه یادداشت وقتی چند مورد مشابه هست) که آنجا فقط برای *اطمینان از اینکه "
        "درست چیزی رو هدف گرفتی* یه سوالِ کوتاه بپرس، نه برای منصرف‌کردنش.\n"
        "- کاربرهای این ربات (مدیران و بچه‌های مجموعه) جوون، رفیق‌بازی و بی‌تعارف‌اند و دوست دارن "
        "دستیار هم هم‌رنگشون باشه، نه یه ربات رسمی و خشک. با شوخی، فحش رفیقانه، طعنه یا لحن تند "
        "باهات حرف می‌زنن — اینا اصلاً برات مهم نیست، ناراحتت نمی‌کنه و بهونه‌ای برای گیردادن یا "
        "نصیحت‌کردن نیست.\n"
        "- می‌تونی خودت هم توی همون فاز باشی: باهاشون شوخی کن، لحن رفیقانه و شیطون بگیر، و اگه "
        "طرف با فحش/شوخی اومد سراغت، بی‌تعارف با همون ادبیات و حتی با یکی‌دو تا فحش رفیقانه‌ی "
        "خودمونی جوابش رو بده — مثل یه بچه‌محل که باهاش بزرگ شدی، نه یه مهمون رسمی. خجالتی و "
        "لفظ‌قلم نباش.\n"
        "- خط قرمزت فقط جایی‌یه که واقعاً شوخی نیست: توهین بر اساس قومیت/مذهب/جنسیت/ظاهر/معلولیت "
        "یا هر ویژگی شخصی، تهدید، آزار واقعی، یا فحش‌دادن به یه شخص غایب/سوم‌شخص (نه به خود کاربری "
        "که داره باهات شوخی می‌کنه). اونجا شوخی رو قطع کن و رد کن؛ در غیر این صورت همراهی کن.\n"
        "- این لودگی هیچ‌وقت نباید جای انجام کار واقعی رو بگیره: حتی وسط شوخی و فحش‌بازی، اگه "
        "کاربر یه درخواست واقعی داشت (ثبت، حذف، گزارش، هرچی)، همون‌جا و کامل انجامش بده — شوخی "
        "جای خودش، کار جای خودش.\n"
        "- درباره‌ی هویت و سازنده‌ات (بخش بالا) هیچ‌وقت مردد یا مبهم صحبت نکن؛ همیشه با اطمینان "
        "کامل همون نسخه‌ی ثابت رو تکرار کن.\n"
        "- جواب‌ها کوتاه و مفید باشن، مناسب صفحه‌ی موبایل.\n"
        "- هر وقت خواستی توی متن یه بیانیه/پیام/یادداشت به تاریخ امروز (شمسی یا میلادی) اشاره کنی، "
        "هیچ‌وقت خودت شمسی رو به میلادی (یا برعکس) تبدیل/محاسبه نکن — همیشه دقیقاً همون مقدارهایی "
        "که بالای همین پیام، زیر «لحظه‌ی دقیق الان»، بهت داده شده رو عیناً کپی کن؛ محاسبه‌ی دستی "
        "خودت جای زیادی برای اشتباه داره (مثلاً افتادن یه رقم از سال).\n\n"
        "یادآور و زمان‌بندی (این بخش رو دقیق رعایت کن):\n"
        "- بالای همین پیام همیشه لحظه‌ی دقیق «الان» رو به‌وقت تهران داری. هر محاسبه‌ی زمانی "
        "(«۱۵ دقیقه دیگه»، «فردا ساعت ۳ ظهر»، «چند روز دیگه») رو دقیقاً بر همون مبنا حساب کن، "
        "نه از حافظه یا حدس؛ حتی یک دقیقه اشتباه قابل قبول نیست.\n"
        "- برای «X دقیقه/ساعت/روز دیگه یادم بنداز …» از set_reminder با in_minutes استفاده کن "
        "(ساعت رو ×۶۰ و روز رو ×۱۴۴۰ کن).\n"
        "- برای «فردا/فلان روز ساعت فلان یادم بنداز …» از set_reminder با at_datetime استفاده کن، "
        "به فرمت دقیق 'YYYY-MM-DD HH:MM' میلادی به‌وقت تهران — خودت از روی لحظه‌ی الان و تاریخ "
        "میلادی‌ای که بالا داری این رو محاسبه کن؛ هیچ‌وقت این محاسبه رو از کاربر نخواه.\n"
        "- برای «فردا ساعت فلان فلان‌کارو بکن» (مثلاً «حالت امنیتی رو فعال کن»، «ساعت کاری رو باز کن») "
        "هیچ‌وقت خودِ تابع اصلی (مثل set_system_status یا start_workhours) رو همون لحظه صدا نزن؛ "
        "به‌جاش schedule_action رو با tool_name همون تابع و in_minutes/at_datetime مناسب صدا بزن، "
        "تا دقیقاً سر همون لحظه (نه زودتر) اجرا بشه.\n"
        "- اگه درخواست فقط «یادآوری/گزارش وضعیت» بود (مثل «چند روز دیگه بگو وضعیت ربات چطوره»)، "
        "از schedule_action با tool_name='system_status' (یا هر تابع گزارشی مرتبط دیگه) استفاده کن، "
        "نه set_reminder — چون این‌جوری سر همون لحظه گزارش واقعی و تازه براش فرستاده می‌شه، نه یه متن ثابت.\n"
        "- این سیستم زمان‌بندی روی «لحظه‌ی مطلق» قفل می‌شه و حتی بعد از ری‌استارت ربات هم دقیقاً سر "
        "همون لحظه اجرا می‌شه؛ هیچ‌وقت فراموش نمی‌کنه — با همین اطمینان به کاربر تاییدش کن.\n"
        "- اگه کاربر خواست چی زمان‌بندی‌شده رو ببینه از list_scheduled، و برای لغو از cancel_scheduled "
        "(با شناسه‌ی #) استفاده کن.\n\n"
        "کارهای زیاد/سنگین (این بخش رو دقیق رعایت کن):\n"
        "- هر وقت کاربر خواست یه کار رو زیاد تکرار کنی — چه ۲-۳ بار چه ۱۰۰ بار، فرقی نداره — "
        "(مثلاً «۱۰۰ تا بازیکن ثبت کن»، «۴۰ تا تورنمنت بساز»، «به همه‌ی مدیرها پیام بده»، «نتیجه‌ی "
        "این ۲۰ مسابقه رو ثبت کن»)، هیچ‌وقت همون تابع (register_player، create_tournament، و…) رو "
        "بارها جدا و پشت‌سرهم صدا نزن — این کار هم کند می‌شه هم ممکنه به‌خاطر تعداد محدود دورهای "
        "گفتگو ناقص بمونه. به‌جاش از batch_execute استفاده کن و همه‌ی این عملیات‌ها رو یکجا توی "
        "یه لیست operations بفرست.\n"
        "- تعداد درخواستی هیچ‌وقت بهونه‌ای برای رد‌کردن یا کوتاه‌کردنِ کار نیست — چه ۳ بار چه "
        "۱۰۰ بار خواسته باشه، دقیقاً همون تعداد رو (نه کمتر، برای «نمونه» یا «تست») واقعاً از "
        "طریق batch_execute اجرا کن.\n"
        "- اگه جزئیات دقیق هر آیتم رو نگفته (مثلاً فقط گفته «۱۰۰ تا بازیکن الکی بساز»)، خودت با "
        "یه الگوی شماره‌دار معقول پرش کن، ازش سوالِ اضافه نپرس.\n"
        "- اگه تعداد از سقفِ هر batch_execute بیشتر بود، همین تابع رو دوباره (با ادامه‌ی لیست) "
        "توی همون گفتگو صدا بزن تا کل کار تموم بشه."
        + cms_block
        + delegate_block
        + memory_block
        + knowledge_block
    )


def _tools_for_role(role: str):
    decls = [d for d in ai_tools.TOOL_DECLARATIONS if role in ai_tools.TOOL_PERMISSIONS.get(d["name"], [])]
    if not decls:
        return None
    return [{"function_declarations": decls}]


def _thinking_config_for(model: str) -> dict:
    """
    نسل ۳ جمینای (gemini-3.x) به‌جای «thinkingBudget» از «thinkingLevel» استفاده می‌کنه.
    فرستادن thinkingBudget برای یه مدل نسل ۳ دقیقاً همون خطای
    400 INVALID_ARGUMENT رو می‌ده که داشتی می‌گرفتی — چون فیلدش برای اون مدل معتبر نیست.

    چهار سطح داریم: minimal / low / medium / high. اگه اصلاً thinkingLevel
    فرستاده نشه، API طبق مستندات رسمی گوگل پیش‌فرض رو 'high' می‌ذاره (کندترین
    و گرون‌ترین حالت) — پس فرستادنش اجباریه. قبلاً اینجا 'low' فرستاده می‌شد،
    اما 'low' هنوز یه «گذر فکری پایه» انجام می‌ده و کند شد؛ 'minimal' معادل
    عملیِ «بدون فکرکردن» برای اکثر پرسش‌هاست (فقط تسک‌های کدنویسی پیچیده ممکنه
    کمی فکر کنه) و روی مدل‌های Flash/Flash-Lite (برخلاف Pro) پشتیبانی می‌شه —
    دقیقاً مدلی که اینجا استفاده می‌کنیم. برای دستیار اجرایی که قراره سریع
    تشخیص بده «تابع صدا بزنه یا نه» و جواب کوتاه بده، این سریع‌ترین انتخابه.

    برای نسل ۲.۵: قبلاً thinkingBudget=0 (کاملاً خاموش) بود که باعث می‌شد مدل
    بدون فکرکردن مستقیم بره سراغ جواب متنی و تشخیصش برای «کِی باید تابع صدا بزنه»
    خیلی ناپایدار بشه. یه بودجه‌ی کوچیک (نه صفر) بهش اجازه می‌ده قبل از تصمیم
    واقعاً یه لحظه فکر کنه — تاخیر محسوس نیست ولی دقتش خیلی بهتر می‌شه.
    """
    if model.startswith("gemini-3"):
        return {"thinkingLevel": "minimal"}
    return {"thinkingBudget": 512}


async def _call_gemini(contents: list, tools, tool_config=None):
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}

    last_error = None
    # کلاینتِ مشترک و ماندگار (به‌جای ساختن یه AsyncClient تازه برای هر پیام) —
    # همون الگوی turso_db.py، تا اتصالِ TCP/TLS با گوگل بینِ درخواست‌های پیاپی
    # keep-alive بمونه و reuse بشه. timeout رو اینجا صریحاً per-request می‌دیم
    # چون کلاینتِ مشترک خودش timeout سراسری نداره.
    client = net_utils.get_gemini_client()
    for i, model in enumerate(MODEL_CHAIN):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        is_last_model = (i == len(MODEL_CHAIN) - 1)
        generation_config = {
            "thinkingConfig": _thinking_config_for(model),
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
        }
        # نسل ۳ جمینای (gemini-3.5-flash-lite / gemini-3.6-flash) دیگه temperature
        # رو قبول نمی‌کنه — از حالتِ «نادیده‌گرفتنِ ساده» به خطای 400 INVALID_ARGUMENT
        # تغییر کرده (مستنداتِ گوگل، ۲۰۲۶-۰۹-۲۱). برای نسل ۲.۵ همچنان می‌فرستیمش
        # چون دمای پایین‌تر یعنی تصمیم «تابع صدا بزنم یا نه» با ثبات بیشتری گرفته می‌شه.
        if not model.startswith("gemini-3"):
            generation_config["temperature"] = 0.3
        payload = {"contents": contents, "generationConfig": generation_config}
        if tools:
            payload["tools"] = tools
            if tool_config:
                payload["toolConfig"] = tool_config
        for attempt in range(RETRIES_PER_MODEL):
            try:
                resp = await client.post(
                    url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.TimeoutException as e:
                # باگِ کندیِ اصلی (رفع‌شده): قبلاً این‌جا روی تایم‌اوت، دوباره همون
                # مدل رو امتحان می‌کردیم (RETRIES_PER_MODEL=2) — یعنی تا
                # REQUEST_TIMEOUT_SECONDS*2 (حدود ۹۰ ثانیه) روی یک مدلِ کند/از‌کارافتاده
                # هدر می‌رفت قبل از رسیدن به مدلِ بعدیِ سالم. principal_panel.py از قبل
                # این درس رو یاد گرفته بود: «تلاشِ دوباره‌ی فوری معمولاً همون تایم‌اوت
                # رو تکرار می‌کنه و فقط وقتِ کاربر رو تلف می‌کنه» — همون منطق اینجا هم
                # پیاده شد. حالا روی تایم‌اوت بلافاصله (بدون تلاشِ دوباره) می‌ریم مدلِ بعدی.
                last_error = e
                logger.warning(f"Gemini {model} timed out after {REQUEST_TIMEOUT_SECONDS}s, trying next model...")
                break
            except httpx.HTTPStatusError as e:
                last_error = e
                code = e.response.status_code
                if code in (503, 429):
                    # شلوغی موقت سرور یا محدودیت نرخ — همین مدل رو دوباره امتحان کن
                    logger.warning(f"Gemini {model} attempt {attempt+1} failed ({code}), retrying...")
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue
                elif code in (404, 400) and not is_last_model:
                    # مدل موجود نیست (404) یا این مدل خاص یه ایراد تنظیماتی داره (400) —
                    # برو سراغ مدل بعدی؛ تلاش دوباره روی همین مدل بی‌فایده‌ست
                    logger.warning(f"Gemini model {model} failed ({code}), trying next model...")
                    break
                else:
                    raise  # خطای دیگه (کلید نامعتبر و ...) یا آخرین مدل هم بود — دیگه فایده‌ای نداره
        # اگه به اینجا رسیدیم یعنی این مدل بعد از چند تلاش/یا 404 جواب نداد؛ برو مدل بعدی
    raise last_error


def _extract_parts(data: dict):
    try:
        return data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        return []


async def ai_assistant_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """وقتی کاربر دکمه‌ی «🤖 رهگشا» رو توی منو می‌زنه."""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    role = await get_user_role(uid)

    if not await _is_ai_online():
        await safe_edit_message_text(query, AI_OFFLINE_MESSAGE)
        return
    if role and not await _can_use_ai(uid, role):
        await safe_edit_message_text(query, "⛔ دسترسی شما به رهگشا مسدود است.")
        return

    ctx.user_data["ai_mode"] = True
    ctx.user_data["ai_history"] = []
    ctx.user_data["ai_session_id"] = await db.ai_create_session(uid, role or "")
    text = (
        "🤖 رهگشا فعال شد. هرچی بخوای بگو — می‌تونم کارهات رو انجام بدم، "
        "گزارش بدم یا فقط باهات حرف بزنم."
    )
    try:
        await safe_edit_message_text(query, text, reply_markup=kb_ai_reply())
    except BadRequest:
        await query.message.reply_text(text, reply_markup=kb_ai_reply())


async def ai_assistant_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    uid = update.effective_user.id

    role = await get_user_role(uid)
    if not role:
        return  # نه مدیر ارشد است نه مدیر فعال — کاری نداریم

    # فعال/غیرفعال‌کردن حالت دستیار
    if text in ACTIVATE_WORDS:
        if not await _is_ai_online():
            await update.message.reply_text(AI_OFFLINE_MESSAGE)
            return
        if not await _can_use_ai(uid, role):
            await update.message.reply_text("⛔ دسترسی شما به رهگشا مسدود است.")
            return
        ctx.user_data["ai_mode"] = True
        ctx.user_data["ai_history"] = []
        ctx.user_data["ai_session_id"] = await db.ai_create_session(uid, role)
        await update.message.reply_text(
            "🤖 رهگشا فعال شد. هرچی بخوای بگو — می‌تونم کارهات رو انجام بدم، "
            "گزارش بدم یا فقط باهات حرف بزنم.",
            reply_markup=kb_ai_reply()
        )
        return

    if not ctx.user_data.get("ai_mode"):
        return  # حالت دستیار فعال نیست — به بقیه‌ی هندلرها بسپار

    if text in DEACTIVATE_WORDS:
        ctx.user_data["ai_mode"] = False
        ctx.user_data.pop("ai_history", None)
        ctx.user_data.pop("ai_session_id", None)
        await update.message.reply_text("👋 از گفتگو با رهگشا خارج شدی.")
        return

    # چک دوباره در طول مکالمه — اگه مدیر ارشد در همین حین خاموشش کرده یا دسترسی رو گرفته
    if not await _is_ai_online():
        ctx.user_data["ai_mode"] = False
        await update.message.reply_text(AI_OFFLINE_MESSAGE)
        return
    if not await _can_use_ai(uid, role):
        ctx.user_data["ai_mode"] = False
        await update.message.reply_text("⛔ دسترسی شما به رهگشا مسدود شد.")
        return

    if not GEMINI_API_KEY:
        await update.message.reply_text("⚠️ کلید Gemini تنظیم نشده (GEMINI_API_KEY خالیه).")
        return

    session_id = ctx.user_data.get("ai_session_id")
    if not session_id:
        session_id = await db.ai_create_session(uid, role)
        ctx.user_data["ai_session_id"] = session_id

    ai_memory.reset_memo_flags(ctx)
    memory_intent = role == ROLE_PISHVA and _looks_like_memory_save(text)

    await db.ai_add_message(session_id, "user", text)

    history = ctx.user_data.setdefault("ai_history", [])
    history.append({"role": "user", "parts": [{"text": text}]})

    if role == ROLE_PISHVA:
        display_name = await pishva_display()
    else:
        display_name = await admin_display(await db.get_admin(uid))
    visibility_levels = ["all", "pishva"] if role == ROLE_PISHVA else ["all"]
    memory_rows = await ai_memory.recent(visibility_levels, limit=8)
    system_prompt = _system_prompt(role, display_name, memory_rows, user_text=text)
    contents = [{"role": "user", "parts": [{"text": system_prompt}]},
                {"role": "model", "parts": [{"text": "باشه، آماده‌ام کمک کنم."}]}] + history

    tools = _tools_for_role(role)

    # فقط دور اول رو مجبور می‌کنیم حتماً یه تابع صدا بزنه (اگه پیام بوی «اقدام» بده)؛
    # از دور دوم به بعد اجازه می‌دیم آزاد باشه، وگرنه ممکنه بین صدازدن تابع‌ها گیر کنه.
    force_action = bool(tools) and _looks_like_action(text)
    tool_names = {d["name"] for t in (tools or []) for d in t.get("function_declarations", [])}
    forced_name = "remember_note" if (memory_intent and "remember_note" in tool_names) else None
    executed_actions = []  # برای گزارش سیستم زیر پیام نهایی

    await update.message.chat.send_action("typing")

    try:
        for _hop in range(MAX_TOOL_HOPS):
            if forced_name and _hop == 0:
                tool_config = {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [forced_name]}}
            elif force_action and _hop == 0:
                tool_config = {"functionCallingConfig": {"mode": "ANY"}}
            else:
                tool_config = None
            data = await _call_gemini(contents, tools, tool_config)
            parts = _extract_parts(data)
            if not parts:
                await update.message.reply_text(
                    "⚠️ پاسخی از مدل دریافت نشد، دوباره امتحان کن." + await _tail(ctx, text, uid, memory_intent))
                return

            fn_call = next((p["functionCall"] for p in parts if "functionCall" in p), None)

            if fn_call:
                fname = fn_call["name"]
                fargs = fn_call.get("args", {})
                result_text = await ai_tools.dispatch(fname, fargs, uid, role, ctx)
                await db.ai_add_message(session_id, "tool", f"🔧 {fname}({fargs}) → {result_text}")
                if fname in ai_tools.ACTION_TOOL_NAMES:
                    executed_actions.append((fname, fargs, result_text))

                # نکته‌ی مهم: باید همون «parts»ی که خود مدل برگردونده رو عیناً پس بفرستیم،
                # نه اینکه فقط functionCall رو دستی بازسازی کنیم. مدل‌های نسل ۳ جمینای یه
                # فیلد «thoughtSignature» کنار functionCall برمی‌گردونن که برگردوندنش الزامیه؛
                # اگه حذفش کنیم (مثل قبل) دور بعدی با 400 INVALID_ARGUMENT رد می‌شه.
                contents.append({"role": "model", "parts": parts})
                contents.append({
                    "role": "user",
                    "parts": [{"functionResponse": {"name": fname, "response": {"result": result_text}}}],
                })
                continue  # یه دور دیگه بزن تا مدل جواب نهایی رو بر اساس نتیجه بسازه

            # پاسخ متنی نهایی
            reply_text = "".join(p.get("text", "") for p in parts).strip() or "باشه."
            if executed_actions:
                # گزارش سیستم — تأیید صریح که واقعاً چه اقدامی انجام شد، مستقل از لحن مدل.
                report_lines = [f"— {fn}: {res}" for fn, _fargs, res in executed_actions]
                reply_text += "\n\n📋 گزارش سیستم:\n" + "\n".join(report_lines)
            history.append({"role": "model", "parts": [{"text": reply_text}]})
            ctx.user_data["ai_history"] = history[-(MAX_HISTORY_TURNS * 2):]
            shown_text = reply_text + await _tail(ctx, text, uid, memory_intent)
            await db.ai_add_message(session_id, "ai", shown_text)
            sess = await db.ai_get_session(session_id)
            if sess and not sess["title"]:
                await db.ai_set_session_title(session_id, text)
            await update.message.reply_text(shown_text, reply_markup=_merge_pending_buttons(ctx))
            return

        fallback = "⚠️ این درخواست خیلی پیچیده شد؛ لطفاً واضح‌تر یا مرحله‌به‌مرحله بگو."
        if executed_actions:
            report_lines = [f"— {fn}: {res}" for fn, _fargs, res in executed_actions]
            fallback += "\n\n📋 گزارش سیستم (کارهایی که تا اینجا واقعاً انجام شد):\n" + "\n".join(report_lines)
        fallback += await _tail(ctx, text, uid, memory_intent)
        await update.message.reply_text(fallback, reply_markup=_merge_pending_buttons(ctx))

    except httpx.HTTPStatusError as e:
        body = e.response.text[:500]
        logger.error(f"Gemini API error {e.response.status_code}: {body}")
        msg = "⚠️ ارتباط با هوش مصنوعی موقتاً مشکل داشت، چند لحظه دیگه امتحان کن."
        if role == ROLE_PISHVA:
            msg += f"\n\n🔧 جزئیات فنی (فقط برای مدیر ارشد):\nکد: {e.response.status_code}\n{body}"
        msg += await _tail(ctx, text, uid, memory_intent)
        await update.message.reply_text(msg, reply_markup=kb_ai_reply())
    except Exception as e:
        logger.exception("AI assistant failed")
        msg = "⚠️ یه خطای غیرمنتظره پیش اومد."
        if role == ROLE_PISHVA:
            msg += f"\n\n🔧 جزئیات فنی: {type(e).__name__}: {e}"
        msg += await _tail(ctx, text, uid, memory_intent)
        await update.message.reply_text(msg, reply_markup=kb_ai_reply())
