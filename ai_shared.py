"""
ai_shared.py — پُل مشترکِ چتِ رهگشا بین «ربات تلگرام» و «پنل من (هاب)».

هدف: چتِ رهگشا در تلگرام و چتِ رهگشا در هاب *یک چت* باشند؛ فقط محیطشان فرق کند.

چطور؟ هر دو جا از همان دو جدولِ دیتابیس (ai_chat_sessions / ai_chat_messages) می‌خوانند و
می‌نویسند، و هر دو جا «جلسه‌ی فعالِ هر کاربر» را از یک نشانگرِ مشترک در دیتابیس
می‌گیرند (نه از حافظه‌ی موقتِ هر محیط). تاریخچه‌ای که مدل می‌بیند هم هر بار از دیتابیس
ساخته می‌شود، نه از لیستِ درون‌حافظه‌ای — پس:
  • پیامی که در تلگرام نوشتی، همان لحظه در هاب هم هست (و برعکس)؛
  • با ری‌استارتِ ربات هم تاریخچه گم نمی‌شود؛
  • «چت جدید» و «ادامه‌ی یک چتِ قدیمی» در هر دو محیط روی همان جلسه اثر می‌گذارد.

این فایل هیچ‌چیزِ تلگرامی ایمپورت نمی‌کند (تا هم ربات هم هاب بدونِ چرخه ازش استفاده کنند).
"""
import logging

import turso_db
import database as db
from config import DB_PATH

logger = logging.getLogger(__name__)

MEMO_TAG = "Memo Updated📝"
HISTORY_TURNS = 6            # همان MAX_HISTORY_TURNS در ai_assistant (رفت‌وبرگشت)
_MAX_MSG_CHARS = 3000        # هر پیام در تاریخچه‌ی مدل حداکثر این‌قدر (کنترلِ توکن)


def _key(uid: int) -> str:
    return f"ai_active_session:{int(uid)}"


async def get_active_session(uid: int):
    """شناسه‌ی جلسه‌ی فعالِ این کاربر (یا None). فقط اگر جلسه واقعاً مالِ خودش باشد."""
    try:
        raw = await db.get_setting(_key(uid), "")
        if not raw:
            return None
        sid = int(raw)
        sess = await db.ai_get_session(sid)
        if sess and int(sess["user_id"]) == int(uid):
            return sid
    except Exception:
        logger.warning("ai_shared.get_active_session failed", exc_info=True)
    return None


async def set_active_session(uid: int, sid: int) -> None:
    try:
        await db.set_setting(_key(uid), str(int(sid)))
    except Exception:
        logger.warning("ai_shared.set_active_session failed", exc_info=True)


async def ensure_active_session(uid: int, role: str) -> int:
    """جلسه‌ی فعال را برمی‌گرداند؛ اگر نبود یکی می‌سازد و فعالش می‌کند."""
    sid = await get_active_session(uid)
    if sid:
        return sid
    sid = await db.ai_create_session(uid, role or "")
    await set_active_session(uid, sid)
    return sid


async def new_session(uid: int, role: str) -> int:
    sid = await db.ai_create_session(uid, role or "")
    await set_active_session(uid, sid)
    return sid


async def history_from_db(session_id: int, turns: int = HISTORY_TURNS) -> list:
    """آخرین پیام‌های user/ai همین جلسه به فرمتِ Gemini (قدیمی→جدید).

    - لاگِ ابزارها (sender='tool') برای مدل لازم نیست و نمی‌آید.
    - اگر لیست با پیامِ «model» شروع شود حذفش می‌کنیم (Gemini باید با user شروع شود).
    - پیام‌های هم‌نقشِ پشت‌سرهم (مثلاً بعد از یک خطا) یکی می‌شوند.
    """
    limit = max(2, int(turns) * 2)
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(
            "SELECT sender, text FROM ai_chat_messages WHERE session_id=? AND sender IN ('user','ai') "
            "ORDER BY id DESC LIMIT ?", (int(session_id), limit)
        ) as cur:
            rows = list(await cur.fetchall())
    rows.reverse()
    out = []
    for r in rows:
        txt = str(r["text"] or "").replace(MEMO_TAG, "").strip()
        if not txt:
            continue
        txt = txt[:_MAX_MSG_CHARS]
        role = "user" if r["sender"] == "user" else "model"
        if out and out[-1]["role"] == role:
            out[-1]["parts"][0]["text"] += "\n" + txt
        else:
            out.append({"role": role, "parts": [{"text": txt}]})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


async def sessions_with_counts(uid: int, limit: int = 40) -> list:
    """جلسه‌های این کاربر که حداقل یک پیام دارند (جلسه‌ی خالی = فقط «شروع» زده شده)."""
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(
            "SELECT s.id, s.title, s.started_at, s.last_message_at, "
            "(SELECT COUNT(*) FROM ai_chat_messages m WHERE m.session_id=s.id AND m.sender IN ('user','ai')) AS n "
            "FROM ai_chat_sessions s WHERE s.user_id=? ORDER BY s.last_message_at DESC LIMIT ?",
            (int(uid), int(limit) * 2)
        ) as cur:
            rows = await cur.fetchall()
    return [r for r in rows if (r["n"] or 0) > 0][:limit]


async def messages_of(session_id: int, limit: int = 300) -> list:
    """آخرین N پیامِ user/ai/tool یک جلسه (قدیمی→جدید) — برای نمایش."""
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(
            "SELECT id, sender, text, sent_at FROM ai_chat_messages WHERE session_id=? "
            "ORDER BY id DESC LIMIT ?", (int(session_id), int(limit))
        ) as cur:
            rows = list(await cur.fetchall())
    rows.reverse()
    return rows
