"""
ai_history.py — منوی چت دستیار هوشمند (خروج/چت جدید/تاریخچه) + بازبینی
سوابق چت مدیران با هوش مصنوعی توسط مدیر ارشد.

این فایل عمداً از ai_assistant.py جدا نگه داشته شده تا اون فایل روی
خود گفت‌وگو با Gemini تمرکز داشته باشه و این یکی روی مدیریت/نمایش تاریخچه.
"""
import json
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from helpers import safe_edit_message_text, box, get_user_role
from config import PISHVA_ID, ROLE_TOURNAMENT_MANAGER

logger = logging.getLogger(__name__)

PERIOD_LABELS = {"today": "📅 امروز", "week": "📆 این هفته", "month": "🗓️ این ماه", "all": "📚 از ابتدا"}


def _fmt_dt(s: str) -> str:
    return (s or "")[:16].replace("T", " ")


def _session_label(sess) -> str:
    title = (sess["title"] or "").strip() or "بدون عنوان"
    if len(title) > 28:
        title = title[:28] + "…"
    return f"{_fmt_dt(sess['last_message_at'])} — {title}"


# ────────────────────────────────────────────────────────────────
# منوی خود کاربر: خروج از چت / چت جدید / تاریخچه
# ────────────────────────────────────────────────────────────────
async def ai_exit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["ai_mode"] = False
    ctx.user_data.pop("ai_history", None)
    ctx.user_data.pop("ai_session_id", None)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await query.message.reply_text("👋 از گفتگو با رهگشا خارج شدی. هر وقت خواستی بنویس «رهگشا».")


async def ai_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """«➕ بیشتر» — منوی جامع رهگشا (ai_more.py)."""
    import ai_more
    query = update.callback_query
    role = await get_user_role(query.from_user.id)
    if not role:
        await query.answer("⛔", show_alert=True)
        return
    await query.answer()
    await ai_more.show_hub(query, ctx, new_message=True)


async def ai_menu_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


async def ai_new_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from ai_assistant import kb_ai_reply, _is_ai_online, _can_use_ai, AI_OFFLINE_MESSAGE
    query = update.callback_query
    uid = query.from_user.id
    role = await get_user_role(uid)
    if not role:
        await query.answer("⛔", show_alert=True)
        return
    if not await _is_ai_online():
        await query.answer()
        await query.message.reply_text(AI_OFFLINE_MESSAGE)
        return
    if not await _can_use_ai(uid, role):
        await query.answer("⛔ دسترسی شما مسدود است.", show_alert=True)
        return
    await query.answer()
    ctx.user_data["ai_mode"] = True
    ctx.user_data["ai_history"] = []
    ctx.user_data["ai_session_id"] = await db.ai_create_session(uid, role)
    await query.message.reply_text(
        "🆕 چت جدید شروع شد. هرچی بخوای بگو.",
        reply_markup=kb_ai_reply()
    )


async def ai_hist_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    sessions = await db.ai_get_sessions_for_user(uid, limit=15)
    if not sessions:
        await query.message.reply_text("📭 هنوز چتی با رهگشا نداشتی.")
        return
    rows = [[InlineKeyboardButton(_session_label(s), callback_data=f"ai_hist_open_{s['id']}")] for s in sessions]
    rows.append([InlineKeyboardButton("🔙 بستن", callback_data="ai_menu_close")])
    await query.message.reply_text(
        f"{box('🕘 تاریخچه‌ی چت‌های تو')}\n\nروی هرکدام بزن تا ادامه‌اش بدی:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown"
    )


async def ai_hist_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from ai_assistant import kb_ai_reply, _is_ai_online, _can_use_ai, AI_OFFLINE_MESSAGE
    query = update.callback_query
    uid = query.from_user.id
    role = await get_user_role(uid)
    sid = int(query.data.split("_")[-1])
    sess = await db.ai_get_session(sid)
    if not sess or sess["user_id"] != uid:
        await query.answer("⛔ این چت مال شما نیست یا پیدا نشد.", show_alert=True)
        return
    if not await _is_ai_online():
        await query.answer()
        await query.message.reply_text(AI_OFFLINE_MESSAGE)
        return
    if not role or not await _can_use_ai(uid, role):
        await query.answer("⛔ دسترسی شما مسدود است.", show_alert=True)
        return
    await query.answer()

    msgs = await db.ai_get_messages(sid)
    # بازسازی تاریخچه‌ی گفت‌وگو به فرمت Gemini (فقط user/ai — لاگ ابزارها برای مدل لازم نیست)
    history = []
    for m in msgs:
        if m["sender"] == "user":
            history.append({"role": "user", "parts": [{"text": m["text"]}]})
        elif m["sender"] == "ai":
            history.append({"role": "model", "parts": [{"text": m["text"]}]})
    ctx.user_data["ai_mode"] = True
    ctx.user_data["ai_history"] = history[-12:]
    ctx.user_data["ai_session_id"] = sid

    recap_lines = []
    for m in msgs[-6:]:
        if m["sender"] == "user":
            recap_lines.append(f"👤 {m['text'][:200]}")
        elif m["sender"] == "ai":
            recap_lines.append(f"🤖 {m['text'][:200]}")
    recap = "\n\n".join(recap_lines) or "— (خالی) —"
    await query.message.reply_text(
        f"{box('🕘 ادامه‌ی چت قبلی')}\n\n{recap}\n\n_ادامه بده…_",
        reply_markup=kb_ai_reply(),
        parse_mode="Markdown"
    )


