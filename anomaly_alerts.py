"""
هشدار حذف مشکوک
────────────────
وقتی یک ادمین توی یه بازه‌ی زمانیِ کوتاه، تعداد زیادی اقدام مخرب (اخراج/
تعلیق/حذف بازیکن از مسابقه، حذف مسابقه، حذف تیم، حذف تورنمنت) انجام بده،
فوری به مدیر ارشد هشدار داده می‌شه — با سه گزینه: خنثی‌سازیِ کل اقدام‌های
همون ادمین در همون روز، خاموش‌کردنِ ربات برای اون ادمین (اخراج فوری)، یا
تایید اینکه مشکلی نیست.

شمارش توی حافظه (نه دیتابیس) انجام می‌شه: چون خودِ ربات یک پردازش تکی
(single-process) است، این هم سریع‌تره و هم از رِیسِ «لاگ هنوز کامل ثبت
نشده» (چون log_action خودش fire-and-forget است) کاملاً بی‌نیازه.
"""
import time
from collections import defaultdict, deque

from telegram import Update
from telegram.ext import ContextTypes

import database as db
import keyboards as kb
from servers import is_boss as _boss, boss_chat as _boss_chat
from config import PISHVA_ID
from helpers import box, now_shamsi, today_gregorian, notify_pishva, safe_edit_message_text

# ─── اقدام‌هایی که «مخرب» حساب می‌شن و توی شمارشِ هشدار وارد می‌شن ───
DESTRUCTIVE_ACTIONS = {
    "kick_player", "eliminate_player", "suspend_player",
    "delete_match", "delete_team", "delete_tournament",
}

# admin_id -> deque[زمانِ وقوعِ هر اقدامِ مخرب (monotonic)]
_recent_destructive = defaultdict(deque)

# admin_id هایی که یک هشدار براشون ارسال شده و هنوز مدیر ارشد روش تصمیم
# نگرفته (نه خنثی‌سازی، نه خاموشی، نه تاییدِ «مشکلی نیست»).
# FIX (باگِ «بعد از تصمیمِ اول، دیگه برای مشکلِ دوم هشدار نمی‌آد»): قبلاً
# سرکوبِ هشدارِ بعدی صرفاً بر مبنای زمان بود (_last_alert_at + همون
# window_sec شمارش) — یعنی اگه مدیر ارشد زود تصمیم می‌گرفت ولی ادمین
# بلافاصله دوباره شروع می‌کرد به کارِ مشکوک، تا پایانِ همون بازه‌ی زمانی
# (که می‌تونست ۱۰+ دقیقه باشه) هیچ هشدارِ تازه‌ای نمی‌رفت — طوری که به نظر
# می‌رسید «دیگه هیچ‌وقت» هشدار نمی‌آد. حالا سرکوب صرفاً وضعیت‌محوره: تا
# وقتی مدیر ارشد تصمیم نگرفته، هشدارِ تازه نمی‌ره؛ به‌محضِ تصمیم (هر کدوم
# از سه گزینه)، شمارنده صفر می‌شه و اولین دسته‌ی جدیدِ اقدام‌های مخرب
# (یعنی «مشکلِ دوم») بلافاصله یک هشدارِ تازه می‌سازه.
_pending_alert = set()

# ─── تصمیم‌گیریِ خودکار برای هشدارِ فعالیتِ مشکوک ───────────────
# اگه فعال باشه، به‌جای فرستادنِ هشدار با سه دکمه و صبر برای تصمیمِ دستیِ
# مدیر ارشد، همون لحظه یکی از این اقدام‌ها به‌صورت خودکار انجام می‌شه و فقط
# یک پیامِ اطلاع‌رسانی (بدون دکمه) برای مدیر ارشد فرستاده می‌شه.
AUTO_ACTION_DISABLE_NOTIFY = "disable_notify"                # خاموشی مدیر + اطلاع
AUTO_ACTION_DISABLE_UNDO_NOTIFY = "disable_undo_notify"       # خاموشی + لغو همه‌ی اقدامات + اطلاع
AUTO_ACTION_UNDO_NOTIFY = "undo_notify"                       # فقط لغو همه‌ی اقدامات (+ اطلاع)
AUTO_ACTION_NOTIFY_ONLY = "notify_only"                       # فقط اطلاع به مدیر ارشد

