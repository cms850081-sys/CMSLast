"""
elo.py — سیستم رتبه‌بندی Elo برای شطرنج
"""
import turso_db as aiosqlite
import logging
from config import DB_PATH

logger = logging.getLogger(__name__)

# ─── ثابت‌های Elo ─────────────────────────────────────────────
ELO_DEFAULT = 1200      # امتیاز اولیه هر بازیکن
ELO_K_NEW = 40          # ضریب K برای بازیکنان جدید (کمتر از ۱۰ بازی)
ELO_K_NORMAL = 20       # ضریب K برای بازیکنان معمولی
ELO_K_MASTER = 10       # ضریب K برای بازیکنان قوی (بالای ۲۴۰۰)


def get_k_factor(rating: float, games_played: int) -> int:
    if games_played < 10:
        return ELO_K_NEW
    if rating >= 2400:
        return ELO_K_MASTER
    return ELO_K_NORMAL


def expected_score(rating_a: float, rating_b: float) -> float:
    """احتمال پیروزی بازیکن A مقابل B"""
    return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))


def calculate_new_ratings(
    rating_white: float, rating_black: float,
    games_white: int, games_black: int,
    result: str  # "white", "black", "draw"
) -> tuple:
    """محاسبه امتیاز جدید هر دو بازیکن"""
    ea = expected_score(rating_white, rating_black)
    eb = expected_score(rating_black, rating_white)

    if result == "white":
        sa, sb = 1.0, 0.0
    elif result == "black":
        sa, sb = 0.0, 1.0
    else:  # draw
        sa, sb = 0.5, 0.5

    ka = get_k_factor(rating_white, games_white)
    kb = get_k_factor(rating_black, games_black)

    new_white = round(rating_white + ka * (sa - ea))
    new_black = round(rating_black + kb * (sb - eb))

    change_white = new_white - rating_white
    change_black = new_black - rating_black

    return new_white, new_black, change_white, change_black


def get_elo_title(rating: float) -> str:
    """عنوان بر اساس امتیاز Elo"""
    if rating >= 2500:
        return "♟️ گرندمستر"
    elif rating >= 2400:
        return "🥇 استاد بین‌المللی"
    elif rating >= 2200:
        return "🥈 استاد فیده"
    elif rating >= 2000:
        return "🥉 کاندیدا استاد"
    elif rating >= 1800:
        return "💎 متخصص"
    elif rating >= 1600:
        return "🔵 کلاس A"
    elif rating >= 1400:
        return "🟢 کلاس B"
    elif rating >= 1200:
        return "🟡 کلاس C"
    else:
        return "🔰 مبتدی"


def get_elo_bar(rating: float) -> str:
    """نوار گرافیکی امتیاز Elo"""
    # بازه ۸۰۰ تا ۲۸۰۰
    pct = max(0, min(100, (rating - 800) / 20))
    filled = int(15 * pct / 100)
    bar = "█" * filled + "░" * (15 - filled)
    return f"[{bar}] {int(rating)}"


# ─── عملیات دیتابیس Elo ──────────────────────────────────────
# FIX (کندیِ ثبت نتیجه‌ی مسابقه): ensure_elo_table قبلاً روی *هر* ثبت‌نتیجه
# صدا زده می‌شد و هر بار ۲ تا CREATE TABLE IF NOT EXISTS جداگانه (یعنی ۲
# رفت‌وبرگشتِ شبکه‌ی کامل به Turso) اجرا می‌کرد — با اینکه بعد از اولین بار
# این جدول‌ها همیشه از قبل وجود دارن و این کار عملاً هیچ‌کاری نمی‌کنه.
# الان فقط یک‌بار در طولِ اجرای پردازه چک/ساخته می‌شه (و همون یک‌بار هم با
# batch در یک رفت‌وبرگشت، نه دوتا).
from srvctx import PerServerValue as _PSV, PerServer as _PS
_elo_tables_ready = _PSV(False)  # به‌ازای هر سرور


async def ensure_elo_table():
    global _elo_tables_ready
    if _elo_tables_ready.get():
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.batch():
            await db.execute("""
                CREATE TABLE IF NOT EXISTS player_elo (
                    player_id INTEGER PRIMARY KEY,
                    rating REAL DEFAULT 1200,
                    peak_rating REAL DEFAULT 1200,
                    games_played INTEGER DEFAULT 0,
                    elo_wins INTEGER DEFAULT 0,
                    elo_losses INTEGER DEFAULT 0,
                    elo_draws INTEGER DEFAULT 0,
                    last_updated TEXT,
                    FOREIGN KEY(player_id) REFERENCES players(id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS elo_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id INTEGER,
                    match_id INTEGER,
                    old_rating REAL,
                    new_rating REAL,
                    change REAL,
                    opponent_id INTEGER,
                    result TEXT,
                    recorded_at TEXT
                )
            """)
    _elo_tables_ready.set(True)


