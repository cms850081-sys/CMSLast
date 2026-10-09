"""
ai_manage.py — بخش «مدیریت دستیار» توی پنل مدیر ارشد.

شامل:
  - منوی اصلی (سوابق چت، اختیارات دستیار، روشن/خاموش کلی، خاموشی برای ادمین خاص)
  - «اختیارات دستیار»: روشن/خاموش کردنِ دسته‌های ابزار دستیار هوشمند
    (ai_tools.AI_PERMISSION_CATEGORIES) — همون چیزی که ai_tools.dispatch()
    واقعاً موقعِ اجرای هر ابزار چک می‌کنه، پس خاموش‌کردن از اینجا واقعاً
    جلوی دستیار رو می‌گیره، نه فقط ظاهریه.
  - «خاموشی برای ادمین خاص»: همون فیلدِ permissions.ai_access روی خودِ
    ادمین رو عوض می‌کنه — دقیقاً همون چیزی که پنل «دسترسی‌های ادمین»
    (kb_admin_permissions) و ابزار toggle_admin_ai_access هم می‌خونن/می‌نویسن،
    پس هرجا عوضش کنی، همه‌جا هماهنگه.
"""
from telegram import Update
from telegram.ext import ContextTypes

import database as db
import keyboards as kb
import ai_tools
from helpers import safe_edit_message_text, box
from config import PISHVA_ID


async def ai_manage_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """FIX: این منو هم از پنل اصلی/خوش‌آمدگویی باز می‌شه (callback
    "ai_manage_menu_main") و هم از پنل مدیر ارشد (callback "ai_manage_menu")،
    و «بازگشت» زیرمنوها هم دوباره میاد اینجا (callback
    "ai_manage_menu_return") — پس فقط وقتی callback_data صراحتاً یکی از دو
    مبدأ رو مشخص می‌کنه مقصدِ «بازگشت» رو ست/عوض می‌کنیم؛ در حالت
    "_return" همون چیزی که قبلاً ست شده (مبدأ واقعی که کاربر اول از آنجا
    وارد شده) دست‌نخورده می‌مونه، وگرنه هر بار برمی‌گشت به پنل مدیر ارشد
    حتی اگه از پنل اصلی باز شده باشه."""
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    if query.data == "ai_manage_menu_main":
        ctx.user_data["ai_manage_back"] = "back_main"
    elif query.data == "ai_manage_menu":
        ctx.user_data["ai_manage_back"] = "menu_pishva"
    elif query.data == "ai_manage_menu_hub":
        ctx.user_data["ai_manage_back"] = "ai_more_home"
    back_target = ctx.user_data.get("ai_manage_back", "menu_pishva")
    ai_online = await db.get_setting("ai_online", "1")
    await safe_edit_message_text(query,
        f"{box('🧑‍💻 مدیریت دستیار')}\n\n📌 یک گزینه را انتخاب کنید:",
        reply_markup=kb.kb_ai_manage_menu(ai_online, back_target),
        parse_mode="Markdown"
    )


