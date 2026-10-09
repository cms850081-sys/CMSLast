"""
ai_more.py — دکمه‌ی «➕ بیشتر» زیر پیام‌های رهگشا

همه‌چیزِ مربوط به دستیار یک‌جا:
  همه‌ی نقش‌ها : چت جدید، تاریخچه، تنظیمات رهگشا، حافظه‌ی من، میانبرها، کارهای زمان‌بندی‌شده‌ی من
                 (با لغو)، توانایی‌های من، «چرا این جواب؟»، راهنما، خروج.
  فقط مدیر ارشد: مدیریت دستیار (اختیارات، روشن/خاموش کلی، خاموشی برای ادمین خاص، سوابق چت مدیران)،
                 قفلِ دستور مدیر ارشد، نمای لحظه‌ای، لاگ مدیران، تنظیمات رهگشای مدیران.
همه‌ی دکمه‌های مدیر ارشد در همین‌جا دوباره بررسی می‌شن (uid == PISHVA_ID).
"""
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import database as db
from config import PISHVA_ID, ROLE_PISHVA
from helpers import safe_edit_message_text, get_user_role

logger = logging.getLogger(__name__)


def _B(text, cb):
    return InlineKeyboardButton(text, callback_data=cb)


async def _hub_markup(uid: int, ctx):
    rows = [
        [_B("🆕 چت جدید", "ai_new_start"), _B("🕘 تاریخچه‌ی چت‌ها", "ai_hist_list")],
        [_B("⚙️ تنظیمات رهگشا", "aip_home"), _B("🧠 حافظه‌ی من", "aip_mem")],
        [_B("⚡ میانبرها", "aip_sc"), _B("🤖 کارهای زمان‌بندی‌شده", "aim_sched")],
        [_B("🛠️ توانایی‌های من", "aim_abil"), _B("❓ چرا این جواب؟", "ai_why")],
        [_B("📖 راهنمای رهگشا", "aim_help")],
    ]
    text = "➕ بیشتر — همه‌چیز رهگشا اینجاست."
    if uid == PISHVA_ID:
        ctx.user_data["ai_manage_back"] = "ai_more_home"   # «بازگشت» پنل‌های مدیریت دستیار به همین منو برگرده
        online = (await db.get_setting("ai_online", "1")) == "1"
        lock = (await db.get_setting("ai_enforce_pishva_only", "1")) == "1"
        rows += [
            [_B("— 👑 مدیریت دستیار (مدیر ارشد) —", "noop_label")],
            [_B("🧑‍💻 مدیریت دستیار", "ai_manage_menu_hub"), _B("🛠️ اختیارات دستیار", "ai_perms_menu")],
            [_B("🗂️ سوابق چت مدیران", "ai_admlog_menu"), _B("🔕 خاموشی برای ادمین", "ai_admtg_menu")],
            [_B(f"🔌 هوش مصنوعی {'✅' if online else '❌'}", "aim_online"),
             _B(f"🔐 قفل دستور مدیر ارشد {'✅' if lock else '❌'}", "aim_lock")],
            [_B("🛰️ نمای لحظه‌ای", "aim_live"), _B("📜 لاگ مدیران", "aim_trail")],
            [_B("🎭 تنظیمات رهگشای مدیران", "aim_pers")],
        ]
    rows.append([_B("🚪 خروج از چت", "ai_exit"), _B("🔙 بستن", "ai_menu_close")])
    return text, InlineKeyboardMarkup(rows)


async def show_hub(query, ctx, new_message: bool):
    uid = query.from_user.id
    text, kb = await _hub_markup(uid, ctx)
    if new_message:
        await query.message.reply_text(text, reply_markup=kb)
    else:
        await safe_edit_message_text(query, text, reply_markup=kb, parse_mode=None)


async def cb_more(update, ctx):
    """ai_more_home (ویرایش درجا) — «ai_menu» قدیمی هم از ai_history به show_hub می‌رسه."""
    q = update.callback_query
    role = await get_user_role(q.from_user.id)
    if not role:
        await q.answer("⛔", show_alert=True)
        return
    await q.answer()
    await show_hub(q, ctx, new_message=False)


def _back_row():
    return [_B("🔙 بیشتر", "ai_more_home")]