AUTO_ACTION_LABELS = {
    AUTO_ACTION_DISABLE_NOTIFY: "🔇 خاموشی مدیر + اطلاع به مدیر ارشد",
    AUTO_ACTION_DISABLE_UNDO_NOTIFY: "🔇↩️ خاموشی + لغو همه‌ی اقدامات + اطلاع",
    AUTO_ACTION_UNDO_NOTIFY: "↩️ فقط لغو همه‌ی اقدامات",
    AUTO_ACTION_NOTIFY_ONLY: "🔔 فقط اطلاع به مدیر ارشد",
}


# ─── تنظیمِ اختصاصیِ هر ادمین (به‌جز تنظیمِ کلی) ────────────────────
# اگه برای یه ادمین مقدار اختصاصی ثبت نشده باشه، همون تنظیمِ کلی/سراسری
# براش اعمال می‌شه. مقدارِ اختصاصی با کلیدهایی به شکلِ
# sadel_admin_<field>_<admin_id> توی همون جدولِ تنظیماتِ کلی (system_settings)
# ذخیره می‌شه — بدون نیاز به تغییرِ اسکیمای دیتابیس.
def _admin_override_key(field: str, admin_id: int) -> str:
    return f"sadel_admin_{field}_{admin_id}"


async def get_admin_override(admin_id: int, field: str):
    """مقدارِ اختصاصیِ ثبت‌شده برای این ادمین رو برمی‌گردونه، یا None اگه
    چیزی ثبت نشده (یعنی این ادمین از تنظیمِ کلی پیروی می‌کنه). field یکی
    از 'enabled'، 'threshold' یا 'window' است."""
    val = await db.get_setting(_admin_override_key(field, admin_id), "")
    return val or None


async def set_admin_override(admin_id: int, field: str, value):
    """value=None یعنی حذفِ تنظیمِ اختصاصی (بازگشت به پیروی از تنظیمِ کلی)؛
    در غیر این‌صورت مقدار به‌عنوانِ تنظیمِ اختصاصیِ همین ادمین ذخیره می‌شه."""
    await db.set_setting(_admin_override_key(field, admin_id), value if value is not None else "")


async def get_effective_settings(admin_id: int):
    """تنظیمِ مؤثر برای این ادمین رو برمی‌گردونه: (enabled, threshold, window_min).
    اول تنظیمِ اختصاصیِ همون ادمین چک می‌شه، وگرنه تنظیمِ کلی."""
    enabled_ov = await get_admin_override(admin_id, "enabled")
    enabled = enabled_ov if enabled_ov is not None else await db.get_setting("suspicious_alert_enabled", "1")

    threshold_ov = await get_admin_override(admin_id, "threshold")
    window_ov = await get_admin_override(admin_id, "window")
    try:
        threshold = int(threshold_ov) if threshold_ov is not None else int(await db.get_setting("suspicious_deletion_threshold", "5"))
        window_min = int(window_ov) if window_ov is not None else int(await db.get_setting("suspicious_deletion_window_minutes", "10"))
    except (TypeError, ValueError):
        threshold, window_min = 5, 10
    return enabled, threshold, window_min


