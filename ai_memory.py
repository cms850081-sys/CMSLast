"""
ai_memory.py — حافظه‌ی بلندمدتِ دستیار هوشمند (نسخه‌ی مقاوم)

چرا این ماژول ساخته شد؟
  جدول قدیمیِ ai_memory روی دیتابیس واقعی (Turso) در زمان‌های مختلف با ستون‌های
  متفاوتی ساخته شده (بعضی نسخه‌ها «content»، بعضی «fact»، بعضی بدونِ visibility)،
  و چون CREATE TABLE IF NOT EXISTS روی جدولِ موجود هیچ کاری نمی‌کنه، هر INSERT
  ممکن بود با «no column …» یا «NOT NULL constraint failed» بترکه — یعنی
  «ثبت در حافظه» بی‌سروصدا از کار می‌افتاد.

  اینجا یک جدولِ کاملاً جدید (ai_notes) با ساختارِ ثابت و تضمین‌شده ساخته می‌شه
  (خودش سرِ اولین استفاده، بدونِ نیاز به تغییرِ init_db). یادداشت‌های جدولِ
  قدیمی (اگه بود و خوندنی بود) یک‌بار، به‌صورت خودکار، به جدولِ جدید منتقل می‌شن.

  visibility:
    'all'    → محتوایی که برای همه‌ی مدیران عمومی بوده (بیانیه/خبر)
    'pishva' → یادداشت‌های خصوصی‌تر که فقط توی چتِ مدیر ارشد به‌عنوان زمینه میاد
"""
import asyncio
from srvctx import PerServerValue as _PSV, PerServer as _PS
import logging
import time

import turso_db
import database as db
from config import DB_PATH

logger = logging.getLogger(__name__)

TABLE = "ai_notes"
_LEGACY_FLAG = "ai_notes_legacy_imported"

_ready = _PSV(False)  # به‌ازای هر سرور
_lock = None  # موقع ایمپورت ساخته نمی‌شه تا به event loop خاصی گره نخوره

_CACHE_TTL = 30  # ثانیه — فقط برای خوندنِ «آخرین یادداشت‌ها» موقع ساختنِ پرامپت
_cache = _PS()  # یادداشت‌های اخیر: به‌ازای هر سرور

MAX_CONTENT_LEN = 4000
MAX_SUBJECT_LEN = 120


# ────────────────────────────────────────────────────────────────
# نشانه‌ی «Memo Updated📝» — هر جا یه یادداشت واقعاً ثبت بشه، این پرچم
# روی user_data همون چت خاموش/روشن می‌شه و ai_assistant.py زیر جواب می‌نویسدش.
# ────────────────────────────────────────────────────────────────
def mark_memo_updated(ctx, explicit: bool = False) -> None:
    """explicit=True یعنی ثبتِ صریحِ «به حافظه اضافه کن» (نه ثبتِ خودکارِ بیانیه/پیام...)."""
    try:
        ud = getattr(ctx, "user_data", None)
        if isinstance(ud, dict):
            ud["_memo_updated"] = True
            if explicit:
                ud["_memo_explicit"] = True
    except Exception:
        pass


def reset_memo_flags(ctx) -> None:
    try:
        ud = getattr(ctx, "user_data", None)
        if isinstance(ud, dict):
            ud.pop("_memo_updated", None)
            ud.pop("_memo_explicit", None)
    except Exception:
        pass


def pop_memo_explicit(ctx) -> bool:
    try:
        ud = getattr(ctx, "user_data", None)
        if isinstance(ud, dict):
            return bool(ud.pop("_memo_explicit", False))
    except Exception:
        pass
    return False


def pop_memo_updated(ctx) -> bool:
    try:
        ud = getattr(ctx, "user_data", None)
        if isinstance(ud, dict):
            return bool(ud.pop("_memo_updated", False))
    except Exception:
        pass
    return False


# ────────────────────────────────────────────────────────────────
async def _import_legacy() -> None:
    """یادداشت‌های جدولِ قدیمیِ ai_memory (هر ساختاری که داشته باشه) رو یک‌بار می‌آره."""
    if (await db.get_setting(_LEGACY_FLAG, "0")) == "1":
        return

    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row

        async with conn.execute("PRAGMA table_info(ai_memory)") as cur:
            info = await cur.fetchall()
        cols = {r["name"] for r in info}
        text_cols = [c for c in ("content", "fact", "text", "note") if c in cols]
        if not cols or not text_cols:
            await db.set_setting(_LEGACY_FLAG, "1")
            return

        # اگه دفعه‌ی قبل وسطِ انتقال قطع شده، دوباره تکرارش نمی‌کنیم (تکراری نشه)
        async with conn.execute(f"SELECT COUNT(*) AS c FROM {TABLE} WHERE source='legacy'") as cur:
            row = await cur.fetchone()
        if row and (row["c"] or 0) > 0:
            await db.set_setting(_LEGACY_FLAG, "1")
            return

        async with conn.execute("SELECT * FROM ai_memory ORDER BY id") as cur:
            rows = await cur.fetchall()

        imported = 0
        for r in rows:
            content = ""
            for c in text_cols:
                v = r[c]
                if v is not None and str(v).strip():
                    content = str(v).strip()
                    break
            if not content:
                continue
            subject = (r["subject"] if "subject" in cols and r["subject"] else "عمومی")
            vis = r["visibility"] if "visibility" in cols else None
            vis = vis if vis in ("all", "pishva") else "pishva"
            created_by = r["created_by"] if "created_by" in cols else None
            created_at = (r["created_at"] if "created_at" in cols and r["created_at"] else db._now_tehran_iso())
            await conn.execute(
                f"INSERT INTO {TABLE}(subject,content,visibility,source,created_by,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (str(subject)[:MAX_SUBJECT_LEN], content[:MAX_CONTENT_LEN], vis, "legacy", created_by, str(created_at)),
            )
            imported += 1
        await conn.commit()

    await db.set_setting(_LEGACY_FLAG, "1")
    logger.info("ai_memory: %d legacy note(s) imported into %s", imported, TABLE)


