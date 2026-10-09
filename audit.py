"""audit.py — ردیابیِ کاملِ اقدامات مدیران (نقطه‌به‌نقطه، بدونِ جاانداختنِ مورد).

دو منبع:
  ۱) ربات: یک handler در گروهِ -5 (قبل از همه) که «هر آپدیتِ» مدیران را ثبت می‌کند:
     /start، دستورها، کلیکِ دکمه (با متنِ دکمه + callback_data)، پیام‌ها، رسانه‌ها، web_app_data و ...
  ۲) سرور وب: middleware که هر درخواست به /hub/api/*، پنل وب ادمین و پنل مدیر مدرسه را ثبت می‌کند
     (بازکردنِ پنل، مشاهده‌ها، تغییرها و دسترسی‌های ردشده).

همه‌چیز در action_logs با action_type = trail_* ذخیره می‌شود. کوئری‌های قدیمی پیش‌فرض این‌ها را نمی‌بینند
(شمارنده‌ی «تعداد اقدامات»، منوی خنثی‌سازی و ... دست‌نخورده می‌مانند)؛ نمایشگرِ هاب با scope=trail/all آن‌ها را نشان می‌دهد.
نوشتن از صفِ پس‌زمینه است تا ربات/سرور هیچ‌وقت منتظرِ دیتابیس نماند و ترتیب حفظ شود.
"""
import asyncio
import json
import logging
import re
import time

from telegram import Update
from telegram.ext import TypeHandler

import database as db
from config import PISHVA_ID

logger = logging.getLogger(__name__)

_Q: "asyncio.Queue" = None
_worker_task = None
_BATCH = 60
_MAX_RETRY = 3

# رمزها/توکن‌ها هرگز ثبت نمی‌شوند
_SECRET_KEY = re.compile(r"pass|pwd|secret|token|init_?data|hash|otp|code|auth|cookie|key", re.I)


def _clip(v, n=300):
    v = "" if v is None else str(v)
    v = v.replace("\r", " ").replace("\n", " ⏎ ")
    return v if len(v) <= n else v[:n] + f"… (+{len(v) - n})"


# ───────────────────────── صف و نویسنده ─────────────────────────
def _ensure_worker():
    global _Q, _worker_task
    if _Q is None:
        _Q = asyncio.Queue()
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.get_running_loop().create_task(_worker())


def push(admin_id, action_type, description, target_id=None):
    """غیرمسدودکننده؛ زمانِ رویداد همین‌جا گرفته می‌شود تا ترتیب درست بماند."""
    try:
        _ensure_worker()
        _Q.put_nowait((admin_id, action_type, _clip(description, 1500), target_id, db._now_tehran_iso()))
    except Exception:
        logger.exception("audit.push failed")


async def _is_logged_actor(uid):
    """فقط مدیرها (مدیر ارشد یا هر ردیفِ جدولِ admins، حتی غیرفعال) — با کشِ get_admin."""
    if uid == PISHVA_ID:
        return True
    try:
        return (await db.get_admin(uid)) is not None
    except Exception:
        return False


async def _worker():
    while True:
        item = await _Q.get()
        batch = [item]
        try:
            await asyncio.sleep(0.05)            # کمی صبر تا رویدادهای هم‌زمان در یک بسته بیایند
            while len(batch) < _BATCH and not _Q.empty():
                batch.append(_Q.get_nowait())
            rows = []
            for admin_id, typ, desc, tid, at, *rest in batch:
                if admin_id > 0 and typ.startswith("trail_") and rest and rest[0] == "check":
                    if not await _is_logged_actor(admin_id):
                        continue
                rows.append((admin_id, typ, desc, tid, at))
            for attempt in range(_MAX_RETRY):
                try:
                    await db.log_actions_bulk(rows)
                    break
                except Exception:
                    if attempt == _MAX_RETRY - 1:
                        logger.exception("audit: دسته‌ی لاگ ذخیره نشد (%d ردیف)", len(rows))
                    else:
                        await asyncio.sleep(0.5 * (attempt + 1))
        except Exception:
            logger.exception("audit worker")
        finally:
            for _ in batch:
                _Q.task_done()


