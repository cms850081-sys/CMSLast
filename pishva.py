from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.error import BadRequest
import asyncio
import logging
import database as db
import keyboards as kb
from helpers import (safe_edit_message_text, box, separator, now_shamsi, broadcast_to_admins,
    notify_pishva, pishva_display, log_line, safe_reply_text, escape_md_legacy,
    format_log_entry, parse_hour_range)
from config import (PISHVA_ID, STATUS_NORMAL, STATUS_BAD, STATUS_DANGER, STATUS_APS,
    ST_TOURNAMENT_NAME, ST_PISHVA_NAME_CHANGE, ST_ADMIN_NAME_CHANGE,
    ST_NEW_YEAR_CONFIRM, ST_NEW_YEAR_PASSWORD, ST_REPAIR_REASON,
    ST_GROUP_ID, ST_CHANNEL_ID, ST_UPDATE_VERSION, ST_UPDATE_DESC,
    ST_RESTORE_FILE, ST_CHESS_AI_BROADCAST_TEXT, NEW_YEAR_PASSWORD,
    ST_LOGS_SEARCH_TERM, ST_LOGS_SEARCH_RANGE)
from telegram.ext import ConversationHandler
import io

logger = logging.getLogger(__name__)

# ─── Status Management ────────────────────────────────────────
async def pishva_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    current = await db.get_setting("system_status", STATUS_NORMAL)
    status_labels = {STATUS_NORMAL: "🟢 نرمال", STATUS_BAD: "🟡 بد",
        STATUS_DANGER: "🔴 خطرناک", STATUS_APS: "🪽 APS"}
    await safe_edit_message_text(query, 
        f"{box('🚦 مدیریت وضعیت سیستم')}\n\n"
        f"⚡ وضعیت فعلی: *{status_labels.get(current, current)}*\n\n"
        f"📌 وضعیت جدید را انتخاب کنید:",
        reply_markup=kb.kb_status_select(current),
        parse_mode="Markdown"
    )

async def set_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    new_status = query.data.split("_")[-1]
    await db.set_setting("system_status", new_status)
    await db.log_action(PISHVA_ID, "set_status", f"تغییر وضعیت به: {new_status}")
    status_labels = {STATUS_NORMAL: "🟢 نرمال", STATUS_BAD: "🟡 بد",
        STATUS_DANGER: "🔴 خطرناک", STATUS_APS: "🪽 APS"}
    pname = await pishva_display()
    ts = now_shamsi()
    if new_status == STATUS_DANGER:
        notif = (
            f"{box('🔴 هشدار — وضعیت بحرانی')}\n\n"
            f"⚠️ سیستم وارد وضعیت خطرناک شد.\n"
            f"🛡️ پروتکل امنیتی فعال است.\n"
            f"🔒 دسترسی شما موقتاً معلق شد.\n"
            f"⏱️ `{ts}`\n\n"
            f"منتظر دستور {pname} باشید."
        )
        await broadcast_to_admins(ctx.bot, notif)
    elif new_status == STATUS_APS:
        notif = (
            f"{box('🪽 حالت امنیتی APS')}\n\n"
            f"🔐 امنیت به سیستم APS واگذار شده.\n"
            f"🔒 دسترسی همه قطع شده است.\n"
            f"⏱️ `{ts}`"
        )
        await broadcast_to_admins(ctx.bot, notif)
    elif new_status == STATUS_NORMAL:
        notif = (
            f"🟢 سیستم به وضعیت نرمال بازگشت.\n"
            f"✅ دسترسی شما فعال است.\n"
            f"⏱️ `{ts}`"
        )
        await broadcast_to_admins(ctx.bot, notif)
    await safe_edit_message_text(query, 
        f"✅ وضعیت به *{status_labels.get(new_status, new_status)}* تغییر یافت.",
        reply_markup=kb.kb_back("pishva_status"),
        parse_mode="Markdown"
    )

# ─── Settings ─────────────────────────────────────────────────
async def pishva_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    keys = ["notifications_enabled", "communications_enabled", "help_enabled",
        "match_registration_enabled", "admin_login_enabled", "bot_active_for_admins",
        "team_mode_enabled", "team_registration_enabled", "managers_can_create_teams",
        "admin_dashboard_enabled", "ai_online", "live_chess_enabled", "hub_enabled",
        "bug_report_to_pishva_enabled", "principal_panel_enabled", "admin_webpanel_enabled",
        "admin_direct_kick_enabled", "top_players_mode",
        "scan_enabled", "scan_default_mode"]
    # FIX: قبلاً این ۱۵ تا db.get_setting با asyncio.gather «هم‌زمان» صدا زده
    # می‌شدن، ولی چون Turso دور و کندِ‌رفت‌وبرگشته، هم‌زمانیِ سطحِ پایتون به
    # یک رفت‌وبرگشتِ شبکه‌ی واحد ختم نمی‌شد — چند موجِ رفت‌وبرگشتِ جدا
    # پشتِ‌سرِهم پیش می‌اومد (دقیقاً همون چیزی که کندیِ /start رو هم باعث
    # می‌شد). حالا با get_settings_bulk، کلیدهایی که در کش نیستن با یک
    # کوئریِ IN(...) واحد گرفته می‌شن.
    settings = await db.get_settings_bulk(keys, "1")
    await safe_edit_message_text(query, 
        f"{box('⚙️ تنظیمات ربات')}\n\n📌 گزینه موردنظر را تغییر دهید:",
        reply_markup=kb.kb_pishva_settings_simple(settings),
        parse_mode="Markdown"
    )