async def record_destructive_action(bot, admin_id: int, action_type: str):
    """بعد از ثبتِ هر اقدامِ مخرب (توسط یک ادمینِ عادی، نه خودِ مدیر ارشد)
    صدا زده می‌شه. اگه تعداد این اقدام‌ها توی بازه‌ی زمانیِ تنظیم‌شده از
    آستانه بیشتر بشه، فوری به مدیر ارشد هشدار می‌ده (یا در صورتِ فعال بودنِ
    تصمیم‌گیریِ خودکار، بلافاصله اقدامِ تنظیم‌شده رو خودش انجام می‌ده)."""
    if _boss(admin_id) or action_type not in DESTRUCTIVE_ACTIONS:
        return

    enabled, threshold, window_min = await get_effective_settings(admin_id)
    if enabled != "1":
        return

    # تا وقتی هشدارِ قبلیِ همین ادمین تصمیم‌گیری نشده، دوباره مزاحمِ مدیر
    # ارشد نمی‌شیم — ولی برخلافِ قبل، این سرکوب صرفاً تا لحظه‌ی تصمیمه، نه
    # یک بازه‌ی زمانیِ ثابت (پایینِ فایل، سه‌تا تابعِ تصمیم این ست رو پاک
    # می‌کنن تا مشکلِ بعدی بتونه فوری هشدارِ تازه بسازه).
    if admin_id in _pending_alert:
        return

    window_sec = max(window_min, 1) * 60

    now = time.monotonic()
    dq = _recent_destructive[admin_id]
    dq.append(now)
    while dq and now - dq[0] > window_sec:
        dq.popleft()

    if len(dq) < threshold:
        return

    admin = await db.get_admin(admin_id)
    admin_name = (admin["display_name"] or admin["full_name"]) if admin else str(admin_id)
    date_str = today_gregorian()
    count = len(dq)
    dq.clear()  # این دسته دیده شد؛ شمارشِ «مشکلِ بعدی» از صفر شروع می‌شه

    auto_enabled = await db.get_setting("suspicious_auto_enabled", "0")
    if auto_enabled == "1":
        action = await db.get_setting("suspicious_auto_action", AUTO_ACTION_NOTIFY_ONLY)
        await _run_auto_action(bot, admin_id, admin_name, action, date_str, count, window_min)
        return

    _pending_alert.add(admin_id)
    date_compact = date_str.replace("-", "")
    text = (
        f"{box('🚨 هشدار — فعالیت مشکوک ادمین')}\n\n"
        f"👤 ادمین: *{admin_name}*\n"
        f"🆔 آیدی: `{admin_id}`\n"
        f"🗑️ تعداد اقدام مخرب: `{count}` در `{window_min}` دقیقه‌ی اخیر\n"
        f"⏱️ `{now_shamsi()}`\n\n"
        f"📌 یکی از گزینه‌های زیر را انتخاب کنید:"
    )
    await notify_pishva(bot, text, reply_markup=kb.kb_suspicious_alert(admin_id, date_compact))


async def _run_auto_action(bot, admin_id: int, admin_name: str, action: str,
                            date_str: str, count: int, window_min: int):
    """اقدامِ تنظیم‌شده‌ی «تصمیم‌گیری خودکار» رو همین الان انجام می‌ده و
    یک پیامِ اطلاع‌رسانی (بدون دکمه) برای مدیر ارشد می‌فرسته."""
    done_lines = []
    if action in (AUTO_ACTION_DISABLE_NOTIFY, AUTO_ACTION_DISABLE_UNDO_NOTIFY):
        await db.kick_admin(admin_id)
        await db.log_action(_boss_chat(), "kick_admin",
                             f"خاموشی خودکار بابت فعالیت مشکوک: {admin_name}", admin_id)
        done_lines.append("🔇 دسترسیِ این ادمین به‌صورت خودکار قطع شد.")
        try:
            await bot.send_message(chat_id=admin_id,
                text="🚫 دسترسی شما به ربات به دلیل فعالیت مشکوک، به‌صورت خودکار قطع شد.")
        except Exception:
            pass

    if action in (AUTO_ACTION_DISABLE_UNDO_NOTIFY, AUTO_ACTION_UNDO_NOTIFY):
        reverted, skipped = await db.undo_admin_actions(admin_id, date_str)
        await db.log_action(_boss_chat(), "undo_admin_actions",
                             f"خنثی‌سازیِ خودکارِ اقدامات {admin_name} در {date_str}: {len(reverted)} مورد")
        done_lines.append(f"↩️ `{len(reverted)}` اقدام به‌صورت خودکار خنثی شد "
                           f"(`{len(skipped)}` مورد قابلِ بازگشتِ خودکار نبود).")

    if action == AUTO_ACTION_NOTIFY_ONLY or not done_lines:
        done_lines.append("🔔 فقط اطلاع‌رسانی — اقدامی به‌صورت خودکار انجام نشد.")

    text = (
        f"{box('🤖 تصمیم‌گیریِ خودکار — فعالیت مشکوک ادمین')}\n\n"
        f"👤 ادمین: *{admin_name}*\n"
        f"🆔 آیدی: `{admin_id}`\n"
        f"🗑️ تعداد اقدام مخرب: `{count}` در `{window_min}` دقیقه‌ی اخیر\n"
        f"⏱️ `{now_shamsi()}`\n\n" + "\n".join(done_lines)
    )
    await notify_pishva(bot, text)


