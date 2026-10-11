import asyncio
import logging
import time

from telegram import Update
from telegram.ext import ContextTypes
from telegram.ext import ApplicationHandlerStop

import database as db
import keyboards as kb
from helpers import safe_edit_message_text, box
from servers import is_boss as _boss, boss_chat as _boss_chat
from config import PISHVA_ID, STATUS_APS


BLOCK_MESSAGE = (
    "🚫 *دسترسی محدود شده*\n\n"
    "دسترسی شما به این ربات توسط واحد امنیتی APS به‌طور دائم محدود شده است.\n"
    "هرگونه تلاش مجدد برای استفاده از ربات ثبت و به مسئولین گزارش می‌شود."
)

QUEUE_MESSAGE = (
    "⏳ *در صف انتظار امنیتی*\n\n"
    "درخواست شما در حال بررسی توسط واحد امنیتی APS است.\n"
    "تا اطلاع ثانویه امکان ارسال درخواست جدید برای شما وجود ندارد. لطفاً صبور باشید."
)

# ─── وضعیت لحظه‌ای فعالیت (برای «آنلاین/الان») ────────────────
# روی هر آپدیتی که به ربات می‌رسه اجرا می‌شه — پیام متنی، دستور، یا هر
# دکمه‌ای — چه توی پیوی چه توی گروه. اگه فرستنده یه ادمینِ ثبت‌شده باشه،
# last_active اون به‌روز می‌شه؛ اگه نه مدیر ارشده نه ادمین («غریبه»)،
# فعالیتش توی stranger_log ثبت می‌شه تا هم توی لیست آنلاین دیده بشه هم
# جزییات کارش با یه دکمه قابل مشاهده باشه.
async def _track_activity(update: Update, admin=None):
    user = update.effective_user
    if user is None:
        return
    uid = user.id
    if _boss(uid):
        return  # مدیر ارشد نیازی به رهگیری نداره

    try:
        if admin is None:
            admin = await db.get_admin(uid)
        if admin and admin["is_active"]:
            await db.update_admin_activity(uid)
            try:
                await db.sync_admin_identity(uid, getattr(user, "username", None), getattr(user, "full_name", None))
            except Exception:
                pass
            return

        # ─── غریبه: نه مدیر ارشد، نه ادمینِ فعال ───
        action = None
        if update.callback_query is not None:
            action = f"دکمه: {(update.callback_query.data or '')[:80]}"
        else:
            msg = update.effective_message
            if msg is not None and msg.text:
                text = msg.text.strip()
                action = f"دستور: {text[:80]}" if text.startswith("/") else f"پیام: {text[:80]}"
            elif msg is not None:
                action = "پیام بدون متن (عکس/فایل/غیره)"
        if action:
            await db.record_stranger_activity(uid, user.username or "", user.full_name or "", action)
    except Exception:
        logging.getLogger(__name__).exception("activity tracking failed")


# ─── پیام دروازه‌ی «وضعیت امنیتی APS» ─────────────────────────
# وقتی وضعیت سیستم روی APS باشد، برای هیچ ادمینی (به‌جز مدیر ارشد)
# نه پنل باز می‌شود، نه دستور کلمه‌ای اجرا می‌شود، و نه هیچ دکمه‌ای
# از پنلی که از قبل باز بوده کار می‌کند.
APS_GATE_MESSAGE = (
    "🪽 *وضعیت امنیتی APS فعال است*\n\n"
    "در حال حاضر کنترل امنیتی ربات به‌طور کامل به سیستم APS واگذار شده و\n"
    "دسترسی شما — از جمله باز کردن پنل، دستورات کلمه‌ای و دکمه‌های پنل —\n"
    "موقتاً و به‌طور کامل قطع شده است.\n\n"
    "لطفاً تا پایان این وضعیت توسط مدیر ارشد شکیبا باشید."
)

APS_GATE_ALERT = (
    "🪽 وضعیت امنیتی APS فعال است؛ دسترسی شما به همه‌ی دکمه‌های پنل "
    "موقتاً قطع شده است."
)