async def flush(timeout=8.0):
    """هنگامِ خاموش‌شدن صدا بزنید تا ردیف‌های مانده نوشته شوند."""
    if _Q is None:
        return
    try:
        await asyncio.wait_for(_Q.join(), timeout)
    except Exception:
        pass


def _push_checked(uid, typ, desc):
    try:
        _ensure_worker()
        _Q.put_nowait((uid, typ, _clip(desc, 1500), None, db._now_tehran_iso(), "check"))
    except Exception:
        logger.exception("audit.push failed")


# ───────────────────────── ربات تلگرام ─────────────────────────
_CHAT_FA = {"private": "خصوصی", "group": "گروه", "supergroup": "سوپرگروه", "channel": "کانال"}
_MEDIA = (("photo", "عکس"), ("document", "فایل"), ("voice", "ویس"), ("video", "ویدیو"), ("video_note", "ویدیو‌پیام"),
          ("audio", "آهنگ"), ("sticker", "استیکر"), ("animation", "گیف"), ("location", "موقعیت مکانی"),
          ("contact", "مخاطب"), ("poll", "نظرسنجی"), ("dice", "تاس"))


def _button_label(q):
    try:
        kb = q.message.reply_markup.inline_keyboard
        for row in kb:
            for b in row:
                if b.callback_data == q.data:
                    return b.text
    except Exception:
        pass
    return None


def _describe(update: Update, ctx):
    """(نوع، شرح) یا None."""
    chat = update.effective_chat
    where = _CHAT_FA.get(getattr(chat, "type", ""), "؟")
    q = update.callback_query
    if q is not None:
        lab = _button_label(q)
        return "trail_button", f"دکمه «{_clip(lab, 80) if lab else '؟'}» ← {_clip(q.data, 200)} ({where})"
    m = update.message or update.edited_message
    if m is not None:
        edited = " (ویرایش‌شده)" if update.edited_message is not None else ""
        if getattr(m, "web_app_data", None):
            return "trail_other", f"داده‌ی وب‌اپ: {_clip(m.web_app_data.data, 400)}"
        text = m.text or m.caption
        if m.text and m.text.startswith("/"):
            typ = "trail_start" if m.text.split()[0].split("@")[0].lower() == "/start" else "trail_command"
            return typ, f"{_clip(m.text, 300)} ({where}){edited}"
        for attr, fa in _MEDIA:
            if getattr(m, attr, None):
                cap = f" — توضیح: {_clip(m.caption, 200)}" if m.caption else ""
                return "trail_media", f"{fa} ارسال شد{cap} ({where}){edited}"
        if text:
            secret = ctx is not None and getattr(ctx, "user_data", None) is not None and ctx.user_data.pop("_audit_secret", False)
            if secret:
                return "trail_message", f"پیامِ حساس (رمز) — محتوا ثبت نشد ({where})"
            return "trail_message", f"{_clip(text, 500)} ({where}){edited}"
        return "trail_other", f"پیامِ بدون متن ({where}){edited}"
    for attr, fa in (("inline_query", "اینلاین‌کوئری"), ("chosen_inline_result", "انتخابِ نتیجه‌ی اینلاین"),
                     ("my_chat_member", "تغییر وضعیتِ ربات در چت"), ("chat_member", "تغییر عضویت"),
                     ("poll_answer", "پاسخ به نظرسنجی"), ("message_reaction", "واکنش")):
        o = getattr(update, attr, None)
        if o is not None:
            extra = ""
            if attr == "inline_query":
                extra = f": {_clip(o.query, 100)}"
            elif attr == "my_chat_member":
                extra = f": {o.old_chat_member.status} → {o.new_chat_member.status}"
            return "trail_other", fa + extra
    return None


async def _audit_handler(update: Update, ctx):
    try:
        u = update.effective_user
        if u is None or u.is_bot:
            return
        d = _describe(update, ctx)
        if d:
            _push_checked(u.id, d[0], d[1])
    except Exception:
        logger.exception("audit handler")        # هرگز نباید مانعِ پردازشِ آپدیت شود


