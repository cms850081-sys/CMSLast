"""
workhours.py — مدیریت ساعت کاری

شامل رفتار قبلی (آغاز/پایان دستی توسط مدیر ارشد، هم با دکمه هم با
/open و /close) به‌علاوهٔ دو قابلیت جدید:

۱) ⏱ پایان خودکار ساعت کاری:
   وقتی روشن باشه، هر بار مدیر ارشد «آغاز ساعت کاری» رو بزنه، ازش یک عدد
   دقیقه (بین ۱ تا ۵۰۰۰) خواسته می‌شه. ساعت کاری دقیقاً بعد از همون
   مدت، بدون نیاز به دخالت مدیر ارشد، خودش بسته می‌شه — برای وقتی که مدیر ارشد
   یادش می‌ره خودش ببندتش.

۲) ⏰ یادآور عدم پایان:
   فقط وقتی پایان خودکار خاموشه معنی داره. اگه روشن باشه، بعد از گذشت
   یک مدت دقیقه‌ای (قابل تنظیم توسط مدیر ارشد) از آغاز ساعت کاری، اگه هنوز
   بسته نشده باشه، یک پیام یادآوری برای مدیر ارشد فرستاده می‌شه.

هر دو با precise_scheduler زمان‌بندی می‌شن، یعنی روی یک لحظهٔ مطلق
(نه شمارش معکوس نسبی) قفل می‌شن و بعد از ری‌استارت رایلوی هم دقیقاً
سر همون لحظه دوباره سرجاشون می‌شینن — نه یک ثانیه زودتر، نه دیرتر.
"""
import logging
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

import database as db
import keyboards as kb
from helpers import safe_edit_message_text, box, now_shamsi, broadcast_to_admins, notify_pishva, TEHRAN_TZ
from servers import is_boss as _boss, boss_chat as _boss_chat
from config import PISHVA_ID, ST_WORKHOURS_AUTOEND_MINUTES, ST_WORKHOURS_REMINDER_MINUTES
import precise_scheduler as sched

logger = logging.getLogger(__name__)

JOB_AUTOEND = "workhours_autoend"
JOB_REMINDER = "workhours_reminder"

MIN_MINUTES = 1
MAX_MINUTES = 5000