# ─── دروازه‌ی امنیتی (روی هر آپدیت اجرا می‌شود) ────────────────
async def block_gate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """اگر کاربر بلاک باشد، هر پیام/دکمه‌ای که بفرستد همینجا متوقف می‌شود
    و به بقیه‌ی هندلرها اصلاً نمی‌رسد.

    FIX (کندیِ کلیِ ربات): این تابع روی *هر* آپدیت اجرا می‌شه. قبلاً
    get_blocked_user اینجا، بعد get_admin توی _track_activity، بعد
    get_setting و دوباره get_admin توی aps_gate — چهار کوئری، پشتِ‌سرِهم،
    که get_admin هم دوبار پرسیده می‌شد. با کشِ گرم فرقی حس نمی‌شه، ولی
    روی کشِ سرد (اولین پیام بعد از چند دقیقه سکوت) یعنی چند رفت‌وبرگشتِ
    سریالیِ اضافه دقیقاً روی مسیری که هیچ آپدیتی ازش فرار نمی‌کنه. الان
    هر سه هم‌زمان گرفته می‌شن و admin فقط یک‌بار بینِ مراحل به اشتراک
    گذاشته می‌شه."""
    user = update.effective_user
    if user is None:
        return
    uid = user.id
    if _boss(uid):
        blocked, admin, status = await db.get_blocked_user(uid), None, None
    else:
        blocked, admin, status = await asyncio.gather(
            db.get_blocked_user(uid),
            db.get_admin(uid),
            db.get_setting("system_status", "normal"),
        )
    if blocked:
        try:
            if update.callback_query:
                await update.callback_query.answer(
                    "🚫 دسترسی شما توسط واحد امنیتی APS محدود شده است.", show_alert=True
                )
            if update.effective_message:
                await update.effective_message.reply_text(BLOCK_MESSAGE, parse_mode="Markdown")
        except Exception:
            pass
        raise ApplicationHandlerStop()

    # کاربر بلاک نیست — فعالیتش رو برای «آنلاین/الان» ثبت می‌کنیم.
    await _track_activity(update, admin)

    # حالا دروازه‌ی وضعیت امنیتی APS را بررسی می‌کنیم.
    await aps_gate(update, ctx, user, admin, status)


# ─── دروازه‌ی «وضعیت امنیتی APS» ──────────────────────────────
async def aps_gate(update: Update, ctx: ContextTypes.DEFAULT_TYPE, user=None, admin=None, status=None):
    """وقتی system_status روی APS باشد:
    - هیچ دکمه‌ی پنلی برای هیچ ادمینی (چه پنل از قبل باز بوده چه نه) کار نمی‌کند؛
      روی هر تپ، همان دکمه با یک پیام هشدار وضعیت را توضیح می‌دهد.
    - اگر ادمین دستور /start بزند، پنل باز نمی‌شود و به‌جایش پیام وضعیت داده می‌شود.
    - اگر ادمین از دستورات کلمه‌ای (مثل «پنل»، «داشبورد»، «امنیت» و ...) استفاده کند،
      باز هم به‌جای اجرای دستور، پیام وضعیت داده می‌شود.
    مدیر ارشد از این محدودیت مستثناست، چون خودش کنترل‌کنندهٔ وضعیت APS است.

    admin/status اگه از قبل (توسطِ block_gate) گرفته شده باشن پاس داده می‌شن
    تا دوباره پرسیده نشن؛ اگه این تابع مستقیم (بدون block_gate) صدا زده بشه،
    خودش می‌گیردشون."""
    if user is None:
        user = update.effective_user
    if user is None or _boss(user.id):
        return

    if status is None:
        status = await db.get_setting("system_status", "normal")
    if status != STATUS_APS:
        return

    if admin is None:
        admin = await db.get_admin(user.id)
    if not (admin and admin["is_active"]):
        return  # این دروازه فقط برای ادمین‌های فعال است

    # ─── دکمه‌های پنل (بدون استثنا) ───
    if update.callback_query:
        try:
            await update.callback_query.answer(APS_GATE_ALERT, show_alert=True)
        except Exception:
            pass
        raise ApplicationHandlerStop()

    # ─── پیام‌های متنی: فقط /start و دستورات کلمه‌ای را می‌گیریم ───
    message = update.effective_message
    if message is None or not message.text:
        return

    text = message.text.strip()
    is_start_cmd = text.startswith("/start")

    is_keyword_cmd = False
    if not is_start_cmd:
        try:
            from keyword_commands import SIMPLE_KEYWORDS, ADMIN_KEYWORDS
            is_keyword_cmd = text in SIMPLE_KEYWORDS or text in ADMIN_KEYWORDS
        except Exception:
            is_keyword_cmd = False

    if not (is_start_cmd or is_keyword_cmd):
        return

    try:
        await message.reply_text(APS_GATE_MESSAGE, parse_mode="Markdown")
    except Exception:
        pass
    raise ApplicationHandlerStop()


