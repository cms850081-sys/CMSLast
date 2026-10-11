"""
owner_panel.py — پنلِ «مدیر مجموعه» (فقط در سرورهای ۲..۶).

مدیر مجموعه = هم‌ترازِ مدیر مسابقات + این‌ها (فقط در سرورِ خودش):
  • مدیریتِ مدیران (محدود): روشن/خاموشِ دسترسی به ربات، حذف، تغییرِ مقام، تغییرِ نام، اخطار
    (شخصی‌سازیِ ریزِ دسترسی‌ها فقط با مدیر ارشد است)
  • لینکِ دعوتِ استخدام (مدیرِ تازه با لینک وارد سرورِ او می‌شود)
  • داشبوردِ مجموعه، ساعتِ کاری (از منوی ساعت کاری)، مخابرات، تقویم، وظایف، رهگشا و شطرنج زنده
  • ارتباط با مدیر ارشد (آیدی + پیامِ مستقیم از طریقِ ربات)

ورودیِ متنی (نام/دلیلِ اخطار/پیام) با یک MessageHandlerِ سبک در group=-4 گرفته می‌شود
و با هر کلیکِ نامرتبط یا گذشتنِ ۲ دقیقه لغو می‌شود.
"""
import html
import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (ApplicationHandlerStop, CallbackQueryHandler, ContextTypes,
                          MessageHandler, filters)

import database as db
import servers
import srvctx
from config import (BOT_USERNAME, PISHVA_ID, PISHVA_USERNAME, ROLE_SECURITY_MANAGER,
                    ROLE_TOURNAMENT_MANAGER)
from helpers import now_shamsi, safe_edit_message_text, safe_send_message

logger = logging.getLogger(__name__)

ROLE_FA = {ROLE_TOURNAMENT_MANAGER: "🏆 مدیر مسابقات", ROLE_SECURITY_MANAGER: "🛡️ مدیر امنیتی"}
_WAIT_TTL = 120  # ثانیه


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _btn(text, data=None, url=None):
    return InlineKeyboardButton(text, url=url) if url else InlineKeyboardButton(text, callback_data=data)


def _back(target="main"):
    return [_btn("🔙 بازگشت", f"back_{target}")]


async def _guard(query) -> bool:
    """فقط مدیر مجموعه‌ی همین سرور."""
    if srvctx.current() == srvctx.MAIN_ID or not servers.is_owner(query.from_user.id):
        await query.answer("⛔ این بخش فقط برای مدیر مجموعه است.", show_alert=True)
        return False
    return True


def _set_wait(ctx, kind, **kw):
    ctx.user_data["own_wait"] = dict(kind=kind, at=time.monotonic(), **kw)


