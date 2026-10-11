"""
routing.py — «روترِ سرور»: اولین هندلرِ ربات (group=-100) برای هر آپدیتِ تلگرام.

  ۱) کاربر را به سرورِ خودش وصل می‌کند (srvctx.set_current)؛ مدیر ارشد و ناشناس‌ها → سرور اصلی،
     اعضای یک مجموعه (حتی عضویتِ موقتِ «لینکِ دعوت») → سرورِ همان مجموعه.
  ۲) اگر سرور خاموش/تعلیق/حذف‌شده است، پیامِ مناسب می‌دهد و پردازش را قطع می‌کند.
  ۳) اگر قابلیتِ مربوط به یک دکمه در آن سرور خاموش است، جلوی اجرا را می‌گیرد.
  ۴) ورودی‌های متنیِ نیمه‌کاره‌ی پنل‌ها را با هر کلیکِ نامرتبط لغو می‌کند.

از آن‌جا که هر آپدیت در تسکِ جداگانه پردازش می‌شود (PerUserUpdateProcessor)، ContextVar بینِ کاربران قاطی نمی‌شود.
"""
import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

import servers
import srvctx

logger = logging.getLogger(__name__)


async def route_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    sid = servers.member_server(user.id) if user else srvctx.MAIN_ID
    srvctx.set_current(sid)

    q = update.callback_query
    if q is not None and q.data:
        d = q.data
        if not d.startswith("own_"):
            ctx.user_data.pop("own_wait", None)
        if not (d.startswith("srv_") or d.startswith("sreq_")):
            ctx.user_data.pop("srv_wait", None)

    if sid == srvctx.MAIN_ID:
        return

    # سرورِ خاموش/تعلیق/حذف‌شده → فقط پیام (مدیر ارشد هرگز این‌جا نمی‌آید)
    gm = servers.gate_message(sid)
    if gm:
        try:
            if q is not None:
                await q.answer(gm[:190], show_alert=True)
            elif update.effective_message is not None:
                await update.effective_message.reply_text(gm)
        except Exception:
            logger.debug("gate reply failed", exc_info=True)
        raise ApplicationHandlerStop

    # دروازه‌ی قابلیت‌ها (بر اساسِ پیشوندِ دکمه‌ها)
    if q is not None and q.data:
        key = servers.feature_of_callback(q.data)
        if key and not servers.feature_on(sid, key):
            try:
                await q.answer("⛔ این قابلیت برای سرورِ شما غیرفعال شده است.", show_alert=True)
            except Exception:
                pass
            raise ApplicationHandlerStop


# ═════════════════════════ وب‌اپ‌ها (هاب / شطرنج زنده) ═════════════════════════
# درخواست‌های وب هم باید روی دیتابیسِ سرورِ «صاحبِ کاربر» اجرا شوند. هویتِ کاربر از initData است
# (هدرِ X-Tg-Init-Data، کوئریِ init، یا فیلدِ init_data در بدنه‌ی JSON). این‌جا فقط «تشخیصِ سرور» انجام می‌شود؛
# اعتبارسنجیِ امضا همان‌طور که قبلاً بود در خودِ هندلرها انجام می‌شود (یک رشته، یک هویت).
import json as _json
from urllib.parse import parse_qsl as _parse_qsl

try:
    from aiohttp import web as _web
except Exception:   # pragma: no cover
    _web = None


def _uid_from_init(init_data: str):
    try:
        pairs = dict(_parse_qsl(init_data or "", keep_blank_values=True))
        u = _json.loads(pairs.get("user", "") or "{}")
        return int(u["id"]) if "id" in u else None
    except Exception:
        return None


async def _request_uid(request):
    uid = _uid_from_init(request.headers.get("X-Tg-Init-Data", ""))
    if uid is None:
        uid = _uid_from_init(request.query.get("init", ""))
    if uid is None and request.method in ("POST", "PUT", "PATCH") and request.can_read_body:
        ct = (request.content_type or "")
        if "json" in ct:
            try:
                raw = await request.read()            # در کشِ aiohttp می‌ماند؛ handler بعداً دوباره می‌خواند
                body = _json.loads(raw.decode("utf-8") or "{}")
                if isinstance(body, dict):
                    uid = _uid_from_init(body.get("init_data") or "")
            except Exception:
                uid = None
    return uid


if _web is not None:
    @_web.middleware
    async def server_middleware(request, handler):
        uid = await _request_uid(request)
        sid = servers.member_server(uid) if uid else srvctx.MAIN_ID
        token = srvctx.set_current(sid)
        try:
            if sid != srvctx.MAIN_ID:
                gm = servers.gate_message(sid)
                if gm:
                    return _web.json_response({"error": "server_unavailable", "message": gm}, status=403,
                                              dumps=lambda o: _json.dumps(o, ensure_ascii=False))
            return await handler(request)
        finally:
            srvctx.reset(token)
