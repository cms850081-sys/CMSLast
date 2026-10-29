"""
hub_chat.py — بک‌اندِ واقعیِ «رهگشا» داخلِ «پنل من (هاب)».

همان مغزِ رهگشای ربات تلگرام (همان پرامپت، همان ابزارها، همان قفل‌ها و سقفِ دسترسیِ نقش‌ها،
همان تنظیماتِ شخصی/حافظه/میانبر، همان تأییدِ قبل از اقدام) — فقط با رابطِ وب.
نقشِ کاربر هر بار از سمتِ سرور و از روی initData امضاشده‌ی تلگرام تشخیص داده می‌شود؛
هیچ‌چیز از سمتِ مرورگر «اعتماد» نمی‌شود.

تاریخچه و دیتا با تلگرام یکی است (ai_shared.py): همان جلسه‌ها، همان پیام‌ها،
همان حافظه، همان تنظیمات، همان کارهای زمان‌بندی‌شده.

مسیرها (همه زیرِ /hub/api/rg/):
  GET  boot                 وضعیتِ کامل برای باز شدنِ چت (نقش، تنظیمات، حافظه، جلسه‌ی فعال، فهرست چت‌ها)
  POST send                 ارسالِ پیام و اجرای کاملِ یک نوبتِ رهگشا
  POST confirm              تأیید/لغوِ اقدامِ مهم (وقتی «تأیید قبل از اقدام» روشن است)
  POST learn                پذیرفتن/رد پیشنهادِ «همیشه همین‌طور جواب بده»
  POST new                  چتِ جدید
  GET  sessions             فهرستِ چت‌های من
  GET  session/{id}         پیام‌های یک چت (و فعال‌کردنش؛ id=0 یعنی چتِ فعالِ فعلی)
  POST pref / text / shortcut / profile / mem / reset     تنظیماتِ شخصی (همه روی دیتابیسِ مشترک)
  GET  sched, POST sched/cancel    کارهای زمان‌بندی‌شده
  GET  abilities, why, preview     توانایی‌ها / «چرا این جواب؟» / پیش‌نمایشِ سبک
  --- فقط مدیر ارشد ---
  GET/POST admin/overview, admin/online, admin/lock, admin/perm, admin/admin-ai,
  GET admin/logs, admin/session/{id}, admin/live, admin/trail,
  GET admin/notes, POST admin/notes/del, GET admin/persona/{tid}, POST admin/persona/{tid}/reset
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime

import httpx
from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai_assistant as aia
import ai_memory
import ai_persona
import ai_scheduler
import ai_shared
import ai_tools
import database as db
import hub
from config import PISHVA_ID, ROLE_PISHVA, ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER
from helpers import pishva_display, admin_display

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()

MAX_TEXT = 4000
_APP = None                 # PTB Application (برای job_queue: یادآور/زمان‌بندی/ساعت کاری)
_UD = {}                    # حافظه‌ی موقتِ هر کاربر (مثلِ ctx.user_data در تلگرام): تأییدِ در انتظار، «چرا این جواب؟»، پرچم‌های حافظه
_LOCKS = {}                 # یک قفل به‌ازای هر کاربر: دو نوبتِ هم‌زمان توی هم نرود
_bg = set()

ROLE_LABELS_FA = {
    ROLE_PISHVA: "مدیر ارشد",
    ROLE_TOURNAMENT_MANAGER: "مسئول مسابقات",
    ROLE_SECURITY_MANAGER: "مسئول انتظامات",
}
_HIDDEN_PREF_KEYS = {"_hits", "_hinted", "_trainer_since"}


def set_application(application):
    """از bot.py صدا زده می‌شود تا ابزارهای زمان‌بندی/ساعت کاری job_queue واقعی داشته باشند."""
    global _APP
    _APP = application


# ─── زیرساخت ────────────────────────────────────────────────────
class _WebCtx:
    """شبیه‌ساز کم‌حجمِ telegram ContextTypes برای ابزارهای رهگشا (ai_tools/ai_persona)."""

    def __init__(self, uid: int):
        self.user_data = _UD.setdefault(int(uid), {})
        self.bot = hub._BOT
        self.application = _APP
        self.job_queue = getattr(_APP, "job_queue", None) if _APP is not None else None
        self.bot_data = getattr(_APP, "bot_data", {}) if _APP is not None else {}


class _VoiceMsg:
    """برای خروجی صوتی: همان reply_document تلگرام، ولی به چتِ ربات می‌فرستد."""

    def __init__(self, bot, uid):
        self.bot, self.uid = bot, uid

    async def reply_document(self, document=None, caption=None, **kw):
        await self.bot.send_document(chat_id=self.uid, document=document, caption=caption)


def _spawn(coro):
    async def _safe():
        try:
            await coro
        except Exception:
            logger.warning("hub_chat: background task failed", exc_info=True)
    t = asyncio.create_task(_safe())
    _bg.add(t)
    t.add_done_callback(_bg.discard)


def _fail(code, message, status=400, **extra):
    cls = {400: web.HTTPBadRequest, 403: web.HTTPForbidden, 404: web.HTTPNotFound,
           409: web.HTTPConflict, 429: web.HTTPTooManyRequests, 502: web.HTTPBadGateway}.get(status, web.HTTPBadRequest)
    return hub._err(cls, code, message=message, **extra)


def _ok(**kw):
    kw["ok"] = True
    return hub._json(kw)


async def _body(request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


async def _auth(request, pishva_only: bool = False):
    """(uid, role, is_pishva, ctx) — هویت فقط از initData امضاشده."""
    is_pishva, admin, user = await hub._require_admin(request)
    uid = int(user["id"])
    role = ROLE_PISHVA if is_pishva else admin["role"]
    if pishva_only and not is_pishva:
        raise _fail("pishva_only", "این بخش فقط برای مدیر ارشد است.", 403)
    return uid, role, is_pishva, _WebCtx(uid)


async def _access_state(uid: int, role: str) -> dict:
    """آیا رهگشا برای این کاربر در دسترس است؟ (مثلِ چکِ ربات)"""
    online = await aia._is_ai_online()
    if not online:
        return {"usable": False, "reason": "offline", "message": aia.AI_OFFLINE_MESSAGE}
    if not await aia._can_use_ai(uid, role):
        return {"usable": False, "reason": "blocked", "message": "⛔ دسترسی شما به رهگشا مسدود است."}
    if not aia.GEMINI_API_KEY:
        return {"usable": False, "reason": "nokey", "message": "⚠️ کلید Gemini تنظیم نشده (GEMINI_API_KEY خالیه)."}
    return {"usable": True, "reason": "", "message": ""}


# ─── نمایشِ پیام‌ها ─────────────────────────────────────────────
_REPORT_MARK = "📋 گزارش سیستم"


def _present(row) -> dict | None:
    sender = row["sender"]
    if sender not in ("user", "ai"):
        return None            # لاگِ ابزارها در چت نمایش داده نمی‌شود
    text = str(row["text"] or "")
    out = {"id": row["id"], "who": "me" if sender == "user" else "ai", "at": str(row["sent_at"] or "")[:16].replace("T", " ")}
    if sender == "ai":
        memo = ai_shared.MEMO_TAG in text
        text = text.replace(ai_shared.MEMO_TAG, "").rstrip()
        report = []
        i = text.find(_REPORT_MARK)
        if i != -1:
            tail = text[i:]
            text = text[:i].rstrip()
            for ln in tail.splitlines()[1:]:
                ln = ln.strip()
                if ln.startswith("—"):
                    report.append(ln.lstrip("— ").strip())
        out["report"] = report
        out["memo"] = memo
    out["text"] = text
    return out


def _day_group(iso: str) -> str:
    try:
        d = (datetime.now().date() - datetime.fromisoformat(str(iso)[:19]).date()).days
    except Exception:
        return "قبل‌تر"
    if d <= 0:
        return "امروز"
    if d == 1:
        return "دیروز"
    if d < 7:
        return "این هفته"
    return "قبل‌تر"


async def _messages_payload(sid: int) -> list:
    rows = await ai_shared.messages_of(sid)
    return [m for m in (_present(r) for r in rows) if m]


async def _sessions_payload(uid: int) -> list:
    rows = await ai_shared.sessions_with_counts(uid, 40)
    return [{"id": r["id"], "title": (r["title"] or "").strip() or "بدون عنوان",
             "group": _day_group(r["last_message_at"]), "n": r["n"]} for r in rows]


def _clean_prefs(prefs: dict) -> dict:
    return {k: v for k, v in (prefs or {}).items() if k not in _HIDDEN_PREF_KEYS}


async def _settings_payload(uid: int, role: str) -> dict:
    prefs = await ai_persona.get_prefs(uid)
    eff = await ai_persona.effective(prefs)
    mem = await ai_persona.get_memory(uid)
    return {
        "prefs": _clean_prefs(prefs),
        "effective": eff,
        "memory": [{"id": m["id"], "content": m["content"]} for m in mem],
    }


# ─── بوت‌استرپ ──────────────────────────────────────────────────
@routes.get("/hub/api/rg/boot")
async def rg_boot(request):
    uid, role, is_pishva, ctx = await _auth(request)
    access = await _access_state(uid, role)
    sid = await ai_shared.get_active_session(uid)
    msgs = await _messages_payload(sid) if sid else []
    settings, sessions = await asyncio.gather(_settings_payload(uid, role), _sessions_payload(uid))
    return _ok(
        me={"id": uid, "role": role, "role_label": ROLE_LABELS_FA.get(role, "مدیر"), "is_pishva": is_pishva},
        access=access, session_id=sid, messages=msgs, sessions=sessions, **settings)


@routes.get("/hub/api/rg/sessions")
async def rg_sessions(request):
    uid, role, _p, _c = await _auth(request)
    return _ok(sessions=await _sessions_payload(uid))


@routes.get("/hub/api/rg/session/{id}")
async def rg_session(request):
    uid, role, _p, _c = await _auth(request)
    try:
        sid = int(request.match_info["id"])
    except ValueError:
        raise _fail("bad_request", "شناسه نامعتبر است.")
    if sid == 0:
        sid = await ai_shared.get_active_session(uid) or 0
        return _ok(session_id=sid or None, messages=(await _messages_payload(sid)) if sid else [])
    sess = await db.ai_get_session(sid)
    if not sess or int(sess["user_id"]) != uid:
        raise _fail("not_found", "این چت مال شما نیست یا پیدا نشد.", 404)
    await ai_shared.set_active_session(uid, sid)
    return _ok(session_id=sid, messages=await _messages_payload(sid))


@routes.post("/hub/api/rg/new")
async def rg_new(request):
    uid, role, _p, _c = await _auth(request)
    access = await _access_state(uid, role)
    if not access["usable"]:
        raise _fail(access["reason"], access["message"], 403)
    sid = await ai_shared.new_session(uid, role)
    return _ok(session_id=sid)


# ─── یک نوبتِ کاملِ رهگشا ──────────────────────────────────────
def _lock_for(uid: int) -> asyncio.Lock:
    lk = _LOCKS.get(uid)
    if lk is None:
        lk = _LOCKS[uid] = asyncio.Lock()
    return lk


async def _flush_pending_buttons(ctx, uid: int) -> bool:
    """ابزارِ open_panel دکمه‌های پنلِ ربات را آماده می‌کند؛ در هاب همان پنل را در چتِ ربات می‌فرستیم."""
    pending = ctx.user_data.pop("_ai_pending_buttons", None)
    if not pending or ctx.bot is None:
        return False
    rows = [[InlineKeyboardButton(label, callback_data=cb)] for label, cb in pending]
    try:
        await ctx.bot.send_message(chat_id=uid, text="📂 پنلِ درخواستی — از اینجا بازش کن:",
                                   reply_markup=InlineKeyboardMarkup(rows))
        return True
    except Exception:
        logger.warning("hub_chat: could not send panel buttons", exc_info=True)
        return False


async def _run_turn(uid: int, role: str, text: str, session_id: int, ctx) -> dict:
    """همان منطقِ ai_assistant_message، بدونِ وابستگی به Update/Message تلگرام."""
    out = {"reply": "", "report": [], "memo": False, "confirm": None, "learn": None,
           "panel_sent": False, "error": None}
    text = await ai_persona.expand_shortcut(uid, text)
    ai_memory.reset_memo_flags(ctx)
    memory_intent = role == ROLE_PISHVA and aia._looks_like_memory_save(text)

    await db.ai_add_message(session_id, "user", text)
    history = await ai_shared.history_from_db(session_id)

    if role == ROLE_PISHVA:
        display_name = await pishva_display()
    else:
        display_name = await admin_display(await db.get_admin(uid))
    visibility_levels = ["all", "pishva"] if role == ROLE_PISHVA else ["all"]
    memory_rows = await ai_memory.recent(visibility_levels, limit=8)
    system_prompt = aia._system_prompt(role, display_name, memory_rows, user_text=text)
    system_prompt += await aia._server_addon()
    _prefs = await ai_persona.get_prefs(uid)
    _persona_block, _mem_used = await ai_persona.build_block_ex(uid, role, _prefs)
    system_prompt += _persona_block
    _eff = await ai_persona.effective(_prefs)
    used_tools = []
    contents = [{"role": "user", "parts": [{"text": system_prompt}]},
                {"role": "model", "parts": [{"text": "باشه، آماده‌ام کمک کنم."}]}] + history

    tools = aia._tools_for_role(role)
    force_action = bool(tools) and aia._looks_like_action(text)
    tool_names = {d["name"] for t in (tools or []) for d in t.get("function_declarations", [])}
    forced_name = "remember_note" if (memory_intent and "remember_note" in tool_names) else None
    executed_actions = []

    def _report_lines():
        return [f"{fn}: {res}" for fn, _a, res in executed_actions]

    try:
        for _hop in range(aia.MAX_TOOL_HOPS):
            if forced_name and _hop == 0:
                tool_config = {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [forced_name]}}
            elif force_action and _hop == 0:
                tool_config = {"functionCallingConfig": {"mode": "ANY"}}
            else:
                tool_config = None
            data = await aia._call_gemini(contents, tools, tool_config)
            parts = aia._extract_parts(data)
            if not parts:
                out["error"] = "⚠️ پاسخی از مدل دریافت نشد، دوباره امتحان کن."
                return out

            fn_call = next((p["functionCall"] for p in parts if "functionCall" in p), None)
            if fn_call:
                fname = fn_call["name"]
                fargs = fn_call.get("args", {})
                gate = await ai_persona.confirm_gate(ctx, uid, fname, fargs)
                if gate:
                    g_text, _kb = gate
                    await db.ai_add_message(session_id, "ai", g_text)
                    ctx.user_data["_aip_pending"]["sid"] = session_id
                    out["confirm"] = {"text": g_text, "ts": ctx.user_data["_aip_pending"]["ts"]}
                    out["reply"] = ""
                    return out
                result_text = await ai_tools.dispatch(fname, fargs, uid, role, ctx)
                used_tools.append((fname, str(fargs), str(result_text)))
                await db.ai_add_message(session_id, "tool", f"🔧 {fname}({fargs}) → {result_text}")
                if fname in ai_tools.ACTION_TOOL_NAMES:
                    executed_actions.append((fname, fargs, result_text))
                contents.append({"role": "model", "parts": parts})
                contents.append({"role": "user",
                                 "parts": [{"functionResponse": {"name": fname, "response": {"result": result_text}}}]})
                continue

            reply_text = "".join(p.get("text", "") for p in parts).strip() or "باشه."
            stored = reply_text
            if executed_actions:
                stored += f"\n\n{_REPORT_MARK}:\n" + "\n".join(f"— {ln}" for ln in _report_lines())
            tail = await aia._tail(ctx, text, uid, memory_intent)
            stored += tail
            hint = await ai_persona.learn_hint(ctx, uid, text)
            if hint:
                m = re.match(r"aip_learn_([a-z_]+)_([^_]+)$", hint[1][0][1]) if hint[1] else None
                if m:
                    out["learn"] = {"text": hint[0].strip(), "key": m.group(1), "val": m.group(2)}
            ai_persona.record_why(ctx, used_tools, _eff["mode"], _mem_used)
            await db.ai_add_message(session_id, "ai", stored)
            sess = await db.ai_get_session(session_id)
            if sess and not sess["title"]:
                await db.ai_set_session_title(session_id, text)
            out["reply"] = reply_text
            out["report"] = _report_lines()
            out["memo"] = ai_shared.MEMO_TAG in tail
            out["extra"] = tail.replace(ai_shared.MEMO_TAG, "").strip()
            out["panel_sent"] = await _flush_pending_buttons(ctx, uid)
            if _eff["voice"] == "1" and ctx.bot is not None:
                _spawn(ai_persona.send_voice_reply(_VoiceMsg(ctx.bot, uid), reply_text))
            return out

        fb = "⚠️ این درخواست خیلی پیچیده شد؛ لطفاً واضح‌تر یا مرحله‌به‌مرحله بگو."
        out["reply"] = fb
        out["report"] = _report_lines()
        if executed_actions:
            await db.ai_add_message(session_id, "ai", fb + f"\n\n{_REPORT_MARK}:\n" + "\n".join(f"— {ln}" for ln in _report_lines()))
        out["panel_sent"] = await _flush_pending_buttons(ctx, uid)
        return out

    except httpx.HTTPStatusError as e:
        body = e.response.text[:500]
        logger.error("Gemini API error %s: %s", e.response.status_code, body)
        msg = "⚠️ ارتباط با هوش مصنوعی موقتاً مشکل داشت، چند لحظه دیگه امتحان کن."
        if role == ROLE_PISHVA:
            msg += f"\n\n🔧 جزئیات فنی (فقط برای مدیر ارشد):\nکد: {e.response.status_code}\n{body}"
        out["error"] = msg
        return out
    except Exception as e:
        logger.exception("hub_chat: AI turn failed")
        msg = "⚠️ یه خطای غیرمنتظره پیش اومد."
        if role == ROLE_PISHVA:
            msg += f"\n\n🔧 جزئیات فنی: {type(e).__name__}: {e}"
        out["error"] = msg
        return out


@routes.post("/hub/api/rg/send")
async def rg_send(request):
    uid, role, _p, ctx = await _auth(request)
    b = await _body(request)
    text = str(b.get("text") or "").strip()
    if not text:
        raise _fail("empty", "پیام خالی است.")
    if len(text) > MAX_TEXT:
        raise _fail("too_long", f"پیام نباید بیشتر از {MAX_TEXT} حرف باشد.")
    access = await _access_state(uid, role)
    if not access["usable"]:
        raise _fail(access["reason"], access["message"], 403)

    lock = _lock_for(uid)
    if lock.locked():
        raise _fail("busy", "رهگشا هنوز دارد به پیام قبلی‌ات جواب می‌دهد؛ چند ثانیه صبر کن.", 429)
    async with lock:
        sid = None
        want = b.get("session_id")
        if want:
            try:
                sess = await db.ai_get_session(int(want))
            except (TypeError, ValueError):
                sess = None
            if not sess or int(sess["user_id"]) != uid:
                raise _fail("not_found", "این چت مال شما نیست یا پیدا نشد.", 404)
            sid = int(want)
            await ai_shared.set_active_session(uid, sid)
        if not sid:
            sid = await ai_shared.ensure_active_session(uid, role)
        res = await _run_turn(uid, role, text, sid, ctx)
    return _ok(session_id=sid, **res)


# ─── تأییدِ اقدام / پیشنهادِ یادگیری ───────────────────────────
@routes.post("/hub/api/rg/confirm")
async def rg_confirm(request):
    uid, role, _p, ctx = await _auth(request)
    b = await _body(request)
    yes = bool(b.get("yes"))
    pend = ctx.user_data.pop("_aip_pending", None)
    sid = (pend or {}).get("sid") or await ai_shared.get_active_session(uid)
    if not yes:
        if sid:
            await db.ai_add_message(sid, "ai", "❌ لغو شد؛ چیزی اجرا نشد.")
        return _ok(reply="❌ لغو شد؛ چیزی اجرا نشد.")
    if not pend or time.time() - pend["ts"] > 600:
        raise _fail("expired", "منقضی شد؛ دوباره درخواستت را بگو.", 409)
    async with _lock_for(uid):
        ctx.user_data["_aip_confirmed"] = (pend["name"], json.dumps(pend["args"], sort_keys=True, ensure_ascii=False, default=str))
        try:
            result = await ai_tools.dispatch(pend["name"], pend["args"], uid, role, ctx)
        finally:
            ctx.user_data.pop("_aip_confirmed", None)
    text = f"✅ انجام شد.\n📋 {pend['name']}: {result}"
    if sid:
        await db.ai_add_message(sid, "tool", f"🔧 {pend['name']}({pend['args']}) → {result}")
        await db.ai_add_message(sid, "ai", text)
    return _ok(reply=text, report=[f"{pend['name']}: {result}"])


@routes.post("/hub/api/rg/learn")
async def rg_learn(request):
    uid, role, _p, ctx = await _auth(request)
    b = await _body(request)
    if not b.get("yes"):
        return _ok()
    key, val = str(b.get("key") or ""), str(b.get("val") or "")
    if not ai_persona._valid(key, val):
        raise _fail("bad_request", "مقدار نامعتبر است.")
    await ai_persona.set_pref(ctx, uid, key, val)
    return _ok(**(await _settings_payload(uid, role)))


# ─── تنظیماتِ شخصی ─────────────────────────────────────────────
@routes.post("/hub/api/rg/pref")
async def rg_pref(request):
    uid, role, _p, ctx = await _auth(request)
    b = await _body(request)
    key, val = str(b.get("key") or ""), b.get("value")
    if key not in ai_persona.OPTIONS:
        raise _fail("bad_request", "تنظیمِ نامعتبر.")
    if val in (None, "def"):
        val = None
    else:
        val = str(val)
        if not ai_persona._valid(key, val):
            raise _fail("bad_request", "مقدارِ نامعتبر برای این تنظیم.")
    await ai_persona.set_pref(ctx, uid, key, val)
    return _ok(**(await _settings_payload(uid, role)))


@routes.post("/hub/api/rg/text")
async def rg_text(request):
    uid, role, _p, ctx = await _auth(request)
    b = await _body(request)
    key = str(b.get("key") or "")
    if key not in ai_persona.TEXT_FIELDS or key in ("shortcut_add", "profile_save"):
        raise _fail("bad_request", "فیلدِ نامعتبر.")
    _label, maxlen, _hint = ai_persona.TEXT_FIELDS[key]
    raw = str(b.get("value") or "").strip()
    val = None if raw in ("", "-", "حذف") else ai_persona._clean_text(raw, maxlen)
    notify = key in ("persona_text", "ai_name", "report_template")
    await ai_persona.set_pref(ctx, uid, key, val, notify=notify)
    return _ok(**(await _settings_payload(uid, role)))


@routes.post("/hub/api/rg/shortcut")
async def rg_shortcut(request):
    uid, role, _p, ctx = await _auth(request)
    b = await _body(request)
    prefs = await ai_persona.get_prefs(uid)
    sc = dict(prefs.get("shortcuts") or {})
    act = b.get("act")
    if act == "add":
        trig = ai_persona._norm(b.get("trig"))[:30]
        cmd = ai_persona._clean_text(b.get("cmd"), ai_persona.TEXT_FIELDS["shortcut_add"][1])
        if not trig or not cmd:
            raise _fail("bad_request", "کلمه یا دستور خالیه.")
        if len(sc) >= ai_persona.MAX_SHORTCUTS and trig not in sc:
            raise _fail("limit", f"حداکثر {ai_persona.MAX_SHORTCUTS} میانبر.")
        sc[trig] = cmd
        await ai_persona._log_change(ctx, uid, "shortcuts", f"{trig} ← {cmd}")
    elif act == "del":
        sc.pop(str(b.get("trig") or ""), None)
        await ai_persona._log_change(ctx, uid, "shortcuts", "حذف")
    else:
        raise _fail("bad_request", "عملیاتِ نامعتبر.")
    prefs["shortcuts"] = sc
    await ai_persona.save_prefs(uid, prefs)
    return _ok(**(await _settings_payload(uid, role)))


@routes.post("/hub/api/rg/profile")
async def rg_profile(request):
    uid, role, _p, ctx = await _auth(request)
    b = await _body(request)
    prefs = await ai_persona.get_prefs(uid)
    profs = dict(prefs.get("profiles") or {})
    act, name = b.get("act"), ai_persona._clean_text(b.get("name"), 20)
    if act == "save":
        if not name:
            raise _fail("bad_request", "یه اسم برای پروفایل بنویس.")
        if len(profs) >= ai_persona.MAX_PROFILES and name not in profs:
            raise _fail("limit", f"حداکثر {ai_persona.MAX_PROFILES} پروفایل؛ یکی رو پاک کن.")
        profs[name] = {k: prefs[k] for k in ai_persona._PROFILE_KEYS if k in prefs}
        prefs["profiles"] = profs
        await ai_persona._log_change(ctx, uid, "profile", f"ذخیره {name}")
    elif act in ("apply", "del"):
        if name not in profs:
            raise _fail("not_found", "پروفایل پیدا نشد.", 404)
        if act == "del":
            profs.pop(name)
            prefs["profiles"] = profs
            await ai_persona._log_change(ctx, uid, "profile", f"حذف {name}")
        else:
            snap = profs[name]
            for k in ai_persona._PROFILE_KEYS:
                prefs.pop(k, None)
            prefs.update(snap)
            prefs["profiles"] = profs
            await ai_persona._log_change(ctx, uid, "profile", f"اعمال {name}", notify=True)
    else:
        raise _fail("bad_request", "عملیاتِ نامعتبر.")
    await ai_persona.save_prefs(uid, prefs)
    return _ok(**(await _settings_payload(uid, role)))


@routes.post("/hub/api/rg/mem")
async def rg_mem(request):
    uid, role, _p, ctx = await _auth(request)
    b = await _body(request)
    act = b.get("act")
    if act == "toggle":
        prefs = await ai_persona.get_prefs(uid)
        await ai_persona.set_pref(ctx, uid, "memory", "0" if prefs.get("memory", "1") == "1" else "1")
    elif act == "del":
        try:
            await ai_persona.delete_memory(uid, mem_id=int(b.get("id")))
        except (TypeError, ValueError):
            raise _fail("bad_request", "شناسه نامعتبر است.")
    elif act == "clear":
        await ai_persona.delete_memory(uid, all_=True)
    else:
        raise _fail("bad_request", "عملیاتِ نامعتبر.")
    return _ok(**(await _settings_payload(uid, role)))


@routes.post("/hub/api/rg/reset")
async def rg_reset(request):
    uid, role, _p, ctx = await _auth(request)
    await ai_persona.save_prefs(uid, {})
    await ai_persona._log_change(ctx, uid, "reset", "بازنشانی کامل")
    return _ok(**(await _settings_payload(uid, role)))


# ─── ابزارها: زمان‌بندی، توانایی‌ها، چرا این جواب، پیش‌نمایش ───────
@routes.get("/hub/api/rg/sched")
async def rg_sched(request):
    uid, role, is_pishva, _c = await _auth(request)
    rows = await ai_scheduler.list_pending_for_panel(is_pishva=is_pishva, caller_id=uid)
    return _ok(jobs=rows, all_admins=is_pishva)


@routes.post("/hub/api/rg/sched/cancel")
async def rg_sched_cancel(request):
    uid, role, is_pishva, ctx = await _auth(request)
    b = await _body(request)
    try:
        job_id = int(b.get("id"))
    except (TypeError, ValueError):
        raise _fail("bad_request", "شناسه نامعتبر است.")
    msg = await ai_scheduler.cancel(ctx.job_queue, job_id, uid, is_pishva)
    rows = await ai_scheduler.list_pending_for_panel(is_pishva=is_pishva, caller_id=uid)
    return _ok(message=msg, jobs=rows)


@routes.get("/hub/api/rg/abilities")
async def rg_abilities(request):
    uid, role, _p, _c = await _auth(request)
    states = await ai_tools.get_category_states()
    items = []
    for key, label, tools in ai_tools.AI_PERMISSION_CATEGORIES:
        mine = [t for t in tools if role in ai_tools.TOOL_PERMISSIONS.get(t, [])]
        if not mine:
            continue
        items.append({"key": key, "label": label, "count": len(mine), "on": states.get(key, "1") == "1"})
    return _ok(items=items)


@routes.get("/hub/api/rg/why")
async def rg_why(request):
    uid, role, _p, ctx = await _auth(request)
    w = ctx.user_data.get("_aip_why")
    if not w:
        return _ok(why=None)
    return _ok(why={
        "mode": ai_persona.MODES.get(w["mode"], ("?",))[0],
        "mem": w["mem"],
        "tools": [{"name": n, "args": ai_persona._clean_text(a, 80), "result": ai_persona._clean_text(r, 120)}
                  for n, a, r in w["tools"]],
    })


@routes.post("/hub/api/rg/preview")
async def rg_preview(request):
    uid, role, _p, _c = await _auth(request)
    if not aia.GEMINI_API_KEY:
        raise _fail("nokey", "⚠️ کلید Gemini تنظیم نشده.", 403)
    block = await ai_persona.build_block(uid, role)
    prompt = (f"تو «رهگشا» دستیار یک سیستم مدیریت مسابقات شطرنج هستی.{block}\n\n"
              "با همین تنظیمات، یک پیام نمونه‌ی کوتاه (دو سه جمله) بنویس که نشون بده دقیقاً چطور جواب می‌دی وقتی "
              "کاربر می‌پرسه «وضعیت مسابقه‌ها چطوره؟». فرض کن داده‌ی نمونه: ۱۲ بازی انجام‌شده، ۴ در انتظار، نفر اول علی با ۵ امتیاز.")
    try:
        data = await aia._call_gemini([{"role": "user", "parts": [{"text": prompt}]}], None)
        txt = "".join(p.get("text", "") for p in aia._extract_parts(data)).strip() or "پاسخی دریافت نشد."
    except Exception as e:
        logger.warning("hub_chat: preview failed", exc_info=True)
        raise _fail("ai_error", f"⚠️ پیش‌نمایش ساخته نشد: {type(e).__name__}", 502)
    return _ok(text=txt)


# ─── فقط مدیر ارشد ─────────────────────────────────────────────
def _admin_name(a) -> str:
    return (a["display_name"] or a["full_name"] or str(a["telegram_id"])) if a else "؟"


@routes.get("/hub/api/rg/admin/overview")
async def rg_admin_overview(request):
    uid, role, _p, _c = await _auth(request, pishva_only=True)
    online = (await db.get_setting("ai_online", "1")) == "1"
    lock = (await db.get_setting("ai_enforce_pishva_only", "1")) == "1"
    states = await ai_tools.get_category_states()
    cats = [{"key": k, "label": l, "on": states.get(k, "1") == "1", "count": len(t)}
            for k, l, t in ai_tools.AI_PERMISSION_CATEGORIES]
    admins = []
    for a in await db.get_all_admins():
        tid = a["telegram_id"]
        admins.append({"id": tid, "name": _admin_name(a), "role_label": ROLE_LABELS_FA.get(a["role"], "مدیر"),
                       "ai_on": bool(await db.get_admin_permission(tid, "ai_access")),
                       "active": bool(a["is_active"])})
    return _ok(online=online, lock=lock, categories=cats, admins=admins)


@routes.post("/hub/api/rg/admin/online")
async def rg_admin_online(request):
    uid, *_ = await _auth(request, pishva_only=True)
    cur = await db.get_setting("ai_online", "1")
    new = "0" if cur == "1" else "1"
    await db.set_setting("ai_online", new)
    await db.log_action(uid, "toggle_setting", f"ai_online -> {new}")
    return _ok(online=new == "1", access=await _access_state(uid, ROLE_PISHVA))


@routes.post("/hub/api/rg/admin/lock")
async def rg_admin_lock(request):
    uid, *_ = await _auth(request, pishva_only=True)
    cur = await db.get_setting("ai_enforce_pishva_only", "1")
    new = "0" if cur == "1" else "1"
    await db.set_setting("ai_enforce_pishva_only", new)
    await db.log_action(uid, "set_enforcement_lock", "روشن" if new == "1" else "خاموش")
    return _ok(lock=new == "1")


@routes.post("/hub/api/rg/admin/perm")
async def rg_admin_perm(request):
    uid, *_ = await _auth(request, pishva_only=True)
    b = await _body(request)
    key = str(b.get("key") or "")
    if key not in ai_tools.CATEGORY_LABELS:
        raise _fail("bad_request", "❌ دسته‌ی نامعتبر.")
    states = await ai_tools.get_category_states()
    new_enabled = states.get(key, "1") != "1"
    await ai_tools.set_category_state(key, new_enabled)
    await db.log_action(uid, "toggle_ai_category",
                        f"{ai_tools.CATEGORY_LABELS[key]} -> {'فعال' if new_enabled else 'غیرفعال'}")
    return _ok(key=key, on=new_enabled)


@routes.post("/hub/api/rg/admin/admin-ai")
async def rg_admin_admin_ai(request):
    uid, role, _p, ctx = await _auth(request, pishva_only=True)
    b = await _body(request)
    try:
        tid = int(b.get("id"))
    except (TypeError, ValueError):
        raise _fail("bad_request", "شناسه نامعتبر است.")
    admin = await db.get_admin(tid)
    if not admin:
        raise _fail("not_found", "مدیر یافت نشد.", 404)
    value = bool(b.get("on"))
    await db.set_admin_permission(tid, "ai_access", value)
    await db.log_action(uid, "toggle_perm", f"ai_access: {value}", tid)
    if ctx.bot is not None:
        _spawn(ctx.bot.send_message(
            chat_id=tid, parse_mode="Markdown",
            text=f"{'⬆️' if value else '⬇️'} دسترسی *دستیار هوشمند* {'فعال ✅' if value else 'غیرفعال ❌'} شد."))
    return _ok(id=tid, on=value)


@routes.get("/hub/api/rg/admin/logs")
async def rg_admin_logs(request):
    await _auth(request, pishva_only=True)
    try:
        tid = int(request.query.get("tid"))
    except (TypeError, ValueError):
        raise _fail("bad_request", "شناسه نامعتبر است.")
    rows = await db.ai_get_sessions_filtered(user_id=tid, period="all", limit=60)
    out = []
    for r in rows:
        out.append({"id": r["id"], "title": (r["title"] or "").strip() or "بدون عنوان",
                    "at": str(r["last_message_at"] or "")[:16].replace("T", " ")})
    return _ok(sessions=out)


@routes.get("/hub/api/rg/admin/session/{id}")
async def rg_admin_session(request):
    await _auth(request, pishva_only=True)
    try:
        sid = int(request.match_info["id"])
    except ValueError:
        raise _fail("bad_request", "شناسه نامعتبر است.")
    if not await db.ai_get_session(sid):
        raise _fail("not_found", "چت پیدا نشد.", 404)
    return _ok(session_id=sid, messages=await _messages_payload(sid))


@routes.get("/hub/api/rg/admin/live")
async def rg_admin_live(request):
    await _auth(request, pishva_only=True)
    import ai_tools_oversight as ov
    return _ok(text=(await ov._get_live_overview({}))[:6000])


@routes.get("/hub/api/rg/admin/trail")
async def rg_admin_trail(request):
    await _auth(request, pishva_only=True)
    import ai_tools_oversight as ov
    return _ok(text=(await ov._get_admin_audit_trail({"limit": 25, "scope": "all"}))[:6000])


@routes.get("/hub/api/rg/admin/notes")
async def rg_admin_notes(request):
    await _auth(request, pishva_only=True)
    rows = await ai_memory.recent(["pishva", "all"], limit=20)
    return _ok(notes=[{"id": r["id"], "subject": r["subject"], "content": ai_persona._clean_text(r["content"], 200)} for r in rows])


@routes.post("/hub/api/rg/admin/notes/del")
async def rg_admin_notes_del(request):
    await _auth(request, pishva_only=True)
    b = await _body(request)
    try:
        await ai_memory.delete(int(b.get("id")))
    except (TypeError, ValueError):
        raise _fail("bad_request", "شناسه نامعتبر است.")
    rows = await ai_memory.recent(["pishva", "all"], limit=20)
    return _ok(notes=[{"id": r["id"], "subject": r["subject"], "content": ai_persona._clean_text(r["content"], 200)} for r in rows])


@routes.get("/hub/api/rg/admin/persona/{tid}")
async def rg_admin_persona(request):
    await _auth(request, pishva_only=True)
    try:
        tid = int(request.match_info["tid"])
    except ValueError:
        raise _fail("bad_request", "شناسه نامعتبر است.")
    a = await db.get_admin(tid)
    prefs = await ai_persona.get_prefs(tid)
    return _ok(name=_admin_name(a) if a else str(tid), summary=ai_persona._summary(prefs))


@routes.post("/hub/api/rg/admin/persona/{tid}/reset")
async def rg_admin_persona_reset(request):
    uid, *_ = await _auth(request, pishva_only=True)
    try:
        tid = int(request.match_info["tid"])
    except ValueError:
        raise _fail("bad_request", "شناسه نامعتبر است.")
    await ai_persona.save_prefs(tid, {})
    await db.log_action(uid, "ai_persona_reset", "بازنشانی تنظیمات رهگشا", tid)
    return _ok(summary=ai_persona._summary({}))
