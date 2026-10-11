"""
Combined handlers for: tasks, admin management, feedback, help, teams, slash commands
"""
import asyncio
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
import database as db
import keyboards as kb
from helpers import (safe_edit_message_text, box, separator, now_shamsi, broadcast_to_admins,
                     notify_pishva, pishva_display, warning_bar_admin,
                     power_bar, send_notification, check_perm, check_status_gate,
                     today_gregorian, parse_hour_range,
                     days_ago_gregorian, date_label_fa, parse_admin_undo_date,
                     TEHRAN_TZ)
from anomaly_alerts import record_destructive_action
from config import (PISHVA_ID, ST_TASK_SELECT_ADMIN, ST_TASK_TITLE,
                    ST_TASK_DESC, ST_TASK_DONE_REASON, ST_FEEDBACK_TEXT,
                    ST_SUGGESTION_TEXT, ST_FEATURE_TITLE, ST_FEATURE_DESC,
                    ST_PRAISE_TEXT, ST_ADMIN_TASK_SS, ST_ADMIN_WARNING_REASON,
                    ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER,
                    ST_ADMIN_UNDO_RANGE, ST_ADMIN_UNDO_DATE)
import json


# ══════════════════════════════════════════
# TASKS
# ══════════════════════════════════════════

async def task_assign_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    admins = await db.get_active_admins()
    if not admins:
        await safe_edit_message_text(query, "❗ هیچ مدیری برای اعطای وظیفه وجود ندارد.")
        return
    rows = []
    for i in range(0, len(admins), 2):
        row = [InlineKeyboardButton(
            f"👤 {a['display_name'] or a['full_name']}",
            callback_data=f"task_to_{a['telegram_id']}"
        ) for a in admins[i:i+2]]
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu_tasks")])
    await safe_edit_message_text(query, 
        f"{box('📋 اعطای وظیفه')}\n\nبه کدام مدیر وظیفه می‌دهید؟",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown"
    )
    return ST_TASK_SELECT_ADMIN


async def task_to_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tid = int(query.data.split("_")[-1])
    ctx.user_data["task_admin"] = tid
    admin = await db.get_admin(tid)
    await safe_edit_message_text(query, f"📝 عنوان وظیفه برای *{admin['display_name'] or admin['full_name']}*:",
                                   parse_mode="Markdown")
    return ST_TASK_TITLE


async def task_title_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["task_title"] = update.message.text.strip()
    await update.message.reply_text("📋 توضیحات وظیفه را وارد کنید:")
    return ST_TASK_DESC


async def task_desc_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    title = ctx.user_data.get("task_title", "")
    desc = update.message.text.strip()
    tid = ctx.user_data.get("task_admin")
    uid = update.effective_user.id

    task_id = await db.create_task(tid, uid, title, desc)
    admin = await db.get_admin(tid)
    pname = await pishva_display()
    ts = now_shamsi()

    notif = (
        f"{box('📋 وظیفه جدید')}\n\n"
        f"👤 {admin['display_name'] or admin['full_name']} عزیز،\n"
        f"یک وظیفه جدید به شما اعطا شد.\n\n"
        f"📌 عنوان: *{title}*\n"
        f"📝 توضیح: _{desc}_\n"
        f"⏱️ زمان اعطا: `{ts}`"
    )
    try:
        await ctx.bot.send_message(
            chat_id=tid, text=notif,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👁️ مشاهده وظیفه", callback_data=f"task_view_{task_id}"),
                 InlineKeyboardButton("✅ تأیید دریافت", callback_data=f"task_ack_{task_id}")]
            ]),
            parse_mode="Markdown"
        )
    except Exception:
        pass

    await db.log_action(uid, "assign_task", f"اعطای وظیفه: {title}", tid)
    await update.message.reply_text(f"✅ وظیفه *{title}* برای {admin['display_name'] or admin['full_name']} ارسال شد.",
                                     parse_mode="Markdown")
    return ConversationHandler.END


async def task_track(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    tasks = await db.get_tasks_for(uid)
    if not tasks:
        await safe_edit_message_text(query, "📌 هیچ وظیفه‌ای دارید.", reply_markup=kb.kb_back("tasks"))
        return
    rows = []
    for t in tasks[:15]:
        icon = "✅" if t["status"] == "done" else "❌" if t["status"] == "failed" else "⏳"
        rows.append([InlineKeyboardButton(f"{icon} {t['title']}", callback_data=f"task_view_{t['id']}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu_tasks")])
    await safe_edit_message_text(query, 
        f"{box('📌 وظایف شما')}\n\n📌 یک وظیفه انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown"
    )


async def task_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[-1])
    t = await db.get_task(task_id)
    if not t:
        await query.answer("وظیفه یافت نشد.", show_alert=True)
        return
    status_label = {"pending": "⏳ در انتظار", "done": "✅ انجام‌شده", "failed": "❌ انجام‌نشده"}.get(t["status"], t["status"])
    text = (
        f"{box('📋 جزئیات وظیفه')}\n\n"
        f"📌 عنوان: *{t['title']}*\n"
        f"📝 توضیح: _{t['description']}_\n"
        f"📊 وضعیت: {status_label}\n"
        f"⏱️ اعطا: `{str(t['assigned_at'])[:19]}`\n"
        f"{'❌ دلیل عدم انجام: ' + t['fail_reason'] if t['fail_reason'] else ''}"
    )
    markup = kb.kb_task_status(task_id) if t["status"] == "pending" else kb.kb_back("task_track")
    await safe_edit_message_text(query, text, reply_markup=markup, parse_mode="Markdown")


async def task_ack(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("✅ تأیید شد.")


async def task_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[-1])
    await db.update_task_status(task_id, "done")
    t = await db.get_task(task_id)
    await notify_pishva(ctx.bot, f"✅ وظیفه «{t['title']}» توسط مدیر انجام شد.\n⏱️ `{now_shamsi()}`")
    await safe_edit_message_text(query, "✅ وظیفه به‌عنوان انجام‌شده ثبت شد.", reply_markup=kb.kb_back("task_track"))


async def task_fail_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[-1])
    ctx.user_data["failing_task"] = task_id
    await safe_edit_message_text(query, "❌ دلیل عدم انجام وظیفه را بنویسید:")
    return ST_TASK_DONE_REASON