# ────────────────────────────────────────────────────────────────
# سمت مدیر ارشد: مشاهده‌ی سوابق چت مدیران با هوش مصنوعی
# ────────────────────────────────────────────────────────────────
async def ai_admlog_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    admins = await db.get_all_admins()
    if not admins:
        await safe_edit_message_text(query, f"{box('🗂️ سوابق AI ادمین‌ها')}\n\n❗ هیچ مدیری ثبت نشده.",
                                       reply_markup=InlineKeyboardMarkup(
                                           [[InlineKeyboardButton("🔙 بازگشت", callback_data="menu_pishva")]]))
        return
    rows = []
    for a in admins:
        role_icon = "🏆" if a["role"] == ROLE_TOURNAMENT_MANAGER else "🛡️"
        name = a["display_name"] or a["full_name"]
        rows.append([InlineKeyboardButton(f"{role_icon} {name}", callback_data=f"ai_admlog_pick_{a['telegram_id']}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu_pishva")])
    await safe_edit_message_text(query, 
        f"{box('🗂️ سوابق AI ادمین‌ها')}\n\n📌 یک مدیر را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown"
    )


async def ai_admlog_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    tid = int(query.data.split("_")[-1])
    admin = await db.get_admin(tid)
    name = (admin["display_name"] or admin["full_name"]) if admin else str(tid)
    rows = [
        [InlineKeyboardButton(PERIOD_LABELS["today"], callback_data=f"ai_admlog_range_{tid}_today"),
         InlineKeyboardButton(PERIOD_LABELS["week"], callback_data=f"ai_admlog_range_{tid}_week")],
        [InlineKeyboardButton(PERIOD_LABELS["month"], callback_data=f"ai_admlog_range_{tid}_month"),
         InlineKeyboardButton(PERIOD_LABELS["all"], callback_data=f"ai_admlog_range_{tid}_all")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="ai_admlog_menu")],
    ]
    await safe_edit_message_text(query, 
        f"{box('🗂️ سوابق AI — ' + name)}\n\n📌 بازه‌ی زمانی را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown"
    )


async def ai_admlog_range(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    parts = query.data.split("_")
    tid = int(parts[-2])
    period = parts[-1]
    admin = await db.get_admin(tid)
    name = (admin["display_name"] or admin["full_name"]) if admin else str(tid)
    sessions = await db.ai_get_sessions_filtered(user_id=tid, period=period, limit=40)
    if not sessions:
        rows = [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"ai_admlog_pick_{tid}")]]
        await safe_edit_message_text(query, 
            f"{box('🗂️ سوابق AI — ' + name)}\n\n❗ چتی در این بازه پیدا نشد.",
            reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")
        return
    rows = [[InlineKeyboardButton(_session_label(s), callback_data=f"ai_admlog_view_{s['id']}")] for s in sessions]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"ai_admlog_pick_{tid}")])
    await safe_edit_message_text(query, 
        f"{box('🗂️ سوابق AI — ' + name)}\n\n👥 تعداد چت: `{len(sessions)}`\n\nروی هرکدام بزن تا کامل ببینی:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown"
    )


async def ai_admlog_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    sid = int(query.data.split("_")[-1])
    sess = await db.ai_get_session(sid)
    if not sess:
        await safe_edit_message_text(query, "❗ این چت دیگر وجود ندارد.")
        return
    admin = await db.get_admin(sess["user_id"])
    name = (admin["display_name"] or admin["full_name"]) if admin else str(sess["user_id"])
    msgs = await db.ai_get_messages(sid)

    lines = [f"{box('🗂️ چت ' + name)}", f"⏱️ شروع: `{_fmt_dt(sess['started_at'])}`", ""]
    for m in msgs:
        if m["sender"] == "user":
            lines.append(f"👤 *{name}:* {m['text']}")
        elif m["sender"] == "ai":
            lines.append(f"🤖 *رهگشا:* {m['text']}")
        else:  # tool
            lines.append(f"🔧 _{m['text']}_")
    text = "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n\n… (متن کامل طولانی‌تر بود و کوتاه شد)"

    rows = [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"ai_admlog_pick_{sess['user_id']}")]]
    await safe_edit_message_text(query, text, reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")
