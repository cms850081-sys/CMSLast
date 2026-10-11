from datetime import datetime, timedelta
import database as db
import keyboards as kb
from config import PISHVA_ID
from servers import boss_chat as _boss_chat
from helpers import safe_edit_message_text

REMINDER_TYPES = {
    "match": {
        "enabled_key": "reminder_match_enabled",
        "interval_key": "reminder_match_interval",
        "last_sent_key": "reminder_match_last_sent",
        "default_interval": "6",
        "label": "♟️ مسابقات بدون نتیجه",
    },
    "task": {
        "enabled_key": "reminder_task_enabled",
        "interval_key": "reminder_task_interval",
        "last_sent_key": "reminder_task_last_sent",
        "default_interval": "12",
        "label": "📋 وظایف انجام‌نشده",
    },
    "db": {
        "enabled_key": "reminder_db_enabled",
        "interval_key": "reminder_db_interval",
        "last_sent_key": "reminder_db_last_sent",
        "default_interval": "1",
        "label": "🗄️ اتصال دیتابیس",
    },
    "admin": {
        "enabled_key": "reminder_admin_enabled",
        "interval_key": "reminder_admin_interval",
        "last_sent_key": "reminder_admin_last_sent",
        "default_interval": "24",
        "label": "👤 مدیران غیرفعال",
    },
}

ADMIN_INACTIVE_DAYS_KEY = "reminder_admin_inactive_days"
ADMIN_INACTIVE_DAYS_DEFAULT = "2"


async def _is_enabled(rtype):
    conf = REMINDER_TYPES[rtype]
    return (await db.get_setting(conf["enabled_key"], "1")) == "1"


async def _get_interval_hours(rtype):
    conf = REMINDER_TYPES[rtype]
    return int(await db.get_setting(conf["interval_key"], conf["default_interval"]))


async def _due(rtype):
    conf = REMINDER_TYPES[rtype]
    last_sent = await db.get_setting(conf["last_sent_key"], "")
    interval = await _get_interval_hours(rtype)
    if not last_sent:
        return True
    try:
        last_dt = datetime.fromisoformat(last_sent)
    except Exception:
        return True
    return datetime.now() - last_dt >= timedelta(hours=interval)


async def _mark_sent(rtype):
    conf = REMINDER_TYPES[rtype]
    await db.set_setting(conf["last_sent_key"], datetime.now().isoformat())


# ─── بررسی‌های هر نوع یادآور ─────────────────────────────────

async def check_unfinished_matches():
    matches = await db.get_pending_matches()
    if not matches:
        return None
    lines = [f"⏳ {m['white_name']} ⚔️ {m['black_name']}" for m in matches[:15]]
    extra = f"\n… و {len(matches) - 15} مورد دیگر" if len(matches) > 15 else ""
    return (
        f"⏰ یادآور: {len(matches)} مسابقه بدون نتیجه ثبت‌شده باقی مانده است.\n\n"
        + "\n".join(lines) + extra
    )


async def check_pending_tasks():
    tasks = await db.get_all_tasks()
    pending = [t for t in tasks if t["status"] == "pending"]
    if not pending:
        return None
    lines = [f"📌 {t['title']}" for t in pending[:15]]
    extra = f"\n… و {len(pending) - 15} مورد دیگر" if len(pending) > 15 else ""
    return (
        f"⏰ یادآور: {len(pending)} وظیفه هنوز انجام نشده است.\n\n"
        + "\n".join(lines) + extra
    )


async def check_db_connection():
    try:
        await db.get_setting("system_status", "")
        return None
    except Exception as e:
        return f"🔴 هشدار: اتصال به دیتابیس با خطا مواجه شد.\nجزئیات: {e}"


async def check_inactive_admins():
    days = int(await db.get_setting(ADMIN_INACTIVE_DAYS_KEY, ADMIN_INACTIVE_DAYS_DEFAULT))
    admins = await db.get_active_admins()
    cutoff = datetime.now() - timedelta(days=days)
    inactive = []
    for a in admins:
        try:
            last = datetime.fromisoformat(a["last_active"])
        except Exception:
            continue
        if last < cutoff:
            inactive.append(a)
    if not inactive:
        return None
    lines = [f"🔸 {a['display_name'] or a['full_name']}" for a in inactive[:15]]
    return (
        f"⏰ یادآور: {len(inactive)} مدیر بیش از {days} روز فعالیتی نداشته‌اند.\n\n"
        + "\n".join(lines)
    )