async def task_fail_reason(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    task_id = ctx.user_data.get("failing_task")
    if task_id:
        await db.update_task_status(task_id, "failed", reason)
        t = await db.get_task(task_id)
        uid = update.effective_user.id
        admin = await db.get_admin(uid)
        name = admin["display_name"] or admin["full_name"] if admin else str(uid)
        await notify_pishva(
            ctx.bot,
            f"❌ وظیفه «{t['title']}» انجام نشد.\n"
            f"👤 توسط: {name}\n"
            f"📋 دلیل: {reason}\n"
            f"⏱️ `{now_shamsi()}`",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 پیگیری علت", callback_data=f"task_followup_{task_id}")]
            ])
        )
        await update.message.reply_text("❌ ثبت شد و مدیر ارشد مطلع شد.")
    return ConversationHandler.END


async def task_followup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[-1])
    t = await db.get_task(task_id)
    await safe_edit_message_text(query, 
        f"🔍 پیگیری وظیفه: *{t['title']}*\n📋 دلیل: {t['fail_reason']}",
        reply_markup=kb.kb_back("menu_tasks"),
        parse_mode="Markdown"
    )


async def task_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, 
        f"{box('📜 تاریخچه وظایف')}\n\n📌 فیلتر را انتخاب کنید:",
        reply_markup=kb.kb_task_history_filter(),
        parse_mode="Markdown"
    )