def register(application):
    """گروهِ -5: قبل از block_gate و بقیه؛ و هیچ‌وقت ApplicationHandlerStop نمی‌دهد."""
    application.add_handler(TypeHandler(Update, _audit_handler), group=-5)


def mark_secret_next(ctx):
    """قبل از درخواستِ رمز صدا بزنید؛ پیامِ بعدیِ همین کاربر ثبت نمی‌شود."""
    try:
        ctx.user_data["_audit_secret"] = True
    except Exception:
        pass


# ───────────────────────── سرور وب (aiohttp) ─────────────────────────
from aiohttp import web  # noqa: E402

_HUB_LABELS = {
    "/hub/api/bootstrap": "باز کردن پنل",
    "/hub/api/weather": "آب‌وهوا", "/hub/api/weather/map": "نقشه‌ی سامانه‌های جوی",
    "/hub/api/players": "فهرست بازیکنان", "/hub/api/rankings": "نفرات برتر",
    "/hub/api/logs": "لاگ اقدامات", "/hub/api/requests": "درخواست‌ها",
}


def _kind(path):
    if path.startswith("/hub/api/"):
        return "hub"
    if path.startswith("/api/principal") or path.startswith("/principal/api"):
        return "principal"
    if path.startswith("/api/admin") or path.startswith("/admin/api") or path.startswith("/api/panel"):
        return "web"
    return None


def _scrub(o, depth=0):
    if depth > 4:
        return "…"
    if isinstance(o, dict):
        return {k: ("***" if _SECRET_KEY.search(str(k)) else _scrub(v, depth + 1)) for k, v in list(o.items())[:40]}
    if isinstance(o, list):
        return [_scrub(v, depth + 1) for v in o[:20]] + (["…"] if len(o) > 20 else [])
    if isinstance(o, str):
        return o if len(o) <= 120 else o[:120] + f"…(+{len(o) - 120})"
    return o


def _body_summary(request):
    raw = getattr(request, "_read_bytes", None)
    if not raw:
        return ""
    if len(raw) > 65536:
        return f" | بدنه {len(raw)} بایت"
    try:
        data = json.loads(raw.decode("utf-8"))
        return " | " + json.dumps(_scrub(data), ensure_ascii=False)
    except Exception:
        return f" | بدنه {len(raw)} بایت"


def _hub_actor(request):
    try:
        import hub
        init = request.headers.get("X-Tg-Init-Data", "") or request.query.get("init", "")
        u = hub._verify_init_data(init) if init else None
        if u and u.get("id"):
            return int(u["id"])
    except Exception:
        pass
    return None


@web.middleware
async def audit_middleware(request, handler):
    kind = _kind(request.path)
    if kind is None or request.method == "OPTIONS":
        return await handler(request)
    status = 500
    try:
        resp = await handler(request)
        status = resp.status
        return resp
    except web.HTTPException as e:
        status = e.status
        raise
    finally:
        try:
            _record_http(request, kind, status)
        except Exception:
            logger.exception("audit middleware")


def _record_http(request, kind, status):
    write = request.method not in ("GET", "HEAD")
    q = {k: ("***" if _SECRET_KEY.search(k) else _clip(v, 80)) for k, v in request.query.items() if k != "init"}
    qs = (" ?" + "&".join(f"{k}={v}" for k, v in q.items())) if q else ""
    ip = request.headers.get("X-Forwarded-For", request.remote or "").split(",")[0].strip()
    label = _HUB_LABELS.get(request.path)
    base = f"{request.method} {request.path}{qs} → {status}"
    desc = (f"{label}: " if label else "") + base + _body_summary(request) + (f" | ip={ip}" if ip else "")
    if kind == "hub":
        uid = _hub_actor(request)
        if uid is None or status in (401, 403):
            push(uid if uid else -2, "trail_hub_denied", desc)
            return
        if request.path == "/hub/api/bootstrap":
            typ = "trail_hub_open"
        else:
            typ = "trail_hub_write" if write else "trail_hub_view"
        _push_checked(uid, typ, desc)
    elif kind == "web":
        push(0, "trail_web_write" if write else "trail_web_view", desc)
    else:
        push(-1, "trail_principal_write" if write else "trail_principal_view", desc)
