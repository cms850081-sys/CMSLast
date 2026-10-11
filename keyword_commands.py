import re
import json
import logging
import platform
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import database as db
import keyboards as kb
from helpers import safe_edit_message_text, box, separator, now_shamsi, format_log_entry, escape_md_legacy
from config import PISHVA_ID, ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER, BOT_USERNAME
from panel_timeout import (
    schedule_panel_timeout, reset_panel_timeout, cancel_panel_timeout,
    CLOSED_WELCOME_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

# ─── میان‌بر «ت<شماره>» → مستقیم صفحه‌ی N پنل مدیر ارشد ─────────────
# پشتیبانی از هر ترکیبِ رایج: «ت1»، «ت 1»، «ت۱»، «ت ۱». عدد می‌تونه
# فارسی یا انگلیسی باشه؛ قبل از matchِ regex به انگلیسی نرمالایز می‌شه.
_DIGIT_TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
TAB_PAGE_RE = re.compile(r"^ت\s*([0-9]+)$")


def _to_ascii_digits(s: str) -> str:
    return s.translate(_DIGIT_TRANSLATION)

# ─── «رمز اختصاصی»: نقطه/ویرگول/عدد قبل یا بعدِ کلمه ────────────────
# چند کلمه‌ی خاص (که یه ربات دیگه هم دقیقاً به همون‌ها گوش می‌ده) اینجا
# لیست شدن تا وقتی یکی از این‌ها با یه نقطه/ویرگول (. ,) یا یک یا چند
# رقم (فارسی/انگلیسی) بلافاصله قبل یا بعدش نوشته بشه، فقط ربات خودمون
# بهش واکنش نشون بده — نه اون ربات رقیب که match دقیق داره. خودِ کلمه‌ی
# خالی (بدون این پسوند/پیشوند) هم مثل قبل کار می‌کنه، دست نخورده.
FUZZY_MATCH_KEYWORDS = {"پنل", "امروز", "تاریخ", "تعطیلی", "تنظیم مدیر", "حذف مدیر", "کیه"}
_FUZZY_DECORATION_RE = re.compile(r"^[.,،؛\s0-9۰-۹]*(.*?)[.,،؛\s0-9۰-۹]*$", re.DOTALL)


def _strip_fuzzy_decoration(text: str) -> str:
    """اگه با حذفِ نقطه/ویرگول/رقمِ اضافه از ابتدا یا انتهای متن، دقیقاً
    یکی از FUZZY_MATCH_KEYWORDS به‌دست بیاد، همون کلمه‌ی خام رو برمی‌گردونه؛
    وگرنه متنِ اصلی رو بدونِ تغییر پس می‌ده (تا رفتارِ match دقیقِ قبلی
    برای بقیه‌ی کلمات دست‌نخورده بمونه)."""
    m = _FUZZY_DECORATION_RE.match(text)
    if not m:
        return text
    core = m.group(1).strip()
    if core in FUZZY_MATCH_KEYWORDS:
        return core
    return text

ADMIN_KEYWORDS = {"تنظیم مدیر", "تنظیم مدیر امنیتی", "حذف مدیر", "حذف مدیر امنیتی"}

# کلمه‌ی گفته‌شده -> نام یکتای عملیات (برای پشتیبانی از مترادف‌ها)
SIMPLE_KEYWORDS = {
    "پنل": "quick_panel",
    "داشبورد": "dashboard",
    "وظیفه": "tasks",
    "مسابقه": "matches",
    "بازیکن": "players",
    "مخابره": "comms",
    "امنیت": "security",
    "وضعیت": "status",
    "کلاس": "classes",
    "تیم": "teams",
    "بکاپ": "backup",
    "درخواست": "requests",
    "لاگ": "logs",
    "گزارش": "logs",
    "راهنما": "help",
    "کمک": "help",
    "قرعه": "lottery",
    "جدول": "elo",
    "رتبه": "elo",
    "قهرمانان": "champions",
    "یادآور": "reminders",
    "تنظیمات": "settings",
    "فیدبک": "feedback",
    "انتقاد": "feedback",
    "شروع": "restart",
    # ─── کلمات جدید ───
    "بستن": "close_panel",
    "خروج": "close_panel",
    "مدیر ارشد": "pishva_panel",       # فقط برای مدیر ارشد پنلش رو باز می‌کنه
    "مدیریت": "restart",              # مترادف «شروع» — خوش‌آمدگویی کامل (/start)
    "دست": "ai_manage_panel",         # فقط برای مدیر ارشد — پنل مدیریت دستیار
    "رصد": "ai_manage_panel",         # مترادف «دست» — پنل مدیریت دستیار
    "اطلاعات": "reply_info",       # ریپلای روی یه پیام → جزئیات کاربر
    "درباره": "reply_info",        # مترادف اطلاعات
    "کیه": "reply_info",           # مترادف اطلاعات
    "مدیر": "reply_admin_panel",   # ریپلای روی پیامِ آیدیِ یه مدیر → باز شدنِ پنلِ همون مدیر
    "آنلاین": "online_admins",     # لیست ادمین‌های آنلاین/فعال
    "الان": "online_admins",       # مترادف آنلاین
    "ادمین‌ها": "admin_list",      # لیست همه ادمین‌ها
    "ادمینا": "admin_list",
    "مدیران": "admin_manage_panel",  # باز کردن پنل «مدیریت مدیران»
    "آمار": "quick_stats",         # آمار سریع بازیکنان و مسابقات
    "نتایج": "recent_results",     # آخرین نتایج مسابقات
    "اخطارها": "warnings_list",    # لیست بازیکنان با اخطار
    "بازی": "live_chess",          # باز کردن منوی شطرنج زنده
    "شطرنج": "live_chess",         # مترادف بازی
    # ─── تقویم مدرسه ───
    # توجه: «ساعت» قبلاً مستقیم به منوی «ساعت کاری» می‌رفت؛ طبق درخواستِ
    # جدید حالا «ساعت» و «وقت» تقویم (با ساعتِ الان توی متنش) رو باز می‌کنن.
    # برای باز کردنِ منوی ساعتِ کاری همچنان از عبارتِ «ساعت کاری» یا کلمه‌ی
    # «کاری»/«زمان» استفاده کن (دستِ‌نخورده مونده‌ن، پایین‌تر همین دیکشنری).
    "تقویم": "calendar",
    "تاریخ": "calendar",
    "ساعت": "calendar",
    "وقت": "calendar",
    "امروز": "calendar_today",       # نزدیک‌ترین تاکید روی تاریخِ امروز
    "ایونت": "calendar_next_event",  # نزدیک‌ترین ایونتِ ثبت‌شده‌ی آینده
    "تعطیلی": "calendar_next_holiday",  # نزدیک‌ترین تعطیلیِ ثبت‌شده‌ی آینده
    # ─── ساعت کاری (فقط مدیر ارشد) ───
    # توجه: «آغاز»/«اغاز» و «پایان»/«تموم» اینجا map نمی‌شن، چون خودشون
    # به‌صورت جداگانه به‌عنوان entry_point به workhours_conv در bot.py
    # اضافه شدن (چون workhour_start ممکنه یک state از مدیر ارشد بخواد
    # و باید داخل خودِ ConversationHandler بمونه، نه اینجا).
    "ساعت کاری": "workhours_menu",  # مترادف
    "کاری": "workhours_menu",       # مترادف
    "زمان": "workhours_menu",       # مترادف
    # ─── پنل امنیتی APS (فقط مدیر ارشد) ───
    "APS": "aps_panel",
    "aps": "aps_panel",
    # ─── چندسرور (فقط مدیر ارشد): پنلِ سرورها ───
    "سرور": "servers_panel",
    "سرورها": "servers_panel",
    "دنیا": "servers_panel",
    "ور": "servers_panel",
    "جهان": "servers_panel",
}

# این کلمات مستقیماً به workhours_conv (در bot.py) به‌عنوان entry_point
# وصل می‌شن، نه از طریق SIMPLE_KEYWORDS — برای این‌که handle_keyword_command
# باید ازشون رد بشه و اجازه بده خودِ ConversationHandler بگیرتشون.
WORKHOURS_START_KEYWORDS = {"آغاز", "اغاز"}
WORKHOURS_END_KEYWORDS = {"پایان", "تموم"}

PISHVA_ONLY_ACTIONS = {
    "security", "status", "backup", "requests", "logs", "reminders", "settings",
    "pishva_panel", "online_admins", "ai_manage_panel", "admin_manage_panel",
    "workhours_menu", "workhours_start", "workhours_end", "reply_admin_panel",
    "aps_panel", "servers_panel",
}

# کارهایی که مدیر مجموعه (فقط در سرورِ خودش) از میانِ کارهای «فقط مدیر ارشد» مجاز است
OWNER_ALLOWED_ACTIONS = {"workhours_menu", "requests"}


# ─── محافظت از مالکیت پنل ────────────────────────────────────
async def panel_ownership_guard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    اگه کسی روی پنل یه نفر دیگه دست بزنه، هشدار بده و متوقف کن.
    فقط در گروه/سوپرگروه فعاله.

    FIX 1: اگه owner_id ثبت نشده (None)، در گروه دکمه‌ها رو بلاک می‌کنه
            (قبلاً با None بود pass می‌شد — یعنی هر کسی می‌تونست بزنه).
    (مدیریتِ مدیران هم مثلِ بقیه‌ی پنل‌ها در گروه مجازه؛ فقط مالکیتِ پنل چک می‌شه.)
    """
    from telegram.ext import ApplicationHandlerStop
    query = update.callback_query
    if not query:
        return

    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return

    msg = query.message
    if not msg:
        return

    requester_id = query.from_user.id
    callback_data = query.data or ""

    # مدیر ارشد: مدیریتِ مدیران هم حالا در گروه مجازه — فقط بررسیِ
    # مالکیتِ پنل (مثلِ بقیه‌ی پنل‌ها) انجام می‌شه.
    if requester_id == PISHVA_ID:
        msg_key = f"panel_owner_{msg.message_id}"
        owner_id = ctx.chat_data.get(msg_key) if ctx.chat_data is not None else None
        if owner_id is not None and requester_id != owner_id:
            await query.answer(
                "⛔ این پنل متعلق به شما نیست!\n"
                "برای باز کردن پنل خودتان، کلمه «پنل» را بفرستید.",
                show_alert=True
            )
            raise ApplicationHandlerStop()
        # کلیک معتبر صاحب پنل → تایمر بی‌فعالیتی رو ریست کن
        if owner_id is not None:
            reset_panel_timeout(ctx, chat.id, msg.message_id, owner_id)
        return

    # ── FIX 1: بررسی مالکیت پنل برای همه ────────────────────────────
    msg_key = f"panel_owner_{msg.message_id}"
    owner_id = ctx.chat_data.get(msg_key) if ctx.chat_data is not None else None

    if owner_id is None:
        # در گروه هیچ owner ثبت نشده → پنل قدیمی یا ناامن → بلاک کن
        await query.answer(
            "⛔ این پنل قابل استفاده نیست.\n"
            "برای باز کردن پنل خودتان، کلمه «پنل» را بفرستید.",
            show_alert=True
        )
        raise ApplicationHandlerStop()

    if requester_id == owner_id:
        # کلیک معتبر صاحب پنل → تایمر بی‌فعالیتی رو ریست کن
        reset_panel_timeout(ctx, chat.id, msg.message_id, owner_id)
        return

    await query.answer(
        "⛔ این پنل متعلق به شما نیست!\n"
        "برای باز کردن پنل خودتان، کلمه «پنل» را بفرستید.",
        show_alert=True
    )
    raise ApplicationHandlerStop()


async def _clear_previous_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                                 chat_id: int, uid: int, new_msg_id: int):
    """پنلِ قبلیِ همین کاربر (در همین گروه) رو، اگه هنوز باز مونده باشه، حذف
    می‌کنه. این باعث می‌شه با گفتنِ هر کلمه‌ی جدید («پنل»، «بستن»/«خروج» و...)
    فقط یک پنل به‌ازای هر کاربر توی گروه باز بمونه، نه چندتا هم‌زمان."""
    if ctx.chat_data is None:
        return
    key = f"active_panel_msg_{uid}"
    prev_msg_id = ctx.chat_data.get(key)
    if not prev_msg_id or prev_msg_id == new_msg_id:
        return
    cancel_panel_timeout(ctx, chat_id, prev_msg_id)
    ctx.chat_data.pop(f"panel_owner_{prev_msg_id}", None)
    try:
        await ctx.bot.delete_message(chat_id=chat_id, message_id=prev_msg_id)
    except Exception as e:
        logger.debug(f"could not delete previous panel message {prev_msg_id} in {chat_id}: {e}")


async def register_panel_owner(update: Update, ctx: ContextTypes.DEFAULT_TYPE, msg_id: int,
                                timeout_seconds: int = None):
    """صاحب پنل رو ثبت می‌کنه، پنلِ قبلیِ همون کاربر (چه در گروه/سوپرگروه، چه
    در پیوی — اگه هنوز باز بود) رو حذف می‌کنه، و تایمر بسته‌شدن خودکار
    (عدم فعالیت) رو فقط برای گروه/سوپرگروه راه‌اندازی می‌کنه (پیوی نیازی به
    قفل مالکیت/تایمر نداره چون فقط خودِ کاربر می‌بینتش).
    timeout_seconds برای override کردنِ بازه‌ی پیش‌فرض (۳ دقیقه) استفاده می‌شه.

    FIX: قبلاً این تابع در پیوی هیچ‌کاری نمی‌کرد (return زودهنگام)، در نتیجه
    وقتی کاربر توی پیوی کلمه‌ی «بستن»/«خروج» (یا هر کلمه‌ی دیگه‌ای که پنل باز
    می‌کنه) رو می‌فرستاد، پیامِ پنلِ قبلی حذف نمی‌شد و فقط پیامِ تازه اضافه
    می‌شد. حالا حذفِ پنلِ قبلی (_clear_previous_panel) در همه‌ی حالت‌ها
    (گروه و پیوی) انجام می‌شه؛ فقط تایمرِ خودکار همچنان مخصوصِ گروه می‌مونه."""
    chat = update.effective_chat
    if not chat:
        return
    uid = update.effective_user.id if update.effective_user else None
    if not uid or ctx.chat_data is None:
        return
    await _clear_previous_panel(update, ctx, chat.id, uid, msg_id)
    ctx.chat_data[f"panel_owner_{msg_id}"] = uid
    ctx.chat_data[f"active_panel_msg_{uid}"] = msg_id
    if chat.type in ("group", "supergroup"):
        schedule_panel_timeout(ctx, chat.id, msg_id, uid, timeout_seconds=timeout_seconds)


# ─── پرسیدن محل باز شدن پنل (گروه vs پیوی) ──────────────────
async def ask_panel_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE, action: str):
    """
    وقتی در گروه keyword زده می‌شه، اول می‌پرسه:
    «می‌خواید پنل اینجا (گروه) باز بشه یا در پیوی؟»
    action رو در user_data ذخیره می‌کنه تا بعد از انتخاب استفاده بشه.
    """
    from telegram.ext import ApplicationHandlerStop
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return False  # در پیوی نپرس، مستقیم باز کن

    uid = update.effective_user.id
    # ذخیره action برای استفاده بعد از انتخاب
    ctx.user_data["pending_panel_action"] = action

    from chess_challenge import _resolve_bot_username
    bot_username = await _resolve_bot_username(ctx.bot)

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📲 همینجا (گروه)", callback_data=f"open_panel_here_{action}"),
            InlineKeyboardButton("🔒 پیوی (امن‌تر)", url=f"https://t.me/{bot_username}?start=panel_{action}"),
        ]
    ])
    sent = await update.message.reply_text(
        f"📌 می‌خواید پنل *{_action_label(action)}* کجا باز بشه؟\n\n"
        "🔒 *پیوی* — امن‌تر، فقط شما می‌بینید\n"
        "📲 *همینجا* — در گروه، با قفل مالکیت",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    # ثبت مالکیت پیام سوال، وگرنه وقتی کاربر روی «همینجا» کلیک کنه،
    # panel_ownership_guard چون owner_id ثبت‌نشده می‌بینه، پیام «پنل قابل استفاده نیست» می‌ده.
    # این کار پنلِ قبلیِ همین کاربر (اگه هنوز باز بود) رو هم حذف می‌کنه.
    await register_panel_owner(update, ctx, sent.message_id)
    raise ApplicationHandlerStop()


def _action_label(action: str) -> str:
    if action.startswith("pishva_page_"):
        try:
            n = int(action[len("pishva_page_"):]) + 1
        except ValueError:
            n = 1
        return f"پنل مدیر ارشد — صفحه {n}"
    labels = {
        "dashboard": "داشبورد",
        "matches": "مسابقات",
        "players": "بازیکنان",
        "teams": "تیم‌ها",
        "comms": "مخابرات",
        "tasks": "وظایف",
        "classes": "کلاس‌ها",
        "lottery": "قرعه‌کشی",
        "pishva_panel": "پنل مدیر ارشد",
        "quick_panel": "پنل",
        "ai_manage_panel": "مدیریت دستیار",
        "admin_manage_panel": "مدیریت مدیران",
        "aps_panel": "پنل امنیتی APS",
        "security": "امنیت",
        "backup": "بکاپ",
        "requests": "درخواست‌ها",
        "logs": "لاگ‌ها",
        "settings": "تنظیمات",
        "reminders": "یادآورها",
        "help": "راهنما",
        "restart": "خوش‌آمدگویی",
        "calendar": "تقویم",
        "calendar_today": "امروز",
        "calendar_next_event": "ایونت بعدی",
        "calendar_next_holiday": "تعطیلی بعدی",
    }
    return labels.get(action, action)


async def _panel_content(action: str, uid: int, is_pishva: bool, admin):
    """
    محتوای (متن، کیبورد) مربوط به یه اکشن پنل رو برمی‌گردونه.
    این تابع منبع مشترکِ محتواست: هم «همینجا (گروه)» و هم دیپ‌لینک پیوی
    از همینجا محتوا می‌گیرن تا دقیقاً یک چیز نمایش داده بشه.

    خروجی: (text, markup, err)
    - موفق: (text, markup, None)
    - ناموفق: (None, None, پیام‌خطا)
    """
    if action == "pishva_panel":
        if not is_pishva:
            return None, None, "⛔ شما مجوز باز کردن این پنل را ندارید."
        return box("👑 پنل مدیر ارشد"), kb.kb_pishva_main(), None

    # ─── میان‌بر «ت<شماره>» — مستقیم صفحه‌ی N از پنلِ خودِ مدیر ارشد
    # (همون کیبوردِ صفحه‌بندی‌شده‌ای که از داخلِ «پنل مدیر ارشد» ← دکمه‌های
    # ◀️/▶️ در دسترسه)، بدونِ نیاز به رفتن مرحله‌به‌مرحله. ─────────────
    if action.startswith("pishva_page_"):
        if not is_pishva:
            return None, None, "⛔ شما مجوز باز کردن این پنل را ندارید."
        try:
            page_index = int(action[len("pishva_page_"):])
        except ValueError:
            page_index = 0
        return box("👑 پنل مدیر ارشد"), kb.kb_pishva_panel(page_index), None


    if action == "quick_panel":
        # پنل ساده و سریع: بدون خوش‌آمدگویی/آب‌وهوا/جزئیات، فقط عنوان + کیبورد کامل.
        # مدیر ارشد → پنل مدیر ارشد، مدیر مسابقات/امنیتی → پنل مخصوص خودشون.
        if is_pishva:
            return box("👑 پنل مدیر ارشد"), kb.kb_pishva_main(), None
        if admin and admin["role"] == ROLE_TOURNAMENT_MANAGER:
            return box("🏆 پنل مدیر مسابقات"), kb.kb_tournament_manager_main(), None
        if admin and admin["role"] == ROLE_SECURITY_MANAGER:
            return box("🛡️ پنل مدیر امنیتی"), kb.kb_security_manager_main(), None
        return None, None, "⛔ شما مجوز باز کردن این پنل را ندارید."

    if action == "ai_manage_panel":
        if not is_pishva:
            return None, None, "⛔ شما مجوز باز کردن این پنل را ندارید."
        ai_online = await db.get_setting("ai_online", "1")
        return (
            f"{box('🧑\u200d💻 مدیریت دستیار')}\n\n📌 یک گزینه را انتخاب کنید:",
            kb.kb_ai_manage_menu(ai_online, "menu_pishva"),
            None,
        )

    if action == "admin_manage_panel":
        if not is_pishva:
            return None, None, "⛔ فقط مدیر ارشد می‌تواند مدیران را مدیریت کند."
        admins = await db.get_all_admins()
        if not admins:
            return (
                f"{box('👥 مدیریت مدیران')}\n\n❗ هیچ مدیری ثبت نشده است.",
                kb.kb_back("main"),
                None,
            )
        return (
            f"{box('👥 مدیریت مدیران')}\n\nیک مدیر انتخاب کنید:",
            kb.kb_admin_list(admins),
            None,
        )

    if action == "aps_panel":
        if not is_pishva:
            return None, None, "⛔ این دستور فقط برای مدیر ارشد است."
        from security import build_security_panel_text
        text = await build_security_panel_text()
        return text, kb.kb_security_panel(), None

    if action == "settings":
        if not is_pishva:
            return None, None, "⛔ این دستور فقط برای مدیر ارشد است."
        keys = ["notifications_enabled", "communications_enabled", "help_enabled",
            "match_registration_enabled", "admin_login_enabled", "bot_active_for_admins",
            "team_mode_enabled", "team_registration_enabled", "managers_can_create_teams",
            "admin_dashboard_enabled", "ai_online", "live_chess_enabled", "hub_enabled",
            "bug_report_to_pishva_enabled", "principal_panel_enabled", "admin_webpanel_enabled",
            "admin_direct_kick_enabled", "top_players_mode"]
        settings = await db.get_settings_bulk(keys, "1")
        return (
            f"{box('⚙️ تنظیمات ربات')}\n\n📌 گزینه موردنظر را تغییر دهید:",
            kb.kb_pishva_settings_simple(settings),
            None,
        )

    if action == "dashboard":
        from dashboard import build_dashboard_pishva_text, build_dashboard_admin_text
        if is_pishva:
            dtext = await build_dashboard_pishva_text()
            return dtext, kb.kb_dashboard_pishva(), None
        dtext = await build_dashboard_admin_text(uid)
        return dtext, kb.kb_dashboard_admin(), None

    if action == "matches":
        return box("♟️ مدیریت مسابقات"), kb.kb_matches_menu(), None

    if action == "players":
        role_key = "pishva" if is_pishva else admin["role"]
        return box("👤 مدیریت بازیکنان"), kb.kb_players_menu(role_key), None

    if action == "teams":
        team_mode = await db.get_setting("team_mode_enabled", "0")
        if team_mode != "1":
            return None, None, "❗ حالت تیمی در حال حاضر غیرفعال است."
        return box("🏆 مدیریت تیم‌ها"), kb.kb_teams_menu(), None

    if action == "comms":
        if is_pishva:
            return box("📡 مخابرات"), kb.kb_comms_pishva(), None
        return box("📡 مخابرات"), kb.kb_comms_admin(), None

    if action == "tasks":
        if is_pishva:
            return box("📋 وظایف"), kb.kb_tasks_pishva(), None
        return box("📋 وظایف"), kb.kb_tasks_admin(), None

    if action == "classes":
        return box("🏫 مدیریت کلاس‌ها"), kb.kb_class_manage(is_pishva=is_pishva), None

    if action == "lottery":
        return (
            f"{box('🎲 قرعه‌کشی هوشمند')}\n\n📌 محدوده بازیکنان:",
            kb.kb_lottery_scope(),
            None,
        )

    if action == "calendar":
        from calendar_panel import content_default
        return await content_default()

    if action == "calendar_today":
        from calendar_panel import content_today
        return await content_today()

    if action == "calendar_next_event":
        from calendar_panel import content_next_event
        return await content_next_event()

    if action == "calendar_next_holiday":
        from calendar_panel import content_next_holiday
        return await content_next_holiday()

    return None, None, "❗ این پنل پشتیبانی نمی‌شود."


async def open_panel_here(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    وقتی کاربر «همینجا» رو انتخاب می‌کنه، پنل رو در گروه باز می‌کنه.
    این callback handler باید در bot.py ثبت بشه.
    """
    from telegram.ext import ApplicationHandlerStop
    query = update.callback_query
    uid = query.from_user.id

    # بررسی که فقط همون کسی که پرسید می‌تونه جواب بده
    # action از callback_data می‌گیریم: open_panel_here_{action}
    parts = query.data.split("_", 3)
    action = parts[-1] if len(parts) >= 4 else ""

    is_pishva = (uid == PISHVA_ID)
    admin = await db.get_admin(uid)
    is_admin = bool(admin and admin["is_active"])

    if not (is_pishva or is_admin):
        await query.answer("⛔ شما مجوز باز کردن پنل را ندارید.", show_alert=True)
        return

    await query.answer()

    # ─── شروع/پنل پیش‌فرض: همون چیزیه که /start نشون می‌ده ───
    if action == "restart":
        from auth import show_pishva_welcome, show_admin_welcome
        if is_pishva:
            await show_pishva_welcome(update, ctx)
        else:
            await show_admin_welcome(update, ctx, admin)
        await register_panel_owner(update, ctx, query.message.message_id)
        return

    text, markup, err = await _panel_content(action, uid, is_pishva, admin)
    if text is None:
        await safe_edit_message_text(query, err or "❗ این پنل در گروه پشتیبانی نمی‌شود. لطفاً در پیوی باز کنید.")
        return

    sent = await safe_edit_message_text(query, text, reply_markup=markup, parse_mode="Markdown")

    # ثبت مالکیت پنل
    if sent:
        msg_id = sent.message_id if hasattr(sent, "message_id") else query.message.message_id
        await register_panel_owner(update, ctx, msg_id)



async def handle_keyword_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from telegram.ext import ApplicationHandlerStop
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    text = _strip_fuzzy_decoration(text)

    # «آغاز/اغاز» و «پایان/تموم» رو دست نمی‌زنیم — می‌ذاریم خودِ
    # workhours_conv (در bot.py) به‌عنوان entry_point بگیردشون، چون
    # ممکنه یک state (مدت دقیقه) از مدیر ارشد بخوان.
    if text in WORKHOURS_START_KEYWORDS or text in WORKHOURS_END_KEYWORDS:
        return

    # ─── میان‌بر «ت<شماره>» (ت1، ت 1، ت۱، ت ۱، ...) ───
    # مستقیم صفحه‌ی N از پنل مدیر ارشد رو باز می‌کنه، بدون نیاز به رفتن
    # داخل «پنل مدیر ارشد» و زدنِ ◀️/▶️ چندبار. صفحه‌ها از ۱ شماره‌گذاری
    # می‌شن (ت1 = صفحه‌ی اول = ایندکسِ صفر در kb_pishva_panel).
    tab_match = TAB_PAGE_RE.match(_to_ascii_digits(text))
    if tab_match:
        uid0 = update.effective_user.id if update.effective_user else None
        if not uid0:
            return
        is_pishva0 = (uid0 == PISHVA_ID)
        admin0 = await db.get_admin(uid0)
        is_admin0 = bool(admin0 and admin0["is_active"])
        if not (is_pishva0 or is_admin0):
            return
        if not is_pishva0:
            await update.message.reply_text("⛔ این دستور فقط برای مدیر ارشد است.")
            raise ApplicationHandlerStop()
        page_num = int(tab_match.group(1))
        page_index = max(0, page_num - 1)
        page_action = f"pishva_page_{page_index}"
        text_out, markup, err = await _panel_content(page_action, uid0, is_pishva0, admin0)
        if text_out is None:
            await update.message.reply_text(err or "⛔ شما مجوز باز کردن این پنل را ندارید.")
            raise ApplicationHandlerStop()
        sent = await update.message.reply_text(text_out, reply_markup=markup, parse_mode="Markdown")
        await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    action = SIMPLE_KEYWORDS.get(text)

    if action is None and text not in ADMIN_KEYWORDS:
        return  # نه keyword → بذار ConversationHandler بگیره

    uid = update.effective_user.id if update.effective_user else None
    if not uid:
        return
    is_pishva = (uid == PISHVA_ID)
    admin = await db.get_admin(uid)
    is_admin = bool(admin and admin["is_active"])

    if not (is_pishva or is_admin):
        return

    import servers as _servers
    _is_owner = bool(is_admin and _servers.is_owner(uid))
    if _is_owner and action == "admin_manage_panel":
        from telegram import InlineKeyboardButton as _IKB, InlineKeyboardMarkup as _IKM
        await update.message.reply_text("👥 مدیریت مدیران مجموعه:", reply_markup=_IKM([[_IKB("👥 باز کردن", callback_data="own_mgrs")]]))
        raise ApplicationHandlerStop()
    if action and action in PISHVA_ONLY_ACTIONS and not is_pishva:
        if _is_owner and action in OWNER_ALLOWED_ACTIONS:
            is_pishva = True   # مدیر مجموعه در سرورِ خودش برای همین کارها هم‌ترازِ مدیر ارشد است
        else:
            await update.message.reply_text("⛔ این دستور فقط برای مدیر ارشد است.")
            raise ApplicationHandlerStop()
    if action == "servers_panel":
        from servers_panel import open_panel
        await open_panel(update, ctx)
        raise ApplicationHandlerStop()

    # ─── بستن پنل (کلمه‌ی «بستن» یا «خروج») ───
    # پنلِ قبلاً بازِ همین کاربر توی گروه رو حذف می‌کنه (از طریق
    # register_panel_owner → _clear_previous_panel) و یک پیامِ تازه با
    # همون خوش‌آمدگوییِ همیشگی می‌فرستد ولی به‌جای کیبوردِ کاملِ پنل،
    # فقط ۲ دکمه زیرش می‌ذاره: لاگ‌ها و ورود به پنل. این پیام (برخلافِ
    # پنل‌های معمولی که ۳ دقیقه صبر می‌کنن) با ۱ دقیقه بی‌فعالیتی
    # خودش بسته می‌شه.
    if action == "close_panel":
        from auth import time_greeting, get_weather_line
        name = (await db.get_setting("pishva_display_name", "مدیر ارشد")) if is_pishva \
            else (admin["display_name"] or admin["full_name"])
        greeting = time_greeting(name)
        weather = await get_weather_line()
        text = greeting
        if weather:
            text += "\n" + weather
        sent = await update.message.reply_text(
            text, reply_markup=kb.kb_panel_closed_menu(), parse_mode="Markdown"
        )
        await register_panel_owner(update, ctx, sent.message_id, timeout_seconds=CLOSED_WELCOME_TIMEOUT_SECONDS)
        raise ApplicationHandlerStop()

    # ─── شطرنج زنده (کلمه‌ی «بازی» یا «شطرنج») ───
    # اگه این پیام ریپلای روی یک نفر باشه (یا روی پیامی که خودِ فرستنده
    # قبلاً آیدی توش فرستاده)، به‌جای نمایشِ کل لیستِ حریف‌ها، مستقیم
    # می‌ریم سراغِ درخواستِ بازی با همون شخص.
    if action == "live_chess":
        from chess_challenge import chess_menu_from_message, chess_challenge_target_from_message
        target_id, target_name, _ = _extract_reply_target(update)
        if target_id:
            sent = await chess_challenge_target_from_message(update, ctx, target_id, target_name)
        else:
            sent = await chess_menu_from_message(update, ctx)
        if sent is not None:
            await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── پنل مدیر ارشد (کلمه «مدیر ارشد») ───
    if action == "pishva_panel":
        chat = update.effective_chat
        if chat and chat.type in ("group", "supergroup"):
            await ask_panel_location(update, ctx, "pishva_panel")
        else:
            sent = await update.message.reply_text(
                box("👑 پنل مدیر ارشد"), reply_markup=kb.kb_pishva_main(), parse_mode="Markdown"
            )
            await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── پنل سریع (کلمه «پنل») ───
    # برای همه‌ی نقش‌ها (مدیر ارشد/مدیر مسابقات/مدیر امنیتی) کار می‌کنه.
    # برخلاف «restart»، هیچ خوش‌آمدگویی/آب‌وهوا/جزئیاتی نداره — فقط عنوانِ
    # کوتاه + همون کیبوردِ کامل، برای باز شدنِ خیلی سریع‌تر.
    if action == "quick_panel":
        chat = update.effective_chat
        if chat and chat.type in ("group", "supergroup"):
            await ask_panel_location(update, ctx, "quick_panel")
        else:
            text, markup, err = await _panel_content("quick_panel", uid, is_pishva, admin)
            if text is None:
                await update.message.reply_text(err or "⛔ شما مجوز باز کردن این پنل را ندارید.")
                raise ApplicationHandlerStop()
            sent = await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
            await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── مدیریت دستیار (کلمه «دست» یا «رصد») ───
    if action == "ai_manage_panel":
        chat = update.effective_chat
        if chat and chat.type in ("group", "supergroup"):
            await ask_panel_location(update, ctx, "ai_manage_panel")
        else:
            ai_online = await db.get_setting("ai_online", "1")
            ctx.user_data["ai_manage_back"] = "menu_pishva"
            sent = await update.message.reply_text(
                f"{box('🧑\u200d💻 مدیریت دستیار')}\n\n📌 یک گزینه را انتخاب کنید:",
                reply_markup=kb.kb_ai_manage_menu(ai_online, "menu_pishva"),
                parse_mode="Markdown"
            )
            await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── مدیریت مدیران (کلمه «مدیران») ───
    if action == "admin_manage_panel":
        chat = update.effective_chat
        if chat and chat.type in ("group", "supergroup"):
            await ask_panel_location(update, ctx, "admin_manage_panel")
        else:
            text, markup, err = await _panel_content("admin_manage_panel", uid, is_pishva, admin)
            if text is None:
                await update.message.reply_text(err or "⛔ شما مجوز باز کردن این پنل را ندارید.")
                raise ApplicationHandlerStop()
            sent = await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
            await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── پنل امنیتی APS (کلمه «APS») ───
    if action == "aps_panel":
        chat = update.effective_chat
        if chat and chat.type in ("group", "supergroup"):
            await ask_panel_location(update, ctx, "aps_panel")
        else:
            text, markup, err = await _panel_content("aps_panel", uid, is_pishva, admin)
            if text is None:
                await update.message.reply_text(err or "⛔ شما مجوز باز کردن این پنل را ندارید.")
                raise ApplicationHandlerStop()
            sent = await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
            await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── داشبورد ───
    if action == "dashboard":
        from dashboard import build_dashboard_pishva_text, build_dashboard_admin_text
        if is_pishva:
            dtext = await build_dashboard_pishva_text()
            sent = await update.message.reply_text(dtext, reply_markup=kb.kb_dashboard_pishva(), parse_mode="Markdown")
        else:
            dtext = await build_dashboard_admin_text(uid)
            sent = await update.message.reply_text(dtext, reply_markup=kb.kb_dashboard_admin(), parse_mode="Markdown")
        await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── اطلاعات/درباره/کیه — ریپلای روی پیام ───
    if action == "reply_info":
        target_id, target_name, target_username = _extract_reply_target(update)
        if not target_id:
            await update.message.reply_text(
                "❗ برای دیدن اطلاعات، روی پیام شخص موردنظر ریپلای کنید و «اطلاعات» بنویسید."
            )
            return
        text_lines = await _build_user_info(target_id, target_name, target_username, ctx)
        try:
            await update.message.reply_text("\n".join(text_lines), parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("\n".join(text_lines))
        raise ApplicationHandlerStop()

    # ─── مدیر — ریپلای روی پیامی که آیدیِ یه مدیر توشه → باز شدنِ پنلِ
    # همون مدیر (فقط مدیر ارشد؛ گیت‌کیپینگش بالاتر با PISHVA_ONLY_ACTIONS
    # انجام شده). از build_admin_view_content توی misc.py استفاده می‌کنه
    # تا دقیقاً همون چیزی که با دکمه هم باز می‌شه نشون داده بشه.
    if action == "reply_admin_panel":
        target_id, target_name, _ = _extract_reply_target(update)
        if not target_id:
            await update.message.reply_text(
                "❗ برای باز کردنِ پنلِ یه مدیر، روی پیامی که آیدیِ اون توشه (یا خودِ پیامش) "
                "ریپلای کنید و «مدیر» بنویسید."
            )
            raise ApplicationHandlerStop()
        from misc import build_admin_view_content
        text, markup = await build_admin_view_content(target_id)
        if text is None:
            await update.message.reply_text(f"❗ آیدیِ `{target_id}` مربوط به هیچ مدیرِ ثبت‌شده‌ای نیست.", parse_mode="Markdown")
            raise ApplicationHandlerStop()
        sent = await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
        await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── آنلاین / الان (وضعیت لحظه‌ایِ همه‌ی ادمین‌ها) ───
    if action == "online_admins":
        admins_all = await db.get_active_admins()
        from datetime import datetime
        now_dt = datetime.now()
        ONLINE_THRESHOLD_MIN = 3  # زیر این چند دقیقه یعنی همین الان روی ربات فعاله
        STRANGER_WINDOW_MIN = 60  # غریبه‌هایی که توی این بازه فعالیت داشتن رو نشون بده

        lines = [box("👥 وضعیت ادمین‌ها"), ""]
        if not admins_all:
            lines.append("❗ هیچ مدیر فعالی ثبت نشده.")
        for a in admins_all:
            name = escape_md_legacy(a["display_name"] or a["full_name"])
            role_lbl = "🏆" if a["role"] == ROLE_TOURNAMENT_MANAGER else "🛡️"
            last = a.get("last_active")
            status = "⚫️ هیچ‌وقت از ربات استفاده نکرده"
            if last:
                try:
                    last_dt = datetime.fromisoformat(str(last))
                    diff = now_dt - last_dt
                    mins = int(diff.total_seconds() // 60)
                    if mins < ONLINE_THRESHOLD_MIN:
                        status = "🟢 آنلاین"
                    elif mins < 60:
                        status = f"🟡 آفلاین — `{mins} دقیقه پیش` فعال بوده"
                    elif mins < 1440:
                        status = f"🟠 آفلاین — `{int(mins // 60)} ساعت پیش` فعال بوده"
                    else:
                        status = f"🔴 آفلاین — `{int(mins // 1440)} روز پیش` فعال بوده"
                except Exception:
                    pass
            lines.append(f"{role_lbl} *{name}*\n    {status}")

        # ─── غریبه‌هایی که اخیراً با ربات کار کردن (نه مدیر ارشد، نه ادمین ثبت‌شده) ───
        strangers = await db.get_recent_strangers(minutes=STRANGER_WINDOW_MIN)
        keyboard_rows = []
        if strangers:
            lines.append("")
            lines.append(separator("👥 غریبه‌های اخیراً فعال"))
            lines.append(f"_(فعالیت در {STRANGER_WINDOW_MIN} دقیقه‌ی گذشته — برای جزییات روی دکمه بزن)_")
            for s in strangers:
                uname = s["username"] or ""
                fname = s["full_name"] or ""
                tid = s["telegram_id"]
                label_bits = []
                if fname:
                    label_bits.append(fname[:20])
                if uname:
                    label_bits.append(f"@{uname}")
                label_bits.append(f"#{tid}")
                label = "🙍 " + " | ".join(label_bits)
                keyboard_rows.append([InlineKeyboardButton(label[:64], callback_data=f"strangerinfo_{tid}")])
        else:
            lines.append("")
            lines.append("👥 هیچ کاربر غریبه‌ای اخیراً با ربات کار نکرده.")

        text = "\n\n".join(lines)
        markup = InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None
        try:
            await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(text, reply_markup=markup)
        raise ApplicationHandlerStop()

    # ─── لیست مدیران ───
    if action == "admin_list":
        admins_all = await db.get_active_admins()
        lines = [box(f"👥 مدیران فعال — {len(admins_all)} نفر")]
        for a in admins_all:
            name = escape_md_legacy(a["display_name"] or a["full_name"])
            uname = f"@{escape_md_legacy(a['username'])}" if a.get("username") else "—"
            role_lbl = "🏆 مدیر مسابقات" if a["role"] == ROLE_TOURNAMENT_MANAGER else "🛡️ مدیر امنیتی"
            lines.append(f"• {name} ({uname}) — {role_lbl}")
        if not admins_all:
            lines.append("❗ هیچ مدیر فعالی ثبت نشده.")
        try:
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("\n".join(lines))
        raise ApplicationHandlerStop()

    # ─── آمار سریع ───
    if action == "quick_stats":
        # این ۴ کوئری مستقلن — هم‌زمان اجرا می‌شن، نه پشتِ‌سرِهم.
        all_players, active_players, all_matches, today_m = await asyncio.gather(
            db.get_all_players(),
            db.get_continuing_players(),
            db.get_matches_by_filter("all"),
            db.get_matches_by_filter("today"),
        )
        done_matches = [m for m in all_matches if m["result"]]
        warned = [p for p in all_players if p["warnings"] > 0]
        lines = [
            box("📊 آمار سریع"),
            separator("👤 بازیکنان"),
            f"کل: `{len(all_players)}` | فعال: `{len(active_players)}` | با اخطار: `{len(warned)}`",
            separator("♟️ مسابقات"),
            f"کل: `{len(all_matches)}` | با نتیجه: `{len(done_matches)}` | امروز: `{len(today_m)}`",
            separator(),
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        raise ApplicationHandlerStop()

    # ─── آخرین نتایج ───
    if action == "recent_results":
        all_matches = await db.get_matches_by_filter("all")
        done = [m for m in all_matches if m["result"]][-10:]
        done.reverse()
        if not done:
            await update.message.reply_text("❗ هنوز مسابقه‌ای با نتیجه ثبت نشده.")
            return
        lines = [box("🏆 آخرین نتایج")]
        for m in done:
            white_n = escape_md_legacy(m['white_name'])
            black_n = escape_md_legacy(m['black_name'])
            if m["result"] == "white":
                res = f"🥇 {white_n} برنده"
            elif m["result"] == "black":
                res = f"🥇 {black_n} برنده"
            else:
                res = "🤝 تساوی"
            lines.append(f"• {white_n} vs {black_n} — {res}")
        try:
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("\n".join(lines))
        raise ApplicationHandlerStop()

    # ─── لیست اخطارها ───
    if action == "warnings_list":
        all_players = await db.get_all_players()
        warned = sorted([p for p in all_players if p["warnings"] > 0],
                        key=lambda p: p["warnings"], reverse=True)
        if not warned:
            await update.message.reply_text("✅ هیچ بازیکنی اخطار فعال ندارد.")
            return
        lines = [box(f"⚠️ بازیکنان با اخطار — {len(warned)} نفر")]
        for p in warned:
            bar = "🔴" * min(p["warnings"], 3) + "⚪" * max(0, 3 - p["warnings"])
            lines.append(f"• {escape_md_legacy(p['full_name'])} — {bar} ({p['warnings']} اخطار)")
        try:
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("\n".join(lines))
        raise ApplicationHandlerStop()

    # ─── وظیفه ───
    if action == "tasks":
        if is_pishva:
            sent = await update.message.reply_text(box("📋 وظایف"), reply_markup=kb.kb_tasks_pishva(), parse_mode="Markdown")
        else:
            sent = await update.message.reply_text(box("📋 وظایف"), reply_markup=kb.kb_tasks_admin(), parse_mode="Markdown")
        await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── مسابقه ───
    if action == "matches":
        sent = await update.message.reply_text(
            box("♟️ مدیریت مسابقات"), reply_markup=kb.kb_matches_menu(), parse_mode="Markdown"
        )
        await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── بازیکن ───
    if action == "players":
        role_key = "pishva" if is_pishva else admin["role"]
        sent = await update.message.reply_text(
            box("👤 مدیریت بازیکنان"), reply_markup=kb.kb_players_menu(role_key), parse_mode="Markdown"
        )
        await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── مخابره ───
    if action == "comms":
        if is_pishva:
            sent = await update.message.reply_text(box("📡 مخابرات"), reply_markup=kb.kb_comms_pishva(), parse_mode="Markdown")
        else:
            sent = await update.message.reply_text(box("📡 مخابرات"), reply_markup=kb.kb_comms_admin(), parse_mode="Markdown")
        await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── امنیت ───
    if action == "security":
        current = await db.get_setting("system_status", "normal")
        await update.message.reply_text(
            f"{box('🚦 وضعیت سیستم')}\n\nوضعیت فعلی را انتخاب کنید:",
            reply_markup=kb.kb_status_select(current), parse_mode="Markdown"
        )
        raise ApplicationHandlerStop()

    # ─── وضعیت (گزارش کامل سیستم) ───
    if action == "status":
        report = await _build_system_status_report()
        await update.message.reply_text(report, parse_mode="Markdown")
        raise ApplicationHandlerStop()

    # ─── کلاس ───
    if action == "classes":
        sent = await update.message.reply_text(
            box("🏫 مدیریت کلاس‌ها"), reply_markup=kb.kb_class_manage(is_pishva=(update.effective_user.id == PISHVA_ID)), parse_mode="Markdown"
        )
        await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── تیم ───
    if action == "teams":
        team_mode = await db.get_setting("team_mode_enabled", "0")
        if team_mode != "1":
            await update.message.reply_text("❗ حالت تیمی در حال حاضر غیرفعال است.")
            return
        sent = await update.message.reply_text(
            box("🏆 مدیریت تیم‌ها"), reply_markup=kb.kb_teams_menu(), parse_mode="Markdown"
        )
        await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── بکاپ ───
    if action == "backup":
        await update.message.reply_text(
            f"{box('💾 سیستم پشتیبان‌گیری')}\n\n📌 بازه زمانی را انتخاب کنید:",
            reply_markup=kb.kb_backup_main(), parse_mode="Markdown"
        )
        raise ApplicationHandlerStop()

    # ─── درخواست ───
    if action == "requests":
        reqs = await db.get_pending_requests()
        if not reqs:
            await update.message.reply_text(
                f"{box('📥 درخواست‌های دسترسی')}\n\n✅ هیچ درخواست جدیدی وجود ندارد.",
                parse_mode="Markdown"
            )
            return
        for req in reqs:
            role_label = "🏆 مدیر مسابقات" if req["role"] == ROLE_TOURNAMENT_MANAGER else "🛡️ مدیر امنیتی"
            rtext = (
                f"📥 *درخواست دسترسی*\n\n"
                f"👤 نام: {escape_md_legacy(req['full_name'])}\n"
                f"🔗 یوزرنیم: {escape_md_legacy(req['username'])}\n"
                f"💼 نقش: {role_label}\n"
                f"📝 پیام: {escape_md_legacy(req['message']) or '—'}\n"
                f"⏱️ زمان: `{str(req['requested_at'])[:19]}`"
            )
            try:
                await update.message.reply_text(
                    rtext, reply_markup=kb.kb_access_request(req["id"]), parse_mode="Markdown"
                )
            except Exception:
                await update.message.reply_text(
                    rtext, reply_markup=kb.kb_access_request(req["id"])
                )
        raise ApplicationHandlerStop()

    # ─── لاگ / گزارش ───
    if action == "logs":
        logs, total = await db.get_action_logs("today", page=0, page_size=15)
        admins_map = {a["telegram_id"]: (a["display_name"] or a["full_name"]) for a in await db.get_all_admins()}
        pname = await db.get_setting("pishva_display_name", "مدیر ارشد")
        if not logs:
            await update.message.reply_text("❗ هیچ اقدامی امروز ثبت نشده.")
            return
        lines = [box(f"🔍 لاگ اقدامات — امروز ({total} مورد)"), ""]
        for log in logs:
            name = pname if log["admin_id"] == PISHVA_ID else admins_map.get(log["admin_id"], str(log["admin_id"]))
            t = str(log["logged_at"] or "")[:16]
            lines.append(format_log_entry(t, name, log["action_type"], log["description"]))
        if total > len(logs):
            lines.append("")
            lines.append("📌 برای دیدن کامل، فیلتر بازه‌ی زمانی، و جستجو، از پنل «🔍 پیگیری اقدامات» مدیر ارشد استفاده کنید.")
        try:
            await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("\n\n".join(lines))
        raise ApplicationHandlerStop()

    # ─── راهنما / کمک ───
    if action == "help":
        from help_center import kb_help_main
        role = "pishva" if is_pishva else "admin"
        await update.message.reply_text(
            box("📚 مرکز راهنمای سیستم"), reply_markup=kb_help_main(role), parse_mode="Markdown"
        )
        raise ApplicationHandlerStop()

    # ─── قرعه ───
    if action == "lottery":
        sent = await update.message.reply_text(
            f"{box('🎲 قرعه‌کشی هوشمند')}\n\n📌 محدوده بازیکنان:",
            reply_markup=kb.kb_lottery_scope(), parse_mode="Markdown"
        )
        await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── جدول / رتبه (Elo) ───
    if action == "elo":
        try:
            from elo import get_elo_leaderboard, get_elo_title
            leaders = await get_elo_leaderboard(10)
        except Exception:
            leaders = []
        if not leaders:
            await update.message.reply_text("❗ هنوز مسابقه‌ای با نتیجه ثبت نشده.")
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = [box("🏆 جدول رتبه‌بندی Elo")]
        for i, p in enumerate(leaders):
            medal = medals[i] if i < 3 else f"`{i+1}.`"
            lines.append(f"{medal} {escape_md_legacy(p['full_name'])} — `{int(p['rating'])}` ({get_elo_title(p['rating'])})")
        try:
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("\n".join(lines))
        raise ApplicationHandlerStop()

    # ─── قهرمانان ───
    if action == "champions":
        try:
            from features import _get_best_player_since
            from datetime import datetime, timedelta
            week_ago = (datetime.now() - timedelta(days=7)).isoformat()
            month_ago = (datetime.now() - timedelta(days=30)).isoformat()
            weekly = await _get_best_player_since(week_ago)
            monthly = await _get_best_player_since(month_ago)
        except Exception:
            weekly = monthly = None
        lines = [
            box("🏆 قهرمانان"),
            "🌟 هفته: " + (f"{escape_md_legacy(weekly['name'])} ({weekly['wins']} برد)" if weekly else "—"),
            "👑 ماه: " + (f"{escape_md_legacy(monthly['name'])} ({monthly['wins']} برد)" if monthly else "—"),
        ]
        try:
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("\n".join(lines))
        raise ApplicationHandlerStop()

    # ─── یادآور ───
    if action == "reminders":
        import reminders as rem
        master = (await db.get_setting("reminder_master_enabled", "1")) == "1"
        items = []
        for rtype, conf in rem.REMINDER_TYPES.items():
            enabled = await rem._is_enabled(rtype)
            interval = await rem._get_interval_hours(rtype)
            items.append((rtype, conf["label"], enabled, interval))
        text = (
            "⏰ تنظیمات یادآورها\n\n"
            "در این بخش می‌توانید هر یادآور را جداگانه فعال یا غیرفعال کنید "
            "و بازه‌ی زمانی ارسال آن را تنظیم نمایید."
        )
        await update.message.reply_text(text, reply_markup=kb.kb_reminders_menu(master, items), parse_mode="Markdown")
        raise ApplicationHandlerStop()

    # ─── تنظیمات ───
    if action == "settings":
        chat = update.effective_chat
        if chat and chat.type in ("group", "supergroup"):
            await ask_panel_location(update, ctx, "settings")
        else:
            keys = ["notifications_enabled", "communications_enabled", "help_enabled",
                "match_registration_enabled", "admin_login_enabled", "bot_active_for_admins",
                "team_mode_enabled", "team_registration_enabled", "managers_can_create_teams",
                "admin_dashboard_enabled", "ai_online", "live_chess_enabled", "hub_enabled",
                "bug_report_to_pishva_enabled", "principal_panel_enabled", "admin_webpanel_enabled",
                "admin_direct_kick_enabled", "top_players_mode"]
            settings = await db.get_settings_bulk(keys, "1")
            sent = await update.message.reply_text(
                f"{box('⚙️ تنظیمات ربات')}\n\n📌 گزینه موردنظر را تغییر دهید:",
                reply_markup=kb.kb_pishva_settings_simple(settings), parse_mode="Markdown"
            )
            await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── ساعت کاری ───
    if action == "workhours_menu":
        import workhours as wh
        autoend_on = (await db.get_setting("workhours_autoend_enabled", "0")) == "1"
        reminder_on = (await db.get_setting("workhours_reminder_enabled", "0")) == "1"
        reminder_minutes = int(await db.get_setting("workhours_reminder_minutes", "60"))
        status_extra = await wh._render_status_extra()
        await update.message.reply_text(
            f"{box('🕐 ساعت کاری')}\n\n📌 عملیات را انتخاب کنید:{status_extra}",
            reply_markup=kb.kb_workhours(autoend_on, reminder_on, reminder_minutes),
            parse_mode="Markdown"
        )
        raise ApplicationHandlerStop()

    # ─── تقویم مدرسه (کلمات «تقویم»/«تاریخ»/«ساعت»/«وقت»/«امروز»/«ایونت»/«تعطیلی») ───
    if action in ("calendar", "calendar_today", "calendar_next_event", "calendar_next_holiday"):
        text, markup, err = await _panel_content(action, uid, is_pishva, admin)
        if text is None:
            await update.message.reply_text(err or "❗ خطا در باز کردن تقویم.")
            raise ApplicationHandlerStop()
        sent = await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
        await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── فیدبک / انتقاد ───
    if action == "feedback":
        await update.message.reply_text(
            box("💡 انتقادات و پیشنهادات"), reply_markup=kb.kb_feedback_menu(), parse_mode="Markdown"
        )
        raise ApplicationHandlerStop()

    # ─── شروع (فقط برای کاربران قبلاً ثبت‌شده — بدون فلوی ثبت‌نام) ───
    if action == "restart":
        from auth import show_pishva_welcome, show_admin_welcome
        if is_pishva:
            sent = await show_pishva_welcome(update, ctx)
        else:
            sent = await show_admin_welcome(update, ctx, admin)
        if sent is not None and hasattr(sent, "message_id"):
            await register_panel_owner(update, ctx, sent.message_id)
        raise ApplicationHandlerStop()

    # ─── تنظیم/حذف مدیر (فقط مدیر ارشد، با ریپلای) ───
    if text in ADMIN_KEYWORDS:
        if not is_pishva:
            await update.message.reply_text("⛔ این دستور فقط برای مدیر ارشد است.")
            return
        target_id, target_name, target_username = _extract_reply_target(update)
        if not target_id:
            await update.message.reply_text(
                "❗ برای این دستور باید روی پیام شخص موردنظر (یا پیامی حاوی آیدی عددی او) ریپلای کنید."
            )
            return
        if target_id == PISHVA_ID:
            await update.message.reply_text("❗ نمی‌توانید مدیر ارشد را به‌عنوان مدیر تنظیم/حذف کنید.")
            return

        if text == "تنظیم مدیر":
            await _set_admin(target_id, target_username, target_name, ROLE_TOURNAMENT_MANAGER)
            await update.message.reply_text(f"✅ {target_name} به‌عنوان 🏆 مدیر مسابقات تنظیم شد.")
        elif text == "تنظیم مدیر امنیتی":
            await _set_admin(target_id, target_username, target_name, ROLE_SECURITY_MANAGER)
            await update.message.reply_text(f"✅ {target_name} به‌عنوان 🛡️ مدیر امنیتی تنظیم شد.")
        elif text in ("حذف مدیر", "حذف مدیر امنیتی"):
            existing = await db.get_admin(target_id)
            if not existing or not existing["is_active"]:
                await update.message.reply_text("❗ این فرد در حال حاضر مدیر فعال نیست.")
                return
            await db.kick_admin(target_id)
            await db.log_action(PISHVA_ID, "kick_admin_keyword", f"حذف مدیر: {target_name}", target_id)
            await update.message.reply_text(f"✅ دسترسی مدیریت {target_name} حذف شد.")
        raise ApplicationHandlerStop()


# ─── جزییات یک کاربر «غریبه» (دکمه‌ی شیشه‌ای زیر لیست آنلاین) ───
async def stranger_info_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """callback_data = strangerinfo_<telegram_id> — فقط مدیر ارشد و مدیر امنیتی می‌بینن."""
    query = update.callback_query
    uid = query.from_user.id
    is_pishva = (uid == PISHVA_ID)
    admin = None if is_pishva else await db.get_admin(uid)
    allowed = is_pishva or (admin and admin["is_active"] and admin["role"] == ROLE_SECURITY_MANAGER)
    if not allowed:
        await query.answer("⛔ فقط مدیر ارشد یا مدیر امنیتی می‌تونه جزییات غریبه‌ها رو ببینه.", show_alert=True)
        return
    await query.answer()

    tid = int(query.data.split("_")[-1])
    summary = await db.get_stranger_summary(tid)
    log_rows = await db.get_stranger_log(tid, limit=15)

    if not summary or not summary["action_count"]:
        await query.message.reply_text("❗ اطلاعاتی از این کاربر پیدا نشد.")
        return

    uname = escape_md_legacy(summary["username"] or "")
    fname = summary["full_name"] or "بی‌نام"
    lines = [
        box("🙍 جزییات کاربر غریبه"),
        "",
        f"👤 نام: *{escape_md_legacy(fname)}*",
        f"🪪 یوزرنیم: {('@' + uname) if uname else '—'}",
        f"🆔 آیدی عددی: `{tid}`",
        f"📊 تعداد کل اقدامات ثبت‌شده: {summary['action_count']}",
        f"🕐 اولین فعالیت: `{str(summary['first_seen'] or '')[:16]}`",
        f"🕐 آخرین فعالیت: `{str(summary['last_active'] or '')[:16]}`",
        "",
        separator("📋 آخرین اقدامات (جدیدترین اول)"),
    ]
    for row in log_rows:
        ts = str(row["ts"] or "")[:16]
        lines.append(f"⏱️ `{ts}` — {escape_md_legacy(row['action'] or '')}")

    text = "\n".join(lines)
    try:
        await query.message.reply_text(text, parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(text)
def _ago_label(iso_ts: str) -> str:
    """یک timestamp ایزو رو به برچسب «چند وقت پیش» با رنگ تبدیل می‌کنه —
    دقیقاً همون آستانه‌هایی که توی «آنلاین/الان» و پنل ادمین استفاده می‌شه،
    تا همه‌جای ربات یک‌دست باشه."""
    from datetime import datetime
    try:
        last_dt = datetime.fromisoformat(str(iso_ts))
    except Exception:
        return "نامشخص"
    diff = datetime.now() - last_dt
    mins = int(diff.total_seconds() // 60)
    if mins < 2:
        return "🟢 همین الان (آنلاین روی ربات)"
    elif mins < 60:
        return f"🟡 {mins} دقیقه پیش"
    elif mins < 1440:
        return f"🟠 {int(mins // 60)} ساعت پیش"
    else:
        return f"🔴 {int(mins // 1440)} روز پیش"


async def _build_user_info(target_id: int, target_name: str, target_username: str, ctx: ContextTypes.DEFAULT_TYPE = None) -> list:
    from datetime import datetime, timedelta
    target_name = escape_md_legacy(target_name)
    target_username = escape_md_legacy(target_username)
    lines = [box(f"🔍 اطلاعات — {target_name}")]

    # یوزرنیم
    uname_txt = target_username if target_username else "—"
    lines.append(f"🪪 یوزرنیم: {uname_txt}")
    lines.append(f"🆔 آیدی: `{target_id}`")
    lines.append("")

    # مدیر ارشد است؟
    if target_id == PISHVA_ID:
        pname = await db.get_setting("pishva_display_name", "مدیر ارشد")
        lines.append(f"👑 *نقش: مدیر ارشد ({pname})*")
        lines.append("🔓 دسترسی: همه چیز")
        return lines

    # ادمینه؟
    admin = await db.get_admin(target_id)
    if admin and admin["is_active"]:
        role_lbl = "🏆 مدیر مسابقات" if admin["role"] == ROLE_TOURNAMENT_MANAGER else "🛡️ مدیر امنیتی"
        lines.append(f"💼 *نقش: {role_lbl}*")
        lines.append(f"✅ وضعیت: فعال")

        # آخرین فعالیت
        last = admin.get("last_active")
        if last:
            try:
                last_dt = datetime.fromisoformat(str(last))
                diff = datetime.now() - last_dt
                mins = int(diff.total_seconds() // 60)
                if mins < 2:
                    ago = "🟢 همین الان"
                elif mins < 60:
                    ago = f"🟡 {mins} دقیقه پیش"
                elif mins < 1440:
                    ago = f"🟠 {int(mins//60)} ساعت پیش"
                else:
                    ago = f"🔴 {int(mins//1440)} روز پیش"
                lines.append(f"⏱️ آخرین فعالیت: {ago}")
            except Exception:
                pass

        # تاریخ عضویت
        joined = admin.get("joined_at")
        if joined:
            lines.append(f"📅 عضویت: `{str(joined)[:10]}`")

        # دسترسی‌ها
        lines.append("")
        lines.append(separator("🔐 دسترسی‌ها"))
        try:
            perms = json.loads(admin.get("permissions") or "{}")
            perm_labels = {
                "match_management": "♟️ مسابقات",
                "view_players": "👤 مشاهده بازیکنان",
                "issue_warning": "⚠️ صدور اخطار",
                "request_ban": "🚫 درخواست اخراج",
                "direct_ban": "❌ اخراج مستقیم",
                "edit_delete_match": "✏️ ویرایش مسابقه",
                "communications": "📡 مخابرات",
                "notifications": "🔔 اعلانات",
                "news": "📰 اخبار",
                "assign_task": "📋 وظیفه",
                "report": "🚨 گزارش",
                "senior_admin": "🌟 ارشد",
                "settings_access": "⚙️ تنظیمات",
                "ai_access": "🤖 دستیار هوشمند",
            }
            active_perms = [lbl for k, lbl in perm_labels.items() if perms.get(k, True)]
            inactive_perms = [lbl for k, lbl in perm_labels.items() if not perms.get(k, True)]
            if active_perms:
                lines.append("✅ " + " | ".join(active_perms))
            if inactive_perms:
                lines.append("❌ " + " | ".join(inactive_perms))
        except Exception:
            lines.append("دسترسی‌ها: نامشخص")

        # اخطارها
        warns = admin.get("warnings", 0)
        if warns:
            lines.append(f"\n⚠️ اخطارهای مدیریتی: `{warns}`")
    else:
        # نه مدیر ارشد، نه ادمینِ فعال — ولی شاید قبلاً ادمین بوده،
        # یا قبلاً («غریبه») با ربات کار کرده و الان دیگه نیست.
        if admin and not admin["is_active"]:
            role_lbl = "🏆 مدیر مسابقات" if admin["role"] == ROLE_TOURNAMENT_MANAGER else "🛡️ مدیر امنیتی"
            lines.append(f"👤 نقش: کاربر عادی *(قبلاً {role_lbl} بوده — الان اخراج/حذف شده)*")
            joined = admin.get("joined_at")
            if joined:
                lines.append(f"📅 تاریخ عضویتِ قبلی: `{str(joined)[:10]}`")
            last = admin.get("last_active")
            if last:
                lines.append(f"⏱️ آخرین فعالیتِ ثبت‌شده (به‌عنوان ادمین): {_ago_label(last)}")
        else:
            lines.append("👤 نقش: کاربر عادی")
            lines.append("❌ در سیستم به‌عنوان مدیر ثبت نشده")

        # سابقه‌ی فعالیتش به‌عنوان «غریبه» (هر پیام/دکمه‌ای که با ربات زده)
        stranger = await db.get_stranger_summary(target_id)
        if stranger and stranger["action_count"]:
            lines.append("")
            lines.append(separator("📜 سابقه‌ی فعالیت روی ربات"))
            lines.append(f"🕐 اولین فعالیت: `{str(stranger['first_seen'] or '')[:16]}`")
            lines.append(f"⏱️ آخرین فعالیت: {_ago_label(stranger['last_active'])}")
            lines.append(f"📊 تعداد کل اقدامات ثبت‌شده: {stranger['action_count']}")
        elif not (admin and not admin["is_active"]):
            lines.append("")
            lines.append("📜 هیچ سابقه‌ی فعالیتی از این کاربر روی ربات ثبت نشده.")

        # پروفایل زنده‌ی تلگرام (تا جایی که Bot API اجازه می‌ده) ───
        # نکته‌ی مهم: تلگرام برای ربات‌ها وضعیت «آنلاین/آفلاین لحظه‌ای»
        # (همون تیک سبز/آخرین بازدید) رو اصلاً در اختیار نمی‌ذاره — این‌ فقط
        # از طریق حساب کاربریِ خودِ تلگرام (نه ربات) و با اجازه‌ی حریم‌خصوصیِ
        # خودِ طرف قابل دیدنه. چیزی که این‌جا می‌شه نشون داد اینه که آیا
        # ربات هنوز می‌تونه به این کاربر پیام بده (یعنی بلاکش نکرده) و
        # آخرین نام/یوزرنیمِ شناخته‌شده‌اش چیه.
        lines.append("")
        lines.append(separator("📡 وضعیت زنده در تلگرام"))
        if ctx is not None:
            try:
                chat = await ctx.bot.get_chat(target_id)
                live_name = " ".join(filter(None, [getattr(chat, "first_name", None), getattr(chat, "last_name", None)])) or "—"
                live_uname = f"@{chat.username}" if getattr(chat, "username", None) else "—"
                lines.append("✅ ربات هنوز به این کاربر دسترسی دارد (بلاک نکرده)")
                lines.append(f"👤 نام فعلی: {escape_md_legacy(live_name)}")
                lines.append(f"🪪 یوزرنیم فعلی: {escape_md_legacy(live_uname)}")
            except Exception:
                lines.append("⛔ ربات دیگر به این کاربر دسترسی ندارد (بلاک کرده یا هیچ‌وقت با ربات شروع نکرده)")
        lines.append(
            "_ℹ️ تلگرام وضعیت «آنلاین/آفلاین لحظه‌ای» (تیک سبز) رو برای ربات‌ها "
            "در دسترس نمی‌ذاره؛ فقط «آخرین فعالیت روی همین ربات» بالا قابل‌مشاهده‌ست._"
        )

        # شاید بلاک باشه
        blocked = await db.get_blocked_user(target_id)
        if blocked:
            lines.append(f"\n🚫 *این کاربر توسط APS بلاک شده است*")
            lines.append(f"📝 دلیل: {blocked.get('reason', '—')}")

    return lines
def _extract_reply_target(update: Update):
    """آیدیِ هدف رو از پیامِ ریپلای‌شده استخراج می‌کنه.

    FIX: قبلاً همیشه اول از from_user پیامِ ریپلای‌شده استفاده می‌کرد — یعنی
    اگه یه نفر توی گروه صرفاً آیدیِ یه شخصِ دیگه رو به‌صورت متن می‌فرستاد
    (مثلاً برای معرفیِ یه بازیکن که خودش عضوِ گروه نیست)، ریپلای‌کردن روی
    همون پیام با «کیه» چیزی برنمی‌گردوند یا اطلاعاتِ خودِ فرستنده رو نشون
    می‌داد، نه آیدیِ نوشته‌شده. الان اگه متنِ پیامِ ریپلای‌شده صرفاً یه عددِ
    تنها باشه (آیدیِ تلگرام)، همون عدد اولویت داره؛ در غیرِ این صورت،
    مثلِ قبل، فرستنده‌ی واقعیِ پیام هدف در نظر گرفته می‌شه."""
    msg = update.message
    if not msg.reply_to_message:
        return None, None, None
    replied = msg.reply_to_message
    if replied.text:
        stripped = replied.text.strip()
        m = re.fullmatch(r"\D{0,12}(\d{5,})\D{0,12}", stripped)
        if m:
            tid = int(m.group(1))
            return tid, f"کاربر {tid}", ""
    if replied.from_user and not replied.from_user.is_bot:
        u = replied.from_user
        name = " ".join(filter(None, [u.first_name, u.last_name])) or (u.username or str(u.id))
        username = f"@{u.username}" if u.username else ""
        return u.id, name, username
    return None, None, None


async def _set_admin(telegram_id: int, username: str, full_name: str, role: str):
    existing = await db.get_admin(telegram_id)
    if existing:
        await db.update_admin_role_active(telegram_id, role)
    else:
        await db.create_admin(telegram_id, username, full_name, role)
    await db.log_action(PISHVA_ID, "set_admin_keyword", f"{full_name} -> {role}", telegram_id)


async def _build_system_status_report() -> str:
    try:
        import resource
        mem_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        mem_mb = round(mem_kb / 1024, 1)
        mem_txt = f"`{mem_mb}` مگابایت"
    except Exception:
        mem_txt = "نامشخص"

    status_map = {"normal": "🟢 نرمال", "bad": "🟡 احتیاطی", "danger": "🔴 خطرناک", "aps": "🪽 APS"}

    async def _teams():
        try:
            return await db.get_all_teams()
        except Exception:
            return []

    # همون فیکسِ داشبورد: ۹ تا await سریالی -> یک asyncio.gather
    (
        status, wh, db_manual, repair,
        all_players, all_matches, all_admins, all_tasks, all_classes, all_teams,
    ) = await asyncio.gather(
        db.get_setting("system_status", "normal"),
        db.get_setting("working_hours_active", "0"),
        db.get_setting("db_manual_status", "1"),
        db.get_setting("repair_mode", "0"),
        db.get_all_players(),
        db.get_matches_by_filter("all"),
        db.get_all_admins(),
        db.get_all_tasks(),
        db.get_all_classes(),
        _teams(),
    )

    lines = [
        box("🗄️ وضعیت کامل سیستم"),
        "⏱️ `" + now_shamsi() + "`",
        "",
        separator("🚦 وضعیت‌ها"),
        "📡 سیستم: " + status_map.get(status, status),
        "🕐 ساعت کاری: " + ("🟢 باز" if wh == "1" else "🔴 بسته"),
        "🗄️ دیتابیس (دستی): " + ("🟢 فعال" if db_manual == "1" else "⚠️ غیرفعال"),
        "🔧 حالت تعمیر: " + ("🔧 فعال" if repair == "1" else "✅ عادی"),
        "",
        separator("💾 حافظه و اجرا"),
        "🧠 مصرف حافظه: " + mem_txt,
        "🐍 نسخه پایتون: `" + platform.python_version() + "`",
        "",
        separator("📊 آمار دیتا"),
        "🏫 کلاس‌ها: `" + str(len(all_classes)) + "`",
        "👤 بازیکنان: `" + str(len(all_players)) + "`",
        "♟️ مسابقات: `" + str(len(all_matches)) + "`",
        "👥 مدیران (کل): `" + str(len(all_admins)) + "`",
        "📋 وظایف: `" + str(len(all_tasks)) + "`",
        "🏆 تیم‌ها: `" + str(len(all_teams)) + "`",
        separator(),
    ]
    return "\n".join(lines)


# ─── ورودی گفتگوی مخابرات از طریق کلمه (برای comms_conv) ────────
async def kw_announce_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from telegram.ext import ConversationHandler
    from config import ST_ANNOUNCEMENT_TEXT
    if update.effective_user.id != PISHVA_ID:
        return ConversationHandler.END
    await update.message.reply_text("📢 متن بیانیه را وارد کنید:")
    return ST_ANNOUNCEMENT_TEXT


async def kw_news_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from telegram.ext import ConversationHandler
    from config import ST_NEWS_TEXT
    if update.effective_user.id != PISHVA_ID:
        return ConversationHandler.END
    await update.message.reply_text("📰 متن خبر فوری را وارد کنید:")
    return ST_NEWS_TEXT
