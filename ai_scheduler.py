"""
ai_scheduler.py — یادآورها و اقدام‌های زمان‌بندی‌شده‌ی دستیار هوشمند

این ماژول همون «معادله‌ی دقیق زمان» که در precise_scheduler.py هست رو
به دستیار هوشمند وصل می‌کنه، تا دستیار بتونه از دو تا ابزار جدید استفاده کنه:

۱) set_reminder — «۱۵ دقیقه دیگه یادم بنداز بیام تو ربات»
   یه پیام متنی ساده، سر یه لحظه‌ی مشخص، برای همون کاربر فرستاده می‌شه.

۲) schedule_action — «فردا ساعت ۳ ظهر حالت امنیتی رو فعال کن»
   اجرای یکی دیگه از ابزارهای دستیار (هر کدوم که TOOL_PERMISSIONS به همون
   نقش اجازه بده) رو به یه لحظه‌ی مشخص در آینده موکول می‌کنه.

معادله‌ی تضمین‌کننده (دقیقاً همون اصل precise_scheduler.py):
    target = لحظه‌ی الان (به‌وقت تهران) + مدت درخواستی
دیگه شمارش معکوس نسبی وجود نداره؛ یه «لحظه‌ی مطلق» ساخته می‌شه و مستقیم به
job_queue.run_once(when=target) داده می‌شه — یعنی دقت زیرثانیه، مستقل از
لود سرور یا تاخیر پردازش.

سیستم یادش نمی‌ره:
هر رویداد قبل از زمان‌بندی، توی جدول ai_scheduled_jobs (این ماژول) +
جدول precise_jobs (ماژول precise_scheduler) ذخیره می‌شه. اگه ربات وسط راه
ری‌استارت بشه (رایلوی)، post_init → restore_all() دوباره دقیقاً سر همون
لحظه‌ی ذخیره‌شده جاب رو برمی‌گردونه — نه یک ثانیه زودتر، نه دیرتر، و اگه
موقع خاموش بودن ربات لحظه‌ش گذشته بود، بلافاصله (نه بی‌سروصدا گم‌شده)
اجرا می‌شه.
"""
import ai_persona
import json
import logging
from datetime import datetime, timedelta

import turso_db as aiosqlite
from config import DB_PATH
from helpers import box, TEHRAN_TZ, weekday_fa
import precise_scheduler as sched

try:
    import jdatetime
except ImportError:  # pragma: no cover
    jdatetime = None

logger = logging.getLogger(__name__)

JOB_PREFIX = "ai_sched_"

KIND_REMINDER = "reminder"
KIND_ACTION = "action"

_TABLE_READY = False


async def _ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_scheduled_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                caller_id INTEGER NOT NULL,
                caller_role TEXT NOT NULL,
                target_at TEXT NOT NULL,
                message TEXT,
                tool_name TEXT,
                tool_args TEXT,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
        """)
        await conn.commit()
    _TABLE_READY = True


def _fmt_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = TEHRAN_TZ.localize(dt)
    else:
        dt = dt.astimezone(TEHRAN_TZ)
    if jdatetime:
        jd = jdatetime.datetime.fromgregorian(datetime=dt)
        return f"{jd.strftime('%Y/%m/%d %H:%M')} ({weekday_fa(dt)})"
    return dt.strftime("%Y-%m-%d %H:%M")


def format_moment(dt: datetime) -> str:
    """نمایش خوانا (شمسی + روز هفته) از یه لحظه‌ی مشخص — برای پیام تاییدیه به کاربر."""
    return _fmt_dt(dt)


def resolve_target(args: dict):
    """
    ورودی مدل رو به یه لحظه‌ی مطلقِ آگاه‌از‌تایم‌زون (تهران) تبدیل می‌کنه.
    خروجی: (target_datetime یا None, پیام خطا یا None)

    دو راه ورودی:
    - in_minutes: مدت نسبی («۱۵ دقیقه دیگه») → target = الان + این مدت
    - at_datetime: لحظه‌ی مطلق («فردا ساعت ۳») به فرمت 'YYYY-MM-DD HH:MM'
      (میلادی، به‌وقت تهران) — مدل با توجه به لحظه‌ی الان که در پرامپت
      سیستمی داره، این رو خودش محاسبه و پر می‌کنه.
    """
    now = datetime.now(TEHRAN_TZ)
    in_minutes = args.get("in_minutes")
    at_datetime = args.get("at_datetime")

    if in_minutes not in (None, ""):
        try:
            minutes = int(in_minutes)
        except (TypeError, ValueError):
            return None, "مقدار in_minutes باید یه عدد صحیح (دقیقه) باشه."
        if minutes <= 0:
            return None, "مدت باید یه عدد مثبت باشه."
        if minutes > 60 * 24 * 365:
            return None, "این مدت خیلی زیاده (بیشتر از یک سال)؛ یه بازه‌ی منطقی‌تر بده."
        return now + timedelta(minutes=minutes), None

    if at_datetime not in (None, ""):
        s = str(at_datetime).strip()
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None, "فرمت at_datetime درست نیست؛ باید 'YYYY-MM-DD HH:MM' (میلادی، وقت تهران) باشه."
        target = TEHRAN_TZ.localize(parsed)
        if target <= now:
            return None, "این لحظه یا همین الانه یا گذشته؛ یه لحظه‌ی آینده بده."
        return target, None

    return None, "باید یکی از in_minutes (مدت به دقیقه) یا at_datetime (لحظه‌ی مطلق) رو بدی."


# ────────────────────────────────────────────────────────────────
# ساخت رویداد جدید
# ────────────────────────────────────────────────────────────────

async def _insert_row(kind, caller_id, caller_role, target_at, message, tool_name, tool_args, description):
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            """INSERT INTO ai_scheduled_jobs
               (kind, caller_id, caller_role, target_at, message, tool_name, tool_args, description, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (kind, caller_id, caller_role, target_at.isoformat(), message, tool_name,
             json.dumps(tool_args or {}), description, datetime.now(TEHRAN_TZ).isoformat()),
        )
        await conn.commit()
        return cur.lastrowid


