"""
ops_tools.py — ابزارهای رهگشا برای کنترلِ «گروه فرماندهی» (فقط مدیر ارشد).

رهگشا با این ابزارها: وضعیتِ گروه/تاپیک‌ها را می‌بیند، در هر تاپیک پیام می‌گذارد، تاپیک می‌سازد/ویرایش می‌کند،
مسیرِ «چه رویدادی به کدام تاپیک برود» را عوض می‌کند، ساعتِ گزارشِ روزانه‌ی مسابقات را تنظیم می‌کند و بررسیِ
امنیتی اجرا می‌کند. حذفِ تاپیک (برگشت‌ناپذیر) همیشه تأییدِ صریحِ مدیر ارشد می‌خواهد.
"""
import logging

import ops_group as og
from config import ROLE_PISHVA

logger = logging.getLogger(__name__)

BOSS = [ROLE_PISHVA]
TOOL_PERMISSIONS = {n: BOSS for n in (
    "ops_status", "ops_post", "ops_topic_create", "ops_topic_edit", "ops_topic_delete", "ops_route_set",
    "ops_matches_time_set", "ops_digest_now", "ops_security_review", "ops_refresh")}
CATEGORY = ("opsgroup", "🏛️ گروه فرماندهی (تاپیک‌ها، مسیردهی، گزارش‌ها)", list(TOOL_PERMISSIONS))
ACTION_NAMES = frozenset({"ops_post", "ops_topic_create", "ops_topic_edit", "ops_topic_delete", "ops_route_set",
                          "ops_matches_time_set"})

_S = {"type": "string"}
_MODE = {**_S, "description": "none = فقط‌خواندنی(بسته) | commands = دستورات | ai = چت با رهگشا"}
TOOL_DECLARATIONS = [
    {"name": "ops_status", "description": "وضعیتِ گروه فرماندهی: وصل است؟ تاپیک‌ها (کلید/عنوان/حالت)، مسیرِ رویدادها و ساعتِ گزارشِ مسابقات. قبل از هر تغییر یک‌بار بگیر.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "ops_post", "description": "گذاشتنِ یک پیام در یک تاپیکِ گروه فرماندهی (با کلیدِ تاپیک از ops_status). برای اعلان/گزارشِ دستی.",
     "parameters": {"type": "object", "properties": {"topic": {**_S, "description": "کلید: security|players|announce|access|strangers|matches|rahgosha|logs|servers|admins یا کلیدِ تاپیکِ ساخته‌شده"},
                                                      "text": _S}, "required": ["topic", "text"]}},
    {"name": "ops_topic_create", "description": "ساختنِ یک تاپیکِ جدید در گروه. بعدش با ops_route_set می‌شود رویدادی را به آن وصل کرد.",
     "parameters": {"type": "object", "properties": {"key": {**_S, "description": "کلیدِ کوتاه و یکتا (حروف/عدد)"}, "title": _S,
                                                      "emoji": _S, "description": _S, "mode": _MODE}, "required": ["key", "title"]}},
    {"name": "ops_topic_edit", "description": "ویرایشِ تاپیک: تغییرِ نام/ایموجی/توضیح یا حالت (باز/بسته‌کردنِ ورودی).",
     "parameters": {"type": "object", "properties": {"key": _S, "title": _S, "emoji": _S, "description": _S, "mode": _MODE}, "required": ["key"]}},
    {"name": "ops_topic_delete", "description": "حذفِ کاملِ یک تاپیک و تمامِ پیام‌هایش (برگشت‌ناپذیر). همیشه از مدیر ارشد تأیید می‌گیرد. اگر رویدادی به آن وصل است اول مسیرش را عوض کن.",
     "parameters": {"type": "object", "properties": {"key": _S}, "required": ["key"]}},
    {"name": "ops_route_set", "description": "تعیینِ این‌که یک نوع رویداد به کدام تاپیک برود. رویدادها: " + "، ".join(og.EVENT_HELP),
     "parameters": {"type": "object", "properties": {"event": {**_S, "description": "|".join(og.EVENT_HELP)}, "topic": {**_S, "description": "کلیدِ تاپیکِ مقصد"}},
                    "required": ["event", "topic"]}},
    {"name": "ops_matches_time_set", "description": "ساعتِ ارسالِ گزارشِ روزانه‌ی مسابقات (وقتِ تهران).",
     "parameters": {"type": "object", "properties": {"time": {**_S, "description": "HH:MM مثل 21:30"}}, "required": ["time"]}},
    {"name": "ops_digest_now", "description": "همین الان گزارشِ مسابقاتِ یک روز را در تاپیکِ مسابقات بفرست.",
     "parameters": {"type": "object", "properties": {"date": {**_S, "description": "YYYY-MM-DD میلادی (پیش‌فرض: امروز)"}}}},
    {"name": "ops_security_review", "description": "اجرای بررسیِ امنیتی: پنلِ زنده را تازه می‌کند و خلاصه‌ی مرتبِ رهگشا (خلاصه/ریسک‌ها/اقدام) را در تاپیکِ امنیت می‌گذارد.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "ops_refresh", "description": "تازه‌سازیِ پنل‌های زنده‌ی گروه.",
     "parameters": {"type": "object", "properties": {"what": {**_S, "description": "security | admins | servers | all"}}}},
]