# ─── پنل امنیتی ─────────────────────────────────────────────────
async def build_security_panel_text() -> str:
    """متنِ پنل امنیتی APS رو می‌سازه. تابعِ مشترک بین بازکردنِ پنل از طریقِ
    دکمه (callback) و از طریقِ کلمه‌ی «APS» در پیوی/گروه (keyword_commands.py)
    تا هر دو مسیر دقیقاً یک چیزِ یکسان نشون بدن."""
    queued = await db.get_queued_requests()
    blocked = await db.get_all_blocked()
    max_n, win_min = await get_flood_settings()
    if await get_flood_enabled():
        flood_line = f"🌊 ضدِ فلود: 🟢 روشن — حداکثر `{max_n}` تلاش در `{fmt_window(win_min)}`\n\n"
    else:
        flood_line = "🌊 ضدِ فلود: 🔴 خاموش\n\n"
    return (
        f"{box('🛡️ پنل امنیتی APS')}\n\n"
        f"⏳ در صف انتظار: `{len(queued)}` نفر\n"
        f"🚫 بلاک‌شده: `{len(blocked)}` نفر\n"
        f"{flood_line}"
        "📌 یک بخش را انتخاب کنید:"
    )


async def security_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد به این بخش دسترسی دارد.", show_alert=True)
        return
    await query.answer()
    text = await build_security_panel_text()
    await safe_edit_message_text(query, text, reply_markup=kb.kb_security_panel(), parse_mode="Markdown")


# ─── صف انتظار ────────────────────────────────────────────────
async def security_queue_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    queued = await db.get_queued_requests()
    if not queued:
        await safe_edit_message_text(query, f"{box('⏳ صف انتظار')}\n\n❗ صف انتظار خالی است.",
                                       reply_markup=kb.kb_security_panel(), parse_mode="Markdown")
        return
    await safe_edit_message_text(query, f"{box('⏳ صف انتظار')}\n\n👥 تعداد: `{len(queued)}`\n\nروی هرکدام بزنید تا جزئیات کامل را ببینید:",
                                   reply_markup=kb.kb_queue_list(queued), parse_mode="Markdown")