async def ai_manage_toggle_online(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """همون تنظیمِ ai_online که توی «تنظیمات ربات» هم هست — یک دکمه‌ی
    دیگه برای همون کلید، اینجا هم موجوده، همیشه هم‌سو."""
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    current = await db.get_setting("ai_online", "1")
    new_val = "0" if current == "1" else "1"
    await db.set_setting("ai_online", new_val)
    await db.log_action(PISHVA_ID, "toggle_setting", f"ai_online -> {new_val}")
    back_target = ctx.user_data.get("ai_manage_back", "menu_pishva")
    await safe_edit_message_text(query,
        f"{box('🧑‍💻 مدیریت دستیار')}\n\n📌 یک گزینه را انتخاب کنید:",
        reply_markup=kb.kb_ai_manage_menu(new_val, back_target),
        parse_mode="Markdown"
    )


async def ai_perms_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    states = await ai_tools.get_category_states()
    await safe_edit_message_text(query,
        f"{box('🛠️ اختیارات دستیار')}\n\n"
        f"📌 هر دسته که خاموش باشه، دستیار هوشمند دیگه نمی‌تونه هیچ‌کدوم از "
        f"ابزارهای اون دسته رو اجرا کنه (چه توسط شما، چه هر مدیر دیگه‌ای):",
        reply_markup=kb.kb_ai_perms_menu(states),
        parse_mode="Markdown"
    )


async def ai_perms_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    category = query.data[len("aiperm_toggle_"):]
    if category not in ai_tools.CATEGORY_LABELS:
        await query.answer("❌ دسته‌ی نامعتبر.", show_alert=True)
        return
    states = await ai_tools.get_category_states()
    new_enabled = states.get(category, "1") != "1"
    await ai_tools.set_category_state(category, new_enabled)
    await db.log_action(PISHVA_ID, "toggle_ai_category",
                         f"{ai_tools.CATEGORY_LABELS[category]} -> {'فعال' if new_enabled else 'غیرفعال'}")
    await query.answer("✅ اعمال شد.")
    new_states = await ai_tools.get_category_states()
    await safe_edit_message_text(query,
        f"{box('🛠️ اختیارات دستیار')}\n\n"
        f"📌 هر دسته که خاموش باشه، دستیار هوشمند دیگه نمی‌تونه هیچ‌کدوم از "
        f"ابزارهای اون دسته رو اجرا کنه (چه توسط شما، چه هر مدیر دیگه‌ای):",
        reply_markup=kb.kb_ai_perms_menu(new_states),
        parse_mode="Markdown"
    )


async def ai_admtg_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """لیستِ مدیران برای خاموش/روشن‌کردنِ دسترسیِ هوش مصنوعیِ هرکدوم جداگانه."""
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    admins = await db.get_all_admins()
    if not admins:
        await safe_edit_message_text(query,
            f"{box('🔕 خاموشی هوش مصنوعی برای ادمین')}\n\n❗ هیچ مدیری ثبت نشده.",
            reply_markup=kb.kb_back("ai_manage_return"), parse_mode="Markdown")
        return
    await safe_edit_message_text(query,
        f"{box('🔕 خاموشی هوش مصنوعی برای ادمین')}\n\n"
        f"🟢 یعنی دستیار براش روشنه، 🔴 یعنی خاموشه.\nیک مدیر را انتخاب کنید:",
        reply_markup=kb.kb_ai_admin_toggle_list(admins),
        parse_mode="Markdown"
    )


async def ai_admtg_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    tid = int(query.data.split("_")[-1])
    admin = await db.get_admin(tid)
    if not admin:
        await query.answer("مدیر یافت نشد.", show_alert=True)
        return
    is_on = await db.get_admin_permission(tid, "ai_access")
    _name = admin["display_name"] or admin["full_name"]
    await safe_edit_message_text(query,
        f"{box('🔕 خاموشی هوش مصنوعی برای ادمین')}\n\n"
        f"👤 *{_name}*\n"
        f"وضعیتِ فعلی: {'🟢 روشن' if is_on else '🔴 خاموش'}\n\n"
        f"دستیار هوشمند برای این مدیر باید روشن باشه یا خاموش؟",
        reply_markup=kb.kb_ai_admin_toggle_pick(tid, is_on),
        parse_mode="Markdown"
    )


async def ai_admtg_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """FIX: از همون db.set_admin_permission استفاده می‌کنه که پنلِ
    «دسترسی‌های ادمین» (perm_{tid}_ai_access) و ابزارِ toggle_admin_ai_access
    هم ازش استفاده می‌کنن — یعنی هرجا عوضش کنی، بقیه‌ی جاها هم فوراً می‌بینن."""
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    parts = query.data.split("_")
    onoff = parts[-1]
    tid = int(parts[-2])
    admin = await db.get_admin(tid)
    if not admin:
        await query.answer("مدیر یافت نشد.", show_alert=True)
        return
    value = onoff == "on"
    await db.set_admin_permission(tid, "ai_access", value)
    await db.log_action(PISHVA_ID, "toggle_perm", f"ai_access: {value}", tid)
    try:
        await ctx.bot.send_message(chat_id=tid,
            text=f"{'⬆️' if value else '⬇️'} دسترسی *دستیار هوشمند* {'فعال ✅' if value else 'غیرفعال ❌'} شد.",
            parse_mode="Markdown")
    except Exception:
        pass
    await query.answer("✅ اعمال شد.")
    _name = admin["display_name"] or admin["full_name"]
    await safe_edit_message_text(query,
        f"{box('🔕 خاموشی هوش مصنوعی برای ادمین')}\n\n"
        f"👤 *{_name}*\n"
        f"وضعیتِ فعلی: {'🟢 روشن' if value else '🔴 خاموش'}\n\n"
        f"دستیار هوشمند برای این مدیر باید روشن باشه یا خاموش؟",
        reply_markup=kb.kb_ai_admin_toggle_pick(tid, value),
        parse_mode="Markdown"
    )