async def toggle_setting(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    key_map = {
        "setting_notifications": "notifications_enabled",
        "setting_communications": "communications_enabled",
        "setting_help": "help_enabled",
        "setting_match_reg": "match_registration_enabled",
        "setting_admin_login": "admin_login_enabled",
        "setting_bot_active": "bot_active_for_admins",
        "setting_team_mode": "team_mode_enabled",
        "setting_team_reg": "team_registration_enabled",
        "setting_mgr_team": "managers_can_create_teams",
        "setting_admin_dashboard": "admin_dashboard_enabled",
        "setting_ai_online": "ai_online",
        "setting_live_chess": "live_chess_enabled",
        "setting_hub": "hub_enabled",
        "setting_bug_report": "bug_report_to_pishva_enabled",
        "setting_principal_panel": "principal_panel_enabled",
        "setting_admin_webpanel": "admin_webpanel_enabled",
        "setting_admin_direct_kick": "admin_direct_kick_enabled",
        "setting_top_players_mode": "top_players_mode",
        "setting_scan_enabled": "scan_enabled",
        "setting_scan_mode": "scan_default_mode",
    }
    key = key_map.get(query.data)
    if key:
        if key == "top_players_mode":
            # این یکی دوحالته‌ی "1"/"0" نیست — بین "auto" و "manual" سوییچ می‌کنه.
            current = await db.get_setting(key, "auto")
            new_val = "manual" if current != "manual" else "auto"
        elif key == "scan_default_mode":
            # دوحالته‌ی "direct" / "approval" (پیش‌فرض: با تأیید)
            current = await db.get_setting(key, "approval")
            new_val = "approval" if current == "direct" else "direct"
        else:
            current = await db.get_setting(key, "1")
            new_val = "0" if current == "1" else "1"
        await db.set_setting(key, new_val)
        await db.log_action(PISHVA_ID, "toggle_setting", f"{key} -> {new_val}")
        if key == "hub_enabled":
            # دکمه‌ی «پنل من» کنارِ چتِ همه‌ی مدیران باید فوراً هم‌گام بشه.
            try:
                from hub import sync_menu_buttons
                await sync_menu_buttons(ctx.bot)
            except Exception:
                logger.exception("hub menu-button resync after hub_enabled toggle failed")
    else:
        new_val = None
    keys = ["notifications_enabled", "communications_enabled", "help_enabled",
        "match_registration_enabled", "admin_login_enabled", "bot_active_for_admins",
        "team_mode_enabled", "team_registration_enabled", "managers_can_create_teams",
        "admin_dashboard_enabled", "ai_online", "live_chess_enabled", "hub_enabled",
        "bug_report_to_pishva_enabled", "principal_panel_enabled", "admin_webpanel_enabled",
        "admin_direct_kick_enabled", "top_players_mode",
        "scan_enabled", "scan_default_mode"]
    # FIX (کندیِ وحشتناکِ هر دکمه‌ی تنظیمات): این‌جا قبلاً، بعد از هر تاگل،
    # ۱۵ تا db.get_setting جداگانه صدا زده می‌شدن. حتی با asyncio.gather،
    # چون Turso دور و کندِ‌رفت‌وبرگشته، هم‌زمانیِ سطحِ پایتون به یک
    # رفت‌وبرگشتِ شبکه‌ی واحد ختم نمی‌شد. حالا با get_settings_bulk یک
    # کوئریِ IN(...) واحد می‌گیرتشون، و چون مقدارِ تنظیمِ تازه‌تاگل‌شده رو
    # همین بالا داریم، دیگه لازم نیست دوباره از دیتابیس بخونیمش.
    settings = await db.get_settings_bulk(keys, "1")
    if key:
        settings[key] = new_val
    await safe_edit_message_text(query, 
        f"{box('⚙️ تنظیمات ربات')}\n\n📌 گزینه موردنظر را تغییر دهید:",
        reply_markup=kb.kb_pishva_settings_simple(settings),
        parse_mode="Markdown"
    )

# ─── تنظیمِ آستانه‌ی هشدار حذف مشکوک ───────────────────────────
async def pishva_suspicious_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    # FIX: این await ها قبلاً پشتِ‌سرِهم بودن؛ حالا موازی خونده می‌شن.
    enabled, threshold, window, auto_enabled, auto_action = await asyncio.gather(
        db.get_setting("suspicious_alert_enabled", "1"),
        db.get_setting("suspicious_deletion_threshold", "5"),
        db.get_setting("suspicious_deletion_window_minutes", "10"),
        db.get_setting("suspicious_auto_enabled", "0"),
        db.get_setting("suspicious_auto_action", "notify_only"),
    )
    status = "🟢 فعال" if enabled == "1" else "🔴 غیرفعال"
    import anomaly_alerts
    auto_status = "🟢 فعال" if auto_enabled == "1" else "🔴 غیرفعال"
    auto_action_label = anomaly_alerts.AUTO_ACTION_LABELS.get(auto_action, auto_action)
    text = (
        f"{box('🚨 هشدار حذف مشکوک')}\n\n"
        f"📊 وضعیت: {status}\n"
        f"🔢 آستانه: `{threshold}` حذف\n"
        f"⏱️ بازه: `{window}` دقیقه\n\n"
        f"🤖 تصمیم‌گیری خودکار: {auto_status}\n"
        + (f"⚙️ اقدامِ خودکار: {auto_action_label}\n\n" if auto_enabled == "1" else "\n")
        + f"💡 اگه یک ادمین توی این بازه به تعداد این آستانه یا بیشتر "
        f"عملیات مخرب (اخراج/تعلیق/حذف بازیکن، حذف مسابقه، حذف تیم، "
        f"حذف تورنمنت) انجام بده، فوراً به شما هشدار داده می‌شه — مگر "
        f"اینکه تصمیم‌گیریِ خودکار فعال باشه، که در اون صورت اقدامِ "
        f"انتخابی‌تون خودش انجام می‌شه و فقط بهتون اطلاع داده می‌شه."
    )
    await safe_edit_message_text(query, text,
        reply_markup=kb.kb_suspicious_settings(enabled, threshold, window, auto_enabled, auto_action),
        parse_mode="Markdown")

async def sadel_auto_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    current = await db.get_setting("suspicious_auto_enabled", "0")
    new_val = "0" if current == "1" else "1"
    await db.set_setting("suspicious_auto_enabled", new_val)
    await db.log_action(PISHVA_ID, "toggle_setting", f"suspicious_auto_enabled -> {new_val}")
    await pishva_suspicious_settings(update, ctx)

async def sadel_auto_action_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    current = await db.get_setting("suspicious_auto_action", "notify_only")
    await safe_edit_message_text(query,
        "⚙️ اقدامی که به‌صورت خودکار انجام شود را انتخاب کنید:",
        reply_markup=kb.kb_suspicious_auto_action(current))

async def sadel_auto_action_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    action = query.data[len("sadel_auto_set_"):]
    await db.set_setting("suspicious_auto_action", action)
    await db.log_action(PISHVA_ID, "toggle_setting", f"suspicious_auto_action -> {action}")
    await query.answer("✅ ثبت شد", show_alert=True)
    await pishva_suspicious_settings(update, ctx)

async def sadel_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    current = await db.get_setting("suspicious_alert_enabled", "1")
    new_val = "0" if current == "1" else "1"
    await db.set_setting("suspicious_alert_enabled", new_val)
    await db.log_action(PISHVA_ID, "toggle_setting", f"suspicious_alert_enabled -> {new_val}")
    await pishva_suspicious_settings(update, ctx)

async def sadel_threshold_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query, 
        "🔢 حداکثر تعداد حذف مجاز (در بازه‌ی زمانی) را انتخاب کنید:",
        reply_markup=kb.kb_suspicious_threshold())

async def sadel_set_threshold(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    n = int(query.data.split("_")[-1])
    await db.set_setting("suspicious_deletion_threshold", str(n))
    await db.log_action(PISHVA_ID, "toggle_setting", f"suspicious_deletion_threshold -> {n}")
    await query.answer(f"✅ آستانه به {n} حذف تغییر یافت", show_alert=True)
    await pishva_suspicious_settings(update, ctx)

async def sadel_window_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query, 
        "⏱️ بازه‌ی زمانیِ شمارش حذف‌ها را انتخاب کنید:",
        reply_markup=kb.kb_suspicious_window())