CHECK_FUNCS = {
    "match": check_unfinished_matches,
    "task": check_pending_tasks,
    "db": check_db_connection,
    "admin": check_inactive_admins,
}


# ─── اجرای دوره‌ای (job_queue) ────────────────────────────────

async def run_reminder_checks(application):
    master = await db.get_setting("reminder_master_enabled", "1")
    if master != "1":
        return
    group_id = await db.get_setting("announcement_group_id", "")
    channel_id = await db.get_setting("announcement_channel_id", "")
    group_on = await db.get_setting("reminder_broadcast_group_enabled", "1")
    channel_on = await db.get_setting("reminder_broadcast_channel_enabled", "1")
    for rtype, func in CHECK_FUNCS.items():
        if not await _is_enabled(rtype):
            continue
        if not await _due(rtype):
            continue
        try:
            text = await func()
        except Exception as e:
            text = f"🔴 هشدار: بررسی «{REMINDER_TYPES[rtype]['label']}» با خطا مواجه شد.\nجزئیات: {e}"
        if text:
            markup = kb.kb_reminder_unfinished_matches() if rtype == "match" else None
            try:
                await application.bot.send_message(_boss_chat(), text, reply_markup=markup)
            except Exception:
                pass
            if group_id and group_on == "1":
                try:
                    await application.bot.send_message(chat_id=int(group_id), text=text, reply_markup=markup)
                except Exception:
                    pass
            if channel_id and channel_on == "1":
                try:
                    await application.bot.send_message(chat_id=int(channel_id), text=text)
                except Exception:
                    pass
        await _mark_sent(rtype)


async def reminder_job(context):
    # چندسرور: یادآورها برای هر سرورِ فعال، در زمینه‌ی دیتابیسِ خودش
    import servers
    await servers.for_each_active(run_reminder_checks, context.application)


# ─── پنل مدیر ارشد ────────────────────────────────────────────────

async def pishva_reminders(update, ctx):
    query = update.callback_query
    await query.answer()
    master = (await db.get_setting("reminder_master_enabled", "1")) == "1"
    items = []
    for rtype, conf in REMINDER_TYPES.items():
        enabled = await _is_enabled(rtype)
        interval = await _get_interval_hours(rtype)
        items.append((rtype, conf["label"], enabled, interval))
    text = (
        "⏰ تنظیمات یادآورها\n\n"
        "در این بخش می‌توانید هر یادآور را جداگانه فعال یا غیرفعال کنید "
        "و بازه‌ی زمانی ارسال آن را تنظیم نمایید."
    )
    await safe_edit_message_text(query, text, reply_markup=kb.kb_reminders_menu(master, items))


async def reminder_toggle(update, ctx):
    query = update.callback_query
    await query.answer()
    key = query.data.replace("reminder_toggle_", "")
    if key == "master":
        current = await db.get_setting("reminder_master_enabled", "1")
        await db.set_setting("reminder_master_enabled", "0" if current == "1" else "1")
    else:
        conf = REMINDER_TYPES.get(key)
        if conf:
            current = await db.get_setting(conf["enabled_key"], "1")
            await db.set_setting(conf["enabled_key"], "0" if current == "1" else "1")
    await pishva_reminders(update, ctx)


async def reminder_interval_menu(update, ctx):
    query = update.callback_query
    await query.answer()
    rtype = query.data.replace("reminder_interval_", "")
    conf = REMINDER_TYPES.get(rtype)
    if not conf:
        return
    current = await _get_interval_hours(rtype)
    await safe_edit_message_text(query, 
        f"⏰ بازه‌ی زمانی «{conf['label']}» را انتخاب کنید:",
        reply_markup=kb.kb_reminder_interval_options(rtype, current)
    )


async def reminder_set_interval(update, ctx):
    query = update.callback_query
    await query.answer()
    _, _, rtype, hours = query.data.split("_")
    conf = REMINDER_TYPES.get(rtype)
    if not conf:
        return
    await db.set_setting(conf["interval_key"], hours)
    await pishva_reminders(update, ctx)
