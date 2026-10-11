"""
precise_scheduler.py — تضمین زمان‌بندی دقیق

هدف: هیچ رویداد زمان‌بندی‌شده‌ای (پایان خودکار ساعت کاری، یادآور، قهرمان
هفتگی و هر چیز مشابه دیگه) نباید حتی یک ثانیه زودتر یا دیرتر از موعدش
اجرا بشه.

معادلهٔ تضمین‌کننده:

    target = آغاز + مدت

به‌جای شمارش معکوس (asyncio.sleep(N) یا job_queue.run_repeating با
interval نسبی)، که با تاخیر پردازش، لود سرور و بازراه‌اندازی رایلوی
دچار انحراف تدریجی می‌شه، این ماژول همیشه یک "لحظهٔ مطلق" (target)
می‌سازه و مستقیماً همون رو به جاب‌کیو می‌ده:

    job_queue.run_once(callback, when=target)

python-telegram-bot (روی APScheduler) این لحظه رو با دقت زیرثانیه پیاده
می‌کنه چون به یک ساعت مطلق (wall-clock) وصله، نه به یک شمارندهٔ نسبی که
از لحظهٔ زمان‌بندی شروع می‌شه.

برای مقاومت در برابر ری‌استارت (که جاب‌کیوی در حافظه رو پاک می‌کنه):
هر لحظهٔ هدف قبل از زمان‌بندی، توی جدول precise_jobs ذخیره می‌شه. موقع
بالا اومدن دوبارهٔ ربات، restore_pending() صدا زده می‌شه و:
  • اگه لحظهٔ هدف هنوز نگذشته → دقیقاً روی همون لحظهٔ ذخیره‌شده (نه یک
    شمارش معکوس تازه) دوباره زمان‌بندی می‌شه → صفر انحراف.
  • اگه لحظهٔ هدف موقع خاموش بودن ربات گذشته → بلافاصله اجرا می‌شه، چون
    دیرتر از این ممکن نیست و نباید بی‌سروصدا گم بشه.
"""
import json
import logging
from datetime import datetime

import turso_db as aiosqlite
from config import DB_PATH

logger = logging.getLogger(__name__)

import srvctx


def _jn(name: str) -> str:
    """نامِ یکتای جاب در JobQueue: سرور اصلی = همان نام؛ سرور N = «نام@sN» (جاب‌های دو مجموعه هرگز قاطی نشوند)."""
    sid = srvctx.current()
    return name if sid == srvctx.MAIN_ID else f"{name}@s{sid}"


def _wrap_cb(callback, sid: int):
    """جاب‌ها بیرون از آپدیتِ تلگرام اجرا می‌شوند؛ پس سرورِ زمان‌بندی‌کننده را صراحتاً در اجرا برقرار می‌کنیم."""
    if sid == srvctx.MAIN_ID:
        return callback

    async def _in_server(context):
        with srvctx.use(sid):
            return await callback(context)
    _in_server.__name__ = getattr(callback, "__name__", "job") + f"_s{sid}"
    return _in_server

from srvctx import PerServerValue as _PSV, PerServer as _PS
_TABLE_READY = _PSV(False)  # به‌ازای هر سرور


async def _ensure_table():
    global _TABLE_READY
    if _TABLE_READY.get():
        return
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS precise_jobs (
                name TEXT PRIMARY KEY,
                target_at TEXT,
                payload TEXT
            )
        """)
        await conn.commit()
    _TABLE_READY.set(True)


async def save_target(name: str, target_at: datetime, payload: dict = None):
    """لحظهٔ هدف رو ذخیره می‌کنه تا بعد از ری‌استارت هم قابل بازیابی باشه."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO precise_jobs(name, target_at, payload) VALUES (?, ?, ?)",
            (name, target_at.isoformat(), json.dumps(payload or {})),
        )
        await conn.commit()


async def clear_target(name: str):
    """رویداد رو از لیست معلق‌ها حذف می‌کنه (مثلاً وقتی دستی/زودتر انجام شد)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM precise_jobs WHERE name=?", (name,))
        await conn.commit()


async def load_target(name: str):
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT target_at, payload FROM precise_jobs WHERE name=?", (name,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None, None
            return datetime.fromisoformat(row[0]), json.loads(row[1] or "{}")


def schedule_exact(job_queue, callback, target_at: datetime, name: str, data: dict = None):
    """رویداد رو دقیقاً روی لحظهٔ مطلق target_at می‌ذاره (جای‌گزین هر جاب قبلی با همین اسم)."""
    if job_queue is None:
        logger.warning(f"job_queue در دسترس نیست؛ رویداد «{name}» زمان‌بندی نشد.")
        return
    jn = _jn(name)
    for job in job_queue.get_jobs_by_name(jn):
        job.schedule_removal()
    job_queue.run_once(_wrap_cb(callback, srvctx.current()), when=target_at, name=jn, data=data or {})


async def schedule_persistent(job_queue, callback, target_at: datetime, name: str, data: dict = None):
    """schedule_exact + ذخیرهٔ لحظهٔ هدف برای بازیابی بعد از ری‌استارت."""
    await save_target(name, target_at, data)
    schedule_exact(job_queue, callback, target_at, name, data)


async def cancel_persistent(job_queue, name: str):
    """رویداد رو هم از جاب‌کیو و هم از دیتابیس پاک می‌کنه."""
    if job_queue is not None:
        for job in job_queue.get_jobs_by_name(_jn(name)):
            job.schedule_removal()
    await clear_target(name)


async def restore_pending_here(job_queue, callback, name: str):
    """مثلِ restore_pending ولی فقط برای «سرورِ جاری» (داخلِ زمینه‌ی همان سرور صدا زده شود)."""
    target_at, data = await load_target(name)
    if target_at is None:
        return
    now = datetime.now(target_at.tzinfo) if target_at.tzinfo else datetime.now()
    fire_at = target_at if target_at > now else now
    schedule_exact(job_queue, callback, fire_at, name, data)
    if fire_at != target_at:
        logger.info(f"رویداد «{name}» (سرور {srvctx.current()}) موقع خاموش بودن ربات سررسید شده بود؛ فوری اجرا می‌شه.")


async def restore_pending(job_queue, callback, name: str):
    """موقع post_init صدا زده می‌شه: اگه این رویداد قبل از قطع‌شدن ربات
    زمان‌بندی شده بود، بدون هیچ انحرافی دوباره سرجاش می‌شینه.
    چندسرور: برای «همه‌ی سرورهای فعال» انجام می‌شود (هرکدام در زمینه‌ی خودش)."""
    try:
        import servers
        sids = servers.active_ids()
    except Exception:
        sids = [srvctx.MAIN_ID]
    for sid in sids:
        with srvctx.use(sid):
            try:
                await restore_pending_here(job_queue, callback, name)
            except Exception:
                logger.exception("restore_pending(%s) روی سرور %s ناموفق", name, sid)