async def sadel_set_window(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    n = int(query.data.split("_")[-1])
    await db.set_setting("suspicious_deletion_window_minutes", str(n))
    await db.log_action(PISHVA_ID, "toggle_setting", f"suspicious_deletion_window_minutes -> {n}")
    await query.answer(f"✅ بازه به {n} دقیقه تغییر یافت", show_alert=True)
    await pishva_suspicious_settings(update, ctx)

# ─── تنظیمِ اختصاصیِ هشدار حذف مشکوک برای هر ادمین ──────────────
_SADEL_ADMIN_PAGE_SIZE = 8


async def sadel_admin_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    try:
        page = int(query.data[len("sadel_admins_p"):])
    except ValueError:
        page = 0

    all_admins = await db.get_all_admins()
    total_pages = max(1, (len(all_admins) + _SADEL_ADMIN_PAGE_SIZE - 1) // _SADEL_ADMIN_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * _SADEL_ADMIN_PAGE_SIZE
    admins_page = all_admins[start:start + _SADEL_ADMIN_PAGE_SIZE]

    import anomaly_alerts
    override_flags = await asyncio.gather(*(
        asyncio.gather(*(
            anomaly_alerts.get_admin_override(a["telegram_id"], field)
            for field in ("enabled", "threshold", "window")
        ))
        for a in admins_page
    ))
    has_override = {
        a["telegram_id"] for a, flags in zip(admins_page, override_flags) if any(flags)
    }

    text = (
        f"{box('👤 تنظیمِ جداگانه — هشدار حذف مشکوک')}\n\n"
        f"📌 ادمینی که می‌خواید براش تنظیمِ اختصاصی بذارید رو انتخاب کنید.\n"
        f"⚙️ یعنی روی این ادمین یک یا چند تنظیمِ اختصاصی فعاله."
    )
    if not admins_page:
        text += "\n\n(هنوز ادمینی ثبت نشده)"
    await safe_edit_message_text(query, text,
        reply_markup=kb.kb_suspicious_admin_list(admins_page, page, total_pages, has_override),
        parse_mode="Markdown")


async def _render_sadel_admin_panel(query, admin_id: int):
    import anomaly_alerts
    admin = await db.get_admin(admin_id)
    admin_name = (admin["display_name"] or admin["full_name"]) if admin else str(admin_id)
    enabled_ov = await anomaly_alerts.get_admin_override(admin_id, "enabled")
    threshold_ov = await anomaly_alerts.get_admin_override(admin_id, "threshold")
    window_ov = await anomaly_alerts.get_admin_override(admin_id, "window")
    enabled, threshold, window_min = await anomaly_alerts.get_effective_settings(admin_id)
    status = "🟢 فعال" if enabled == "1" else "🔴 غیرفعال"
    text = (
        f"{box('👤 تنظیمِ اختصاصی — ' + admin_name)}\n\n"
        f"📊 وضعیتِ مؤثر: {status}\n"
        f"🔢 آستانهٔ مؤثر: `{threshold}` حذف\n"
        f"⏱️ بازهٔ مؤثر: `{window_min}` دقیقه\n\n"
        f"💡 هر گزینه‌ای که «↩️ پیروی از کلی» باشه، همون تنظیمِ عمومی رو "
        f"می‌گیره؛ وگرنه مقدارِ اختصاصیِ همین ادمین اعمال می‌شه."
    )
    await safe_edit_message_text(query, text,
        reply_markup=kb.kb_suspicious_admin_panel(admin_id, enabled_ov, threshold_ov, window_ov),
        parse_mode="Markdown")


async def sadel_admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    admin_id = int(query.data[len("sadel_admin_"):])
    await _render_sadel_admin_panel(query, admin_id)


async def sadel_admin_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    admin_id = int(query.data[len("sadel_admin_toggle_"):])
    import anomaly_alerts
    current = await anomaly_alerts.get_admin_override(admin_id, "enabled")
    # چرخه: پیروی از کلی → روشن (اختصاصی) → خاموش (اختصاصی) → پیروی از کلی
    nxt = "1" if current is None else ("0" if current == "1" else None)
    await anomaly_alerts.set_admin_override(admin_id, "enabled", nxt)
    await db.log_action(PISHVA_ID, "toggle_setting",
        f"sadel_admin_enabled_{admin_id} -> {nxt if nxt is not None else 'inherit'}")
    await query.answer("✅ ثبت شد")
    await _render_sadel_admin_panel(query, admin_id)


async def sadel_admin_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    admin_id = int(query.data[len("sadel_admin_reset_"):])
    import anomaly_alerts
    for field in ("enabled", "threshold", "window"):
        await anomaly_alerts.set_admin_override(admin_id, field, None)
    await db.log_action(PISHVA_ID, "toggle_setting", f"sadel_admin_reset_{admin_id}")
    await query.answer("♻️ به حالت پیروی از تنظیمِ کلی برگشت", show_alert=True)
    await _render_sadel_admin_panel(query, admin_id)


async def sadel_admin_threshold_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    admin_id = int(query.data[len("sadel_admin_thr_menu_"):])
    await safe_edit_message_text(query,
        "🔢 آستانهٔ اختصاصیِ این ادمین رو انتخاب کنید:",
        reply_markup=kb.kb_suspicious_admin_threshold(admin_id))


async def sadel_admin_set_threshold(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    rest = query.data[len("sadel_admin_thr_set_"):]
    admin_id_str, value = rest.rsplit("_", 1)
    admin_id = int(admin_id_str)
    import anomaly_alerts
    if value == "def":
        await anomaly_alerts.set_admin_override(admin_id, "threshold", None)
        msg = "↩️ آستانه به پیروی از کلی برگشت"
    else:
        await anomaly_alerts.set_admin_override(admin_id, "threshold", value)
        msg = f"✅ آستانهٔ اختصاصی به {value} تغییر یافت"
    await db.log_action(PISHVA_ID, "toggle_setting", f"sadel_admin_threshold_{admin_id} -> {value}")
    await query.answer(msg, show_alert=True)
    await _render_sadel_admin_panel(query, admin_id)


async def sadel_admin_window_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    admin_id = int(query.data[len("sadel_admin_win_menu_"):])
    await safe_edit_message_text(query,
        "⏱️ بازهٔ زمانیِ اختصاصیِ این ادمین رو انتخاب کنید:",
        reply_markup=kb.kb_suspicious_admin_window(admin_id))


async def sadel_admin_set_window(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    rest = query.data[len("sadel_admin_win_set_"):]
    admin_id_str, value = rest.rsplit("_", 1)
    admin_id = int(admin_id_str)
    import anomaly_alerts
    if value == "def":
        await anomaly_alerts.set_admin_override(admin_id, "window", None)
        msg = "↩️ بازه به پیروی از کلی برگشت"
    else:
        await anomaly_alerts.set_admin_override(admin_id, "window", value)
        msg = f"✅ بازهٔ اختصاصی به {value} دقیقه تغییر یافت"
    await db.log_action(PISHVA_ID, "toggle_setting", f"sadel_admin_window_{admin_id} -> {value}")
    await query.answer(msg, show_alert=True)
    await _render_sadel_admin_panel(query, admin_id)

# ─── کارهای زمان‌بندی‌شدهٔ دستیار هوشمند ───────────────────────
async def pishva_ai_scheduled(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    import ai_scheduler
    rows = await ai_scheduler.list_pending_for_panel(is_pishva=True)
    await safe_edit_message_text(query, 
        f"{box('🤖 کارهای زمان‌بندی‌شدهٔ دستیار')}\n\n"
        f"📌 کارهایی که دستیار هوشمند قراره در آینده انجام بده؛ برای لغو هرکدام، دکمهٔ زیرش را بزنید.",
        reply_markup=kb.kb_ai_scheduled_list(rows),
        parse_mode="Markdown"
    )

async def ai_scheduled_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    import ai_scheduler
    job_id = query.data.replace("aischedcancel_", "")
    try:
        job_id = int(job_id)
    except ValueError:
        await query.answer("❌ شناسه نامعتبر.", show_alert=True)
        return
    result = await ai_scheduler.cancel(ctx.job_queue, job_id, PISHVA_ID, is_pishva=True)
    await query.answer(result, show_alert=True)
    await pishva_ai_scheduled(update, ctx)

# ─── Action Logs ─────────────────────────────────────────────
async def pishva_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query, 
        f"{box('🔍 پیگیری اقدامات')}\n\n📌 بازه زمانی را انتخاب کنید:",
        reply_markup=kb.kb_logs_filter(),
        parse_mode="Markdown"
    )

LOGS_PERIOD_LABEL = {"today": "امروز", "week": "این هفته", "month": "این ماه", "all": "کل تاریخ"}
LOGS_PAGE_SIZE = 10


async def _return_none():
    """placeholder برای asyncio.gather وقتی یکی از شاخه‌ها نیازی به کوئری نداره
    (مثلاً وقتی admin_id خالیه) ولی باید تعداد آیتم‌های gather ثابت بمونه."""
    return None


async def _render_logs_page(query, period: str, page: int):
    """یک صفحه از لاگِ اقدامات (بر اساس فیلتر بازه‌ی زمانی) را رندر می‌کند.
    از صفحه‌بندیِ واقعیِ سمت دیتابیس استفاده می‌کند، پس حتی اگه تعداد کل
    نتایج خیلی زیاد باشه (ده‌ها هزار ردیف)، فقط همون صفحه خونده می‌شه.

    FIX: قبلاً get_action_logs، get_all_admins و pishva_display پشتِ‌سرِهم
    صدا زده می‌شدن؛ این سه به‌هم وابسته نیستن، پس حالا هم‌زمان اجرا می‌شن
    (۳ رفت‌وبرگشتِ شبکه‌ای پشتِ‌سرِهم → عملاً زمانِ کندترینِ تکی)."""
    (rows, total), all_admins, pname = await asyncio.gather(
        db.get_action_logs(period, page=page, page_size=LOGS_PAGE_SIZE),
        db.get_all_admins(),
        pishva_display(),
    )
    if not rows:
        await safe_edit_message_text(query, "❗ هیچ اقدامی در این بازه ثبت نشده.", reply_markup=kb.kb_logs_filter())
        return

    admins = {a["telegram_id"]: (a["display_name"] or a["full_name"]) for a in all_admins}
    label = LOGS_PERIOD_LABEL.get(period, period)
    total_pages = max(1, (total + LOGS_PAGE_SIZE - 1) // LOGS_PAGE_SIZE)

    lines = [f"{box('🔍 لاگ اقدامات — ' + label)}", f"یافت‌شده: {total} مورد — صفحه {page + 1} از {total_pages}", ""]
    for log in rows:
        name = pname if log["admin_id"] == PISHVA_ID else admins.get(log["admin_id"], str(log["admin_id"]))
        t = str(log["logged_at"] or "")[:16]
        lines.append(format_log_entry(t, name, log["action_type"], log["description"]))

    text = "\n\n".join(lines)
    keyboard = kb.kb_logs_list(period, page, total_pages)
    try:
        await safe_edit_message_text(query, text, reply_markup=keyboard, parse_mode="Markdown")
    except BadRequest:
        await safe_edit_message_text(query, text, reply_markup=keyboard)


async def show_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """انتخاب یکی از فیلترهای بازه (امروز/این‌هفته/این‌ماه/کل) → صفحه‌ی اول."""
    query = update.callback_query
    await query.answer()
    period = query.data.split("_")[-1]
    ctx.user_data["logs_period"] = period
    await _render_logs_page(query, period, 0)


async def logs_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """دکمه‌ی صفحه‌ی بعد/قبل در لیست لاگ: callback_data = logspage_<period>_<page>"""
    query = update.callback_query
    await query.answer()
    _, period, page_str = query.data.split("_")
    try:
        page = int(page_str)
    except ValueError:
        page = 0
    ctx.user_data["logs_period"] = period
    await _render_logs_page(query, period, page)


# ─── جستجوی لاگ (دو مرحله‌ای: عبارت جستجو → بازه‌ی ساعت) ────────
# این جستجو هم برای «کل لاگ» و هم برای «اقدامات یک مدیرِ خاص» به‌کار
# می‌ره؛ محدوده (همه یا یک مدیر مشخص) توی ctx.user_data["logs_search_admin_id"]
# نگه داشته می‌شه (None یعنی کل لاگ). به‌جای فرستادن «-» برای رد شدن از
# هر مرحله، یه دکمه‌ی «رد شدن» هست.
async def logs_search_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["logs_search_admin_id"] = None
    ctx.user_data.pop("logs_search_term", None)
    ctx.user_data.pop("logs_search_hour_from", None)
    ctx.user_data.pop("logs_search_hour_to", None)
    await safe_edit_message_text(
        query,
        f"{box('🔍 جستجو در لاگ')}\n\n"
        "🔎 عبارت جستجو رو بفرستید — نام مدیر، نام بازیکن، نوع اقدام، تاریخ یا هر چیز مرتبط.",
        reply_markup=kb.kb_logs_search_skip_term(),
        parse_mode="Markdown",
    )
    return ST_LOGS_SEARCH_TERM


async def admin_logs_search_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """جستجوی اقدامات مخصوصِ یک مدیر — از دکمه‌ی 🔍 توی پروفایل همون مدیر."""
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    tid = int(query.data.split("_")[-1])
    ctx.user_data["logs_search_admin_id"] = tid
    ctx.user_data.pop("logs_search_term", None)
    ctx.user_data.pop("logs_search_hour_from", None)
    ctx.user_data.pop("logs_search_hour_to", None)
    admin = await db.get_admin(tid)
    name = (admin["display_name"] or admin["full_name"]) if admin else str(tid)
    await safe_edit_message_text(
        query,
        f"{box('🔍 جستجو در اقدامات — ' + name)}\n\n"
        "🔎 عبارت جستجو رو بفرستید — نام بازیکن، نوع اقدام، تاریخ یا هر چیز مرتبط.",
        reply_markup=kb.kb_logs_search_skip_term(),
        parse_mode="Markdown",
    )
    return ST_LOGS_SEARCH_TERM


async def logs_search_term_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ctx.user_data["logs_search_term"] = text
    await update.message.reply_text(
        f"{box('⏳ بازه‌ی ساعت (اختیاری)')}\n\n"
        "اگه می‌خوای فقط اتفاق‌های یه بازه‌ی ساعتِ مشخص رو ببینی، دو عدد بفرست، مثلاً: 22 تا 24",
        reply_markup=kb.kb_logs_search_skip_range(),
        parse_mode="Markdown",
    )
    return ST_LOGS_SEARCH_RANGE


async def logs_search_term_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """دکمه‌ی «رد شدن» توی مرحله‌ی عبارت — بدون کلمه، مستقیم برو سراغ بازه."""
    query = update.callback_query
    await query.answer()
    ctx.user_data["logs_search_term"] = ""
    await safe_edit_message_text(
        query,
        f"{box('⏳ بازه‌ی ساعت (اختیاری)')}\n\n"
        "اگه می‌خوای فقط اتفاق‌های یه بازه‌ی ساعتِ مشخص رو ببینی، دو عدد بفرست، مثلاً: 22 تا 24",
        reply_markup=kb.kb_logs_search_skip_range(),
        parse_mode="Markdown",
    )
    return ST_LOGS_SEARCH_RANGE


async def _build_logs_search_view(ctx: ContextTypes.DEFAULT_TYPE, page: int):
    term = ctx.user_data.get("logs_search_term", "") or ""
    hf = ctx.user_data.get("logs_search_hour_from")
    ht = ctx.user_data.get("logs_search_hour_to")
    admin_id = ctx.user_data.get("logs_search_admin_id")

    # FIX: search_action_logs، get_admin(admin_id)، get_all_admins و
    # pishva_display به‌هم وابسته نیستن؛ همه با هم موازی خونده می‌شن به‌جای
    # پشتِ‌سرِهم (فقط برای حالتِ رایج — نتیجه پیدا شدن — کمی کوئریِ اضافه/
    # بی‌استفاده در حالتِ نادرِ «نتیجه‌ای نبود» می‌خوریم که ارزششو داره).
    (rows, total), scope_admin, all_admins, pname = await asyncio.gather(
        db.search_action_logs(term=term, hour_from=hf, hour_to=ht, admin_id=admin_id,
                               page=page, page_size=LOGS_PAGE_SIZE),
        db.get_admin(admin_id) if admin_id else _return_none(),
        db.get_all_admins(),
        pishva_display(),
    )

    scope_name = None
    if admin_id:
        scope_name = (scope_admin["display_name"] or scope_admin["full_name"]) if scope_admin else str(admin_id)
    title = ("🔍 نتایج جستجو — " + scope_name) if scope_name else "🔍 نتایج جستجو"

    if not rows:
        text = f"{box(title)}\n\n❗ نتیجه‌ای یافت نشد."
        keyboard = kb.kb_admin_logs_search_list(admin_id, 0, 1) if admin_id else kb.kb_logs_search_list(0, 1)
        return text, keyboard

    admins = {a["telegram_id"]: (a["display_name"] or a["full_name"]) for a in all_admins}
    total_pages = max(1, (total + LOGS_PAGE_SIZE - 1) // LOGS_PAGE_SIZE)

    lines = [f"{box(title)}", f"یافت‌شده: {total} مورد — صفحه {page + 1} از {total_pages}", ""]
    for log in rows:
        name = pname if log["admin_id"] == PISHVA_ID else admins.get(log["admin_id"], str(log["admin_id"]))
        t = str(log["logged_at"] or "")[:16]
        lines.append(format_log_entry(t, name, log["action_type"], log["description"]))

    text = "\n\n".join(lines)
    keyboard = kb.kb_admin_logs_search_list(admin_id, page, total_pages) if admin_id else kb.kb_logs_search_list(page, total_pages)
    return text, keyboard


async def logs_search_range_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    hour_from, hour_to = parse_hour_range(text)
    ctx.user_data["logs_search_hour_from"] = hour_from
    ctx.user_data["logs_search_hour_to"] = hour_to

    view_text, keyboard = await _build_logs_search_view(ctx, 0)
    await safe_reply_text(update.message, view_text, reply_markup=keyboard, parse_mode="Markdown")
    return ConversationHandler.END


async def logs_search_range_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """دکمه‌ی «رد شدن» توی مرحله‌ی بازه — بدون فیلتر ساعت، همین الان جستجو رو اجرا کن."""
    query = update.callback_query
    await query.answer()
    ctx.user_data["logs_search_hour_from"] = None
    ctx.user_data["logs_search_hour_to"] = None

    view_text, keyboard = await _build_logs_search_view(ctx, 0)
    await safe_edit_message_text(query, view_text, reply_markup=keyboard, parse_mode="Markdown")
    return ConversationHandler.END


async def logs_search_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """دکمه‌ی صفحه‌ی بعد/قبل در نتایج جستجوی کل لاگ: callback_data = logssearchpage_<page>"""
    query = update.callback_query
    await query.answer()
    page = int(query.data.split("_")[-1])
    view_text, keyboard = await _build_logs_search_view(ctx, page)
    await safe_edit_message_text(query, view_text, reply_markup=keyboard, parse_mode="Markdown")


async def admin_logs_search_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """دکمه‌ی صفحه‌ی بعد/قبل در نتایج جستجوی یک مدیر: callback_data = adminlogssearchpg_<tid>_<page>"""
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    page = int(parts[-1])
    view_text, keyboard = await _build_logs_search_view(ctx, page)
    await safe_edit_message_text(query, view_text, reply_markup=keyboard, parse_mode="Markdown")


# ─── پیگیری اقدامات مخصوصِ یک مدیر (از دکمه‌ی 🔍 توی پروفایل مدیر) ──
async def admin_logs_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """callback_data = adminlogsmenu_<tid> — دقیقاً مثل پنل کلیِ لاگ،
    فقط مخصوصِ همون یک مدیر."""
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
    name = admin["display_name"] or admin["full_name"]
    await safe_edit_message_text(
        query,
        f"{box('🔍 پیگیری اقدامات — ' + name)}\n\n📌 بازه زمانی را انتخاب کنید:",
        reply_markup=kb.kb_admin_logs_filter(tid),
        parse_mode="Markdown",
    )


async def _render_admin_logs_page(query, tid: int, period: str, page: int):
    # FIX: دو کوئریِ مستقل، قبلاً پشتِ‌سرِهم — حالا هم‌زمان.
    (rows, total), admin = await asyncio.gather(
        db.get_action_logs(period, admin_id=tid, page=page, page_size=LOGS_PAGE_SIZE),
        db.get_admin(tid),
    )
    name = (admin["display_name"] or admin["full_name"]) if admin else str(tid)

    if not rows:
        await safe_edit_message_text(
            query, f"❗ هیچ اقدامی از {name} در این بازه ثبت نشده.", reply_markup=kb.kb_admin_logs_filter(tid)
        )
        return

    label = LOGS_PERIOD_LABEL.get(period, period)
    total_pages = max(1, (total + LOGS_PAGE_SIZE - 1) // LOGS_PAGE_SIZE)

    lines = [f"{box('🔍 اقدامات ' + name + ' — ' + label)}", f"یافت‌شده: {total} مورد — صفحه {page + 1} از {total_pages}", ""]
    for log in rows:
        t = str(log["logged_at"] or "")[:16]
        lines.append(format_log_entry(t, name, log["action_type"], log["description"]))

    text = "\n\n".join(lines)
    keyboard = kb.kb_admin_logs_list(tid, period, page, total_pages)
    await safe_edit_message_text(query, text, reply_markup=keyboard, parse_mode="Markdown")


async def show_admin_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """callback_data = adminlogsperiod_<tid>_<period>"""
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    tid = int(parts[1])
    period = parts[2]
    await _render_admin_logs_page(query, tid, period, 0)


async def admin_logs_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """callback_data = adminlogspg_<tid>_<period>_<page>"""
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    tid = int(parts[1])
    period = parts[2]
    page = int(parts[3])
    await _render_admin_logs_page(query, tid, period, page)

# ─── پیگیری بازی‌های شطرنج مدیران ──────────────────────────────
# دلیلِ پایانِ هر بازی (status توی جدول chess_games) یکی از این‌هاست:
# checkmate (کیش‌ومات) / resigned (تسلیم) / timeout (اتمام وقت) / draw
# (مساوی، شامل پات و بی‌حرکتی و تکرار). این دیکشنری برای نمایشِ فارسیِ
# «دلیل» توی لیست استفاده می‌شود.
CHESS_REASON_LABEL = {
    "checkmate": "🏆 کیش و مات",
    "resigned":  "🏳️ تسلیم",
    "timeout":   "⏱ اتمام وقت",
    "draw":      "🤝 مساوی",
}

async def pishva_chess_games(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query,
        f"{box('♟️ بازی‌های مدیران')}\n\n📌 بازه زمانی را انتخاب کنید:",
        reply_markup=kb.kb_chess_games_filter(),
        parse_mode="Markdown"
    )

async def show_chess_games(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    period = query.data.split("_")[-1]
    games = await db.get_chess_games_log(period)
    if not games:
        await safe_edit_message_text(query, "❗ هیچ بازیِ تمام‌شده‌ای در این بازه ثبت نشده.",
            reply_markup=kb.kb_chess_games_filter())
        return
    pname = await pishva_display()
    admins = {a["telegram_id"]: (a["display_name"] or a["full_name"]) for a in await db.get_all_admins()}

    def name_of(uid):
        if uid == PISHVA_ID:
            return pname
        return admins.get(uid, str(uid)) if uid else "؟"

    lines = []
    # مرتب: از get_chess_games_log از قبل بر اساس finished_at نزولی (جدیدترین اول) آمده.
    for g in games[:30]:
        white = escape_md_legacy(g["white_name"] or name_of(g["white_id"]))
        black = escape_md_legacy(g["black_name"] or name_of(g["black_id"]))
        reason = CHESS_REASON_LABEL.get(g["status"], g["status"])
        if g["status"] == "draw":
            result = "مساوی"
        elif g["winner_id"]:
            winner_name = escape_md_legacy(name_of(g["winner_id"]))
            result = f"برد {winner_name}"
        else:
            result = "—"
        t = str(g["finished_at"] or g["created_at"] or "")[:16]
        elo_bits = []
        if g["white_elo_change"] is not None:
            sign = "+" if g["white_elo_change"] > 0 else ""
            elo_bits.append(f"{white} {sign}{g['white_elo_change']}")
        if g["black_elo_change"] is not None:
            sign = "+" if g["black_elo_change"] > 0 else ""
            elo_bits.append(f"{black} {sign}{g['black_elo_change']}")
        elo_txt = f" | 📊 {' / '.join(elo_bits)}" if elo_bits else ""
        lines.append(
            f"⏱️ `{t}` | ♟️ {white} 🆚 {black}\n"
            f"   نتیجه: {result} — دلیل: {reason}{elo_txt}"
        )
    header = f"{box('♟️ بازی‌های مدیران')}\n\n🔢 {len(games)} بازی در این بازه\n\n"
    text = header + "\n\n".join(lines)
    # محدودیتِ تلگرام روی طول پیام (۴۰۹۶ کاراکتر) — اگر لیست خیلی بلند شد،
    # آن‌قدر از آخر (قدیمی‌ترین‌های نمایش‌داده‌شده) کم می‌کنیم تا جا شود،
    # به‌جای این‌که تلگرام کل پیام را با خطا رد کند.
    if len(text) > 3900:
        while lines and len(header + "\n\n".join(lines)) > 3800:
            lines.pop()
        text = header + "\n\n".join(lines) + f"\n\n… و {len(games) - len(lines)} بازیِ دیگر (برای دیدن بقیه، بازه را کوتاه‌تر کنید)"
    try:
        await safe_edit_message_text(query, text, reply_markup=kb.kb_chess_games_filter(), parse_mode="Markdown")
    except BadRequest:
        await safe_edit_message_text(query, text, reply_markup=kb.kb_chess_games_filter())

# ─── Access Requests ─────────────────────────────────────────
async def pishva_requests(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    reqs = await db.get_pending_requests()
    if not reqs:
        await safe_edit_message_text(query, 
            f"{box('📥 درخواست‌های دسترسی')}\n\n✅ هیچ درخواست جدیدی وجود ندارد.",
            reply_markup=kb.kb_back("pishva_panel"),
            parse_mode="Markdown"
        )
        return
    for req in reqs:
        role_label = "🏆 مدیر مسابقات" if req["role"] == "tournament_manager" else "🛡️ مدیر امنیتی"
        text = (
            f"📥 *درخواست دسترسی*\n\n"
            f"👤 نام: {escape_md_legacy(req['full_name'])}\n"
            f"🔗 یوزرنیم: {escape_md_legacy(req['username'])}\n"
            f"💼 نقش: {role_label}\n"
            f"📝 پیام: {escape_md_legacy(req['message']) if req['message'] else '—'}\n"
            f"⏱️ زمان: `{str(req['requested_at'])[:19]}`"
        )
        try:
            await safe_reply_text(query.message, text, reply_markup=kb.kb_access_request(req["id"]))
        except Exception:
            pass
    await safe_edit_message_text(query, 
        f"📥 {len(reqs)} درخواست نمایش داده شد.",
        reply_markup=kb.kb_back("pishva_panel"),
        parse_mode="Markdown"
    )

# ─── Backup ───────────────────────────────────────────────────
async def pishva_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query, 
        f"{box('💾 سیستم پشتیبان‌گیری')}\n\n📌 بازه زمانی را انتخاب کنید:",
        reply_markup=kb.kb_backup_main(),
        parse_mode="Markdown"
    )

async def backup_period_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    period = query.data.split("_")[-1]
    ctx.user_data["backup_period"] = period
    await safe_edit_message_text(query, 
        f"📊 فرمت فایل را انتخاب کنید:",
        reply_markup=kb.kb_backup_format()
    )

async def backup_format_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    fmt = query.data.split("_")[-1]
    period = ctx.user_data.get("backup_period", "all")
    await safe_edit_message_text(query, "⏳ در حال تهیه بکاپ، لطفاً صبر کنید...")
    try:
        from backup_utils import send_backup
        await send_backup(ctx.bot, PISHVA_ID, period, fmt)
        await db.log_action(PISHVA_ID, "backup", f"تهیه بکاپ {fmt} — {period}")
        await safe_edit_message_text(query, 
            f"✅ بکاپ با موفقیت تهیه و ارسال شد.\n📁 فرمت: {fmt} | بازه: {period}",
            reply_markup=kb.kb_back("pishva_panel")
        )
    except Exception as e:
        await safe_edit_message_text(query, 
            f"❌ خطا در تهیه بکاپ:\n`{str(e)}`",
            reply_markup=kb.kb_back("pishva_panel"),
            parse_mode="Markdown"
        )

# ─── Restore (بازگردانی بکاپ — برعکسِ بکاپ) ────────────────────
async def pishva_restore_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    ctx.user_data.pop("restore_data", None)
    ctx.user_data.pop("restore_preview", None)
    await safe_edit_message_text(query, 
        f"{box('📥 بازگردانی بکاپ')}\n\n"
        f"📎 فایل بکاپ (Excel یا Word) را که قبلاً از همین ربات دریافت کرده‌اید ارسال کنید.\n"
        f"می‌توانید قبل از ارسال، داده‌های داخل فایل را ویرایش کنید — ربات محتوا را می‌خواند و "
        f"دقیقاً برعکسِ فرآیند بکاپ، آن‌ها را در سیستم وارد می‌کند.",
        reply_markup=kb.kb_back("pishva_backup"),
        parse_mode="Markdown"
    )
    return ST_RESTORE_FILE

async def restore_file_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != PISHVA_ID:
        return ST_RESTORE_FILE
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❗ لطفاً فایل بکاپ (xlsx یا docx) را به‌صورت سند ارسال کنید.")
        return ST_RESTORE_FILE

    from restore_utils import detect_format, parse_excel_backup, parse_word_backup, build_diff_preview
    fmt = detect_format(doc.file_name)
    if fmt is None:
        await update.message.reply_text("❌ فرمت فایل شناسایی نشد. فقط فایل‌های xlsx یا docx پشتیبانی می‌شوند.")
        return ST_RESTORE_FILE

    msg = await update.message.reply_text("⏳ در حال خواندن فایل، لطفاً صبر کنید...")
    try:
        tg_file = await ctx.bot.get_file(doc.file_id)
        file_bytes = bytes(await tg_file.download_as_bytearray())
        data = parse_excel_backup(file_bytes) if fmt == "excel" else parse_word_backup(file_bytes)
    except Exception as e:
        await msg.edit_text(f"❌ خطا در خواندن فایل:\n`{str(e)}`", parse_mode="Markdown")
        return ST_RESTORE_FILE

    total = len(data["classes"]) + len(data["players"]) + len(data["tournaments"]) + len(data["matches"])
    if total == 0:
        await msg.edit_text(
            "❗ هیچ داده‌ی قابل‌شناسایی‌ای در این فایل پیدا نشد.\n"
            "فایل باید همان ساختار بکاپ ربات (شیت‌ها/جدول‌های بازیکنان، مسابقات، تورنمنت‌ها، کلاس‌ها) را داشته باشد."
        )
        return ST_RESTORE_FILE

    await msg.edit_text("⏳ در حال مقایسه با اطلاعات فعلی سیستم، لطفاً صبر کنید...")
    try:
        preview = await build_diff_preview(data)
    except Exception as e:
        await msg.edit_text(f"❌ خطا در تحلیل تغییرات:\n`{str(e)}`", parse_mode="Markdown")
        return ST_RESTORE_FILE

    ctx.user_data["restore_data"] = data
    ctx.user_data["restore_preview"] = preview
    from restore_utils import build_preview_summary_text
    await msg.edit_text(
        f"{box('📋 پیش‌نمایش بازگردانی')}\n\n"
        f"{build_preview_summary_text(preview)}\n\n"
        f"⚠️ با تایید، موارد بالا در سیستم فعلی درج/به‌روزرسانی می‌شوند. برای دیدنِ دقیقِ تک‌تکِ "
        f"تغییرات، «جزئیات کامل تغییرات» را بزنید. آیا ادامه می‌دهید؟",
        reply_markup=kb.kb_restore_confirm(),
        parse_mode="Markdown"
    )
    return ST_RESTORE_FILE


async def restore_show_details(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return ST_RESTORE_FILE
    await query.answer()
    preview = ctx.user_data.get("restore_preview")
    if not preview:
        await safe_edit_message_text(query, "❗ داده‌ای برای نمایش یافت نشد. دوباره فایل را ارسال کنید.")
        return ST_RESTORE_FILE
    from restore_utils import build_preview_detail_text
    await safe_edit_message_text(query,
        f"{box('🔍 جزئیات کامل تغییرات')}\n\n{build_preview_detail_text(preview)}",
        reply_markup=kb.kb_restore_details(),
        parse_mode="Markdown"
    )
    return ST_RESTORE_FILE


async def restore_show_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return ST_RESTORE_FILE
    await query.answer()
    preview = ctx.user_data.get("restore_preview")
    if not preview:
        await safe_edit_message_text(query, "❗ داده‌ای برای نمایش یافت نشد. دوباره فایل را ارسال کنید.")
        return ST_RESTORE_FILE
    from restore_utils import build_preview_summary_text
    await safe_edit_message_text(query,
        f"{box('📋 پیش‌نمایش بازگردانی')}\n\n"
        f"{build_preview_summary_text(preview)}\n\n"
        f"⚠️ با تایید، موارد بالا در سیستم فعلی درج/به‌روزرسانی می‌شوند. برای دیدنِ دقیقِ تک‌تکِ "
        f"تغییرات، «جزئیات کامل تغییرات» را بزنید. آیا ادامه می‌دهید؟",
        reply_markup=kb.kb_restore_confirm(),
        parse_mode="Markdown"
    )
    return ST_RESTORE_FILE

async def restore_confirm_apply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return ST_RESTORE_FILE
    await query.answer()
    data = ctx.user_data.get("restore_data")
    if not data:
        await safe_edit_message_text(query, "❗ داده‌ای برای بازگردانی یافت نشد. دوباره فایل را ارسال کنید.")
        return ST_RESTORE_FILE
    await safe_edit_message_text(query, "⏳ در حال بازگردانی اطلاعات...")
    from restore_utils import apply_restore, build_summary_text
    try:
        counts = await apply_restore(data, PISHVA_ID)
        await db.log_action(PISHVA_ID, "restore", "بازگردانی بکاپ از فایل آپلودی")
        await safe_edit_message_text(query, 
            f"{box('✅ بازگردانی انجام شد')}\n\n{build_summary_text(counts)}",
            reply_markup=kb.kb_back("pishva_panel"),
            parse_mode="Markdown"
        )
    except Exception as e:
        await safe_edit_message_text(query, 
            f"❌ خطا در بازگردانی:\n`{str(e)}`",
            reply_markup=kb.kb_back("pishva_panel"),
            parse_mode="Markdown"
        )
    ctx.user_data.pop("restore_data", None)
    ctx.user_data.pop("restore_preview", None)
    return ConversationHandler.END

async def restore_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data.pop("restore_data", None)
    ctx.user_data.pop("restore_preview", None)
    await safe_edit_message_text(query, "❌ بازگردانی لغو شد.", reply_markup=kb.kb_back("pishva_backup"))
    return ConversationHandler.END

# ─── Working Hours ────────────────────────────────────────────
# منطق ساعت کاری (شروع/پایان دستی + پایان خودکار + یادآور) به‌طور کامل
# به ماژول workhours.py منتقل شد؛ اونجا pishva_workhours/workhour_start/
# workhour_end (به‌علاوهٔ قابلیت‌های جدید) تعریف شدن. bot.py مستقیماً از
# workhours.py ایمپورت می‌کنه.

# ─── Repair Mode ──────────────────────────────────────────────
async def pishva_repair(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    repair_status = await db.get_setting("repair_mode", "0")
    status = "🔧 فعال" if repair_status == "1" else "✅ غیرفعال"
    await safe_edit_message_text(query, 
        f"{box('🔧 حالت تعمیر')}\n\nوضعیت فعلی: {status}",
        reply_markup=kb.kb_repair_menu(),
        parse_mode="Markdown"
    )

async def repair_on(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # FIX: این سه‌تا (دو نوشتن + یک خواندن، روی کلیدهای کاملاً جدا از هم)
    # قبلاً پشتِ‌سرِهم اجرا می‌شدن؛ حالا هر سه موازی.
    _, _, reason = await asyncio.gather(
        db.set_setting("repair_mode", "1"),
        db.set_setting("bot_update_mode", "1"),
        db.get_setting("repair_reason", ""),
    )
    ts = now_shamsi()
    notif = (
        f"{box('🔧 حالت تعمیر فعال شد')}\n\n"
        f"🛠️ ربات در حال تعمیر و بروزرسانی است.\n"
        f"⏱️ `{ts}`\n"
        f"{'📝 دلیل: ' + reason if reason else ''}\n\n"
        f"لطفاً منتظر بمانید."
    )
    await broadcast_to_admins(ctx.bot, notif)
    await db.log_action(PISHVA_ID, "repair_on", "فعال‌سازی حالت تعمیر")
    await safe_edit_message_text(query, "🔧 حالت تعمیر فعال شد.", reply_markup=kb.kb_back("pishva_repair"))

async def repair_off(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # FIX: موازی‌سازیِ دو نوشتنِ مستقل (مثل repair_on).
    await asyncio.gather(db.set_setting("repair_mode", "0"), db.set_setting("bot_update_mode", "0"))
    ts = now_shamsi()
    notif = f"✅ تعمیر پایان یافت. ربات آماده استفاده است.\n⏱️ `{ts}`"
    await broadcast_to_admins(ctx.bot, notif)
    await db.log_action(PISHVA_ID, "repair_off", "غیرفعال‌سازی حالت تعمیر")
    await safe_edit_message_text(query, "✅ حالت تعمیر غیرفعال شد.", reply_markup=kb.kb_back("pishva_repair"))

async def repair_reason_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, "📝 دلیل تعمیر را وارد کنید:")
    return ST_REPAIR_REASON

async def repair_reason_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    await db.set_setting("repair_reason", reason)
    await update.message.reply_text(f"✅ دلیل تعمیر ذخیره شد:\n_{reason}_", parse_mode="Markdown")
    return ConversationHandler.END

# ─── Database Status (Manual) ──────────────────────────────────
async def _render_dbstatus(query):
    current = await db.get_setting("db_manual_status", "1")
    label = "🟢 فعال" if current == "1" else "⚠️ غیرفعال"
    await safe_edit_message_text(query, 
        f"{box('🗄️ وضعیت دیتابیس')}\n\nوضعیت فعلی: {label}\n\n"
        f"این وضعیت کاملاً دستی است و مستقل از اتصال واقعی به دیتابیس.",
        reply_markup=kb.kb_dbstatus_menu(current),
        parse_mode="Markdown"
    )

async def pishva_dbstatus(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    await _render_dbstatus(query)

async def dbstatus_on(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await db.set_setting("db_manual_status", "1")
    await db.log_action(PISHVA_ID, "dbstatus_on", "تنظیم دستی وضعیت دیتابیس: فعال")
    await _render_dbstatus(query)

async def dbstatus_off(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await db.set_setting("db_manual_status", "0")
    await db.log_action(PISHVA_ID, "dbstatus_off", "تنظیم دستی وضعیت دیتابیس: غیرفعال")
    await _render_dbstatus(query)

# ─── Identity ─────────────────────────────────────────────────
async def pishva_identity(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    pname = await pishva_display()
    await safe_edit_message_text(query, 
        f"{box('🪪 تغییر هویت')}\n\nنام نمایشی فعلی مدیر ارشد: *{pname}*",
        reply_markup=kb.kb_identity(),
        parse_mode="Markdown"
    )

async def identity_pishva_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, "📝 نام نمایشی جدید برای مدیر ارشد را وارد کنید:")
    return ST_PISHVA_NAME_CHANGE

async def identity_pishva_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    await db.set_setting("pishva_display_name", new_name)
    await db.log_action(PISHVA_ID, "identity_change", f"نام مدیر ارشد به: {new_name}")
    await update.message.reply_text(
        f"✅ نام نمایشی مدیر ارشد به *{new_name}* تغییر یافت.\n"
        f"این تغییر در سراسر ربات اعمال شد.",
        reply_markup=kb.kb_back("pishva_identity"),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def identity_admin_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    admins = await db.get_all_admins()
    if not admins:
        await safe_edit_message_text(query, "❗ هیچ مدیری ثبت نشده.", reply_markup=kb.kb_back("pishva_identity"))
        return
    rows = []
    for i in range(0, len(admins), 2):
        row = [InlineKeyboardButton(
            f"👤 {a['display_name'] or a['full_name']}",
            callback_data=f"identity_set_{a['telegram_id']}"
        ) for a in admins[i:i+2]]
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="pishva_identity")])
    await safe_edit_message_text(query, 
        "👥 مدیری که می‌خواهید نامش را تغییر دهید انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(rows)
    )

async def identity_admin_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tid = int(query.data.split("_")[-1])
    ctx.user_data["identity_admin_tid"] = tid
    admin = await db.get_admin(tid)
    await safe_edit_message_text(query, 
        f"✏️ نام نمایشی جدید برای *{admin['full_name']}* را وارد کنید:",
        parse_mode="Markdown"
    )
    return ST_ADMIN_NAME_CHANGE

async def identity_admin_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    tid = ctx.user_data.get("identity_admin_tid")
    if tid:
        await db.update_admin_display_name(tid, new_name)
        await db.log_action(PISHVA_ID, "admin_identity_change", f"نام مدیر {tid} به: {new_name}")
    await update.message.reply_text(
        f"✅ نام نمایشی مدیر به *{new_name}* تغییر یافت.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ─── New Year ─────────────────────────────────────────────────
async def pishva_newyear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query, 
        f"{box('⚠️ هشدار جدی')}\n\n"
        f"این عملیات تمام اطلاعات سال تحصیلی جاری\n"
        f"(بازیکنان، تیم‌ها، مسابقات، رتبه‌بندی‌ها، اخطارها و آمار)\n"
        f"را آرشیو کرده و سیستم فعال را کاملاً پاک می‌کند.\n\n"
        f"آیا ادامه می‌دهید؟",
        reply_markup=kb.kb_newyear_confirm(),
        parse_mode="Markdown"
    )

async def newyear_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, "🔐 رمز امنیتی را وارد کنید:")
    ctx.user_data["_audit_secret"] = True       # پیامِ بعدی (رمز) در ردیابی ثبت نمی‌شود
    return ST_NEW_YEAR_PASSWORD

async def newyear_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pwd = update.message.text.strip()
    if pwd != NEW_YEAR_PASSWORD:
        await update.message.reply_text("❌ رمز اشتباه است. عملیات لغو شد.")
        return ConversationHandler.END
    await update.message.reply_text("⏳ در حال تهیه بکاپ و پاکسازی سیستم...")
    try:
        from backup_utils import generate_excel_backup
        from helpers import now_shamsi
        ts = now_shamsi()
        buf = await generate_excel_backup("all")
        await ctx.bot.send_document(
            chat_id=PISHVA_ID,
            document=buf,
            filename=f"newyear_backup_{ts.replace('/', '-').replace(' ', '_')}.xlsx",
            caption=f"📦 بکاپ سالانه — {ts}"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ خطا در بکاپ: {e}")
    await db.reset_active_data()
    await db.log_action(PISHVA_ID, "new_year_reset", "ریست سال تحصیلی جدید")
    await update.message.reply_text(
        f"{box('✅ سال تحصیلی جدید آغاز شد')}\n\n"
        f"📦 بکاپ سال قبل با موفقیت ذخیره شد.\n"
        f"سیستم آماده ثبت اطلاعات جدید است.",
        reply_markup=kb.kb_back("pishva_panel"),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ─── Update Mode ──────────────────────────────────────────────
async def pishva_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query, 
        f"{box('🔄 آپدیت ربات')}\n\n📌 عملیات را انتخاب کنید:",
        reply_markup=kb.kb_update_menu(),
        parse_mode="Markdown"
    )

async def update_sleep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    update_mode = await db.get_setting("bot_update_mode", "0")
    if update_mode == "1":
        await db.set_setting("bot_update_mode", "0")
        await safe_edit_message_text(query, "✅ ربات از حالت آپدیت خارج شد.", reply_markup=kb.kb_back("pishva_update"))
    else:
        await db.set_setting("bot_update_mode", "1")
        ts = now_shamsi()
        notif = f"🔄 ربات در حال آپدیت است. لطفاً منتظر بمانید.\n⏱️ `{ts}`"
        await broadcast_to_admins(ctx.bot, notif)
        await safe_edit_message_text(query, "💤 ربات برای ادمین‌ها خاموش شد.", reply_markup=kb.kb_back("pishva_update"))

async def update_announce_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, "🚀 نام/شماره نسخه جدید را وارد کنید:")
    return ST_UPDATE_VERSION

async def update_version_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["update_version"] = update.message.text.strip()
    await update.message.reply_text("📝 توضیحات آپدیت را وارد کنید:")
    return ST_UPDATE_DESC

async def update_desc_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    version = ctx.user_data.get("update_version", "?")
    desc = update.message.text.strip()
    ts = now_shamsi()
    pname = await pishva_display()
    announce = (
        f"╔══════════════════════════════╗\n"
        f"║ 🚀 آپدیت جدید — نسخه {version} ║\n"
        f"╚══════════════════════════════╝\n\n"
        f"✨ {desc}\n\n"
        f"⏱️ `{ts}`\n"
        f"👑 {pname}"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 بازخورد/انتقاد", callback_data="comms_msg_pishva"),
        InlineKeyboardButton("❓ پرسش", callback_data="comms_msg_pishva")],
    ])
    await broadcast_to_admins(ctx.bot, announce, reply_markup=markup)
    await update.message.reply_text(f"✅ اعلام آپدیت نسخه {version} برای همه ارسال شد.")
    return ConversationHandler.END

# ─── Announcement Group ───────────────────────────────────────
async def pishva_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    current = await db.get_setting("announcement_group_id", "")
    await safe_edit_message_text(query, 
        f"{box('📡 گروه اعلانات')}\n\n"
        f"گروه فعلی: `{current or 'تنظیم نشده'}`\n\n"
        f"آیدی یا لینک گروه را وارد کنید\n_(مثلاً @mygroupname یا -100123456789)_:",
        parse_mode="Markdown"
    )
    return ST_GROUP_ID

async def group_id_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    gid = update.message.text.strip()
    await db.set_setting("announcement_group_id", gid)
    await db.log_action(PISHVA_ID, "set_group", f"تنظیم گروه اعلانات: {gid}")
    await update.message.reply_text(
        f"✅ گروه اعلانات تنظیم شد:\n`{gid}`",
        reply_markup=kb.kb_back("pishva_panel"),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ─── Announcement Channel (جدا از گروه) ────────────────────────
async def pishva_channel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    current = await db.get_setting("announcement_channel_id", "")
    await safe_edit_message_text(query, 
        f"{box('📢 کانال اعلانات')}\n\n"
        f"کانال فعلی: `{current or 'تنظیم نشده'}`\n\n"
        f"آیدی عددی کانال را وارد کنید\n_(مثلاً -1001234567890)_:",
        parse_mode="Markdown"
    )
    return ST_CHANNEL_ID

async def channel_id_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.message.text.strip()
    await db.set_setting("announcement_channel_id", cid)
    await db.log_action(PISHVA_ID, "set_channel", f"تنظیم کانال اعلانات: {cid}")
    await update.message.reply_text(
        f"✅ کانال اعلانات تنظیم شد:\n`{cid}`",
        reply_markup=kb.kb_back("pishva_panel"),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ─── پخش خودکار به گروه/کانال (هرکدام جدا برای گروه و کانال) ──
BROADCAST_ITEMS = [
    ("announcement", "📢 بیانیه‌ها", "broadcast_announcement_group_enabled", "broadcast_announcement_channel_enabled"),
    ("result", "♟️ نتیجه مسابقات", "broadcast_result_group_enabled", "broadcast_result_channel_enabled"),
    ("champion", "🏆 قهرمان هفتگی", "broadcast_champion_group_enabled", "broadcast_champion_channel_enabled"),
    ("reminder", "⏰ یادآورها", "reminder_broadcast_group_enabled", "reminder_broadcast_channel_enabled"),
    ("chess_ai_defeat", "🤖 شکستِ هوش‌مصنوعی (سخت)", "broadcast_chess_ai_defeat_group_enabled", "broadcast_chess_ai_defeat_channel_enabled"),
]

async def pishva_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    # FIX: قبلاً برای هر کدوم از موارد BROADCAST_ITEMS، دو تا db.get_setting
    # جداگانه صدا زده می‌شد (برای ۵ مورد فعلی، ۱۰ رفت‌وبرگشتِ شبکه‌ای). حتی
    # با asyncio.gather هم چون Turso دور و کندِ‌رفت‌وبرگشته، هم‌زمانیِ سطحِ
    # پایتون یک رفت‌وبرگشتِ واحد نمی‌شد. حالا get_settings_bulk با یک
    # کوئریِ IN(...) واحد همه‌شون رو می‌گیره.
    all_keys = []
    for _, _, group_key, channel_key in BROADCAST_ITEMS:
        all_keys.append(group_key)
        all_keys.append(channel_key)
    flags = await db.get_settings_bulk(all_keys, "1")
    items = []
    for key, label, group_key, channel_key in BROADCAST_ITEMS:
        g_on = flags[group_key] == "1"
        c_on = flags[channel_key] == "1"
        items.append((key, label, group_key, g_on, channel_key, c_on))
    await safe_edit_message_text(query, 
        f"{box('📡 پخش خودکار به گروه/کانال')}\n\n"
        f"برای هر مورد، ارسال به گروه و کانال جداگانه کنترل می‌شود:",
        reply_markup=kb.kb_broadcast_menu(items),
        parse_mode="Markdown"
    )

async def broadcast_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    key = query.data.replace("broadcast_toggle_", "")
    valid_keys = []
    for _, _, group_key, channel_key in BROADCAST_ITEMS:
        valid_keys.append(group_key)
        valid_keys.append(channel_key)
    if key in valid_keys:
        current = await db.get_setting(key, "1")
        new_val = "0" if current == "1" else "1"
        await db.set_setting(key, new_val)
        await db.log_action(PISHVA_ID, "broadcast_toggle", f"{key} -> {new_val}")
    await pishva_broadcast(update, ctx)

# ─── شخصی‌سازیِ متنِ اعلانِ «شکستِ هوش‌مصنوعیِ سخت» ───────────────
DEFAULT_CHESS_AI_DEFEAT_TEXT = "🏆 *{name}* موفق شد هوش مصنوعیِ شطرنج را در سطحِ سخت شکست دهد!"

async def pishva_chess_ai_broadcast_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    current = await db.get_setting("chess_ai_defeat_broadcast_text", DEFAULT_CHESS_AI_DEFEAT_TEXT)
    await safe_edit_message_text(query, 
        f"{box('🤖 متنِ اعلانِ شکستِ هوش‌مصنوعی (سخت)')}\n\n"
        f"متنِ فعلی:\n{current}\n\n"
        f"متنِ جدید را بفرستید؛ جای نامِ برنده را با `{{name}}` بگذارید.\n"
        f"برای بازگشت به متنِ پیش‌فرض، کلمه‌ی «پیش‌فرض» را بفرستید.",
        parse_mode="Markdown"
    )
    return ST_CHESS_AI_BROADCAST_TEXT

async def chess_ai_broadcast_text_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text in ("پیش‌فرض", "پیشفرض", "/default"):
        await db.set_setting("chess_ai_defeat_broadcast_text", DEFAULT_CHESS_AI_DEFEAT_TEXT)
        await update.message.reply_text(
            "✅ متن به پیش‌فرض بازگشت.", reply_markup=kb.kb_back("pishva_broadcast")
        )
        return ConversationHandler.END
    await db.set_setting("chess_ai_defeat_broadcast_text", text)
    await db.log_action(PISHVA_ID, "set_chess_ai_broadcast_text", "تنظیم متنِ اعلانِ شکستِ هوش‌مصنوعی")
    await update.message.reply_text(
        f"✅ متنِ اعلان ذخیره شد:\n\n{text}",
        reply_markup=kb.kb_back("pishva_broadcast"),
    )
    return ConversationHandler.END

# ─── Vault ────────────────────────────────────────────────────
async def pishva_vault(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    # FIX: این دو تا await قبلاً پشتِ‌سرِهم بودن؛ حالا موازی خونده می‌شن.
    players, backups = await asyncio.gather(
        db.get_all_players(),
        db.get_all_backups(),
    )
    rows = []
    for i in range(0, min(len(players), 20), 2):
        row = [InlineKeyboardButton(
            f"📂 {p['full_name']}",
            callback_data=f"player_view_{p['id']}"
        ) for p in players[i:i+2]]
        rows.append(row)
    backup_lines = "\n".join(
        [f"💾 {b['label'] or b['period']} | {b['format']} | {str(b['created_at'])[:10]}" for b in backups[:10]]
    ) or "_هیچ بکاپی وجود ندارد_"
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu_pishva")])
    await safe_edit_message_text(query, 
        f"{box('🏦 خزانه مدیر ارشد')}\n\n"
        f"📂 *پرونده بازیکنان:* `{len(players)}` بازیکن\n\n"
        f"{separator('🗄️ بکاپ‌ها')}\n"
        f"{backup_lines}",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown"
    )

# ─── Auto Backup Settings ─────────────────────────────────────
async def pishva_auto_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    # FIX: این ۴ تا await قبلاً پشتِ‌سرِهم بودن؛ حالا موازی خونده می‌شن.
    enabled, interval, fmt, period = await asyncio.gather(
        db.get_setting("auto_backup_enabled", "0"),
        db.get_setting("auto_backup_interval", "24"),
        db.get_setting("auto_backup_format", "excel"),
        db.get_setting("auto_backup_period", "all"),
    )
    fmt_label = "Excel" if fmt == "excel" else "Word"
    period_fa = {"today": "امروز", "week": "هفته", "month": "ماه", "all": "کامل"}.get(period, period)
    status = "🟢 فعال" if enabled == "1" else "🔴 غیرفعال"
    text = (
        f"{box('🔄 بکاپ خودکار')}\n\n"
        f"📊 وضعیت: {status}\n"
        f"⏰ فاصله زمانی: هر {interval} ساعت\n"
        f"📁 فرمت: {fmt_label}\n"
        f"📅 بازه: {period_fa}\n\n"
        f"💡 بکاپ خودکار فایل را مستقیم برای مدیر ارشد ارسال می‌کند."
    )
    from keyboards import kb_auto_backup_settings
    await safe_edit_message_text(query, 
        text,
        reply_markup=kb_auto_backup_settings(enabled, interval, fmt_label, period_fa),
        parse_mode="Markdown"
    )

async def auto_backup_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    current = await db.get_setting("auto_backup_enabled", "0")
    new_val = "0" if current == "1" else "1"
    await db.set_setting("auto_backup_enabled", new_val)
    if new_val == "1":
        interval = int(await db.get_setting("auto_backup_interval", "24"))
        from backup_utils import schedule_auto_backup
        schedule_auto_backup(ctx.application, interval)
        await query.answer("✅ بکاپ خودکار فعال شد", show_alert=True)
    else:
        try:
            jobs = ctx.application.job_queue.get_jobs_by_name("auto_backup")
            for job in jobs:
                job.schedule_removal()
        except Exception:
            pass
        await query.answer("❌ بکاپ خودکار غیرفعال شد", show_alert=True)
    await pishva_auto_backup(update, ctx)

async def auto_backup_interval_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    from keyboards import kb_auto_backup_interval
    await safe_edit_message_text(query, 
        "⏰ فاصله زمانی بکاپ خودکار را انتخاب کنید:",
        reply_markup=kb_auto_backup_interval()
    )

async def auto_backup_set_interval(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    hours = int(query.data.split("_")[-1])
    await db.set_setting("auto_backup_interval", str(hours))
    enabled = await db.get_setting("auto_backup_enabled", "0")
    if enabled == "1":
        from backup_utils import schedule_auto_backup
        schedule_auto_backup(ctx.application, hours)
    await query.answer(f"✅ فاصله زمانی به {hours} ساعت تغییر یافت", show_alert=True)
    await pishva_auto_backup(update, ctx)

async def auto_backup_fmt_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current = await db.get_setting("auto_backup_format", "excel")
    new_fmt = "word" if current == "excel" else "excel"
    await db.set_setting("auto_backup_format", new_fmt)
    await pishva_auto_backup(update, ctx)

async def auto_backup_period_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    periods = ["all", "month", "week", "today"]
    current = await db.get_setting("auto_backup_period", "all")
    idx = periods.index(current) if current in periods else 0
    new_period = periods[(idx + 1) % len(periods)]
    await db.set_setting("auto_backup_period", new_period)
    await pishva_auto_backup(update, ctx)