async def cb_aim(update, ctx):
    q = update.callback_query
    uid = q.from_user.id
    role = await get_user_role(uid)
    if not role:
        await q.answer("⛔", show_alert=True)
        return
    d = q.data
    is_pishva = uid == PISHVA_ID

    # ── کارهای زمان‌بندی‌شده (همه؛ مدیر ارشد همه‌ی کارها را می‌بیند) ──
    if d == "aim_sched" or d.startswith("aim_cancel_"):
        import ai_scheduler
        if d.startswith("aim_cancel_"):
            try:
                job_id = int(d.split("_")[-1])
            except ValueError:
                await q.answer("شناسه نامعتبر", show_alert=True)
                return
            res = await ai_scheduler.cancel(ctx.job_queue, job_id, uid, is_pishva)
            await q.answer(res, show_alert=True)
        else:
            await q.answer()
        rows = await ai_scheduler.list_pending_for_panel(is_pishva=is_pishva, caller_id=uid)
        kb = []
        for r in rows[:15]:
            kb.append([_B(r["label"][:60], "noop_label")])
            kb.append([_B("❌ لغو", f"aim_cancel_{r['id']}")])
        if not rows:
            kb.append([_B("📭 چیزی زمان‌بندی نشده", "noop_label")])
        kb.append(_back_row())
        title = "🤖 کارهای زمان‌بندی‌شده" + (" (همه‌ی مدیران)" if is_pishva else " (فقط مال خودت)")
        return await safe_edit_message_text(q, title, reply_markup=InlineKeyboardMarkup(kb), parse_mode=None)

    # ── توانایی‌های من ──
    if d == "aim_abil":
        await q.answer()
        import ai_tools
        states = await ai_tools.get_category_states()
        lines = ["🛠️ توانایی‌های رهگشا برای نقش تو:", ""]
        for key, label, tools in ai_tools.AI_PERMISSION_CATEGORIES:
            mine = [t for t in tools if role in ai_tools.TOOL_PERMISSIONS.get(t, [])]
            if not mine:
                continue
            on = states.get(key, "1") == "1"
            lines.append(f"{'✅' if on else '❌'} {label} ({len(mine)} ابزار)" + ("" if on else " — توسط مدیر ارشد خاموش شده"))
        return await safe_edit_message_text(q, "\n".join(lines), reply_markup=InlineKeyboardMarkup([_back_row()]),
                                            parse_mode=None)

    # ── راهنما ──
    if d == "aim_help":
        await q.answer()
        txt = ("📖 راهنمای رهگشا\n\n"
               "• عادی بنویس؛ هر کاری که نقشت اجازه می‌ده خودم انجام می‌دم.\n"
               "• «یادم بنداز …» یا «فردا ساعت ۹ … رو انجام بده» برای کارهای زمان‌بندی‌شده.\n"
               "• «از این به بعد کوتاه بگو / اموجی نذار» لحنت رو همون لحظه تنظیم می‌کنه.\n"
               "• «چرا این جواب؟» نشون می‌ده چه ابزارهایی صدا زده شد.\n"
               "• میانبرها: یه کلمه‌ی کوتاه که یه دستور بلند رو اجرا می‌کنه.\n"
               "• اخطار، اخراج و حذف فقط با دستور مدیر ارشد انجام می‌شه.\n"
               "• چت‌های شما با رهگشا برای مدیر ارشد قابل مشاهده است.")
        return await safe_edit_message_text(q, txt, reply_markup=InlineKeyboardMarkup([_back_row()]), parse_mode=None)

    # ───────── از اینجا به بعد فقط مدیر ارشد ─────────
    if not is_pishva:
        await q.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return

    if d == "aim_online":
        cur = await db.get_setting("ai_online", "1")
        new = "0" if cur == "1" else "1"
        await db.set_setting("ai_online", new)
        await db.log_action(uid, "toggle_setting", f"ai_online -> {new}")
        await q.answer("اعمال شد")
        return await show_hub(q, ctx, new_message=False)

    if d == "aim_lock":
        cur = await db.get_setting("ai_enforce_pishva_only", "1")
        new = "0" if cur == "1" else "1"
        await db.set_setting("ai_enforce_pishva_only", new)
        await db.log_action(uid, "set_enforcement_lock", "روشن" if new == "1" else "خاموش")
        await q.answer("🔐 قفل روشن شد" if new == "1" else "🔓 قفل خاموش شد", show_alert=True)
        return await show_hub(q, ctx, new_message=False)

    if d == "aim_live":
        await q.answer("در حال جمع‌آوری…")
        import ai_tools_oversight as ov
        txt = await ov._get_live_overview({})
        return await q.message.reply_text(txt[:4000])

    if d == "aim_trail":
        await q.answer("در حال جمع‌آوری…")
        import ai_tools_oversight as ov
        txt = await ov._get_admin_audit_trail({"limit": 25, "scope": "all"})
        return await q.message.reply_text(txt[:4000])

    if d == "aim_pers" or d.startswith("aim_pers_") or d.startswith("aim_persreset_"):
        import ai_persona
        from ai_tools_ext import _admin_name
        await q.answer()
        if d.startswith("aim_persreset_"):
            tid = int(d.split("_")[-1])
            await ai_persona.save_prefs(tid, {})
            await db.log_action(uid, "ai_persona_reset", "بازنشانی تنظیمات رهگشا", tid)
        if d.startswith("aim_pers_") or d.startswith("aim_persreset_"):
            tid = int(d.split("_")[-1])
            a = await db.get_admin(tid)
            summary = ai_persona._summary(await ai_persona.get_prefs(tid))
            kb = [[_B("♻️ بازنشانی تنظیمات این مدیر", f"aim_persreset_{tid}")], [_B("🔙 لیست", "aim_pers")]]
            return await safe_edit_message_text(
                q, f"🎭 تنظیمات رهگشای {_admin_name(a) if a else tid}:\n\n{summary}",
                reply_markup=InlineKeyboardMarkup(kb), parse_mode=None)
        admins = await db.get_all_admins()
        kb = [[_B(f"👤 {_admin_name(a)}", f"aim_pers_{a['telegram_id']}")] for a in admins]
        kb.append(_back_row())
        return await safe_edit_message_text(q, "🎭 تنظیمات رهگشای کدام مدیر؟", reply_markup=InlineKeyboardMarkup(kb),
                                            parse_mode=None)