def _fmt_status(cfg) -> str:
    if not cfg["group_id"]:
        return ("گروه هنوز وصل نیست. مراحل: ۱) یک سوپرگروه بسازید و «Topics» را روشن کنید ۲) ربات را ادمین کنید "
                "(دسترسی‌های مدیریت تاپیک‌ها، پین و حذفِ پیام) ۳) داخل گروه /bindops بزنید.")
    lines = [f"گروه: {cfg['group_id']} | ساعتِ گزارشِ مسابقات: {cfg['matches_time']}", "تاپیک‌ها:"]
    for k, t in cfg["topics"].items():
        lines.append(f"- {k}: {t['emoji']} {t['title']} | حالت={t.get('mode')} | thread={t.get('thread_id') or 'ساخته‌نشده'}")
    lines.append("مسیرِ رویدادها:")
    for e, k in cfg["routes"].items():
        lines.append(f"- {e} → {k}  ({og.EVENT_HELP.get(e, '')})")
    return "\n".join(lines)


async def dispatch_tool(name, args, caller_id, caller_role, ctx):
    args = args or {}
    if caller_role != ROLE_PISHVA:
        return "⛔ این ابزار فقط برای مدیر ارشد است."
    bot = ctx.bot
    og._BOT = og._BOT or bot
    cfg = await og.load_cfg(force=True)

    if name == "ops_status":
        return _fmt_status(cfg)
    if not cfg["group_id"]:
        return "❌ " + _fmt_status(cfg)

    if name == "ops_post":
        key = str(args.get("topic") or "").strip()
        if key not in cfg["topics"]:
            return f"❌ تاپیکِ «{key}» نیست. معتبر: " + "، ".join(cfg["topics"])
        text = str(args.get("text") or "").strip()
        if not text:
            return "❌ متن خالی است."
        m = await og.post(key, f"🤖 <b>رهگشا</b>\n{og.h(text)}")
        return "✅ در تاپیک گذاشته شد." if m else "❌ ارسال نشد (ربات هنوز ادمینِ گروه با دسترسی کافی است؟)."
    if name == "ops_topic_create":
        ok, msg = await og.create_topic(bot, args.get("key"), args.get("title"), args.get("emoji") or "📌",
                                        args.get("description") or "", args.get("mode") or "none")
        return msg
    if name == "ops_topic_edit":
        ok, msg = await og.edit_topic(bot, args.get("key"), args.get("title"), args.get("emoji"), args.get("description"), args.get("mode"))
        return msg
    if name == "ops_topic_delete":
        import ai_outreach
        if not ai_outreach._is_confirmed(name, args, ctx):
            return "⛔ حذفِ تاپیک فقط با تأییدِ صریحِ مدیر ارشد انجام می‌شود و تأییدی ثبت نشده؛ چیزی حذف نشد."
        ok, msg = await og.delete_topic(bot, args.get("key"))
        return msg
    if name == "ops_route_set":
        ok, msg = await og.set_route(str(args.get("event") or ""), str(args.get("topic") or ""))
        return msg
    if name == "ops_matches_time_set":
        t = str(args.get("time") or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        try:
            hh, mm = (int(x) for x in t.split(":"))
            assert 0 <= hh < 24 and 0 <= mm < 60
        except Exception:
            return "❌ ساعت باید به شکلِ HH:MM باشد (مثل 21:30)."
        await og.save_kv("matches_time", f"{hh:02d}:{mm:02d}")
        return f"✅ گزارشِ روزانه‌ی مسابقات از این به بعد ساعت {hh:02d}:{mm:02d} (وقتِ تهران) ارسال می‌شود."
    if name == "ops_digest_now":
        ok, msg = await og.send_digest(args.get("date") or None, force=True)
        return msg
    if name == "ops_security_review":
        await og.security_review(bot)
        return "✅ بررسیِ امنیتی انجام و در تاپیکِ امنیت گذاشته شد."
    if name == "ops_refresh":
        what = str(args.get("what") or "all")
        fns = {"security": [og.refresh_security], "admins": [og.refresh_admins], "servers": [og.refresh_servers],
               "all": [og.refresh_security, og.refresh_admins, og.refresh_servers]}.get(what)
        if not fns:
            return "❌ what باید security|admins|servers|all باشد."
        for f in fns:
            await f()
        return "✅ تازه شد."
    return None
