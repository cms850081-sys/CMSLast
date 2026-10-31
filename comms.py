from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
import database as db
import keyboards as kb
from helpers import (box, separator, now_shamsi, broadcast_to_admins,
    notify_pishva, pishva_display, send_notification, safe_edit_message_text,
    safe_send_media, safe_send_message)
import html
import logging

logger = logging.getLogger(__name__)
from servers import is_boss as _boss, boss_chat as _boss_chat
from config import (PISHVA_ID, ST_SEND_MSG_SELECT_ADMIN, ST_SEND_MSG_TEXT,
    ST_ANNOUNCEMENT_TEXT, ST_ANNOUNCEMENT_FILE, ST_NEWS_TEXT)

# ─── Send message to admin (Pishva) ──────────────────────────
async def comms_msg_admin_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    admins = await db.get_active_admins()
    if not admins:
        await safe_edit_message_text(query, "❗ هیچ مدیر فعالی وجود ندارد.", reply_markup=kb.kb_back("comms_pishva"))
        return
    rows = []
    for i in range(0, len(admins), 2):
        row = [InlineKeyboardButton(
            f"👤 {a['display_name'] or a['full_name']}",
            callback_data=f"cmsg_{a['telegram_id']}"
        ) for a in admins[i:i+2]]
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_comms")])
    await safe_edit_message_text(query, 
        "💬 به کدام مدیر پیام می‌فرستید؟",
        reply_markup=InlineKeyboardMarkup(rows)
    )
    return ST_SEND_MSG_SELECT_ADMIN

async def comms_msg_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tid = int(query.data.split("_")[-1])
    ctx.user_data["msg_target"] = tid
    admin = await db.get_admin(tid)
    await safe_edit_message_text(query, 
        f"✍️ پیام به *{admin['display_name'] or admin['full_name']}*:",
        parse_mode="Markdown"
    )
    return ST_SEND_MSG_TEXT

async def comms_msg_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    tid = ctx.user_data.get("msg_target")
    uid = update.effective_user.id
    pname = await pishva_display()
    if tid and text:
        msg_id = await db.send_message_db(uid, tid, text)
        ts = now_shamsi()
        notif = (
            f"{box('📨 پیام جدید')}\n\n"
            f"📬 شما یک پیام جدید دارید.\n"
            f"👤 از: {pname if uid == PISHVA_ID else 'مدیر ارشد'}\n"
            f"⏱️ `{ts}`\n\n"
            f"💬 متن: _{text}_"
        )
        try:
            sent = await ctx.bot.send_message(
                chat_id=tid, text=notif,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ تأیید مطالعه", callback_data=f"msg_ack_{msg_id}")]
                ]),
                parse_mode="Markdown"
            )
            # آی‌دیِ پیامِ ارسال‌شده رو ذخیره می‌کنیم تا اگه فرستنده بعداً از
            # «پیام‌های ارسالی» حذفش کرد، بشه واقعاً از چتِ گیرنده هم پاکش کرد.
            await db.set_message_notif(msg_id, sent.chat_id, sent.message_id)
        except Exception:
            pass
    await update.message.reply_text("✅ پیام ارسال شد.")
    return ConversationHandler.END

# ─── Send msg to Pishva (Admin) ───────────────────────────────
async def comms_msg_pishva_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["msg_target"] = PISHVA_ID
    pname = await pishva_display()
    await safe_edit_message_text(query, f"✍️ پیام به *{pname}*:", parse_mode="Markdown")
    return ST_SEND_MSG_TEXT

# ─── Send msg to other admin ──────────────────────────────────
async def comms_msg_other_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    admins = await db.get_active_admins()
    others = [a for a in admins if a["telegram_id"] != uid]
    if not others:
        await safe_edit_message_text(query, "❗ هیچ مدیر دیگری فعال نیست.", reply_markup=kb.kb_back("comms_admin"))
        return
    rows = []
    for i in range(0, len(others), 2):
        row = [InlineKeyboardButton(
            f"👤 {a['display_name'] or a['full_name']}",
            callback_data=f"cmsg_{a['telegram_id']}"
        ) for a in others[i:i+2]]
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_comms")])
    await safe_edit_message_text(query, "💬 به کدام مدیر پیام می‌فرستید؟", reply_markup=InlineKeyboardMarkup(rows))
    return ST_SEND_MSG_SELECT_ADMIN