async def _ensure() -> None:
    global _ready, _lock
    if _ready.get():
        return
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if _ready.get():
            return
        async with turso_db.connect(DB_PATH) as conn:
            await conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL DEFAULT 'عمومی',
                    content TEXT NOT NULL,
                    visibility TEXT NOT NULL DEFAULT 'pishva',
                    source TEXT DEFAULT 'manual',
                    created_by INTEGER,
                    created_at TEXT
                )"""
            )
            await conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_vis ON {TABLE}(visibility, id)")
        _ready.set(True)
        try:
            await _import_legacy()
        except Exception:
            # انتقالِ یادداشت‌های قدیمی هیچ‌وقت نباید جلوی کارِ حافظه‌ی جدید رو بگیره
            logger.exception("ai_memory: legacy import failed (ignored)")


def _to_dict(r) -> dict:
    return {k: r[k] for k in ("id", "subject", "content", "visibility", "source", "created_at")}


# ────────────────────────────────────────────────────────────────
async def add(subject: str, content: str, visibility: str = "pishva",
              created_by=None, source: str = "manual") -> int:
    """یه یادداشت ثبت می‌کنه و id ردیف رو برمی‌گردونه. خطا رو *بالا می‌فرسته*
    (صدازننده تصمیم می‌گیره پنهونش کنه یا به کاربر بگه)."""
    content = (content or "").strip()
    if not content:
        raise ValueError("متن یادداشت خالیه")
    subject = ((subject or "").strip() or "عمومی")[:MAX_SUBJECT_LEN]
    if visibility not in ("all", "pishva"):
        visibility = "pishva"
    await _ensure()
    async with turso_db.connect(DB_PATH) as conn:
        cur = await conn.execute(
            f"INSERT INTO {TABLE}(subject,content,visibility,source,created_by,created_at) VALUES (?,?,?,?,?,?)",
            (subject, content[:MAX_CONTENT_LEN], visibility, source, created_by, db._now_tehran_iso()),
        )
        await conn.commit()
    _cache.clear()
    return getattr(cur, "lastrowid", None)


async def recent(visibility_levels, limit: int = 8) -> list:
    """آخرین یادداشت‌ها (برای تزریق به پرامپت). هرگز exception نمی‌ده."""
    levels = tuple(sorted(visibility_levels))
    key = (levels, limit)
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    try:
        await _ensure()
        ph = ",".join("?" * len(levels))
        async with turso_db.connect(DB_PATH) as conn:
            conn.row_factory = turso_db.Row
            async with conn.execute(
                f"SELECT id,subject,content,visibility,source,created_at FROM {TABLE} "
                f"WHERE visibility IN ({ph}) ORDER BY id DESC LIMIT ?",
                (*levels, limit),
            ) as cur:
                rows = await cur.fetchall()
        out = [_to_dict(r) for r in rows]
    except Exception:
        logger.exception("ai_memory.recent failed")
        return []
    _cache[key] = (time.monotonic() + _CACHE_TTL, out)
    return out


def _tokens(query: str) -> list:
    return [t for t in (query or "").replace("‌", " ").split() if t][:5]


async def search(query: str, visibility_levels, limit: int = 10) -> list:
    toks = _tokens(query)
    if not toks:
        return []
    await _ensure()
    levels = tuple(visibility_levels)
    ph = ",".join("?" * len(levels))
    where = " AND ".join("(subject LIKE ? OR content LIKE ?)" for _ in toks)
    params = []
    for t in toks:
        params += [f"%{t}%", f"%{t}%"]
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(
            f"SELECT id,subject,content,visibility,source,created_at FROM {TABLE} "
            f"WHERE {where} AND visibility IN ({ph}) ORDER BY id DESC LIMIT ?",
            (*params, *levels, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [_to_dict(r) for r in rows]


async def delete(memory_id: int) -> bool:
    await _ensure()
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(f"SELECT id FROM {TABLE} WHERE id = ?", (memory_id,)) as cur:
            existing = await cur.fetchone()
        if existing is None:
            return False
        await conn.execute(f"DELETE FROM {TABLE} WHERE id = ?", (memory_id,))
        await conn.commit()
    _cache.clear()
    return True