async def security_queue_item(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    req_id = int(query.data.split("_")[-1])
    r = await db.get_access_request(req_id)
    if not r:
        await safe_edit_message_text(query, "❗ این درخواست دیگر وجود ندارد.", reply_markup=kb.kb_security_panel())
        return
    role_map = {"tournament_manager": "🏆 مدیر مسابقات", "security_manager": "🛡️ مدیر امنیتی"}
    role_label = role_map.get(r["role"], r["role"])
    username_line = f"@{r['username']}" if r["username"] else "—"
    text = (
        f"{box('⏳ جزئیات صف انتظار')}\n\n"
        f"👤 نام کامل: {r['full_name'] or '—'}\n"
        f"🪪 یوزرنیم: {username_line}\n"
        f"🆔 آیدی عددی: `{r['telegram_id']}`\n"
        f"💼 نقش درخواستی: {role_label}\n"
        f"📝 پیام درخواست: {r['message'] or '—'}\n"
        f"⏱️ زمان درخواست: `{r['requested_at']}`"
    )
    await safe_edit_message_text(query, text, reply_markup=kb.kb_queue_item_actions(req_id), parse_mode="Markdown")


async def request_to_queue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """از روی نوتیفیکیشن درخواست دسترسی: شخص را به صف انتظار می‌فرستد."""
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    req_id = int(query.data.split("_")[-1])
    r = await db.get_access_request(req_id)
    if not r or r["status"] != "pending":
        await query.answer("این درخواست قبلاً پردازش شده.", show_alert=True)
        return
    await db.set_request_status(req_id, "queued")
    await db.log_action(_boss_chat(), "queue_request", f"صف انتظار: {r['full_name']}", r["telegram_id"])
    try:
        await ctx.bot.send_message(chat_id=r["telegram_id"], text=QUEUE_MESSAGE, parse_mode="Markdown")
    except Exception:
        pass
    old_text = query.message.text or ""
    await safe_edit_message_text(query, old_text + "\n\n⏳ *این شخص به صف انتظار منتقل شد.*", parse_mode="Markdown")


async def queue_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    req_id = int(query.data.split("_")[-1])
    r = await db.get_access_request(req_id)
    if not r:
        await query.answer("یافت نشد.", show_alert=True)
        return
    await db.set_request_status(req_id, "approved")
    await db.create_admin(r["telegram_id"], r["username"], r["full_name"], r["role"])
    await db.close_other_open_requests(r["telegram_id"], req_id)
    await db.log_action(_boss_chat(), "approve_from_queue", f"تأیید از صف انتظار: {r['full_name']}", r["telegram_id"])
    role_map = {"tournament_manager": "🏆 مدیر مسابقات", "security_manager": "🛡️ مدیر امنیتی"}
    role_label = role_map.get(r["role"], r["role"])
    try:
        await ctx.bot.send_message(
            chat_id=r["telegram_id"],
            text=f"✅ *دسترسی شما تأیید شد*\n💼 نقش: {role_label}\n\nبرای شروع /start را بزنید.",
            parse_mode="Markdown"
        )
    except Exception:
        pass
    await security_queue_list(update, ctx)


async def queue_release(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    req_id = int(query.data.split("_")[-1])
    r = await db.get_access_request(req_id)
    if r:
        await db.set_request_status(req_id, "rejected")
        await db.log_action(_boss_chat(), "release_from_queue", f"خروج از صف: {r['full_name']}", r["telegram_id"])
        try:
            await ctx.bot.send_message(
                chat_id=r["telegram_id"],
                text="🔓 از صف انتظار امنیتی خارج شدید. در صورت نیاز می‌توانید دوباره درخواست دسترسی ارسال کنید."
            )
        except Exception:
            pass
    await security_queue_list(update, ctx)


async def queue_block_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    req_id = int(query.data.split("_")[-1])
    r = await db.get_access_request(req_id)
    if not r:
        await query.answer("یافت نشد.", show_alert=True)
        return
    await safe_edit_message_text(query, 
        f"⚠️ آیا از بلاک دائم *{r['full_name']}* مطمئن هستید؟\nاین شخص برای همیشه دسترسی خود به ربات را از دست می‌دهد.",
        reply_markup=kb.kb_block_confirm(f"req_{req_id}", back_cb=f"queueview_{req_id}"),
        parse_mode="Markdown"
    )


async def request_block_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """از روی نوتیفیکیشن درخواست دسترسی."""
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    req_id = int(query.data.split("_")[-1])
    r = await db.get_access_request(req_id)
    if not r:
        await query.answer("یافت نشد.", show_alert=True)
        return
    await safe_edit_message_text(query, 
        f"⚠️ آیا از بلاک دائم *{r['full_name']}* مطمئن هستید؟\nاین شخص برای همیشه دسترسی خود به ربات را از دست می‌دهد.",
        reply_markup=kb.kb_block_confirm(f"req_{req_id}", back_cb="menu_pishva"),
        parse_mode="Markdown"
    )


# ─── بلاک دائم ────────────────────────────────────────────────
async def block_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    token = query.data.split("_", 1)[-1]  # "req_12" یا یک آیدی عددی خام

    if token.startswith("req_"):
        req_id = int(token.split("_")[-1])
        r = await db.get_access_request(req_id)
        if not r:
            await safe_edit_message_text(query, "❗ یافت نشد.", reply_markup=kb.kb_security_panel())
            return
        telegram_id, username, full_name = r["telegram_id"], r["username"], r["full_name"]
        await db.set_request_status(req_id, "blocked")
    else:
        telegram_id = int(token)
        b = await db.get_blocked_user(telegram_id)
        username = b["username"] if b else ""
        full_name = b["full_name"] if b else str(telegram_id)

    await db.block_user(telegram_id, username or "", full_name or "", "بلاک توسط مدیر ارشد", _boss_chat())
    await db.log_action(_boss_chat(), "block_user", f"بلاک: {full_name} ({telegram_id})", telegram_id)
    try:
        await ctx.bot.send_message(chat_id=telegram_id, text=BLOCK_MESSAGE, parse_mode="Markdown")
    except Exception:
        pass
    await safe_edit_message_text(query, f"🚫 *{full_name or telegram_id}* با موفقیت بلاک شد.",
                                   reply_markup=kb.kb_security_panel(), parse_mode="Markdown")


# ─── لیست بلاک‌شده‌ها ──────────────────────────────────────────
async def security_blocked_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    blocked = await db.get_all_blocked()
    if not blocked:
        await safe_edit_message_text(query, f"{box('🚫 لیست بلاک‌شده‌ها')}\n\n❗ فعلاً کسی بلاک نیست.",
                                       reply_markup=kb.kb_security_panel(), parse_mode="Markdown")
        return
    await safe_edit_message_text(query, f"{box('🚫 لیست بلاک‌شده‌ها')}\n\n👥 تعداد: `{len(blocked)}`",
                                   reply_markup=kb.kb_blocked_list(blocked), parse_mode="Markdown")


async def security_blocked_item(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tid = int(query.data.split("_")[-1])
    b = await db.get_blocked_user(tid)
    if not b:
        await safe_edit_message_text(query, "❗ یافت نشد.", reply_markup=kb.kb_security_panel())
        return
    text = (
        f"{box('🚫 جزئیات بلاک')}\n\n"
        f"👤 نام: {b['full_name'] or '—'}\n"
        f"🪪 یوزرنیم: {('@' + b['username']) if b['username'] else '—'}\n"
        f"🆔 آیدی عددی: `{b['telegram_id']}`\n"
        f"📝 دلیل: {b['reason'] or '—'}\n"
        f"⏱️ زمان بلاک: `{b['blocked_at']}`"
    )
    await safe_edit_message_text(query, text, reply_markup=kb.kb_blocked_item_actions(tid), parse_mode="Markdown")


async def unblock_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    tid = int(query.data.split("_")[-1])
    b = await db.get_blocked_user(tid)
    name = b["full_name"] if b else str(tid)
    await db.unblock_user(tid)
    await db.log_action(_boss_chat(), "unblock_user", f"آنبلاک: {name} ({tid})", tid)
    try:
        await ctx.bot.send_message(chat_id=tid, text="✅ دسترسی شما به ربات مجدداً فعال شد.")
    except Exception:
        pass
    await security_blocked_list(update, ctx)


# ─── ضدِ فلود روی /start غریبه‌ها (صف انتظارِ خودکار) ──────────────
# وقتی یک کاربر غریبه (نه مدیر ارشد، نه ادمین، نه از قبل در صف) در یک بازه‌ی
# زمانیِ مشخص بیش از تعدادِ مجاز /start بزنه، به‌جای این‌که هر بار دوباره
# منوی انتخاب نقش رو ببینه، خودکار (بدون نیاز به دخالتِ دستیِ مدیر ارشد)
# دقیقاً مثلِ زمانی که مدیر ارشد از دکمه‌ی «صف انتظار» استفاده می‌کنه، وارد
# صف انتظار امنیتی می‌شه. تعداد و بازه از پنل امنیتی APS قابل‌تنظیم‌اند.
FLOOD_ENABLED_KEY = "aps_flood_enabled"   # "1" = روشن، "0" = خاموش
FLOOD_MAX_KEY = "aps_flood_max"
FLOOD_WINDOW_KEY = "aps_flood_window_min"
FLOOD_ENABLED_DEFAULT = "1"
FLOOD_MAX_DEFAULT = "3"
FLOOD_WINDOW_DEFAULT = "10"
# رابطِ جدید: دکمه‌های ➖/➕ به‌جای گزینه‌های ثابت.
FLOOD_MAX_MIN, FLOOD_MAX_MAX = 1, 50
# نردبانِ بازه‌ی زمانی (دقیقه) — هر ➕/➖ یک پله جابه‌جا می‌شه.
FLOOD_WINDOW_STEPS = (1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360, 720, 1440)
# (قدیمی؛ فقط برای سازگاری با پیام‌های قدیمیِ باز مانده)
FLOOD_MAX_CHOICES = (2, 3, 5, 10)
FLOOD_WINDOW_CHOICES = (5, 10, 15, 30)

AUTO_QUEUE_MESSAGE = (
    "⏳ *به‌دلیل تعداد زیادِ درخواست در بازه‌ی زمانیِ کوتاه، به‌طور خودکار وارد صف انتظار امنیتی شدید.*\n\n"
    "درخواست شما در حال بررسی توسط واحد امنیتی APS است.\n"
    "تا اطلاع ثانویه امکان ارسال درخواست جدید برای شما وجود ندارد. لطفاً صبور باشید."
)


async def get_flood_enabled() -> bool:
    """آیا ضدِ فلود روشنه؟ (پیش‌فرض: روشن)"""
    v = await db.get_setting(FLOOD_ENABLED_KEY, FLOOD_ENABLED_DEFAULT)
    return str(v) != "0"


def fmt_window(minutes: int) -> str:
    """نمایشِ خوانای بازه‌ی زمانی: ۹۰ → «1 ساعت و 30 دقیقه»."""
    minutes = int(minutes)
    if minutes < 60:
        return f"{minutes} دقیقه"
    h, m = divmod(minutes, 60)
    if h >= 24 and m == 0 and h % 24 == 0:
        return f"{h // 24} روز"
    return f"{h} ساعت" if m == 0 else f"{h} ساعت و {m} دقیقه"


def _step_window(current: int, direction: int) -> int:
    """یک پله بالا/پایین روی نردبانِ زمان؛ اگه مقدارِ فعلی روی نردبان نباشه
    (مثلاً از تنظیمِ قدیمی)، به نزدیک‌ترین پله‌ی بعدی/قبلی می‌ره."""
    steps = FLOOD_WINDOW_STEPS
    if direction > 0:
        for v in steps:
            if v > current:
                return v
        return steps[-1]
    for v in reversed(steps):
        if v < current:
            return v
    return steps[0]


async def get_flood_settings():
    """(حداکثرِ تعدادِ مجاز، بازه‌ی زمانی به‌دقیقه) رو از تنظیماتِ ذخیره‌شده
    (پنل امنیتی APS) برمی‌گردونه؛ اگه هنوز تنظیم نشده باشن، مقدارِ پیش‌فرض."""
    max_s = await db.get_setting(FLOOD_MAX_KEY, FLOOD_MAX_DEFAULT)
    win_s = await db.get_setting(FLOOD_WINDOW_KEY, FLOOD_WINDOW_DEFAULT)
    try:
        max_n = max(1, int(max_s))
    except (TypeError, ValueError):
        max_n = int(FLOOD_MAX_DEFAULT)
    try:
        win_n = max(1, int(win_s))
    except (TypeError, ValueError):
        win_n = int(FLOOD_WINDOW_DEFAULT)
    return max_n, win_n


async def check_stranger_flood(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                                uid: int, username: str, full_name: str) -> bool:
    """ضدِ فلودِ /start برای غریبه‌ها (auth.cmd_start فراخوانش می‌کنه، *فقط*
    برای کسی که نه مدیر ارشده، نه ادمین، نه از قبل در صفه).

    اگه کاربر از سقفِ مجاز (تعداد/بازه‌ی تنظیم‌شده در پنل APS) رد شده باشه:
    - خودش رو خودکار وارد صف انتظار امنیتی می‌کنه (access_requests با
      status='queued'، دقیقاً مثلِ صف‌کردنِ دستی)،
    - پیامِ مناسب براش می‌فرسته،
    - True برمی‌گردونه یعنی فراخوان (cmd_start) باید همینجا متوقف بشه.

    در غیرِ این صورت (هنوز داخلِ سقفه) فقط این تلاش رو در ردیاب (حافظه‌ی
    موقتِ bot_data — نیازی به نوشتنِ دیتابیس در هر تلاش نیست) ثبت می‌کنه و
    False برمی‌گردونه تا cmd_start مسیرِ عادی رو ادامه بده."""
    if not await get_flood_enabled():
        return False  # ضدِ فلود خاموشه؛ مسیرِ عادیِ /start ادامه پیدا می‌کنه
    max_n, win_min = await get_flood_settings()
    window_seconds = win_min * 60
    now_ts = time.monotonic()

    tracker = ctx.bot_data.setdefault("aps_flood_tracker", {})
    hits = [t for t in tracker.get(uid, []) if now_ts - t < window_seconds]
    hits.append(now_ts)

    if len(hits) <= max_n:
        tracker[uid] = hits
        return False

    # ── سقف رد شد → صف انتظارِ خودکار ──
    tracker.pop(uid, None)  # ریست، تا بلافاصله دوباره تریگر نشه
    req_id = await db.create_access_request(
        uid, username or "", full_name or "", "نامشخص (ضدِ فلود خودکار)", ""
    )
    await db.set_request_status(req_id, "queued")
    await db.log_action(
        _boss_chat(), "auto_queue_flood",
        f"صف انتظار خودکار (ضدِ فلود): {full_name or uid} — بیش از {max_n} تلاش در {win_min} دقیقه",
        uid,
    )
    try:
        await update.message.reply_text(AUTO_QUEUE_MESSAGE, parse_mode="Markdown")
    except Exception:
        pass
    return True


# ─── تنظیمِ ضدِ فلود (بخشِ جدید در پنل امنیتی APS) ──────────────
def _flood_menu_text(enabled: bool, max_n: int, win_min: int) -> str:
    status = "🟢 روشن" if enabled else "🔴 خاموش"
    note = "" if enabled else "\n⚠️ تا وقتی خاموش است، هیچ‌کس به‌خاطر تعداد /start وارد صف نمی‌شود. مقادیر زیر ذخیره می‌مانند.\n"
    return (
        f"{box('🌊 ضدِ فلود — صف انتظار خودکار')}\n\n"
        "وقتی یک کاربر غریبه در بازه‌ی زمانیِ زیر، بیش از تعدادِ مجازِ زیر "
        "دکمه‌ی /start را بزند، به‌طور خودکار (بدون نیاز به تأییدِ شما) وارد "
        "صف انتظار امنیتی می‌شود.\n\n"
        f"🔘 وضعیت: {status}\n"
        f"🔢 تعدادِ مجاز: `{max_n}` تلاش\n"
        f"⏱️ بازه‌ی زمانی: `{fmt_window(win_min)}`\n"
        f"{note}\n"
        "📌 با دکمه‌های ➖ / ➕ مقدار را تغییر دهید:"
    )


async def security_flood_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    enabled = await get_flood_enabled()
    max_n, win_min = await get_flood_settings()
    await safe_edit_message_text(
        query, _flood_menu_text(enabled, max_n, win_min),
        reply_markup=kb.kb_flood_menu(enabled, max_n, win_min), parse_mode="Markdown"
    )


async def _render_flood_menu(query):
    enabled = await get_flood_enabled()
    max_n, win_min = await get_flood_settings()
    await safe_edit_message_text(
        query, _flood_menu_text(enabled, max_n, win_min),
        reply_markup=kb.kb_flood_menu(enabled, max_n, win_min), parse_mode="Markdown"
    )


async def security_flood_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    new_state = not await get_flood_enabled()
    await db.set_setting(FLOOD_ENABLED_KEY, "1" if new_state else "0")
    ctx.bot_data.pop("aps_flood_tracker", None)  # شمارشِ قدیمی بعد از روشن/خاموش کردن معتبر نیست
    await db.log_action(_boss_chat(), "flood_toggle", f"ضدِ فلود {'روشن' if new_state else 'خاموش'} شد")
    await query.answer("🟢 ضدِ فلود روشن شد" if new_state else "🔴 ضدِ فلود خاموش شد", show_alert=True)
    await _render_flood_menu(query)


async def security_flood_step(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """دکمه‌های ➖/➕ : flood_max_{dec5|dec|inc|inc5} و flood_win_{dec|inc}"""
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    parts = query.data.split("_")  # flood, max|win, action
    kind, action = parts[1], parts[2]
    max_n, win_min = await get_flood_settings()

    if kind == "max":
        delta = {"dec5": -5, "dec": -1, "inc": 1, "inc5": 5}.get(action, 0)
        new = min(FLOOD_MAX_MAX, max(FLOOD_MAX_MIN, max_n + delta))
        if new == max_n:
            await query.answer("به حدِ مجاز رسیده‌اید.")
            return
        await db.set_setting(FLOOD_MAX_KEY, str(new))
    else:
        new = _step_window(win_min, 1 if action == "inc" else -1)
        if new == win_min:
            await query.answer("به حدِ مجاز رسیده‌اید.")
            return
        await db.set_setting(FLOOD_WINDOW_KEY, str(new))
    ctx.bot_data.pop("aps_flood_tracker", None)
    await query.answer()
    await _render_flood_menu(query)


async def security_flood_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await db.set_setting(FLOOD_MAX_KEY, FLOOD_MAX_DEFAULT)
    await db.set_setting(FLOOD_WINDOW_KEY, FLOOD_WINDOW_DEFAULT)
    ctx.bot_data.pop("aps_flood_tracker", None)
    await query.answer("♻️ مقادیر به پیش‌فرض برگشت", show_alert=True)
    await _render_flood_menu(query)


async def security_flood_noop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("از دکمه‌های ➖ / ➕ کنارش استفاده کنید.")


# (قدیمی) هندلرهای گزینه‌های ثابت — برای پیام‌های قدیمیِ هنوز بازِ چتِ مدیر ارشد نگه داشته شده‌اند.
async def security_flood_set_max(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    n = min(FLOOD_MAX_MAX, max(FLOOD_MAX_MIN, int(query.data.split("_")[-1])))
    await db.set_setting(FLOOD_MAX_KEY, str(n))
    await query.answer(f"✅ تعدادِ مجاز به {n} تغییر یافت", show_alert=True)
    await security_flood_menu(update, ctx)


async def security_flood_set_window(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    n = max(1, int(query.data.split("_")[-1]))
    await db.set_setting(FLOOD_WINDOW_KEY, str(n))
    await query.answer(f"✅ بازه‌ی زمانی به {fmt_window(n)} تغییر یافت", show_alert=True)
    await security_flood_menu(update, ctx)
