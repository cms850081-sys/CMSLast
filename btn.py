"""
btn.py — ساختِ دکمه‌ی رنگی با قاعده‌ی یکسان برای پنل‌های چندسرور (servers_panel / owner_panel).

همان سه رنگِ باقیِ پروژه: primary (آبی)، success (سبز)، danger (قرمز).
قاعده (بر اساسِ شروعِ متن و پیشوندِ callback):
  • قرمز : بازگشت/انصراف/حذف/پاک‌سازی/اخراج/خاموش/هشدار، و مراحلِ تأییدِ حذف
  • سبز  : تأیید/ساخت/افزودن/روشن/بازیابی/آغاز
  • آبی  : ناوبری و بقیه‌ی کارها
  • وضعیت: 🟢 سبز، 🔴 قرمز، 🟠 آبی، ⚫ و 🔒 بی‌رنگ (غیرفعال/خاموش)
"""
from telegram import InlineKeyboardButton

_DANGER_START = ("🔙", "❌", "🗑", "🔥", "🚫", "⛔", "🔴", "🚪", "⚠️")
_SUCCESS_START = ("✅", "🟢", "➕", "▶️")
_NEUTRAL_START = ("⚫", "🔒")
_DANGER_DATA = ("srv_d_", "srv_d2_", "srv_d3_", "srv_d4_", "srv_px_", "srv_pxb_", "srv_pxn_",
                "own_rm_", "own_rmy_", "srv_gaic", "srv_aic_", "srv_invo_", "own_inv_off", "sreq_no_", "sreq_bl_")
_SUCCESS_DATA = ("srv_new", "sreq_ok_", "srv_rec2_", "own_inv_new", "srv_wo_", "srv_gwo", "srv_u_", "own_bkx_", "srv_bkx_")


def style_for(text: str, data: str = ""):
    t = (text or "").strip()
    d = data or ""
    if d.startswith(("srv_d2_", "srv_d3_", "srv_d4_", "own_rmy_", "srv_px", "own_rm_", "srv_d_")):
        return "danger"
    if any(d.startswith(p) for p in _SUCCESS_DATA):
        return "success"
    if any(d.startswith(p) for p in _DANGER_DATA):
        return "danger"
    if t.startswith(_NEUTRAL_START):
        return None
    if t.startswith(_DANGER_START):
        return "danger"
    if t.startswith(_SUCCESS_START):
        return "success"
    if t.startswith("♻️") and ("بازیاب" in t or "بازگردان" in t):
        return "success"
    if t.startswith("🟠"):
        return "primary"
    return "primary"


def mk(text: str, data: str = None, url: str = None, style: str = "auto"):
    """دکمه‌ی اینلاین با رنگِ خودکار (style="auto") یا صریح (None = بی‌رنگ)."""
    st = style_for(text, data or "") if style == "auto" else style
    kw = {"style": st} if st else {}
    if url:
        return InlineKeyboardButton(text, url=url, **kw)
    return InlineKeyboardButton(text, callback_data=data, **kw)