async def task_history_filter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    f_type = query.data.replace("thistory_", "")
    tasks = await db.get_all_tasks()
    if f_type == "done":
        tasks = [t for t in tasks if t["status"] == "done"]
    elif f_type == "pending":
        tasks = [t for t in tasks if t["status"] == "pending"]
    rows = []
    for t in tasks[:15]:
        icon = "✅" if t["status"] == "done" else "❌" if t["status"] == "failed" else "⏳"
        rows.append([InlineKeyboardButton(f"{icon} {t['title']}", callback_data=f"task_view_{t['id']}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="task_history")])
    await safe_edit_message_text(query, 
        f"{box('📜 وظایف')}\n\nتعداد: `{len(tasks)}`",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown"
    )


# ══════════════════════════════════════════
# ADMIN MANAGEMENT
# ══════════════════════════════════════════

async def build_admin_view_content(tid: int):
    """محتوای (متن، کیبورد) پروفایلِ یه مدیرِ خاص رو می‌سازه — منبعِ مشترکِ
    نمایش، چه از طریقِ دکمه (admin_view) چه از طریقِ ریپلای‌کردن با کلمه‌ی
    «مدیر». اگه مدیر پیدا نشه، (None, None) برمی‌گردونه."""
    admin, tasks, (_logs_rows, logs_total) = await asyncio.gather(
        db.get_admin(tid), db.get_tasks_for(tid), db.get_action_logs("all", tid)
    )
    if not admin:
        return None, None

    warn_bar = warning_bar_admin(admin["warnings"])
    role_label = "🏆 مدیر مسابقات" if admin["role"] == ROLE_TOURNAMENT_MANAGER else "🛡️ مدیر امنیتی"

    _name = admin["display_name"] or admin["full_name"]
    text = (
        f"{box("👤 پروفایل: " + _name + "")}\n\n"
        f"🔗 یوزرنیم: {admin['username']}\n"
        f"💼 نقش: {role_label}\n"
        f"🆔 آیدی: `{admin['telegram_id']}`\n"
        f"📅 تاریخ ثبت: `{str(admin['joined_at'])[:10]}`\n"
        f"⏱️ آخرین فعالیت: `{str(admin['last_active'] or '')[:16]}`\n"
        f"📊 اقدامات: `{logs_total}`\n"
        f"📋 وظایف: `{len(tasks)}`\n"
        f"{'✅ فعال' if admin['is_active'] else '🔴 غیرفعال'}\n\n"
        f"{separator('⚠️ اخطارها')}\n"
        f"{warn_bar}"
    )
    return text, kb.kb_admin_actions(tid, admin["is_active"])


async def admin_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    tid = int(query.data.split("_")[-1])
    # قبلاً get_admin، بعد get_tasks_for، بعد get_action_logs پشتِ‌سرِهم صدا
    # زده می‌شدن — یعنی همون «باز شدنِ کندِ پنلِ مدیر» که حس می‌شد. هر سه
    # مستقلن، پس با asyncio.gather توی build_admin_view_content هم‌زمان
    # گرفته می‌شن.
    text, markup = await build_admin_view_content(tid)
    if text is None:
        await query.answer("مدیر یافت نشد.", show_alert=True)
        return
    await safe_edit_message_text(query, text, reply_markup=markup, parse_mode="Markdown")


async def admin_perms(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    tid = int(query.data.split("_")[-1])
    admin = await db.get_admin(tid)
    try:
        perms = json.loads(admin["permissions"])
    except Exception:
        perms = {}
    _name = admin["display_name"] or admin["full_name"]
    await safe_edit_message_text(query, 
        f"{box("⬆️ دسترسی‌های " + _name + "")}\n\n"
        f"📌 دسترسی‌ها را تغییر دهید:",
        reply_markup=kb.kb_admin_permissions(tid, perms),
        parse_mode="Markdown"
    )


async def perm_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    parts = query.data.split("_")
    tid = int(parts[1])
    perm_name = "_".join(parts[2:])
    admin = await db.get_admin(tid)
    if not admin:
        await query.answer("مدیر یافت نشد.", show_alert=True)
        return
    try:
        perms = json.loads(admin["permissions"])
    except Exception:
        perms = {}
    perms[perm_name] = not perms.get(perm_name, False)
    await db.set_admin_permission(tid, perm_name, perms[perm_name])
    await db.log_action(PISHVA_ID, "toggle_perm", perm_name + ": " + str(perms[perm_name]), tid)
    status_txt = "فعال ✅" if perms[perm_name] else "غیرفعال ❌"
    icon = "⬆️" if perms[perm_name] else "⬇️"
    try:
        await ctx.bot.send_message(chat_id=tid,
            text=icon + " دسترسی *" + perm_name + "* " + status_txt + " شد.",
            parse_mode="Markdown")
    except Exception:
        pass
    admin2 = await db.get_admin(tid)
    try:
        perms2 = json.loads(admin2["permissions"])
    except Exception:
        perms2 = perms
    await safe_edit_message_text(query, 
        "⬆️ *دسترسی‌های مدیر*\n\n📌 دسترسی‌ها را تغییر دهید:",
        reply_markup=kb.kb_admin_permissions(tid, perms2),
        parse_mode="Markdown"
    )


# ─── لغو اقدامات یک مدیرِ خاص، با امکانِ انتخابِ تاریخ + بازه‌ی ساعتی ────
# (همون قابلیتی که توی هشدارِ فعالیتِ مشکوک بود، ولی این‌جا از منوی
# مدیریتِ خودِ مدیر باز می‌شه، اجازه‌ی انتخابِ یه روزِ دیگه (نه فقط
# امروز) رو هم می‌ده، و بعد از انتخابِ روز، بازه‌ی ساعتی هم قابلِ‌تعیینه.)
async def admin_undo_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    tid = int(query.data[len("admin_undo_menu_"):])
    admin = await db.get_admin(tid)
    admin_name = (admin["display_name"] or admin["full_name"]) if admin else str(tid)
    await safe_edit_message_text(
        query,
        f"{box('↩️ لغو اقدامات — ' + admin_name)}\n\n"
        f"📌 اول تاریخِ موردنظر رو انتخاب کنید:",
        reply_markup=kb.kb_admin_undo_date_menu(tid),
        parse_mode="Markdown"
    )


async def _show_admin_undo_hourmenu(query, tid: int, date_str: str):
    admin = await db.get_admin(tid)
    admin_name = (admin["display_name"] or admin["full_name"]) if admin else str(tid)
    is_today = (date_str == today_gregorian())
    await safe_edit_message_text(
        query,
        f"{box('↩️ لغو اقدامات — ' + admin_name)}\n\n"
        f"📅 تاریخ: {date_label_fa(date_str)}\n"
        f"📌 بازه‌ی زمانیِ اقدام‌هایی که می‌خواید خنثی بشن رو انتخاب کنید:",
        reply_markup=kb.kb_admin_undo_hourmenu(tid, date_str, is_today),
        parse_mode="Markdown"
    )


async def admin_undo_daypick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    rest = query.data[len("admin_undo_daypick_"):]
    tid_str, days_str = rest.split("_")
    tid = int(tid_str)
    date_str = days_ago_gregorian(int(days_str))
    await _show_admin_undo_hourmenu(query, tid, date_str)


async def admin_undo_daycustom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    tid = int(query.data[len("admin_undo_daycustom_"):])
    ctx.user_data["undo_admin_tid"] = tid
    await safe_edit_message_text(
        query,
        "✍️ تاریخِ موردنظر رو بفرستید. فرمت‌های قابلِ قبول:\n"
        "• یه عددِ ساده، مثلاً «۱۷» یعنی ۱۷ روز پیش\n"
        "• شمسی، مثلاً «1403/06/20»\n"
        "• میلادی، مثلاً «2026-09-10»",
        reply_markup=kb.kb_cancel(f"admin_undo_menu_{tid}")
    )
    return ST_ADMIN_UNDO_DATE


async def admin_undo_date_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tid = ctx.user_data.get("undo_admin_tid")
    if not tid:
        return ConversationHandler.END
    date_str = parse_admin_undo_date(update.message.text)
    if date_str is None:
        await update.message.reply_text(
            "❌ تاریخِ نامعتبر. دوباره بفرستید (مثلاً «1403/06/20» یا «۱۷»):"
        )
        return ST_ADMIN_UNDO_DATE
    admin = await db.get_admin(tid)
    admin_name = (admin["display_name"] or admin["full_name"]) if admin else str(tid)
    is_today = (date_str == today_gregorian())
    await update.message.reply_text(
        f"{box('↩️ لغو اقدامات — ' + admin_name)}\n\n"
        f"📅 تاریخ: {date_label_fa(date_str)}\n"
        f"📌 بازه‌ی زمانیِ اقدام‌هایی که می‌خواید خنثی بشن رو انتخاب کنید:",
        reply_markup=kb.kb_admin_undo_hourmenu(tid, date_str, is_today),
        parse_mode="Markdown"
    )
    return ConversationHandler.END


def _store_undo_batch(ctx, log_ids: list) -> str:
    """لیستِ idهای خنثی‌شده رو با یه شناسه‌ی کوتاه توی bot_data نگه می‌داره تا
    دکمه‌ی Redo بتونه بعداً همینا رو دوباره اعمال کنه (بدون نیاز به گنجوندنِ
    کلِ لیست توی callback_data که محدودیتِ طول داره)."""
    batches = ctx.bot_data.setdefault("undo_batches", {})
    batch_id = uuid.uuid4().hex[:10]
    batches[batch_id] = log_ids
    return batch_id


def _undo_result_markup(tid: int, reverted: list, ctx) -> InlineKeyboardMarkup:
    rows = []
    if reverted:
        batch_id = _store_undo_batch(ctx, [r["id"] for r in reverted])
        rows.append([InlineKeyboardButton("↪️ Redo (برگردوندنِ همینا)",
                     callback_data=f"admin_redo_{tid}_{batch_id}", style="success")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"admin_view_{tid}", style="danger")])
    return InlineKeyboardMarkup(rows)


async def _do_admin_undo(query, ctx, tid: int, date_str: str, hour_from, hour_to):
    admin = await db.get_admin(tid)
    admin_name = (admin["display_name"] or admin["full_name"]) if admin else str(tid)
    reverted, skipped = await db.undo_admin_actions_range(tid, date_str, hour_from, hour_to)
    range_label = "کلِ روز" if (hour_from in (None, 0) and hour_to in (None, 23)) else f"ساعت {hour_from} تا {hour_to}"
    date_label = date_label_fa(date_str)
    await db.log_action(PISHVA_ID, "undo_admin_actions",
                         f"خنثی‌سازیِ اقدامات {admin_name} ({date_label} — {range_label}): {len(reverted)} مورد")
    await safe_edit_message_text(
        query,
        f"{box('↩️ لغو اقدامات — ' + admin_name)}\n\n"
        f"📅 تاریخ: {date_label}\n"
        f"📌 بازه: {range_label}\n"
        f"✅ برگردانده‌شده: `{len(reverted)}`\n"
        f"⏭️ ردشده (بدون امکان بازگشت خودکار): `{len(skipped)}`",
        reply_markup=_undo_result_markup(tid, reverted, ctx),
        parse_mode="Markdown"
    )


async def admin_redo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """برعکسِ Undo: اقدام‌هایی که با دکمه‌ی Redoِ زیرِ یه نتیجه‌ی Undo
    مشخص شدن رو دوباره اعمال می‌کنه. فقط برای همون دسته‌ای که تازه undo
    شده کار می‌کنه (نه هر اقدامِ خنثی‌شده‌ی قدیمی‌ای)."""
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    rest = query.data[len("admin_redo_"):]
    tid_str, batch_id = rest.split("_", 1)
    tid = int(tid_str)
    admin = await db.get_admin(tid)
    admin_name = (admin["display_name"] or admin["full_name"]) if admin else str(tid)
    batches = ctx.bot_data.get("undo_batches", {})
    log_ids = batches.pop(batch_id, None)
    if not log_ids:
        await safe_edit_message_text(
            query,
            f"{box('↪️ Redo اقدامات — ' + admin_name)}\n\n"
            f"⛔ این دسته دیگه در دسترس نیست (یا قبلاً Redo شده، یا ربات ری‌استارت شده).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"admin_view_{tid}", style="danger")]]),
            parse_mode="Markdown"
        )
        return
    redone, skipped = await db.redo_admin_actions(log_ids)
    await db.log_action(PISHVA_ID, "redo_admin_actions",
                         f"بازگردانیِ دوبارهٔ اقدامات {admin_name}: {len(redone)} مورد")
    await safe_edit_message_text(
        query,
        f"{box('↪️ Redo اقدامات — ' + admin_name)}\n\n"
        f"✅ دوباره‌اجراشده: `{len(redone)}`\n"
        f"⏭️ ردشده: `{len(skipped)}`",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"admin_view_{tid}", style="danger")]]),
        parse_mode="Markdown"
    )