# ═════════════════════════ داشبورد ═════════════════════════
async def own_dash(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _guard(q):
        return
    await q.answer("🔄 در حال بارگذاری…")
    sid = srvctx.current()
    srv = servers.get(sid) or {}
    try:
        admins = [a for a in await db.get_active_admins() if int(a["telegram_id"]) != q.from_user.id]
        players = await db.get_continuing_players()
        today = await db.get_matches_by_filter("today")
        pending = await db.get_pending_matches()
        wh = (await db.get_setting("working_hours_active", "0")) == "1"
    except Exception as ex:
        logger.exception("own_dash")
        await safe_edit_message_text(q, f"❌ خطا در بارگذاری داشبورد: {_e(ex)}",
                                     reply_markup=InlineKeyboardMarkup([_back()]), parse_mode="HTML")
        return
    tm = sum(1 for a in admins if a["role"] == ROLE_TOURNAMENT_MANAGER)
    sec = sum(1 for a in admins if a["role"] == ROLE_SECURITY_MANAGER)
    text = (
        f"🏫 <b>داشبورد مجموعه — {_e(srv.get('name'))}</b>\n"
        f"🛰️ <code>{_e(now_shamsi())}</code>\n\n"
        f"📡 وضعیت سرور: {servers.STATUS_FA.get(srv.get('status'), '—')}\n"
        f"🕐 ساعت کاری: {'🟢 باز' if wh else '🔴 بسته'}\n\n"
        f"👥 مدیران: <b>{len(admins)}</b>  (🏆 {tm} · 🛡️ {sec})\n"
        f"👤 بازیکنان فعال: <b>{len(players)}</b>\n"
        f"♟️ مسابقات امروز: <b>{len(today)}</b> · در انتظار نتیجه: <b>{len(pending)}</b>"
    )
    kb = InlineKeyboardMarkup([[_btn("🔄 به‌روزرسانی", "own_dash"), *_back()]])
    await safe_edit_message_text(q, text, reply_markup=kb, parse_mode="HTML")


# ═════════════════════════ مدیریتِ مدیران (محدود) ═════════════════════════
async def own_mgrs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _guard(q):
        return
    await q.answer()
    me = q.from_user.id
    admins = [a for a in await db.get_all_admins() if int(a["telegram_id"]) != me]
    rows = []
    for a in admins[:40]:
        on = bool(a["is_active"])
        name = a["display_name"] or a["full_name"] or str(a["telegram_id"])
        rows.append([_btn(f"{'🟢' if on else '⚫'} {name} — {ROLE_FA.get(a['role'], a['role'])}",
                          f"own_m_{a['telegram_id']}")])
    rows.append([_btn("🔗 لینکِ دعوتِ مدیر", "own_invite")])
    rows.append(_back())
    body = "👥 <b>مدیریت مدیران</b>\n\n" + ("یک مدیر را انتخاب کنید:" if admins else
                                           "هنوز مدیری استخدام نشده است.\nبرای استخدام، لینکِ دعوت را برایش بفرستید.")
    await safe_edit_message_text(q, body, reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")


async def _manager_view(q, tid: int, note: str = ""):
    a = await db.get_admin(tid)
    if not a:
        await safe_edit_message_text(q, "❌ این مدیر پیدا نشد.", reply_markup=InlineKeyboardMarkup([_back("main")]))
        return
    on = bool(a["is_active"])
    try:
        bot_on = await db.get_admin_permission(tid, "bot_active")
    except Exception:
        bot_on = True
    name = a["display_name"] or a["full_name"] or str(tid)
    text = (
        f"👤 <b>{_e(name)}</b>\n"
        f"🆔 <code>{tid}</code>  {_e(a['username'] or '')}\n"
        f"🎖 مقام: {ROLE_FA.get(a['role'], _e(a['role']))}\n"
        f"📌 وضعیت: {'🟢 فعال' if on else '⚫ غیرفعال/اخراج‌شده'}\n"
        f"🤖 دسترسی به ربات: {'🟢 روشن' if bot_on else '🔴 خاموش'}\n"
        f"⚠️ اخطار: {a['warnings'] or 0}"
    )
    if note:
        text += f"\n\n{note}"
    other = ROLE_SECURITY_MANAGER if a["role"] == ROLE_TOURNAMENT_MANAGER else ROLE_TOURNAMENT_MANAGER
    rows = [
        [_btn("🔴 خاموش‌کردنِ ربات" if bot_on else "🟢 روشن‌کردنِ ربات", f"own_tg_{tid}")],
        [_btn(f"🔄 تغییرِ مقام ← {ROLE_FA.get(other)}", f"own_rl_{tid}")],
        [_btn("✏️ تغییرِ نام", f"own_rn_{tid}"), _btn("⚠️ اخطار", f"own_wn_{tid}")],
        [_btn("🗑 حذف از مجموعه", f"own_rm_{tid}")],
        [_btn("🔙 فهرستِ مدیران", "own_mgrs")],
    ]
    await safe_edit_message_text(q, text, reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")


async def _target(q, data_prefix):
    """tid را از callback می‌خواند و مطمئن می‌شود هدف همین سرور و غیرِ مدیر مجموعه است."""
    tid = int(q.data[len(data_prefix):])
    if servers.is_owner(tid):
        await q.answer("⛔ روی مدیر مجموعه اجرا نمی‌شود.", show_alert=True)
        return None
    a = await db.get_admin(tid)
    if not a:
        await q.answer("❌ مدیر پیدا نشد.", show_alert=True)
        return None
    return tid


async def own_manager(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    tid = await _target(q, "own_m_")
    if tid is None:
        return
    await q.answer()
    await _manager_view(q, tid)


async def own_toggle(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    tid = await _target(q, "own_tg_")
    if tid is None:
        return
    try:
        cur = await db.get_admin_permission(tid, "bot_active")
    except Exception:
        cur = True
    await db.set_admin_permission(tid, "bot_active", not cur)
    await db.log_action(q.from_user.id, "owner_toggle_bot", f"دسترسی به ربات {'خاموش' if cur else 'روشن'} شد", tid)
    await q.answer("✅ انجام شد")
    await _manager_view(q, tid)


async def own_role(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    tid = await _target(q, "own_rl_")
    if tid is None:
        return
    a = await db.get_admin(tid)
    new = ROLE_SECURITY_MANAGER if a["role"] == ROLE_TOURNAMENT_MANAGER else ROLE_TOURNAMENT_MANAGER
    await db.set_admin_role(tid, new)
    await db.log_action(q.from_user.id, "owner_set_role", f"مقام به {ROLE_FA.get(new)} تغییر کرد", tid)
    try:
        await safe_send_message(ctx.bot, tid, f"🔄 مقامِ شما توسط مدیر مجموعه به <b>{ROLE_FA.get(new)}</b> تغییر کرد.\n/start را بزنید.",
                                parse_mode="HTML")
    except Exception:
        pass
    await q.answer("✅ مقام تغییر کرد")
    await _manager_view(q, tid)


async def own_remove(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    tid = await _target(q, "own_rm_")
    if tid is None:
        return
    await q.answer()
    kb = InlineKeyboardMarkup([[_btn("✅ بله، حذف شود", f"own_rmy_{tid}"), _btn("❌ انصراف", f"own_m_{tid}")]])
    await safe_edit_message_text(q, "⚠️ این مدیر از مجموعه حذف شود؟\n(دسترسی‌اش به این سرور قطع می‌شود.)",
                                 reply_markup=kb, parse_mode="HTML")


async def own_remove_yes(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    tid = await _target(q, "own_rmy_")
    if tid is None:
        return
    await db.kick_admin(tid)
    await db.log_action(q.from_user.id, "owner_kick_admin", "مدیر از مجموعه حذف شد", tid)
    try:
        await safe_send_message(ctx.bot, tid, "⛔ دسترسیِ شما به این مجموعه توسط مدیر مجموعه قطع شد.")
    except Exception:
        pass
    await q.answer("🗑 حذف شد")
    await own_mgrs(update, ctx)


async def own_rename(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    tid = await _target(q, "own_rn_")
    if tid is None:
        return
    await q.answer()
    _set_wait(ctx, "rename", tid=tid)
    await safe_edit_message_text(q, "✏️ نامِ جدید را بفرستید (حداکثر ۴۰ کاراکتر):",
                                 reply_markup=InlineKeyboardMarkup([[_btn("❌ انصراف", f"own_m_{tid}")]]))


async def own_warn(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    tid = await _target(q, "own_wn_")
    if tid is None:
        return
    await q.answer()
    _set_wait(ctx, "warn", tid=tid)
    await safe_edit_message_text(q, "⚠️ دلیلِ اخطار را بفرستید:",
                                 reply_markup=InlineKeyboardMarkup([[_btn("❌ انصراف", f"own_m_{tid}")]]))


# ═════════════════════════ لینکِ دعوت ═════════════════════════
def _invite_link(code: str, bot_username: str) -> str:
    return f"https://t.me/{bot_username}?start=join_{code}"


async def own_invite(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    await q.answer()
    sid = srvctx.current()
    if not servers.feature_on(sid, "hire"):
        await safe_edit_message_text(q, "⛔ قابلیتِ استخدام برای این سرور توسط مدیر ارشد غیرفعال شده است.",
                                     reply_markup=InlineKeyboardMarkup([_back()]))
        return
    code = (servers.get(sid) or {}).get("invite_code") or ""
    uname = BOT_USERNAME or (ctx.bot.username or "")
    if code:
        body = (f"🔗 <b>لینکِ دعوتِ مدیر</b>\n\n<code>{_invite_link(code, uname)}</code>\n\n"
                "هر کس با این لینک وارد ربات شود، درخواستِ «مدیر مسابقات / مدیر امنیتی» برای "
                "<b>همین سرور</b> می‌فرستد و شما تأیید یا رد می‌کنید.\n"
                "لینک را فقط برای افرادِ مورد اعتماد بفرستید؛ هر وقت خواستید لینک را عوض یا غیرفعال کنید.")
        rows = [[_btn("♻️ ساختِ لینکِ تازه", "own_inv_new"), _btn("🚫 غیرفعال‌کردن", "own_inv_off")], _back()]
    else:
        body = "🔗 لینکِ دعوت غیرفعال است."
        rows = [[_btn("✅ ساختِ لینکِ دعوت", "own_inv_new")], _back()]
    await safe_edit_message_text(q, body, reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")


async def own_invite_new(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    await servers.regenerate_invite(srvctx.current())
    await db.log_action(q.from_user.id, "owner_invite_new", "لینکِ دعوتِ مدیر ساخته/تازه شد")
    await own_invite(update, ctx)


async def own_invite_off(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    await servers.disable_invite(srvctx.current())
    await db.log_action(q.from_user.id, "owner_invite_off", "لینکِ دعوتِ مدیر غیرفعال شد")
    await own_invite(update, ctx)


# ═════════════════════════ بکاپ ═════════════════════════
async def own_backup(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    await q.answer()
    await safe_edit_message_text(q, "💾 <b>بکاپِ مجموعه</b>\nفرمت؟", parse_mode="HTML",
                                 reply_markup=InlineKeyboardMarkup([[_btn("📊 Excel", "own_bkx_excel"), _btn("📝 Word", "own_bkx_word")], _back()]))


async def own_backup_send(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    fmt = q.data.rsplit("_", 1)[1]
    await q.answer("⏳ در حال تهیه…")
    from backup_utils import send_server_backup
    try:
        await send_server_backup(ctx.bot, q.from_user.id, srvctx.current(), "all", fmt)
        await db.log_action(q.from_user.id, "owner_backup", f"بکاپ مجموعه ({fmt})")
        await safe_edit_message_text(q, "✅ بکاپ ارسال شد.", reply_markup=InlineKeyboardMarkup([_back()]))
    except Exception as ex:
        logger.exception("own_backup")
        await safe_edit_message_text(q, f"❌ خطا در تهیه‌ی بکاپ: {_e(ex)}", reply_markup=InlineKeyboardMarkup([_back()]), parse_mode="HTML")


# ═════════════════════════ ارتباط با مدیر ارشد ═════════════════════════
async def own_contact(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    await q.answer()
    uname = (PISHVA_USERNAME or "").lstrip("@")
    lines = ["☎️ <b>ارتباط با مدیر ارشد</b>", ""]
    if uname:
        lines.append(f"آیدی: @{_e(uname)}")
    lines.append("برای ارسالِ پیامِ مستقیم از طریقِ ربات، دکمه‌ی زیر را بزنید.")
    rows = []
    if uname:
        rows.append([_btn("💬 چتِ مستقیم", url=f"https://t.me/{uname}")])
    rows.append([_btn("✉️ ارسالِ پیام از طریقِ ربات", "own_msg")])
    rows.append(_back())
    await safe_edit_message_text(q, "\n".join(lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")


async def own_msg(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    await q.answer()
    _set_wait(ctx, "to_pishva")
    await safe_edit_message_text(q, "✉️ متنِ پیام را برای مدیر ارشد بفرستید:",
                                 reply_markup=InlineKeyboardMarkup([[_btn("❌ انصراف", "own_contact")]]))


# ═════════════════════════ ورودیِ متنی ═════════════════════════
async def own_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    w = ctx.user_data.get("own_wait")
    if not w:
        return
    uid = update.effective_user.id
    if time.monotonic() - w["at"] > _WAIT_TTL or srvctx.current() == srvctx.MAIN_ID or not servers.is_owner(uid):
        ctx.user_data.pop("own_wait", None)
        return
    ctx.user_data.pop("own_wait", None)
    text = (update.message.text or "").strip()
    kind = w["kind"]
    if kind == "rename":
        tid = w["tid"]
        name = text[:40]
        if not name:
            return
        await db.update_admin_display_name(tid, name)
        await db.log_action(uid, "owner_rename_admin", f"نام به «{name}» تغییر کرد", tid)
        await update.message.reply_text(f"✅ نام به «{name}» تغییر کرد.")
    elif kind == "warn":
        tid = w["tid"]
        await db.add_admin_warning(tid, text[:300], uid)
        await db.log_action(uid, "owner_warn_admin", f"اخطار: {text[:200]}", tid)
        try:
            await safe_send_message(ctx.bot, tid, f"⚠️ <b>اخطار از مدیر مجموعه</b>\n\n{_e(text[:300])}", parse_mode="HTML")
        except Exception:
            pass
        await update.message.reply_text("✅ اخطار ثبت و ارسال شد.")
    elif kind == "to_pishva":
        srv = servers.get(srvctx.current()) or {}
        me = update.effective_user
        header = (f"📨 <b>پیام از مدیر مجموعه</b>\n🏫 {_e(srv.get('name'))} (سرور {srvctx.current()})\n"
                  f"👤 {_e(me.full_name)} — <code>{me.id}</code>\n\n")
        try:
            await ctx.bot.send_message(
                PISHVA_ID, header + _e(text[:3500]), parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 پاسخ", url=f"tg://user?id={me.id}")]]))
            await update.message.reply_text("✅ پیام برای مدیر ارشد ارسال شد.")
        except Exception:
            logger.exception("own_msg send")
            await update.message.reply_text("❌ ارسال نشد؛ لطفاً از «چتِ مستقیم» استفاده کنید.")
    raise ApplicationHandlerStop


def register(app):
    cb = CallbackQueryHandler
    app.add_handler(cb(own_dash, pattern=r"^own_dash$"))
    app.add_handler(cb(own_mgrs, pattern=r"^own_mgrs$"))
    app.add_handler(cb(own_manager, pattern=r"^own_m_\d+$"))
    app.add_handler(cb(own_toggle, pattern=r"^own_tg_\d+$"))
    app.add_handler(cb(own_role, pattern=r"^own_rl_\d+$"))
    app.add_handler(cb(own_remove, pattern=r"^own_rm_\d+$"))
    app.add_handler(cb(own_remove_yes, pattern=r"^own_rmy_\d+$"))
    app.add_handler(cb(own_rename, pattern=r"^own_rn_\d+$"))
    app.add_handler(cb(own_warn, pattern=r"^own_wn_\d+$"))
    app.add_handler(cb(own_invite, pattern=r"^own_invite$"))
    app.add_handler(cb(own_invite_new, pattern=r"^own_inv_new$"))
    app.add_handler(cb(own_invite_off, pattern=r"^own_inv_off$"))
    app.add_handler(cb(own_backup, pattern=r"^own_bk$"))
    app.add_handler(cb(own_backup_send, pattern=r"^own_bkx_(excel|word)$"))
    app.add_handler(cb(own_contact, pattern=r"^own_contact$"))
    app.add_handler(cb(own_msg, pattern=r"^own_msg$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, own_text), group=-4)
