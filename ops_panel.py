"""ops_panel.py — بخشِ «🏛️ گروه فرماندهی» در پنلِ مدیر ارشد (کنترلِ دستی + مشاوره با رهگشا)."""
import logging

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M
from telegram.ext import CallbackQueryHandler

import ops_group as og
from config import PISHVA_ID
from helpers import safe_edit_message_text

logger = logging.getLogger(__name__)

TIMES = ("18:00", "20:00", "21:00", "22:00", "23:00")


def _kb_main():
    return M([
        [B("📋 وضعیت تاپیک‌ها", callback_data="opsp_status"), B("🧭 مسیر رویدادها", callback_data="opsp_routes")],
        [B("🔄 ساخت/تعمیر تاپیک‌ها", callback_data="opsp_rebuild"), B("🧪 پیام تست", callback_data="opsp_test")],
        [B("⏰ ساعت گزارش مسابقات", callback_data="opsp_time"), B("📤 گزارش مسابقات همین الان", callback_data="opsp_digest")],
        [B("🛡️ بررسی امنیتی", callback_data="opsp_secrev"), B("🔃 تازه‌سازی پنل‌ها", callback_data="opsp_refresh")],
        [B("🤖 مشاوره/دستور با رهگشا", callback_data="opsp_ai")],
        [B("📖 راهنمای اتصال", callback_data="opsp_howto")],
        [B("🔙 بازگشت", callback_data="pishva_panel")],
    ])


def _back():
    return M([[B("🔙 گروه فرماندهی", callback_data="ops_menu")]])


async def _guard(q) -> bool:
    if q.from_user.id != PISHVA_ID:
        await q.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return False
    return True