async def comms_inbox(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    msgs = await db.get_messages_for(uid)
    if not msgs:
        await safe_edit_message_text(query, "📭 هیچ پیامی دریافت نکرده‌اید.", reply_markup=kb.kb_back("comms"))
        return
    pname = await pishva_display()
    admins = {a["telegram_id"]: (a["display_name"] or a["full_name"]) for a in await db.get_all_admins()}
    lines = []
    for m in msgs[:15]:
        sender_name = pname if m["sender_id"] == PISHVA_ID else admins.get(m["sender_id"], "رهگشا 🤖" if m["sender_id"] == 0 else "ربات")
        read_icon = "✅" if m["is_read"] else "🔵"
        lines.append(f"{read_icon} از {sender_name}: _{str(m['text'])[:80]}_")
    await safe_edit_message_text(query, 
        f"{box('📨 پیام‌های دریافتی')}\n\n" + "\n\n".join(lines),
        reply_markup=kb.kb_back("comms"),
        parse_mode="Markdown"
    )

async def comms_sent_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """تاریخچه‌ی پیام‌های ارسالیِ خودِ کاربر (چه مدیر ارشد چه یه ادمین
    معمولی) — هر کدوم فقط پیام‌های خودشون رو می‌بینن."""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    msgs = await db.get_sent_messages_for(uid)
    if not msgs:
        back_to = "comms_pishva" if _boss(uid) else "comms_admin"
        await safe_edit_message_text(query, "📭 هیچ پیامی ارسال نکرده‌اید.", reply_markup=kb.kb_back(back_to))
        return
    admins = {a["telegram_id"]: (a["display_name"] or a["full_name"]) for a in await db.get_all_admins()}
    pname = await pishva_display()
    rows = []
    for m in msgs[:15]:
        rn = pname if m["receiver_id"] == PISHVA_ID else admins.get(m["receiver_id"], str(m["receiver_id"]))
        rows.append([
            InlineKeyboardButton(
                f"📤 به {rn}: {str(m['text'])[:30]}...",
                callback_data=f"sent_msg_view_{m['id']}"
            )
        ])
    back_to = "comms_pishva" if _boss(uid) else "comms_admin"
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=back_to)])
    await safe_edit_message_text(query, 
        f"{box('📤 پیام‌های ارسالی')}\n\n📌 یک پیام را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown"
    )