async def admin_undo_go(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    rest = query.data[len("admin_undo_go_"):]
    tid_str, date_str, hfrom_str, hto_str = rest.split("_")
    await _do_admin_undo(query, ctx, int(tid_str), date_str, int(hfrom_str), int(hto_str))


async def admin_undo_last(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    rest = query.data[len("admin_undo_last_"):]
    tid_str, hours_str = rest.split("_")
    tid, hours = int(tid_str), int(hours_str)
    from datetime import datetime
    # FIX: قبلاً datetime.now().hour (ساعتِ سرور/UTC) بود، در حالی که بقیه‌ی
    # منویِ Undo (و الان خودِ logged_at هم) بر پایه‌ی وقتِ تهرانه — دو ساعتِ
    # متفاوت توی یک فیچر قاطی شده بود.
    current_hour = datetime.now(TEHRAN_TZ).hour
    hour_from = max(0, current_hour - hours)
    await _do_admin_undo(query, ctx, tid, today_gregorian(), hour_from, current_hour)


async def admin_undo_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    rest = query.data[len("admin_undo_custom_"):]
    tid_str, date_str = rest.split("_", 1)
    tid = int(tid_str)
    ctx.user_data["undo_admin_tid"] = tid
    ctx.user_data["undo_admin_date"] = date_str
    await safe_edit_message_text(
        query,
        "✍️ بازه‌ی ساعتِ موردنظر رو بفرستید (مثلاً «۱۰ تا ۱۴» یا «10-14»؛ برای یک ساعتِ خاص فقط همون عدد رو بفرستید):",
        reply_markup=kb.kb_cancel(f"admin_undo_menu_{tid}")
    )
    return ST_ADMIN_UNDO_RANGE


async def admin_undo_range_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tid = ctx.user_data.get("undo_admin_tid")
    if not tid:
        return ConversationHandler.END
    hour_from, hour_to = parse_hour_range(update.message.text)
    if hour_from is None:
        await update.message.reply_text("❌ بازه‌ی نامعتبر. دوباره بفرستید (مثلاً «10-14»):")
        return ST_ADMIN_UNDO_RANGE
    admin = await db.get_admin(tid)
    admin_name = (admin["display_name"] or admin["full_name"]) if admin else str(tid)
    date_str = ctx.user_data.get("undo_admin_date") or today_gregorian()
    date_label = date_label_fa(date_str)
    reverted, skipped = await db.undo_admin_actions_range(tid, date_str, hour_from, hour_to)
    await db.log_action(PISHVA_ID, "undo_admin_actions",
                         f"خنثی‌سازیِ اقدامات {admin_name} ({date_label} — ساعت {hour_from} تا {hour_to}): {len(reverted)} مورد")
    await update.message.reply_text(
        f"{box('↩️ لغو اقدامات — ' + admin_name)}\n\n"
        f"📅 تاریخ: {date_label}\n"
        f"📌 بازه: ساعت {hour_from} تا {hour_to}\n"
        f"✅ برگردانده‌شده: `{len(reverted)}`\n"
        f"⏭️ ردشده (بدون امکان بازگشت خودکار): `{len(skipped)}`",
        reply_markup=_undo_result_markup(tid, reverted, ctx),
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def admin_warn_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    tid = int(query.data.split("_")[-1])
    ctx.user_data["warn_admin_tid"] = tid
    admin = await db.get_admin(tid)
    await safe_edit_message_text(query, f"⚠️ دلیل اخطار برای *{admin['display_name'] or admin['full_name']}*:",
                                   reply_markup=kb.kb_cancel("menu_pishva"), parse_mode="Markdown")
    return ST_ADMIN_WARNING_REASON


async def admin_warn_reason(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    tid = ctx.user_data.get("warn_admin_tid")
    if not tid:
        return ConversationHandler.END

    await db.add_admin_warning(tid, reason, PISHVA_ID)
    admin = await db.get_admin(tid)
    warn_bar = warning_bar_admin(admin["warnings"])
    ts = now_shamsi()

    notif = (
        f"{box('⚠️ اخطار رسمی')}\n\n"
        f"👤 {admin['display_name'] or admin['full_name']} عزیز،\n"
        f"شما یک اخطار رسمی دریافت کردید.\n"
        f"⏱️ زمان: `{ts}`\n"
        f"📋 دلیل: {reason}\n\n"
        f"⚠️ وضعیت اخطار:\n{warn_bar}"
    )
    try:
        await ctx.bot.send_message(chat_id=tid, text=notif, parse_mode="Markdown")
    except Exception:
        pass

    await db.log_action(PISHVA_ID, "admin_warning", f"اخطار به مدیر {tid}: {reason}", tid)
    await update.message.reply_text(f"✅ اخطار ثبت شد. مجموع اخطارها: {admin['warnings']}")
    return ConversationHandler.END


async def admin_clear_warnings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    tid = int(query.data.split("_")[-1])
    admin = await db.get_admin(tid)
    if not admin:
        await query.answer("مدیر یافت نشد.", show_alert=True)
        return
    await db.set_admin_warnings(tid, 0)
    await db.log_action(PISHVA_ID, "admin_clear_warnings", "پاک‌کردن اخطارهای مدیر", tid)
    await query.answer("✅ اخطارها پاک شد.", show_alert=True)
    admin2 = await db.get_admin(tid)
    warn_bar = warning_bar_admin(admin2["warnings"])
    role_label = "🏆 مدیر مسابقات" if admin2["role"] == ROLE_TOURNAMENT_MANAGER else "🛡️ مدیر امنیتی"
    tasks = await db.get_tasks_for(tid)
    _logs_rows, logs_total = await db.get_action_logs("all", tid)
    _name = admin2["display_name"] or admin2["full_name"]
    text = (
        f"{box("👤 پروفایل: " + _name + "")}\n\n"
        f"🔗 یوزرنیم: {admin2['username']}\n"
        f"💼 نقش: {role_label}\n"
        f"🆔 آیدی: `{admin2['telegram_id']}`\n"
        f"📅 تاریخ ثبت: `{str(admin2['joined_at'])[:10]}`\n"
        f"⏱️ آخرین فعالیت: `{str(admin2['last_active'] or '')[:16]}`\n"
        f"📊 اقدامات: `{logs_total}`\n"
        f"📋 وظایف: `{len(tasks)}`\n"
        f"{'✅ فعال' if admin2['is_active'] else '🔴 غیرفعال'}\n\n"
        f"{separator('⚠️ اخطارها')}\n"
        f"{warn_bar}"
    )
    await safe_edit_message_text(query, text, reply_markup=kb.kb_admin_actions(tid, admin2["is_active"]), parse_mode="Markdown")


async def admin_kick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    tid = int(query.data.split("_")[-1])
    admin = await db.get_admin(tid)
    await db.kick_admin(tid)
    await db.log_action(PISHVA_ID, "kick_admin", f"اخراج مدیر: {admin['full_name']}", tid)
    try:
        await ctx.bot.send_message(chat_id=tid, text="🚫 دسترسی شما به ربات توسط مدیر ارشد لغو شد.")
    except Exception:
        pass
    await safe_edit_message_text(query, f"🚫 مدیر *{admin['full_name']}* اخراج شد.", reply_markup=kb.kb_back("menu_admins"), parse_mode="Markdown")


async def admin_revive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """FIX: قبلاً بعد از اخراجِ یک مدیر، هیچ دکمه/راهی برای برگردوندنش نبود."""
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
    await db.revive_admin(tid)
    await db.log_action(PISHVA_ID, "revive_admin", f"احیای مدیر: {admin['full_name']}", tid)
    try:
        await ctx.bot.send_message(chat_id=tid, text="✅ دسترسی شما به ربات توسط مدیر ارشد دوباره فعال شد.\n/start بزنید.")
    except Exception:
        pass
    await safe_edit_message_text(query, f"🔄 مدیر *{admin['full_name']}* احیا شد.", reply_markup=kb.kb_back("menu_admins"), parse_mode="Markdown")


async def admin_msg_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    tid = int(query.data.split("_")[-1])
    ctx.user_data["msg_target"] = tid
    admin = await db.get_admin(tid)
    await safe_edit_message_text(query, f"✍️ پیام به *{admin['display_name'] or admin['full_name']}*:", parse_mode="Markdown")
    return ST_SEND_MSG_TEXT


async def admin_task_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    tid = int(query.data.split("_")[-1])
    ctx.user_data["task_admin"] = tid
    admin = await db.get_admin(tid)
    await safe_edit_message_text(query, f"📝 عنوان وظیفه برای *{admin['display_name'] or admin['full_name']}*:",
                                   parse_mode="Markdown")
    return ST_TASK_TITLE


async def admin_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tid = int(query.data.split("_")[-1])
    await admin_view(update, ctx)


# ─── /ss command (quick task assignment) ─────────────────────
async def cmd_ss(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != PISHVA_ID:
        return
    admins = await db.get_active_admins()
    if not admins:
        await update.message.reply_text("❗ هیچ مدیری وجود ندارد.")
        return ConversationHandler.END
    rows = []
    for i in range(0, len(admins), 2):
        row = [InlineKeyboardButton(
            f"👤 {a['display_name'] or a['full_name']}",
            callback_data=f"task_to_{a['telegram_id']}"
        ) for a in admins[i:i+2]]
        rows.append(row)
    await update.message.reply_text(
        "📋 به کدام مدیر وظیفه می‌دهید؟",
        reply_markup=InlineKeyboardMarkup(rows)
    )
    return ST_TASK_SELECT_ADMIN


# ══════════════════════════════════════════
# FEEDBACK
# ══════════════════════════════════════════

async def fb_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    fb_type = query.data.replace("fb_", "")
    ctx.user_data["fb_type"] = fb_type
    prompts = {
        "critique": "📝 انتقاد خود را بنویسید:",
        "suggestion": "💡 پیشنهاد خود را بنویسید:",
        "praise": "🏆 پیام تقدیر خود را بنویسید:",
        "feature": "🔧 عنوان قابلیت پیشنهادی را بنویسید:",
    }
    await safe_edit_message_text(query, prompts.get(fb_type, "📝 متن را وارد کنید:"))
    return ST_FEEDBACK_TEXT


async def fb_text_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    fb_type = ctx.user_data.get("fb_type", "critique")
    uid = update.effective_user.id

    if fb_type == "feature":
        ctx.user_data["feature_title"] = text
        await update.message.reply_text("📋 توضیحات بیشتر را وارد کنید:")
        return ST_FEATURE_DESC

    await db.create_feedback(uid, fb_type, "", text)
    pname = await pishva_display()
    admin = await db.get_admin(uid)
    sender = admin["display_name"] or admin["full_name"] if admin else str(uid)
    type_labels = {"critique": "📝 انتقاد", "suggestion": "💡 پیشنهاد", "praise": "🏆 تقدیر"}

    await notify_pishva(
        ctx.bot,
        f"{type_labels.get(fb_type, '📋')} از *{sender}*:\n\n_{text}_\n\n⏱️ `{now_shamsi()}`"
    )
    await update.message.reply_text("✅ ارسال شد. ممنون!", reply_markup=kb.kb_back("menu_feedback"))
    return ConversationHandler.END


async def fb_feature_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip()
    title = ctx.user_data.get("feature_title", "")
    uid = update.effective_user.id
    await db.create_feedback(uid, "feature", title, desc)
    admin = await db.get_admin(uid)
    sender = admin["display_name"] or admin["full_name"] if admin else str(uid)
    await notify_pishva(
        ctx.bot,
        f"🔧 *درخواست قابلیت* از *{sender}*:\n📌 {title}\n📝 {desc}\n\n⏱️ `{now_shamsi()}`"
    )
    await update.message.reply_text("✅ درخواست ارسال شد!", reply_markup=kb.kb_back("menu_feedback"))
    return ConversationHandler.END


async def fb_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    fb_type = query.data.replace("fb_view_", "")
    feedbacks = await db.get_all_feedback()
    if fb_type != "all":
        feedbacks = [f for f in feedbacks if f["fb_type"] == fb_type]
    if not feedbacks:
        await safe_edit_message_text(query, "❗ موردی وجود ندارد.", reply_markup=kb.kb_back("menu_feedback"))
        return
    type_icons = {"critique": "📝", "suggestion": "💡", "praise": "🏆", "feature": "🔧", "report": "🚨"}
    lines = []
    for f in feedbacks[:15]:
        icon = type_icons.get(f["fb_type"], "📋")
        lines.append(f"{icon} `{str(f['sent_at'])[:10]}`: {f['content'][:80]}")
    await safe_edit_message_text(query, 
        f"{box('📋 انتقادات و پیشنهادات')}\n\n" + "\n\n".join(lines),
        reply_markup=kb.kb_back("menu_feedback"),
        parse_mode="Markdown"
    )


# ══════════════════════════════════════════
# HELP
# ══════════════════════════════════════════

HELP_TEXTS = {
    "tournament": (
        "🏅 *راهنمای مدیریت تورنمنت*\n\n"
        "• افزودن تورنمنت: فقط نام کافی است.\n"
        "• مدیریت: ویرایش، پایان، تعویق و حذف.\n"
        "• پیش‌فرض: تورنمنتی که مسابقات به آن اضافه می‌شوند.\n"
        "• حذف فقط توسط مدیر ارشد ممکن است."
    ),
    "players": (
        "👤 *راهنمای مدیریت بازیکنان*\n\n"
        "• ابتدا کلاس بسازید، سپس بازیکن ثبت کنید.\n"
        "• اخطار ۳: بازیکن باید تعلیق یا حذف شود.\n"
        "• احیا: بازگشت بازیکن به لیست فعال.\n"
        "• ⛔ نشانه حذف در مسابقه است."
    ),
    "matches": (
        "♟️ *راهنمای مدیریت مسابقات*\n\n"
        "• بازیکن سفید و سیاه از لیست ادامه‌دهندگان انتخاب شود.\n"
        "• قرعه‌کشی هوشمند: اولویت با زوج‌هایی که قبلاً بازی نکرده‌اند.\n"
        "• پس از ثبت نتیجه می‌توان بازنده را حذف کرد."
    ),
    "comms": (
        "📡 *راهنمای مخابرات*\n\n"
        "• مدیر ارشد می‌تواند بیانیه، خبر و پیام مستقیم بفرستد.\n"
        "• ادمین‌ها می‌توانند به مدیر ارشد و یکدیگر پیام دهند.\n"
        "• پیام‌های مشکوک قابل گزارش هستند."
    ),
    "warnings": (
        "⚠️ *راهنمای سیستم اخطار*\n\n"
        "بازیکنان: حداکثر ۳ اخطار\n"
        "• اخطار ۳: مدیر ارشد تصمیم می‌گیرد.\n\n"
        "ادمین‌ها: حداکثر ۵ اخطار\n"
        "• اخطار ۳: هشدار + رصد\n"
        "• اخطار ۴: کاهش دسترسی\n"
        "• اخطار ۵: تصمیم مدیر ارشد"
    ),
    "tasks": (
        "📋 *راهنمای وظایف*\n\n"
        "• مدیر ارشد وظیفه تعیین می‌کند.\n"
        "• ادمین پس از دریافت باید تأیید کند.\n"
        "• انجام‌شده یا انجام‌نشده گزارش دهید.\n"
        "• عدم انجام + دلیل = اطلاع‌رسانی به مدیر ارشد."
    ),
    "faq": (
        "❓ *سوالات متداول*\n\n"
        "Q: چطور بازیکن حذف‌شده را برگردانم؟\n"
        "A: از پنل بازیکن، دکمه 🔄 احیا را بزنید.\n\n"
        "Q: چطور تورنمنت پیش‌فرض را عوض کنم؟\n"
        "A: از بخش تورنمنت → پیش‌فرض فعلی.\n\n"
        "Q: قرعه‌کشی چطور کار می‌کند؟\n"
        "A: سیستم سعی می‌کند بازیکنانی انتخاب کند که قبلاً با هم بازی نداشته‌اند."
    ),
    "errors": (
        "🛠️ *کدهای خطا*\n\n"
        "```\n"
        "❌ ERROR_CODE: 001\n"
        "```\n"
        "دو بازیکن برای شروع وجود ندارد.\n\n"
        "```\n"
        "❌ ERROR_CODE: 002\n"
        "```\n"
        "هیچ کلاسی ثبت نشده است.\n\n"
        "```\n"
        "❌ ERROR_CODE: 003\n"
        "```\n"
        "تورنمنت پیش‌فرض تنظیم نشده.\n\n"
        "```\n"
        "❌ ERROR_CODE: 004\n"
        "```\n"
        "دسترسی در وضعیت فعلی غیرفعال است."
    ),
}


async def help_section(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    section = query.data.replace("help_", "")
    text = HELP_TEXTS.get(section, "❗ راهنمایی برای این بخش موجود نیست.")
    await safe_edit_message_text(query, 
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ صفحه قبل", callback_data="menu_help"),
             InlineKeyboardButton("▶️ صفحه بعد", callback_data="menu_help")],
            [InlineKeyboardButton("🔙 بازگشت به راهنما", callback_data="menu_help")],
        ]),
        parse_mode="Markdown"
    )


# ══════════════════════════════════════════
# TEAMS (additional handlers)
# ══════════════════════════════════════════

async def teams_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    team_mode = await db.get_setting("team_mode_enabled", "0")
    if team_mode != "1":
        await query.answer("🏆 حالت تیمی غیرفعال است. از تنظیمات مدیر ارشد فعال کنید.", show_alert=True)
        return
    await safe_edit_message_text(query, 
        f"{box('🏆 بخش تیم‌ها')}\n\n📌 بخش موردنظر را انتخاب کنید:",
        reply_markup=kb.kb_teams_menu(),
        parse_mode="Markdown"
    )


async def teams_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    teams = await db.get_all_teams()
    if not teams:
        await safe_edit_message_text(query, 
            f"{box('📋 تیم‌ها')}\n\n❗ هیچ تیمی ثبت نشده.",
            reply_markup=kb.kb_back("teams_menu"),
            parse_mode="Markdown"
        )
        return
    await safe_edit_message_text(query, 
        f"{box('📋 تیم‌ها')}\n\n📌 یک تیم انتخاب کنید:",
        reply_markup=kb.kb_team_list(teams),
        parse_mode="Markdown"
    )


async def team_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tid = int(query.data.split("_")[-1])
    team = await db.get_team(tid)
    if not team:
        await query.answer("تیم یافت نشد.", show_alert=True)
        return
    members = await db.get_team_members(tid)
    stats = await db.get_team_stats(tid)
    classes = list(set(m["class_name"] for m in members if m["class_name"]))
    captain_name = "تعیین نشده"
    if team["captain_id"]:
        p = await db.get_player(team["captain_id"])
        captain_name = p["full_name"] if p else "تعیین نشده"

    elite_count = sum(1 for m in members if m.get("is_elite"))
    special_count = sum(1 for m in members if m.get("is_special"))
    total = stats["wins"] + stats["losses"] + stats["draws"]
    win_pct = int(stats["wins"] / total * 100) if total > 0 else 0
    from helpers import progress_bar, warning_bar_admin
    power = progress_bar(win_pct)
    warn_bar = warning_bar_admin(team["warnings"], 3)

    text = (
        f"{box('🏆 پنل تیم: ' + team['name'])}\n\n"
        f"🎯 شعار: _{team['slogan'] or '—'}_\n"
        f"🔑 کد تیم: `{team['team_code']}`\n"
        f"📅 تاریخ ثبت: `{str(team['created_at'])[:10]}`\n"
        f"🙋 درخواست‌دهنده: {team['requester_name'] or '—'}\n"
        f"👑 سرگروه: {captain_name}\n\n"
        f"{separator('📊 آمار')}\n"
        f"👥 تعداد اعضا: `{len(members)}`\n"
        f"🏫 کلاس‌ها: {', '.join(classes) or '—'}\n"
        f"✅ برد: `{stats['wins']}` | ❌ باخت: `{stats['losses']}` | 🤝 مساوی: `{stats['draws']}`\n"
        f"🌟 بازیکنان ستاره: `{elite_count}` | ⚡ نیروهای ویژه: `{special_count}`\n"
        f"⚡ سطح قدرت: `{power}`\n"
        f"⚠️ اخطارهای تیم: {warn_bar}"
    )
    await safe_edit_message_text(query, text, reply_markup=kb.kb_team_actions(tid), parse_mode="Markdown")


async def team_members_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tid = int(query.data.split("_")[-1])
    members = await db.get_team_members(tid)
    team = await db.get_team(tid)
    if not members:
        await safe_edit_message_text(query, 
            f"👥 اعضای تیم *{team['name']}*:\n\n❗ هیچ عضوی ندارد.",
            reply_markup=kb.kb_back(f"team_view_{tid}"),
            parse_mode="Markdown"
        )
        return
    rows = []
    for m in members:
        icon = "🟢" if m["player_status"] == "active" else "🔴"
        rows.append([
            InlineKeyboardButton(
                f"{icon} {m['full_name']} [{m['class_name'] or ''}]",
                callback_data=f"tmember_{tid}_{m['player_id']}"
            )
        ])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"team_view_{tid}")])
    await safe_edit_message_text(query, 
        f"👥 اعضای تیم *{team['name']}* ({len(members)} نفر):",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown"
    )