async def get_player_elo(player_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM player_elo WHERE player_id=?", (player_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
            return {
                "player_id": player_id,
                "rating": ELO_DEFAULT,
                "peak_rating": ELO_DEFAULT,
                "games_played": 0,
                "elo_wins": 0,
                "elo_losses": 0,
                "elo_draws": 0,
            }


async def get_player_elo_pair(id_a: int, id_b: int) -> tuple:
    """مثلِ دوبار صدا زدنِ get_player_elo، ولی هر دو بازیکن با یک کوئریِ
    IN(...) (یک رفت‌وبرگشتِ شبکه‌ی واحد، نه دوتا) گرفته می‌شن — دقیقاً
    همون الگویی که get_settings_bulk در database.py استفاده می‌کنه."""
    def _default(pid):
        return {
            "player_id": pid, "rating": ELO_DEFAULT, "peak_rating": ELO_DEFAULT,
            "games_played": 0, "elo_wins": 0, "elo_losses": 0, "elo_draws": 0,
        }
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM player_elo WHERE player_id IN (?,?)", (id_a, id_b)
        ) as cur:
            rows = {row["player_id"]: dict(row) for row in await cur.fetchall()}
    return rows.get(id_a) or _default(id_a), rows.get(id_b) or _default(id_b)


async def update_elo_after_match(
    white_id: int, black_id: int,
    result: str, match_id: int
):
    """آپدیت Elo پس از ثبت نتیجه مسابقه"""
    from datetime import datetime
    now = datetime.now().isoformat()

    white_elo, black_elo = await get_player_elo_pair(white_id, black_id)

    new_w, new_b, chg_w, chg_b = calculate_new_ratings(
        white_elo["rating"], black_elo["rating"],
        white_elo["games_played"], black_elo["games_played"],
        result
    )

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.batch():
            # Update/Insert white
            await db.execute("""
                INSERT INTO player_elo(player_id, rating, peak_rating, games_played,
                    elo_wins, elo_losses, elo_draws, last_updated)
                VALUES (?, ?, ?, 1,
                    ?, 0, ?,
                    ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    rating=?,
                    peak_rating=MAX(peak_rating, ?),
                    games_played=games_played+1,
                    elo_wins=elo_wins+?,
                    elo_losses=elo_losses+?,
                    elo_draws=elo_draws+?,
                    last_updated=?
            """, (
                white_id, new_w, new_w,
                1 if result == "white" else 0,
                1 if result == "draw" else 0,
                now,
                new_w, new_w,
                1 if result == "white" else 0,
                1 if result == "black" else 0,
                1 if result == "draw" else 0,
                now
            ))

            # Update/Insert black
            await db.execute("""
                INSERT INTO player_elo(player_id, rating, peak_rating, games_played,
                    elo_wins, elo_losses, elo_draws, last_updated)
                VALUES (?, ?, ?, 1,
                    ?, 0, ?,
                    ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    rating=?,
                    peak_rating=MAX(peak_rating, ?),
                    games_played=games_played+1,
                    elo_wins=elo_wins+?,
                    elo_losses=elo_losses+?,
                    elo_draws=elo_draws+?,
                    last_updated=?
            """, (
                black_id, new_b, new_b,
                1 if result == "black" else 0,
                1 if result == "draw" else 0,
                now,
                new_b, new_b,
                1 if result == "black" else 0,
                1 if result == "white" else 0,
                1 if result == "draw" else 0,
                now
            ))

            # History for white
            await db.execute("""
                INSERT INTO elo_history(player_id, match_id, old_rating, new_rating,
                    change, opponent_id, result, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (white_id, match_id, white_elo["rating"], new_w, chg_w, black_id,
                  "win" if result == "white" else "loss" if result == "black" else "draw", now))

            # History for black
            await db.execute("""
                INSERT INTO elo_history(player_id, match_id, old_rating, new_rating,
                    change, opponent_id, result, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (black_id, match_id, black_elo["rating"], new_b, chg_b, white_id,
                  "win" if result == "black" else "loss" if result == "white" else "draw", now))

    return new_w, new_b, chg_w, chg_b


async def get_elo_leaderboard(limit: int = 10) -> list:
    """جدول رتبه‌بندی Elo"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT pe.*, p.full_name, c.name as class_name
            FROM player_elo pe
            JOIN players p ON pe.player_id = p.id
            LEFT JOIN classes c ON p.class_id = c.id
            WHERE p.status = 'active'
            ORDER BY pe.rating DESC
            LIMIT ?
        """, (limit,)) as cur:
            return await cur.fetchall()