async def sent_msg_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    msg_id = int(query.data.split("_")[-1])
    m = await db.get_message(msg_id)
    # فقط خودِ فرستنده حق دیدن/حذفِ پیامِ ارسالی‌ش رو داره.
    if not m or m["sender_id"] != uid:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    admins = {a["telegram_id"]: (a["display_name"] or a["full_name"]) for a in await db.get_all_admins()}
    pname = await pishva_display()
    rn = pname if m["receiver_id"] == PISHVA_ID else admins.get(m["receiver_id"], str(m["receiver_id"]))
    rows = [
        [InlineKeyboardButton("🗑️ حذف پیام", callback_data=f"sent_msg_delete_{msg_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="comms_sent_history")]
    ]
    await safe_edit_message_text(query, 
        f"📤 <b>پیام ارسالی</b>\n\n👤 به: {html.escape(str(rn))}\n💬 {html.escape(str(m['text']))}\n\n"
        f"⏱️ <code>{html.escape(str(m['sent_at'])[:19])}</code>",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML"
    )

async def sent_msg_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    msg_id = int(query.data.split("_")[-1])
    m = await db.get_message(msg_id)
    if not m or m["sender_id"] != uid:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    # حذفِ واقعی: خودِ پیامی که برای گیرنده فرستاده شده رو از چتش پاک
    # می‌کنیم، نه فقط رکورد رو توی دیتابیس علامت می‌زنیم.
    if m["notif_chat_id"] and m["notif_message_id"]:
        try:
            await ctx.bot.delete_message(chat_id=m["notif_chat_id"], message_id=m["notif_message_id"])
        except Exception as e:
            logger.warning(f"Failed to delete telegram message for sent msg {msg_id}: {e}")
    # حذفِ نرم: فقط از تاریخچه‌ی خودِ فرستنده پاک می‌شه؛ اگه فرستنده یه
    # ادمینِ معمولی باشه، ردیف توی دیتابیس می‌مونه و همچنان توی «پیام
    # ادمین‌ها»ی مدیر ارشد (comms_all_msgs) دیده می‌شه.
    await db.delete_sent_message(msg_id)
    await safe_edit_message_text(
        query, "🗑️ پیام حذف شد.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="comms_sent_history")]])
    )

async def comms_all_msgs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    msgs = await db.get_all_messages()
    admins = {a["telegram_id"]: (a["display_name"] or a["full_name"]) for a in await db.get_all_admins()}
    pname = await pishva_display()
    lines = []
    for m in msgs[:20]:
        sn = pname if m["sender_id"] == PISHVA_ID else admins.get(m["sender_id"], str(m["sender_id"]))
        rn = pname if m["receiver_id"] == PISHVA_ID else admins.get(m["receiver_id"], str(m["receiver_id"]))
        lines.append(f"👤 {sn} → {rn}: _{str(m['text'])[:60]}_")
    await safe_edit_message_text(query, 
        f"{box('👁️ پیام ادمین‌ها')}\n\n" + "\n\n".join(lines) if lines else "❗ پیامی وجود ندارد.",
        reply_markup=kb.kb_back("comms"),
        parse_mode="Markdown"
    )

# ─── Announcements ────────────────────────────────────────────
async def comms_announce_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query, "📢 متن بیانیه را وارد کنید:")
    return ST_ANNOUNCEMENT_TEXT

async def comms_announce_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # update.message.text خامه و فرمت‌بندی (بولد/ایتالیک و...) که کاربر با
    # دکمه‌های خودِ تلگرام روی متن اعمال کرده رو حذف می‌کنه، چون اون فرمت‌ها
    # به‌صورت entity جدا ذخیره می‌شن نه کاراکتر توی خودِ متن. text_html همون
    # entity ها رو به تگ HTML تبدیل می‌کنه تا موقع ارسال دوباره حفظ بشن.
    text = (update.message.text_html or update.message.text or "").strip()
    ctx.user_data["announce_text"] = text
    await update.message.reply_text(
        "آیا فایلی برای پیوست دارید؟",
        reply_markup=kb.kb_announce_file()
    )
    return ST_ANNOUNCEMENT_FILE

async def comms_announce_no_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = ctx.user_data.get("announce_text", "")
    await _send_announcement(ctx.bot, text, "", "")
    await safe_edit_message_text(query, "✅ بیانیه ارسال شد.", reply_markup=kb.kb_back("comms"))
    return ConversationHandler.END

async def comms_announce_with_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, "📎 فایل موردنظر را ارسال کنید (عکس، سند، ویدیو یا صوت):")
    return ST_ANNOUNCEMENT_FILE

async def comms_announce_file_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = ctx.user_data.get("announce_text", "")
    file_id = ""
    file_type = ""
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_type = "photo"
    elif update.message.document:
        file_id = update.message.document.file_id
        file_type = "document"
    elif update.message.video:
        file_id = update.message.video.file_id
        file_type = "video"
    elif update.message.audio or update.message.voice:
        obj = update.message.audio or update.message.voice
        file_id = obj.file_id
        file_type = "audio"
    await _send_announcement(ctx.bot, text, file_id, file_type)
    await update.message.reply_text("✅ بیانیه با پیوست ارسال شد.", reply_markup=kb.kb_back("comms"))
    return ConversationHandler.END