async def create_reminder(job_queue, caller_id: int, caller_role: str, target_at: datetime, message: str) -> int:
    job_id = await _insert_row(KIND_REMINDER, caller_id, caller_role, target_at, message, None, None, message)
    await sched.schedule_persistent(
        job_queue, reminder_fire_job, target_at, f"{JOB_PREFIX}{job_id}", {"job_id": job_id}
    )
    return job_id


async def create_action(job_queue, caller_id: int, caller_role: str, target_at: datetime,
                         tool_name: str, tool_args: dict, description: str) -> int:
    job_id = await _insert_row(KIND_ACTION, caller_id, caller_role, target_at, None, tool_name, tool_args, description)
    await sched.schedule_persistent(
        job_queue, action_fire_job, target_at, f"{JOB_PREFIX}{job_id}", {"job_id": job_id}
    )
    return job_id


# ────────────────────────────────────────────────────────────────
# خواندن/لغو
# ────────────────────────────────────────────────────────────────

async def _get_row(job_id: int):
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM ai_scheduled_jobs WHERE id=?", (job_id,)) as cur:
            return await cur.fetchone()


async def _set_status(job_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("UPDATE ai_scheduled_jobs SET status=? WHERE id=?", (status, job_id))
        await conn.commit()


async def list_pending(caller_id: int, caller_role: str, is_pishva: bool):
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        if is_pishva:
            query = "SELECT * FROM ai_scheduled_jobs WHERE status='pending' ORDER BY target_at ASC"
            params = ()
        else:
            query = "SELECT * FROM ai_scheduled_jobs WHERE status='pending' AND caller_id=? ORDER BY target_at ASC"
            params = (caller_id,)
        async with conn.execute(query, params) as cur:
            return await cur.fetchall()


async def list_pending_for_panel(is_pishva: bool = True, caller_id: int = None):
    """لیست کارهای در انتظار برای نمایش دکمه‌ای در پنل مدیر ارشد.
    خروجی: [{"id": ..., "label": ...}, ...] مرتب‌شده بر اساس نزدیک‌ترین زمان."""
    rows = await list_pending(caller_id or 0, "", is_pishva)
    out = []
    for r in rows:
        target = datetime.fromisoformat(r["target_at"])
        when = _fmt_dt(target)
        if r["kind"] == KIND_REMINDER:
            label = f"⏰ یادآور «{r['message']}» — {when}"
        else:
            desc = r["description"] or r["tool_name"]
            label = f"🛠 «{desc}» — {when}"
        out.append({"id": r["id"], "label": label})
    return out


async def render_pending_list(caller_id: int, caller_role: str, is_pishva: bool) -> str:
    rows = await list_pending(caller_id, caller_role, is_pishva)
    if not rows:
        return "📭 هیچ یادآور یا اقدام زمان‌بندی‌شده‌ای در انتظار نیست."
    lines = []
    for r in rows:
        target = datetime.fromisoformat(r["target_at"])
        when = _fmt_dt(target)
        if r["kind"] == KIND_REMINDER:
            lines.append(f"#{r['id']} ⏰ یادآور — «{r['message']}» — {when}")
        else:
            label = r["description"] or r["tool_name"]
            lines.append(f"#{r['id']} 🛠 اقدام «{label}» — {when}")
    return box("📋 یادآورها و اقدام‌های زمان‌بندی‌شده") + "\n\n" + "\n".join(lines)


async def cancel(job_queue, job_id: int, caller_id: int, is_pishva: bool) -> str:
    row = await _get_row(job_id)
    if not row:
        return f"❌ شناسه‌ی #{job_id} پیدا نشد."
    if row["status"] != "pending":
        return f"⚠️ #{job_id} از قبل «{row['status']}» شده و دیگه در انتظار نیست."
    if not is_pishva and row["caller_id"] != caller_id:
        return "⛔ فقط صاحب همین یادآور/اقدام (یا مدیر ارشد) می‌تونه لغوش کنه."
    await sched.cancel_persistent(job_queue, f"{JOB_PREFIX}{job_id}")
    await _set_status(job_id, "cancelled")
    return f"✅ #{job_id} لغو شد."


# ────────────────────────────────────────────────────────────────
# اجرای واقعی — دقیقاً سر لحظه‌ی هدف صدا زده می‌شه
# ────────────────────────────────────────────────────────────────

async def reminder_fire_job(context):
    job_id = context.job.data.get("job_id")
    await sched.clear_target(f"{JOB_PREFIX}{job_id}")
    row = await _get_row(job_id)
    if not row or row["status"] != "pending":
        return
    await _set_status(job_id, "done")
    text = box("⏰ یادآوری") + f"\n\n{row['message']}"
    try:
        await ai_persona.deliver(context.bot, row["caller_id"], text)
    except Exception:
        logger.exception(f"Could not deliver reminder #{job_id}")


async def action_fire_job(context):
    job_id = context.job.data.get("job_id")
    await sched.clear_target(f"{JOB_PREFIX}{job_id}")
    row = await _get_row(job_id)
    if not row or row["status"] != "pending":
        return
    await _set_status(job_id, "done")

    # ایمپورت محلی — تا چرخه‌ی ایمپورت با ai_tools.py (که خودش این ماژول رو
    # در بالای فایل ایمپورت می‌کنه) پیش نیاد.
    import ai_tools

    try:
        tool_args = json.loads(row["tool_args"] or "{}")
    except Exception:
        tool_args = {}

    try:
        result = await ai_tools.dispatch(row["tool_name"], tool_args, row["caller_id"], row["caller_role"], context)
    except Exception as e:
        logger.exception(f"Scheduled action #{job_id} ({row['tool_name']}) failed")
        result = f"⚠️ در اجرای این اقدام خطایی رخ داد: {e}"

    label = row["description"] or row["tool_name"]
    text = box("⏰ اقدام زمان‌بندی‌شده اجرا شد") + f"\n\n🛠 {label}\n📋 نتیجه: {result}"
    try:
        await ai_persona.deliver(context.bot, row["caller_id"], text)
    except Exception:
        logger.exception(f"Could not deliver scheduled-action result #{job_id}")


# ────────────────────────────────────────────────────────────────
# بازیابی بعد از ری‌استارت
# ────────────────────────────────────────────────────────────────

async def restore_all(application):
    """موقع post_init صدا زده می‌شه: هر یادآور/اقدامی که هنوز pending مونده
    (چه سررسیدش گذشته باشه چه نه)، دقیقاً سر لحظه‌ی ذخیره‌شده دوباره
    زمان‌بندی می‌شه — بدون کوچک‌ترین انحراف."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT id, kind FROM ai_scheduled_jobs WHERE status='pending'") as cur:
            rows = await cur.fetchall()
    for r in rows:
        callback = reminder_fire_job if r["kind"] == KIND_REMINDER else action_fire_job
        await sched.restore_pending(application.job_queue, callback, f"{JOB_PREFIX}{r['id']}")
    if rows:
        logger.info(f"AI scheduler: {len(rows)} pending reminder/action(s) restored.")