async def team_member_actions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    tid = int(parts[1])
    pid = int(parts[2])
    p = await db.get_player(pid)
    await safe_edit_message_text(query, 
        f"👤 *{p['full_name']}*\n\nعملیات:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ حذف از تیم", callback_data=f"tremove_{tid}_{pid}"),
             InlineKeyboardButton("👑 سرگروه", callback_data=f"tcaptain_set_{tid}_{pid}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"team_members_{tid}")],
        ]),
        parse_mode="Markdown"
    )


async def team_remove_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await check_perm(query, "match_management"):
        return
    await query.answer()
    parts = query.data.split("_")
    tid = int(parts[-2])
    pid = int(parts[-1])
    await db.remove_team_member(tid, pid)
    p = await db.get_player(pid)
    await safe_edit_message_text(query, 
        f"✅ *{p['full_name']}* از تیم حذف شد.",
        reply_markup=kb.kb_back(f"team_members_{tid}"),
        parse_mode="Markdown"
    )


async def team_captain_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await check_perm(query, "match_management"):
        return
    await query.answer()
    tid = int(query.data.split("_")[-1])
    members = await db.get_team_members(tid)
    if not members:
        await safe_edit_message_text(query, "❗ تیم عضوی ندارد.", reply_markup=kb.kb_back(f"team_view_{tid}"))
        return
    rows = []
    for i in range(0, len(members), 2):
        row = [InlineKeyboardButton(f"👤 {m['full_name']}", callback_data=f"tcaptain_set_{tid}_{m['player_id']}") for m in members[i:i+2]]
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"team_view_{tid}")])
    await safe_edit_message_text(query, "👑 سرگروه را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(rows))


async def team_captain_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await check_perm(query, "match_management"):
        return
    await query.answer()
    parts = query.data.split("_")
    tid = int(parts[-2])
    pid = int(parts[-1])
    await db.update_team(tid, captain_id=pid)
    p = await db.get_player(pid)
    await safe_edit_message_text(query, 
        f"👑 *{p['full_name']}* به‌عنوان سرگروه تنظیم شد.",
        reply_markup=kb.kb_back(f"team_view_{tid}"),
        parse_mode="Markdown"
    )


async def team_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # حذف تیم فقط برای مدیر ارشد است
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ حذف تیم فقط توسط مدیر ارشد مجاز است.", show_alert=True)
        return
    await query.answer()
    tid = int(query.data.split("_")[-1])
    team = await db.get_team(tid)
    await db.delete_team(tid)
    await db.log_action(query.from_user.id, "delete_team", f"حذف تیم: {team['name']}", tid)
    await record_destructive_action(ctx.bot, query.from_user.id, "delete_team")
    await safe_edit_message_text(query, f"🗑️ تیم *{team['name']}* حذف شد.", reply_markup=kb.kb_back("teams_list"), parse_mode="Markdown")


async def team_warnings_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tid = int(query.data.split("_")[-1])
    team = await db.get_team(tid)
    from helpers import warning_bar_admin
    warn_bar = warning_bar_admin(team["warnings"], 3)
    await safe_edit_message_text(query, 
        f"⚠️ اخطارهای تیم *{team['name']}*:\n\n{warn_bar}\n\nمجموع: `{team['warnings']}`",
        reply_markup=kb.kb_back(f"team_view_{tid}"),
        parse_mode="Markdown"
    )


# ══════════════════════════════════════════
# SLASH COMMANDS
# ══════════════════════════════════════════

async def cmd_panic(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != PISHVA_ID:
        return
    await db.set_setting("system_status", "danger")
    ts = now_shamsi()
    pname = await pishva_display()
    notif = (
        f"{box('🔴 هشدار اضطراری')}\n\n"
        f"🚨 فرمان PANIC صادر شد!\n"
        f"🔒 تمام دسترسی‌ها قطع شد.\n"
        f"⏱️ `{ts}`\n"
        f"منتظر دستور {pname} باشید."
    )
    await broadcast_to_admins(ctx.bot, notif)
    await db.log_action(PISHVA_ID, "panic", "فرمان اضطراری PANIC")
    await update.message.reply_text("🔴 PANIC فعال شد. تمام دسترسی‌ها قطع.")


async def cmd_unpanic(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != PISHVA_ID:
        return
    await db.set_setting("system_status", "normal")
    ts = now_shamsi()
    await broadcast_to_admins(ctx.bot, f"🟢 سیستم به وضعیت نرمال بازگشت.\n⏱️ `{ts}`")
    await db.log_action(PISHVA_ID, "unpanic", "بازگشت از PANIC")
    await update.message.reply_text("🟢 PANIC غیرفعال شد.")


async def cmd_freeze_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != PISHVA_ID:
        return
    await db.set_setting("system_status", "aps")
    ts = now_shamsi()
    await broadcast_to_admins(ctx.bot, f"{box('🪽 حالت APS')}\n\n🔐 کنترل به APS واگذار شد.\n⏱️ `{ts}`")
    await db.log_action(PISHVA_ID, "freeze_all", "فعال‌سازی APS")
    await update.message.reply_text("🪽 APS فعال شد.")


async def cmd_terminal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != PISHVA_ID:
        return
    import os
    admins = await db.get_active_admins()
    status = await db.get_setting("system_status", "normal")
    default_t = await db.get_default_tournament()
    players = await db.get_all_players()
    matches = await db.get_matches_by_filter("all")
    db_size = os.path.getsize("chess_bot.db") / 1024 if os.path.exists("chess_bot.db") else 0

    text = (
        f"```\n"
        f"╔═══════════════════════╗\n"
        f"║  🖥️  SYSTEM TERMINAL   ║\n"
        f"╚═══════════════════════╝\n"
        f"TIME    : {now_shamsi()}\n"
        f"STATUS  : {status.upper()}\n"
        f"ADMINS  : {len(admins)} active\n"
        f"PLAYERS : {len(players)}\n"
        f"MATCHES : {len(matches)}\n"
        f"TOURNEY : {default_t['name'] if default_t else 'NONE'}\n"
        f"DB SIZE : {db_size:.1f} KB\n"
        f"```"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_backup_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != PISHVA_ID:
        return
    await update.message.reply_text("⏳ در حال تهیه بکاپ...")
    try:
        # چندسرور: /backup_now all  ← همه‌ی سرورها | /backup_now 2  ← فقط سرور ۲
        _arg = (ctx.args[0].strip() if ctx.args else "")
        if _arg == "all" or _arg.isdigit():
            from backup_utils import send_all_servers_backup, send_server_backup
            if _arg == "all":
                ok, fail = await send_all_servers_backup(ctx.bot, PISHVA_ID)
                await update.message.reply_text(f"✅ بکاپِ {ok} سرور ارسال شد" + (f"، {fail} ناموفق" if fail else "."))
            else:
                import servers as _sv
                if int(_arg) == 1 or not _sv.get(int(_arg)):
                    await update.message.reply_text("❌ سرورِ مجموعه‌ای با این شماره نیست.")
                else:
                    await send_server_backup(ctx.bot, PISHVA_ID, int(_arg))
            return
        from backup_utils import generate_excel_backup
        buf = await generate_excel_backup("all")
        await ctx.bot.send_document(
            chat_id=PISHVA_ID,
            document=buf,
            filename=f"emergency_backup_{now_shamsi().replace('/', '-').replace(' ', '_')}.xlsx",
            caption=f"💾 بکاپ اضطراری — {now_shamsi()}"
        )
        await db.log_action(PISHVA_ID, "backup_now", "بکاپ اضطراری")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {e}")


async def cmd_override_strike(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != PISHVA_ID:
        return
    parts = (update.message.text or "").split()
    if len(parts) < 3:
        await update.message.reply_text("❌ فرمت: /override_strike [admin_id] [count]")
        return
    try:
        admin_id = int(parts[1])
        count = int(parts[2])
        # قبلاً اینجا aiosqlite واقعی روی فایلِ محلی می‌نوشت، نه Turso؛ یعنی تغییر هرگز به
        # دیتابیسِ اصلی (و پنل هاب) نمی‌رسید. حالا از همان تابعِ استانداردِ پروژه (با invalidate کش).
        await db.set_admin_warnings(admin_id, count)
        await update.message.reply_text(f"✅ اخطارهای مدیر {admin_id} به {count} تغییر یافت.")
        await db.log_action(PISHVA_ID, "override_strike", f"تغییر اخطار مدیر {admin_id} به {count}", admin_id)
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {e}")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid == PISHVA_ID:
        text = (
            "👑 *دستورات مدیر ارشد:*\n\n"
            "/panic — وضعیت خطرناک اضطراری\n"
            "/unpanic — بازگشت به نرمال\n"
            "/freeze\\_all — حالت APS\n"
            "/terminal — تله‌متری سیستم\n"
            "/backup\\_now — بکاپ اضطراری\n"
            "/override\\_strike [id] [count] — تغییر اخطار\n"
            "/ss — اعطای سریع وظیفه\n"
            "/start — منوی اصلی"
        )
    else:
        admin = await db.get_admin(uid)
        if admin and admin["role"] == ROLE_TOURNAMENT_MANAGER:
            text = (
                "🏆 *دستورات مدیر مسابقات:*\n\n"
                "/start — منوی اصلی\n"
                "/help — این راهنما"
            )
        elif admin and admin["role"] == ROLE_SECURITY_MANAGER:
            text = (
                "🛡️ *دستورات مدیر امنیتی:*\n\n"
                "/start — منوی اصلی\n"
                "/help — این راهنما"
            )
        else:
            text = "⛔ شما دسترسی ندارید."
    await update.message.reply_text(text, parse_mode="Markdown")


# cmd_open / cmd_close (دستورات /open و /close) به workhours.py منتقل
# شدن — همون workhour_start/workhour_end هستن که هم از دکمه هم از
# دستور متنی صدا زده می‌شن و پایان خودکار/یادآور رو هم پشتیبانی می‌کنن.