async def _send_announcement(bot, text: str, file_id: str, file_type: str, via_assistant: bool = False):
    ann_id = await db.create_announcement(text, file_id, file_type)
    pname = await db.get_setting("pishva_display_name", "مدیر ارشد")
    ts = now_shamsi()
    footer = f"⏱️ <code>{html.escape(ts)}</code>\n👑 {html.escape(pname)}"
    if via_assistant:
        # وقتی بیانیه از طریق دستیار هوشمند (نه مستقیم از پنل) فرستاده می‌شه،
        # صراحتاً بگو که ارسالش کار دستیار بوده — نه اینکه انگار خودِ مدیر
        # ارشد لحظه‌به‌لحظه پشت پنل نشسته و تایپ کرده.
        footer += "\n🤖 ارسال‌شده توسط رهگشا"
    # از HTML به‌جای Markdown استفاده می‌کنیم: هم فرمت بولد/ایتالیکی که کاربر
    # توی تلگرام روی متن اعمال کرده (تبدیل‌شده به تگ توسط text_html) درست
    # نمایش داده می‌شه، هم دیگه یه کاراکتر معمولی مثل «_» یا «*» توی متنِ
    # بیانیه باعث خطای «Can't parse entities» و ارسال‌نشدنِ کامل پیام نمی‌شه.
    full_text = f"📢 <b>بیانیه رسمی</b>\n\n{text}\n\n{footer}"
    notif_on = await db.get_setting("notifications_enabled", "1")
    if notif_on != "1":
        return
    admins = await db.get_active_admins()
    group_id = await db.get_setting("announcement_group_id", "")
    channel_id = await db.get_setting("announcement_channel_id", "")
    group_broadcast_on = await db.get_setting("broadcast_announcement_group_enabled", "1")
    channel_broadcast_on = await db.get_setting("broadcast_announcement_channel_enabled", "1")
    targets = [a["telegram_id"] for a in admins]
    if group_id and group_broadcast_on == "1":
        targets.append(int(group_id))
    if channel_id and channel_broadcast_on == "1":
        targets.append(int(channel_id))
    for tid in targets:
        if file_id:
            await safe_send_media(bot, tid, file_type, file_id, full_text, parse_mode="HTML")
        else:
            try:
                await safe_send_message(bot, tid, full_text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"Failed to send announcement to {tid}: {e}")

async def comms_ann_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    anns = await db.get_all_announcements()
    if not anns:
        await safe_edit_message_text(query, "📭 هیچ بیانیه‌ای وجود ندارد.", reply_markup=kb.kb_back("comms"))
        return
    rows = []
    for a in anns[:15]:
        rows.append([
            InlineKeyboardButton(
                f"📜 {str(a['sent_at'])[:10]}: {str(a['text'])[:30]}...",
                callback_data=f"ann_view_{a['id']}"
            )
        ])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_comms")])
    await safe_edit_message_text(query, 
        f"{box('📜 تاریخچه بیانیات')}\n\n📌 یک بیانیه انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown"
    )