async def ops_menu(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    await q.answer()
    og._BOT = og._BOT or ctx.bot
    cfg = await og.load_cfg(force=True)
    if cfg["group_id"]:
        made = sum(1 for t in cfg["topics"].values() if t.get("thread_id"))
        head = f"✅ وصل است — {made}/{len(cfg['topics'])} تاپیک فعال\n⏰ گزارشِ مسابقات: {cfg['matches_time']}"
    else:
        head = "⚪️ هنوز وصل نیست — «📖 راهنمای اتصال» را ببینید."
    await safe_edit_message_text(q, f"🏛️ گروه فرماندهی\n━━━━━━━━━━━━━━\n{head}", reply_markup=_kb_main())


async def ops_cb(update, ctx):
    q = update.callback_query
    if not await _guard(q):
        return
    d = q.data
    og._BOT = og._BOT or ctx.bot
    cfg = await og.load_cfg(force=True)

    if d == "opsp_howto":
        await q.answer()
        await safe_edit_message_text(q, (
            "📖 اتصالِ گروه (۴ مرحله)\n\n"
            "۱) یک «سوپرگروه» بسازید و در تنظیماتش گزینه‌ی Topics را روشن کنید.\n"
            "۲) ربات را به گروه اضافه و «ادمین» کنید؛ دسترسی‌های مدیریتِ تاپیک‌ها، پین‌کردن و حذفِ پیام را بدهید.\n"
            "۳) خودتان داخل گروه بنویسید: /bindops\n"
            "۴) ربات تاپیک‌ها را می‌سازد؛ تاپیک‌های بدونِ ورودی بسته می‌شوند.\n\n"
            "هر وقت تاپیکی پاک شد یا خراب شد: /opsrebuild"), reply_markup=_back())
        return
    if d == "opsp_status":
        await q.answer()
        if not cfg["group_id"]:
            await safe_edit_message_text(q, "⚪️ گروه وصل نیست.", reply_markup=_back())
            return
        lines = ["📋 تاپیک‌ها\n"]
        for k, t in cfg["topics"].items():
            mode = {"none": "🔒 فقط‌خواندنی", "commands": "⌨️ دستوری", "ai": "🤖 چت رهگشا"}.get(t.get("mode"), t.get("mode"))
            lines.append(f"{t['emoji']} {t['title']} ({k}) — {mode}" + ("" if t.get("thread_id") else "  ⚠️ ساخته نشده"))
        await safe_edit_message_text(q, "\n".join(lines), reply_markup=_back())
        return
    if d == "opsp_routes":
        await q.answer()
        lines = ["🧭 مسیرِ رویدادها\n"]
        for e, k in cfg["routes"].items():
            t = cfg["topics"].get(k, {})
            lines.append(f"• {og.EVENT_HELP.get(e, e)} ← {t.get('emoji', '')} {t.get('title', k)}")
        lines.append("\nبرای تغییر به رهگشا بگویید، مثلاً: «اعلان‌ها بره توی تاپیک جدیدی به اسم ...».")
        await safe_edit_message_text(q, "\n".join(lines), reply_markup=_back())
        return
    if d == "opsp_rebuild":
        if not cfg["group_id"]:
            await q.answer("اول گروه را وصل کنید.", show_alert=True)
            return
        await q.answer("⏳")
        res = await og.ensure_topics(ctx.bot)
        bad = [k for k, ok in res.items() if not ok]
        await safe_edit_message_text(q, "✅ همه‌ی تاپیک‌ها آماده است." if not bad else "⚠️ ساخته نشد: " + "، ".join(bad), reply_markup=_back())
        return
    if d == "opsp_test":
        if not cfg["group_id"]:
            await q.answer("اول گروه را وصل کنید.", show_alert=True)
            return
        n = 0
        for k in cfg["topics"]:
            if await og.post(k, "🧪 پیامِ تستِ مرکزِ فرماندهی"):
                n += 1
        await q.answer(f"{n} تاپیک پیام گرفت.", show_alert=True)
        return
    if d == "opsp_time":
        await q.answer()
        rows = [[B(("✅ " if t == cfg["matches_time"] else "") + t, callback_data=f"opsp_t_{t.replace(':', '')}") for t in TIMES]]
        rows.append([B("🔙 گروه فرماندهی", callback_data="ops_menu")])
        await safe_edit_message_text(q, f"⏰ ساعتِ گزارشِ روزانه‌ی مسابقات (فعلی: {cfg['matches_time']})\nساعتِ دلخواه را از رهگشا بخواهید.",
                                     reply_markup=M(rows))
        return
    if d.startswith("opsp_t_"):
        t = d[7:]
        await og.save_kv("matches_time", f"{t[:2]}:{t[2:]}")
        await q.answer("✅ ثبت شد.")
        await ops_menu(update, ctx)
        return
    if d == "opsp_digest":
        ok, msg = await og.send_digest(force=True)
        await q.answer(msg, show_alert=True)
        return
    if d == "opsp_secrev":
        if not cfg["group_id"]:
            await q.answer("اول گروه را وصل کنید.", show_alert=True)
            return
        await q.answer("⏳ در حال بررسی…")
        await og.security_review(ctx.bot)
        return
    if d == "opsp_refresh":
        await q.answer("⏳")
        for f in (og.refresh_security, og.refresh_admins, og.refresh_servers):
            try:
                await f()
            except Exception:
                logger.info("ops_panel refresh failed", exc_info=True)
        await q.answer("✅ تازه شد.", show_alert=True)
        return
    if d == "opsp_ai":
        import ai_assistant as aia
        await aia.ai_assistant_open(update, ctx)
        await q.message.reply_text(
            "🏛️ درباره‌ی گروه فرماندهی هر چه خواستید بگویید: مشاوره بخواهید، تاپیکِ جدید بسازید (جزئیاتش را بدهید)، "
            "مسیرِ رویدادها را عوض کنید، ساعتِ گزارشِ مسابقات را تنظیم کنید یا بررسیِ امنیتی بخواهید.")
        return
    await q.answer()


def register(app):
    app.add_handler(CallbackQueryHandler(ops_menu, pattern=r"^ops_menu$"))
    app.add_handler(CallbackQueryHandler(ops_cb, pattern=r"^opsp_"))