def _fmt_time(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = TEHRAN_TZ.localize(dt)
    else:
        dt = dt.astimezone(TEHRAN_TZ)
    return dt.strftime("%H:%M:%S")


# ─── Core start/end (shared by buttons and /open /close) ──────

async def _do_start(bot, job_queue, autoend_minutes: int = None):
    await db.set_setting("bot_active_for_admins", "1")
    await db.set_setting("working_hours_active", "1")
    ts = now_shamsi()

    # هر جاب معلق قبلی (از دور قبل) رو پاک می‌کنیم؛ چون داریم از نو شروع می‌کنیم
    await sched.cancel_persistent(job_queue, JOB_AUTOEND)
    await sched.cancel_persistent(job_queue, JOB_REMINDER)

    extra = ""
    if autoend_minutes:
        target = datetime.now(TEHRAN_TZ) + timedelta(minutes=autoend_minutes)
        await sched.schedule_persistent(job_queue, workhours_autoend_job, target, JOB_AUTOEND)
        extra = f"\n⏱️ پایان خودکار: `{_fmt_time(target)}` (بعد از {autoend_minutes} دقیقه)"
    else:
        reminder_on = (await db.get_setting("workhours_reminder_enabled", "0")) == "1"
        if reminder_on:
            minutes = int(await db.get_setting("workhours_reminder_minutes", "60"))
            target = datetime.now(TEHRAN_TZ) + timedelta(minutes=minutes)
            await sched.schedule_persistent(job_queue, workhours_reminder_job, target, JOB_REMINDER)

    notif = (
        f"{box('🟢 آغاز ساعت کاری')}\n\n"
        f"درود بر شما،\n"
        f"⏱️ ساعت کاری از `{ts}`\n"
        f"   توسط مدیر ارشد آغاز شد.{extra}\n\n"
        f"✅ دسترسی شما به ربات فعال است.\n"
        f"سیستم آماده دریافت فرمان. 🛰️"
    )
    await broadcast_to_admins(bot, notif)
    await db.log_action(_boss_chat(), "workhour_start", f"آغاز ساعت کاری: {ts}" + (f" (پایان خودکار {autoend_minutes} دقیقه)" if autoend_minutes else ""))
    return ts, extra


async def _do_end(bot, job_queue, reason: str = "manual"):
    await db.set_setting("bot_active_for_admins", "0")
    await db.set_setting("working_hours_active", "0")
    await sched.cancel_persistent(job_queue, JOB_AUTOEND)
    await sched.cancel_persistent(job_queue, JOB_REMINDER)
    ts = now_shamsi()

    if reason == "auto":
        notif = (
            f"{box('🔴 پایان خودکار ساعت کاری')}\n\n"
            f"خسته نباشید! 🌙\n"
            f"⏱️ ساعت کاری به‌طور خودکار در `{ts}`\n"
            f"   به پایان رسید (مدیر ارشد آن را دستی نبست).\n\n"
            f"🔒 دسترسی شما موقتاً قطع شد.\n"
            f"ممنون از زحمات شما! 🏆"
        )
    else:
        notif = (
            f"{box('🔴 پایان ساعت کاری')}\n\n"
            f"خسته نباشید! 🌙\n"
            f"⏱️ ساعت کاری در `{ts}`\n"
            f"   به پایان رسید.\n\n"
            f"🔒 دسترسی شما موقتاً قطع شد.\n"
            f"ممنون از زحمات شما! 🏆"
        )
    await broadcast_to_admins(bot, notif)
    await db.log_action(_boss_chat(), "workhour_end", f"پایان ساعت کاری ({reason}): {ts}")
    return ts


# ─── Job callbacks (fired by precise_scheduler at the exact moment) ───

async def workhours_autoend_job(context: ContextTypes.DEFAULT_TYPE):
    await sched.clear_target(JOB_AUTOEND)
    active = (await db.get_setting("working_hours_active", "0")) == "1"
    if not active:
        return  # مدیر ارشد خودش زودتر بسته بود
    await _do_end(context.bot, context.job_queue, reason="auto")


async def workhours_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    await sched.clear_target(JOB_REMINDER)
    active = (await db.get_setting("working_hours_active", "0")) == "1"
    if not active:
        return  # قبلاً بسته شده، یادآوری لازم نیست
    minutes = await db.get_setting("workhours_reminder_minutes", "60")
    text = (
        f"{box('⏰ یادآور ساعت کاری')}\n\n"
        f"ساعت کاری {minutes} دقیقه پیش آغاز شده و هنوز باز است.\n"
        f"اگر کارتان تمام شده، فراموش نکنید آن را ببندید."
    )
    await notify_pishva(context.bot, text)


# ─── Startup restoration ───────────────────────────────────────

async def restore_workhours_jobs(application):
    """موقع post_init صدا زده می‌شه تا رویدادهای معلق از قبل از ری‌استارت
    دقیقاً سر همون لحظهٔ ذخیره‌شده دوباره زمان‌بندی بشن."""
    await sched.restore_pending(application.job_queue, workhours_autoend_job, JOB_AUTOEND)
    await sched.restore_pending(application.job_queue, workhours_reminder_job, JOB_REMINDER)


# ─── Menu ───────────────────────────────────────────────────────

async def _render_status_extra() -> str:
    active = (await db.get_setting("working_hours_active", "0")) == "1"
    if not active:
        return ""
    target, _ = await sched.load_target(JOB_AUTOEND)
    if target:
        return f"\n\n⏱️ پایان خودکار در `{_fmt_time(target)}` انجام می‌شود."
    target, _ = await sched.load_target(JOB_REMINDER)
    if target:
        return f"\n\n⏰ در `{_fmt_time(target)}` یادآور ارسال می‌شود (اگر تا آن‌موقع بسته نشده باشد)."
    return ""


async def pishva_workhours(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    autoend_on = (await db.get_setting("workhours_autoend_enabled", "0")) == "1"
    reminder_on = (await db.get_setting("workhours_reminder_enabled", "0")) == "1"
    reminder_minutes = int(await db.get_setting("workhours_reminder_minutes", "60"))
    status_extra = await _render_status_extra()
    await safe_edit_message_text(query, 
        f"{box('🕐 ساعت کاری')}\n\n📌 عملیات را انتخاب کنید:{status_extra}",
        reply_markup=kb.kb_workhours(autoend_on, reminder_on, reminder_minutes),
        parse_mode="Markdown"
    )


# ─── Start (button + /open) ─────────────────────────────────────

async def workhour_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """نقطهٔ ورودی شروع ساعت کاری — هم از دکمه wh_start، هم از /open."""
    is_command = update.message is not None
    user_id = update.effective_user.id
    if not _boss(user_id):
        if not is_command:
            await update.callback_query.answer("⛔", show_alert=True)
        return ConversationHandler.END

    if not is_command:
        await update.callback_query.answer()

    autoend_on = (await db.get_setting("workhours_autoend_enabled", "0")) == "1"
    if autoend_on:
        text = f"⏱ پایان خودکار روشن است.\nمدت ساعت کاری را به *دقیقه* بفرستید (بین {MIN_MINUTES} تا {MAX_MINUTES}):"
        if is_command:
            await update.message.reply_text(text, parse_mode="Markdown")
        else:
            await safe_edit_message_text(update.callback_query, text, parse_mode="Markdown")
        return ST_WORKHOURS_AUTOEND_MINUTES

    ts, extra = await _do_start(ctx.bot, ctx.job_queue)
    reply_text = f"🟢 ساعت کاری آغاز شد و به همه اطلاع داده شد.\n⏱️ `{ts}`{extra}"
    if is_command:
        await update.message.reply_text(reply_text, parse_mode="Markdown")
    else:
        await safe_edit_message_text(update.callback_query, reply_text, reply_markup=kb.kb_back("pishva_panel"), parse_mode="Markdown")
    return ConversationHandler.END


async def workhour_start_minutes_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    if not raw.isdigit() or not (MIN_MINUTES <= int(raw) <= MAX_MINUTES):
        await update.message.reply_text(
            f"❌ عدد نامعتبر است. یک عدد صحیح بین {MIN_MINUTES} تا {MAX_MINUTES} (دقیقه) بفرستید:"
        )
        return ST_WORKHOURS_AUTOEND_MINUTES

    minutes = int(raw)
    ts, extra = await _do_start(ctx.bot, ctx.job_queue, autoend_minutes=minutes)
    await update.message.reply_text(
        f"🟢 ساعت کاری آغاز شد و به همه اطلاع داده شد.\n⏱️ `{ts}`{extra}",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


# ─── End (button + /close) ──────────────────────────────────────

async def workhour_end(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """نقطهٔ ورودی پایان دستی ساعت کاری — هم از دکمه wh_end، هم از /close."""
    is_command = update.message is not None
    user_id = update.effective_user.id
    if not _boss(user_id):
        if not is_command:
            await update.callback_query.answer("⛔", show_alert=True)
        return

    if not is_command:
        await update.callback_query.answer()

    ts = await _do_end(ctx.bot, ctx.job_queue, reason="manual")
    reply_text = f"🔴 ساعت کاری پایان یافت و به همه اطلاع داده شد.\n⏱️ `{ts}`"
    if is_command:
        await update.message.reply_text(reply_text, parse_mode="Markdown")
    else:
        await safe_edit_message_text(update.callback_query, reply_text, reply_markup=kb.kb_back("pishva_panel"), parse_mode="Markdown")


# ─── Auto-end toggle ─────────────────────────────────────────

async def workhours_autoend_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    current = await db.get_setting("workhours_autoend_enabled", "0")
    await db.set_setting("workhours_autoend_enabled", "0" if current == "1" else "1")
    await pishva_workhours(update, ctx)


# ─── Reminder toggle + minutes ─────────────────────────────────

async def workhours_reminder_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    current = await db.get_setting("workhours_reminder_enabled", "0")
    await db.set_setting("workhours_reminder_enabled", "0" if current == "1" else "1")
    await pishva_workhours(update, ctx)


async def workhours_reminder_minutes_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await safe_edit_message_text(query, 
        f"⏰ عدد دقیقهٔ یادآور را بفرستید (بین {MIN_MINUTES} تا {MAX_MINUTES}):"
    )
    return ST_WORKHOURS_REMINDER_MINUTES


async def workhours_reminder_minutes_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    if not raw.isdigit() or not (MIN_MINUTES <= int(raw) <= MAX_MINUTES):
        await update.message.reply_text(
            f"❌ عدد نامعتبر است. یک عدد صحیح بین {MIN_MINUTES} تا {MAX_MINUTES} (دقیقه) بفرستید:"
        )
        return ST_WORKHOURS_REMINDER_MINUTES

    await db.set_setting("workhours_reminder_minutes", raw)
    autoend_on = (await db.get_setting("workhours_autoend_enabled", "0")) == "1"
    reminder_on = (await db.get_setting("workhours_reminder_enabled", "0")) == "1"
    status_extra = await _render_status_extra()
    await update.message.reply_text(
        f"{box('🕐 ساعت کاری')}\n\n✅ دقیقهٔ یادآور روی {raw} تنظیم شد.{status_extra}",
        reply_markup=kb.kb_workhours(autoend_on, reminder_on, int(raw)),
        parse_mode="Markdown"
    )
    return ConversationHandler.END