async def ann_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ann_id = int(query.data.split("_")[-1])
    anns = await db.get_all_announcements()
    ann = next((a for a in anns if a["id"] == ann_id), None)
    if not ann:
        await query.answer("بیانیه یافت نشد.", show_alert=True)
        return
    rows = []
    if _boss(query.from_user.id):
        rows.append([InlineKeyboardButton("🗑️ حذف بیانیه", callback_data=f"ann_delete_{ann_id}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="comms_ann_history")])
    await safe_edit_message_text(query, 
        f"📢 <b>بیانیه</b>\n\n{ann['text']}\n\n⏱️ <code>{html.escape(str(ann['sent_at'])[:19])}</code>\n"
        f"{'📎 دارای پیوست' if ann['file_id'] else ''}",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML"
    )

async def ann_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    ann_id = int(query.data.split("_")[-1])
    await db.delete_announcement(ann_id)
    await safe_edit_message_text(query, "🗑️ بیانیه حذف شد.", reply_markup=kb.kb_back("comms"))

# ─── News ─────────────────────────────────────────────────────
async def comms_news_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    await safe_edit_message_text(query, "📰 متن خبر فوری را وارد کنید:")
    return ST_NEWS_TEXT

async def comms_news_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # همون دلیل بیانیه: text_html فرمت بولد/ایتالیکِ اعمال‌شده توسط کاربر رو
    # حفظ می‌کنه، برخلاف .text خام.
    text = (update.message.text_html or update.message.text or "").strip()
    ts = now_shamsi()
    pname = await pishva_display()
    news_text = f"✨ <b>خبر فوری از سیستم</b>✨\n\n{text}\n\n⏱️ <code>{html.escape(ts)}</code>"
    await db.create_news(text)
    await broadcast_to_admins(ctx.bot, news_text, parse_mode="HTML")
    await update.message.reply_text("✅ خبر فوری برای همه ارسال شد.")
    return ConversationHandler.END

async def comms_news_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    news = await db.get_all_news()
    if not news:
        await safe_edit_message_text(query, "📭 هیچ خبری ثبت نشده.", reply_markup=kb.kb_back("comms"))
        return
    lines = [f"📰 <code>{html.escape(str(n['sent_at'])[:10])}</code> — {n['text'][:80]}" for n in news[:20]]
    await safe_edit_message_text(query, 
        f"{box('📰 اخبار')}\n\n" + "\n\n".join(lines),
        reply_markup=kb.kb_back("comms"),
        parse_mode="HTML"
    )

async def comms_notifs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    msgs = await db.get_all_news()
    anns = await db.get_all_announcements()
    combined = [(n["sent_at"], "📰 خبر", n["text"]) for n in msgs[:5]]
    combined += [(a["sent_at"], "📢 بیانیه", a["text"]) for a in anns[:5]]
    combined.sort(key=lambda x: x[0], reverse=True)
    lines = [f"{t} <code>{html.escape(str(d)[:10])}</code>: {txt[:60]}" for d, t, txt in combined[:15]]
    await safe_edit_message_text(
        query,
        f"{box('🔔 اعلانات اخیر')}\n\n" + ("\n\n".join(lines) or "❗ اعلانی وجود ندارد."),
        reply_markup=kb.kb_back("comms"),
        parse_mode="HTML"
    )

async def comms_reports(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _boss(query.from_user.id):
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    feedbacks = await db.get_all_feedback()
    reports = [f for f in feedbacks if f["fb_type"] == "report"]
    if not reports:
        await safe_edit_message_text(query, "📊 هیچ گزارشی دریافت نشده.", reply_markup=kb.kb_back("comms"))
        return
    lines = [f"🚨 {r['content'][:80]}" for r in reports[:15]]
    await safe_edit_message_text(
        query,
        f"{box('📊 گزارشات')}\n\n" + "\n\n".join(lines),
        reply_markup=kb.kb_back("comms")
    )

async def msg_ack(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    msg_id = None
    try:
        msg_id = int(query.data.split("_")[-1])
    except (ValueError, IndexError):
        pass
    if msg_id is not None:
        await db.mark_message_read(msg_id)
        m = await db.get_message(msg_id)
        if m:
            receiver_id = m["receiver_id"]
            if receiver_id == PISHVA_ID:
                receiver_name = await pishva_display()
            else:
                admin = await db.get_admin(receiver_id)
                receiver_name = (admin["display_name"] or admin["full_name"]) if admin else "کاربر"
            try:
                await ctx.bot.send_message(
                    chat_id=m["sender_id"],
                    text=(
                        f"{box('✅ تأیید مطالعه')}\n\n"
                        f"👤 *{receiver_name}* پیام شما را خواند.\n"
                        f"💬 _{str(m['text'])[:80]}_"
                    ),
                    parse_mode="Markdown"
                )
            except Exception:
                pass
    await query.answer("✅ پیام خوانده شد.")
    # دکمه رو بعد از تأیید حذف می‌کنیم تا معلوم بشه قبلاً خونده شده.
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