def _parse_alert_data(data: str):
    """'sadel_undo_<admin_id>_<yyyymmdd>' یا 'sadel_disable_<admin_id>' یا
    'sadel_dismiss_<admin_id>_<yyyymmdd>' رو پارس می‌کنه."""
    parts = data.split("_")
    admin_id = int(parts[2])
    date_compact = parts[3] if len(parts) > 3 else None
    date_str = f"{date_compact[0:4]}-{date_compact[4:6]}-{date_compact[6:8]}" if date_compact else None
    return admin_id, date_str


async def suspicious_undo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    admin_id, date_str = _parse_alert_data(query.data)
    _pending_alert.discard(admin_id)
    _recent_destructive[admin_id].clear()
    admin = await db.get_admin(admin_id)
    admin_name = (admin["display_name"] or admin["full_name"]) if admin else str(admin_id)
    reverted, skipped = await db.undo_admin_actions(admin_id, date_str)
    await db.log_action(_boss_chat(), "undo_admin_actions",
                         f"خنثی‌سازی اقدامات {admin_name} در {date_str}: {len(reverted)} مورد")
    old_text = query.message.text or ""
    text = (
        f"{old_text}\n\n"
        f"↩️ *خنثی‌سازی انجام شد*\n"
        f"✅ برگردانده‌شده: `{len(reverted)}`\n"
        f"⏭️ ردشده (بدون امکان بازگشت خودکار): `{len(skipped)}`"
    )
    await safe_edit_message_text(query, text, reply_markup=kb.kb_back("menu_pishva"))


async def suspicious_disable(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    admin_id, _ = _parse_alert_data(query.data)
    _pending_alert.discard(admin_id)
    _recent_destructive[admin_id].clear()
    admin = await db.get_admin(admin_id)
    admin_name = (admin["display_name"] or admin["full_name"]) if admin else str(admin_id)
    await db.kick_admin(admin_id)
    await db.log_action(_boss_chat(), "kick_admin",
                         f"خاموشی خودکار بابت فعالیت مشکوک: {admin_name}", admin_id)
    try:
        await ctx.bot.send_message(
            chat_id=admin_id,
            text="🚫 دسترسی شما به ربات به دلیل فعالیت مشکوک توسط مدیر ارشد قطع شد."
        )
    except Exception:
        pass
    old_text = query.message.text or ""
    await safe_edit_message_text(
        query,
        f"{old_text}\n\n🔇 *ادمین «{admin_name}» غیرفعال شد.*",
        reply_markup=kb.kb_back("menu_pishva")
    )


async def suspicious_dismiss(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer("✅ ثبت شد.")
    admin_id, _ = _parse_alert_data(query.data)
    _pending_alert.discard(admin_id)
    _recent_destructive[admin_id].clear()
    old_text = query.message.text or ""
    await safe_edit_message_text(
        query,
        f"{old_text}\n\n✅ *مدیر ارشد این هشدار را بررسی و تایید کرد که مشکلی نیست.*",
        reply_markup=kb.kb_back("menu_pishva")
    )
