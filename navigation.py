import logging
import asyncio
from telegram import Update
from telegram.ext import ContextTypes
import database as db
import keyboards as kb
from helpers import (safe_edit_message_text, now_shamsi, box, separator, pishva_display,
                     check_status_gate, get_user_role, power_bar, progress_bar,
                     warning_bar_player, check_perm)
from config import PISHVA_ID, ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER

logger = logging.getLogger(__name__)


async def get_main_markup(user_id: int):
    if user_id == PISHVA_ID:
        return kb.kb_pishva_main()
    admin = await db.get_admin(user_id)
    if admin and admin["is_active"]:
        import servers
        if servers.is_owner(user_id):
            return kb.kb_owner_main()
        if admin["role"] == ROLE_TOURNAMENT_MANAGER:
            return kb.kb_tournament_manager_main()
        else:
            return kb.kb_security_manager_main()
    return kb.kb_role_select()


async def back_main(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    markup = await get_main_markup(uid)
    pname = await pishva_display()

    if uid == PISHVA_ID:
        text = f"👑 پنل فرماندهی {pname}\n\n🛰️ `{now_shamsi()}`"
    else:
        admin = await db.get_admin(uid)
        name = admin["display_name"] or admin["full_name"] if admin else "ادمین"
        text = f"📋 منوی اصلی — {name}\n\n🛰️ `{now_shamsi()}`"

    await safe_edit_message_text(query, text, reply_markup=markup, parse_mode="Markdown")


async def menu_tournament(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await check_status_gate(query):
        return
    await query.answer()
    await safe_edit_message_text(query, 
        f"{box('🏅 مدیریت تورنمنت')}\n\n📌 بخش موردنظر را انتخاب کنید:",
        reply_markup=kb.kb_tournament_menu(),
        parse_mode="Markdown"
    )


async def menu_players(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await check_status_gate(query, "view_players"):
        return
    if await check_perm(query, "view_players"):
        return
    await query.answer()
    role = await get_user_role(query.from_user.id)
    await safe_edit_message_text(query, 
        f"{box('👤 مدیریت بازیکنان')}\n\n📌 بخش موردنظر را انتخاب کنید:",
        reply_markup=kb.kb_players_menu(role),
        parse_mode="Markdown"
    )


async def menu_matches(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await check_status_gate(query, "match_management"):
        return
    if await check_perm(query, "match_management"):
        return
    await query.answer()
    await safe_edit_message_text(query, 
        f"{box('♟️ مدیریت مسابقات')}\n\n📌 بخش موردنظر را انتخاب کنید:",
        reply_markup=kb.kb_matches_menu(),
        parse_mode="Markdown"
    )


async def menu_pishva(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != PISHVA_ID:
        await query.answer("⛔ این بخش فقط برای مدیر ارشد است.", show_alert=True)
        return
    await query.answer()
    try:
        # FIX (کندیِ حسِ‌شده روی دکمه‌ی «پنل مدیر ارشد»): این دو تا await
        # قبلاً پشتِ‌سرِهم (نه موازی) اجرا می‌شدن — یعنی با کشِ سرد، دو
        # رفت‌وبرگشتِ کاملِ شبکه‌ای به Turso، یکی بعد از اون یکی. با
        # asyncio.gather هر دو هم‌زمان می‌رن، یعنی زمانِ کل تقریباً برابرِ
        # کندترینِ تک‌کوئری می‌شه، نه مجموعِ هر دو.
        pname, status = await asyncio.gather(
            pishva_display(),
            db.get_setting("system_status", "normal"),
        )
        status_map = {"normal": "🟢 نرمال", "bad": "🟡 بد", "danger": "🔴 خطرناک", "aps": "🪽 APS"}
        await safe_edit_message_text(query,
            f"{box(f'👑 پنل مدیر ارشد — {pname}')}\n\n"
            f"🚦 وضعیت فعلی: {status_map.get(status, status)}\n"
            f"⏱️ `{now_shamsi()}`\n\n"
            f"📌 بخش موردنظر را انتخاب کنید:",
            reply_markup=kb.kb_pishva_panel(),
            parse_mode="Markdown"
        )
    except Exception as e:
        # این پنل قبلاً در سکوت خراب می‌شد (کوئری DB خطا می‌داد، پیام هم ادیت
        # نمی‌شد، کاربر هم هیچ خطایی نمی‌دید — فقط دکمه لود می‌شد و هیچی باز نمی‌شد).
        # حالا خطا لاگ می‌شه و یه نسخه‌ی حداقلی از پنل (بدون وضعیت زنده) باز می‌شه
        # تا مدیر ارشد گیر نکنه.
        logger.error(f"[MENU-PISHVA-DEBUG] failed to build/edit پنل مدیر ارشد: {e}", exc_info=True)
        try:
            await safe_edit_message_text(query,
                f"{box('👑 پنل مدیر ارشد')}\n\n📌 بخش موردنظر را انتخاب کنید:",
                reply_markup=kb.kb_pishva_panel(),
                parse_mode="Markdown"
            )
        except Exception as e2:
            logger.error(f"[MENU-PISHVA-DEBUG] fallback edit also failed: {e2}", exc_info=True)
            try:
                await query.message.reply_text(
                    "⚠️ خطا در باز کردن پنل مدیر ارشد. لطفاً دوباره تلاش کنید یا /start را بزنید."
                )
            except Exception:
                pass


async def pishva_panel_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """جابه‌جاییِ صفحه‌های پنلِ مدیر ارشد. عمداً هیچ کوئریِ دیتابیسی یا
    await ای غیر از خودِ ادیت نداره — فقط کیبورد عوض می‌شه — تا رفتن بین
    صفحه‌ها کاملاً آنی حس بشه، نه اینکه دوباره منتظرِ یک رفت‌وبرگشتِ
    شبکه‌ای بمونیم."""
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ این بخش فقط برای مدیر ارشد است.", show_alert=True)
        return
    await query.answer()
    try:
        page = int(query.data[len("pishva_panel_p"):])
    except ValueError:
        page = 0
    try:
        await query.edit_message_reply_markup(reply_markup=kb.kb_pishva_panel(page))
    except Exception:
        pass


async def menu_comms(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await check_status_gate(query, "communications"):
        return
    if await check_perm(query, "communications"):
        return
    uid = query.from_user.id
    comms_on = await db.get_setting("communications_enabled", "1")
    if comms_on != "1" and uid != PISHVA_ID:
        await query.answer("📡 سیستم مخابرات غیرفعال است.", show_alert=True)
        return
    await query.answer()
    if uid == PISHVA_ID:
        markup = kb.kb_comms_pishva()
    else:
        markup = kb.kb_comms_admin()
    await safe_edit_message_text(query, 
        f"{box('📡 مخابرات')}\n\n📌 بخش موردنظر را انتخاب کنید:",
        reply_markup=markup,
        parse_mode="Markdown"
    )


async def menu_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from help_center import help_main
    query = update.callback_query
    help_on = await db.get_setting("help_enabled", "1")
    if help_on != "1":
        await query.answer("❓ راهنما غیرفعال است.", show_alert=True)
        return
    await help_main(update, ctx)


async def menu_admins(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد می‌تواند مدیران را مدیریت کند.", show_alert=True)
        return
    await query.answer()
    admins = await db.get_all_admins()
    if not admins:
        await safe_edit_message_text(query, 
            f"{box('👥 مدیریت مدیران')}\n\n❗ هیچ مدیری ثبت نشده است.",
            reply_markup=kb.kb_back("main"),
            parse_mode="Markdown"
        )
        return
    await safe_edit_message_text(query, 
        f"{box('👥 مدیریت مدیران')}\n\nیک مدیر انتخاب کنید:",
        reply_markup=kb.kb_admin_list(admins),
        parse_mode="Markdown"
    )


async def menu_tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await check_status_gate(query):
        return
    await query.answer()
    uid = query.from_user.id
    if uid == PISHVA_ID:
        markup = kb.kb_tasks_pishva()
    else:
        markup = kb.kb_tasks_admin()
    await safe_edit_message_text(query, 
        f"{box('📋 مدیریت وظایف')}\n\n📌 بخش موردنظر را انتخاب کنید:",
        reply_markup=markup,
        parse_mode="Markdown"
    )


async def menu_feedback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if uid == PISHVA_ID:
        markup = kb.kb_feedback_pishva()
        title = "📋 انتقادات و پیشنهادات دریافتی"
    else:
        markup = kb.kb_feedback_menu()
        title = "💡 انتقادات و پیشنهادات"
    await safe_edit_message_text(query, 
        f"{box(title)}\n\n📌 بخش موردنظر را انتخاب کنید:",
        reply_markup=markup,
        parse_mode="Markdown"
    )


# ─── Back shortcuts ───────────────────────────────────────────
async def back_tournament(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, 
        f"{box('🏅 مدیریت تورنمنت')}\n\n📌 بخش موردنظر را انتخاب کنید:",
        reply_markup=kb.kb_tournament_menu(),
        parse_mode="Markdown"
    )


async def back_players(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    role = await get_user_role(query.from_user.id)
    await safe_edit_message_text(query, 
        f"{box('👤 مدیریت بازیکنان')}\n\n📌 بخش موردنظر را انتخاب کنید:",
        reply_markup=kb.kb_players_menu(role),
        parse_mode="Markdown"
    )


async def back_matches(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, 
        f"{box('♟️ مدیریت مسابقات')}\n\n📌 بخش موردنظر را انتخاب کنید:",
        reply_markup=kb.kb_matches_menu(),
        parse_mode="Markdown"
    )


async def back_class_manage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, 
        f"{box('🏫 مدیریت کلاس‌ها')}\n\n📌 بخش موردنظر را انتخاب کنید:",
        reply_markup=kb.kb_class_manage(is_pishva=(query.from_user.id == PISHVA_ID)),
        parse_mode="Markdown"
    )


async def back_player_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    players = await db.get_all_players()
    await safe_edit_message_text(query, 
        f"{box('👤 لیست بازیکنان')}\n\n👥 تعداد کل: `{len(players)}`\n\nیک بازیکن انتخاب کنید:",
        reply_markup=kb.kb_player_list(players),
        parse_mode="Markdown"
    )


async def back_teams_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, 
        f"{box('🏆 بخش تیم‌ها')}\n\n📌 بخش موردنظر را انتخاب کنید:",
        reply_markup=kb.kb_teams_menu(),
        parse_mode="Markdown"
    )


async def refresh_dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """بازسازی داشبورد با داده‌های جدید"""
    query = update.callback_query
    await query.answer("🔄 در حال به‌روزرسانی...")
    from auth import show_pishva_welcome, show_admin_welcome
    import database as db_r
    uid = query.from_user.id
    from config import PISHVA_ID as PID
    if uid == PID:
        await show_pishva_welcome(update, ctx)
    else:
        admin = await db_r.get_admin(uid)
        if admin:
            await show_admin_welcome(update, ctx, admin)