# ─── سیستم Elo مخصوص شطرنج زنده (جدول جدا) ────────────────────
async def ensure_chess_elo_table():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chess_elo (
                player_id INTEGER PRIMARY KEY,
                display_name TEXT,
                rating REAL DEFAULT 1200,
                peak_rating REAL DEFAULT 1200,
                games_played INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                draws INTEGER DEFAULT 0,
                last_updated TEXT
            )
        """)
        await db.commit()


async def get_chess_player_elo(player_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM chess_elo WHERE player_id=?", (player_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
            return {
                "player_id": player_id,
                "rating": ELO_DEFAULT,
                "peak_rating": ELO_DEFAULT,
                "games_played": 0,
                "wins": 0,
                "losses": 0,
                "draws": 0,
            }


async def update_chess_elo_after_game(
    white_id: int, white_name: str, black_id: int, black_name: str, result: str
):
    """محاسبه و ذخیره‌ی Elo بعد از پایان یک بازی شطرنج زنده.
    خروجی: (new_white_rating, new_black_rating, change_white, change_black)"""
    from datetime import datetime
    now = datetime.now().isoformat()

    white_elo = await get_chess_player_elo(white_id)
    black_elo = await get_chess_player_elo(black_id)

    new_w, new_b, chg_w, chg_b = calculate_new_ratings(
        white_elo["rating"], black_elo["rating"],
        white_elo["games_played"], black_elo["games_played"],
        result
    )

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO chess_elo(player_id, display_name, rating, peak_rating, games_played,
                wins, losses, draws, last_updated)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                display_name=?,
                rating=?,
                peak_rating=MAX(peak_rating, ?),
                games_played=games_played+1,
                wins=wins+?,
                losses=losses+?,
                draws=draws+?,
                last_updated=?
        """, (
            white_id, white_name, new_w, new_w,
            1 if result == "white" else 0,
            1 if result == "black" else 0,
            1 if result == "draw" else 0,
            now,
            white_name, new_w, new_w,
            1 if result == "white" else 0,
            1 if result == "black" else 0,
            1 if result == "draw" else 0,
            now
        ))

        await db.execute("""
            INSERT INTO chess_elo(player_id, display_name, rating, peak_rating, games_played,
                wins, losses, draws, last_updated)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                display_name=?,
                rating=?,
                peak_rating=MAX(peak_rating, ?),
                games_played=games_played+1,
                wins=wins+?,
                losses=losses+?,
                draws=draws+?,
                last_updated=?
        """, (
            black_id, black_name, new_b, new_b,
            1 if result == "black" else 0,
            1 if result == "white" else 0,
            1 if result == "draw" else 0,
            now,
            black_name, new_b, new_b,
            1 if result == "black" else 0,
            1 if result == "white" else 0,
            1 if result == "draw" else 0,
            now
        ))
        await db.commit()

    return new_w, new_b, chg_w, chg_b


async def get_chess_elo_leaderboard(limit: int = 10) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM chess_elo ORDER BY rating DESC LIMIT ?", (limit,)
        ) as cur:
            return await cur.fetchall()


async def get_player_elo_history(player_id: int, limit: int = 10) -> list:
    """تاریخچه تغییرات Elo یک بازیکن"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT eh.*, p.full_name as opponent_name
            FROM elo_history eh
            LEFT JOIN players p ON eh.opponent_id = p.id
            WHERE eh.player_id = ?
            ORDER BY eh.recorded_at DESC
            LIMIT ?
        """, (player_id, limit)) as cur:
            return await cur.fetchall()


async def recalculate_all_elo():
    """
    بازسازیِ کاملِ Elo از صفر.

    چرا لازمه: بر خلاف wins/losses/draws (که یه شمارنده‌ی ساده‌ست و
    برگردوندنش فقط یعنی یکی کم کردن)، Elo زنجیره‌ایه — امتیاز هر مسابقه
    به امتیازِ *لحظه‌ی* قبلِ دو بازیکن بستگی داره. اگه یه مسابقه‌ی قدیمی
    حذف یا نتیجه‌ش اصلاح بشه، همه‌ی مسابقات *بعدی*ِ همون دو بازیکن هم
    باید با رتبه‌های درست از نو حساب بشن؛ کم/زیاد کردنِ ساده‌ی همون یک
    تغییر کافی نیست. راه درست: کل جدول player_elo/elo_history رو خالی و
    تمام مسابقاتِ دارای‌نتیجه رو به ترتیب تاریخ، از اول تا آخر، دوباره
    شبیه‌سازی می‌کنیم — دقیقاً همون‌طور که هر بار real-time محاسبه می‌شد.

    فعلاً بعد از هر delete_match_safely یا correct_match_result باید این
    تابع صدا زده بشه تا Elo هم مثل wins/losses/draws همیشه با matches
    همگام بمونه.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("DELETE FROM player_elo")
        await db.execute("DELETE FROM elo_history")
        await db.commit()

        async with db.execute(
            "SELECT id, white_player_id, black_player_id, result FROM matches "
            "WHERE result IN ('white','black','draw') ORDER BY created_at ASC, id ASC"
        ) as cur:
            rows = await cur.fetchall()

    replayed = 0
    for m in rows:
        await update_elo_after_match(m["white_player_id"], m["black_player_id"], m["result"], m["id"])
        replayed += 1
    return replayed
