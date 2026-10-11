"""
chess_challenge.py
جریان «درخواست بازی شطرنج» بین مدیران و مدیر ارشد (پیشوا):
مدیر درخواست می‌فرستد ← طرف مقابل قبول/رد می‌کند ← در صورت قبول،
یک بازی ساخته و دکمه‌ی ورود به مینی‌اپ برای هر دو طرف ارسال می‌شود.
"""

import logging
import random
import time
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import database as db
from chess_ai import AI_LEVELS, ai_display_name
from servers import is_boss as _boss, boss_chat as _boss_chat
from config import CHESS_AI_ID, PISHVA_ID, WEBAPP_URL, BOT_USERNAME
from game_server import expire_game_if_timed_out, maybe_play_ai_move, new_game_token
from helpers import safe_edit_message_text, box, pishva_display

logger = logging.getLogger(__name__)

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

# ─── نامِ واقعیِ پروفایلِ تلگرامی (نه نامِ ثبت‌شده‌ی ادمین) ──────────
# قبلاً نام/آواتاری که در شطرنج زنده نشان داده می‌شد از روی display_name/
# full_name ثبت‌شده در جدولِ admins بود (همان اسمی که موقعِ ثبت‌نام در ربات
# وارد شده)، نه پروفایلِ واقعیِ تلگرامِ کاربر. آواتار از قبل با
# get_user_profile_photos درست از تلگرام گرفته می‌شد؛ این تابع همان کار را
# برای «نام» هم انجام می‌دهد (get_chat → first_name/last_name/username) تا
# نام و عکس هر دو دقیقاً همان چیزی باشند که کاربر الان در تلگرامش دارد.
# با همان الگوی کش TTL کوتاه (مثل آواتار در game_server.py) تا هر
# فراخوانی باعثِ یک درخواستِ جدید به تلگرام نشود.
_name_cache = {}  # user_id -> (name_or_None, monotonic_expiry)
_NAME_CACHE_TTL = 300
_NAME_CACHE_TTL_EMPTY = 30


async def _telegram_profile_name(bot, user_id: int):
    if bot is None or not user_id:
        return None
    cached = _name_cache.get(user_id)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    name = None
    try:
        chat = await bot.get_chat(user_id)
        first = (chat.first_name or "").strip()
        last = (chat.last_name or "").strip()
        full = (first + " " + last).strip()
        name = full or (f"@{chat.username}" if chat.username else None)
    except TelegramError as e:
        logger.debug("Telegram error resolving live profile name for %s: %s", user_id, e)
    except Exception:
        logger.exception("Failed to resolve live Telegram profile name for %s", user_id)
    ttl = _NAME_CACHE_TTL if name else _NAME_CACHE_TTL_EMPTY
    _name_cache[user_id] = (name, time.monotonic() + ttl)
    return name


async def _active_game_for(uid: int):
    """بازیِ فعالِ واقعیِ کاربر (یا None).

    قبلاً یک بازیِ رهاشده (کاربر مینی‌اپ را وسطِ بازی بسته و ساعتش تمام
    شده) تا ابد «active» می‌ماند و ربات مدام می‌گفت «بازیِ فعال داری».
    نگهبانِ پس‌زمینه‌ی game_server معمولاً چنین بازی‌ای را همان لحظه‌ی
    اتمامِ زمان می‌بندد؛ این چک ایمنیِ دوم است تا اگر نگهبان هنوز به آن
    بازی نرسیده بود هم، قبل از هر تصمیمی بر پایه‌ی «بازیِ فعال»، بازیِ
    ساعت‌تمام‌شده خودکار بسته شود و کاربر مجبور به ورود و «بستن» نباشد."""
    game = None
    for _ in range(5):  # ممکن است (به‌خاطرِ باگ‌های قدیمی) چند ردیفِ active باشد
        game = await db.get_active_chess_game_for(uid)
        if not game or not await expire_game_if_timed_out(game):
            return game
    return await db.get_active_chess_game_for(uid)


async def _chess_block_reason(uid: int) -> str:
    """اگر شطرنج زنده برای این کاربر مسدود باشد، متن پیام مناسب را برمی‌گرداند؛
    در غیر این صورت رشته‌ی خالی (یعنی مجاز است).

    FIX: قبلاً این ۳ چک (قفل‌بودنِ کلی / خاموشیِ دستیِ مدیر ارشد / دسترسیِ
    خودِ ادمین) پشتِ‌سرِهم صدا زده می‌شدن؛ برای بیشترِ ادمین‌ها (که هر سه‌تا
    باید چک بشه تا مجاز بشناسیمش) این یعنی ۳ رفت‌وبرگشتِ شبکه‌ایِ پشتِ‌سرِهم
    فقط برای باز کردنِ منوی شطرنج. حالا هر سه هم‌زمان با asyncio.gather
    خونده می‌شن؛ اولویتِ پیام‌ها (قفلِ کلی > خاموشیِ دستی > بی‌دسترسی) دقیقاً
    مثل قبل حفظ شده، فقط زمانِ صبر کمتر شده."""
    if _boss(uid):
        if await db.is_chess_locked_by_status():
            return (
                f"{box('♟️ شطرنج زنده غیرفعال است')}\n\n"
                "🔴 به‌دلیل وضعیت امنیتی فعلی سیستم (خطرناک/APS)، این بخش به‌طور کامل از کار افتاده است.\n"
                "به‌محض بازگشت وضعیت به «بد» یا «نرمال»، دوباره در دسترس قرار می‌گیرد."
            )
        return ""

    locked, switched_off, can_use = await asyncio.gather(
        db.is_chess_locked_by_status(),
        db.is_chess_admin_switch_off(),
        db.can_use_live_chess(uid),
    )
    if locked:
        return (
            f"{box('♟️ شطرنج زنده غیرفعال است')}\n\n"
            "🔴 به‌دلیل وضعیت امنیتی فعلی سیستم (خطرناک/APS)، این بخش به‌طور کامل از کار افتاده است.\n"
            "به‌محض بازگشت وضعیت به «بد» یا «نرمال»، دوباره در دسترس قرار می‌گیرد."
        )
    if switched_off:
        return (
            f"{box('♟️ شطرنج زنده غیرفعال است')}\n\n"
            "⛔ این بخش در حال حاضر توسط مدیر ارشد خاموش شده است."
        )
    if not can_use:
        return (
            f"{box('♟️ شطرنج زنده غیرفعال است')}\n\n"
            "⛔ شما دسترسی استفاده از شطرنج زنده را ندارید. برای فعال‌سازی با مدیر ارشد در ارتباط باشید."
        )
    return ""


async def _display_name(user_id: int, bot=None) -> str:
    if _boss(user_id):
        return await pishva_display()
    tg_name = await _telegram_profile_name(bot, user_id)
    if tg_name:
        return tg_name
    admin = await db.get_admin(user_id)
    if admin:
        return admin["display_name"] or admin["full_name"]
    return "ادمین"


async def _eligible_opponents(requester_id: int):
    """پیشوا + همه‌ی مدیران فعال به‌جز خود درخواست‌دهنده.
    FIX: pishva_display() و get_active_admins() به هم وابسته نیستن؛ قبلاً
    پشتِ‌سرِهم صدا زده می‌شدن، حالا هم‌زمان."""
    opponents = []
    if not _boss(requester_id):
        pname, admins = await asyncio.gather(pishva_display(), db.get_active_admins())
        opponents.append((_boss_chat(), pname))
    else:
        admins = await db.get_active_admins()
    for a in admins:
        if a["telegram_id"] != requester_id:
            opponents.append((a["telegram_id"], a["display_name"] or a["full_name"]))
    return opponents


async def _chess_menu_content(uid: int, chat_type: str = "private", bot=None):
    """محتوای (متن، کیبورد) منوی شطرنج زنده رو می‌سازه.
    هم دکمه‌ی «♟️ شطرنج زنده» (کال‌بک) و هم کلمه‌ی کلیدی «بازی»/«شطرنج»
    (پیام متنی) از همین تابع محتوا می‌گیرن تا دقیقاً یک چیز نمایش داده بشه.

    chat_type/bot لازمه چون توی گروه/سوپرگروه، تلگرام اصلاً اجازه‌ی
    دکمه‌ی وب‌اپ (web_app) رو نمی‌ده (خطای Button_type_invalid)؛ پس اگه
    این منو داخل گروه باز شده باشه، به‌جای دکمه‌های ورود/تماشای مستقیم،
    دکمه‌ی لینک به پیوی ربات گذاشته می‌شه.

    خروجی: (text, markup, reason)
    - موفق: (text, markup, None)
    - مسدود: (None, None, پیام‌دلیل)
    """
    reason = await _chess_block_reason(uid)
    if reason:
        return None, None, reason

    is_group = chat_type in ("group", "supergroup")
    # FIX: resolve_bot_username (فقط لازمِ حالتِ گروه) و گرفتنِ بازیِ فعالِ
    # کاربر، به هم وابسته نیستن؛ حالا هم‌زمان اجرا می‌شن به‌جای پشتِ‌سرِهم.
    if is_group:
        bot_username, active_game = await asyncio.gather(
            _resolve_bot_username(bot), _active_game_for(uid)
        )
    else:
        bot_username = None
        active_game = await _active_game_for(uid)

    if active_game:
        text = (
            f"{box('♟️ شطرنج زنده')}\n\n"
            f"⏳ شما یک بازی فعال دارید. برای ادامه، روی دکمه‌ی زیر بزنید:"
        )
        return text, _kb_play_for_chat(active_game["token"], is_group, bot_username), None

    # FIX: فهرستِ حریف‌های مجاز و فهرستِ بازی‌های دیگرانِ درحالِ‌اجرا هم به هم
    # وابسته نیستن؛ هم‌زمان اجرا می‌شن.
    opponents, other_games = await asyncio.gather(
        _eligible_opponents(uid), db.get_active_chess_games_excluding(uid)
    )

    rows = []
    rows.append([InlineKeyboardButton("🤖 بازی با هوش مصنوعی", callback_data="chessai_menu")])
    rows.append([InlineKeyboardButton("📋 بازی‌های فعال", callback_data="chess_active_games")])
    rows.append([InlineKeyboardButton("📜 لیست بازی‌ها", callback_data="chess_history")])
    for opp_id, name in opponents:
        label = ("👑 " if _boss(opp_id) else "🎖️ ") + name
        rows.append([InlineKeyboardButton(label, callback_data=f"chess_req_{opp_id}")])

    if other_games and (WEBAPP_URL or is_group):
        for g in other_games:
            label = f"👁 تماشا: ⚪ {g['white_name']} در مقابل ⚫ {g['black_name']}"
            btn = _watch_button(label, g["token"], is_group, bot_username)
            if btn:
                rows.append([btn])

    rows.append([InlineKeyboardButton("🏆 جدول Elo شطرنج زنده", callback_data="chess_elo_board")])
    if uid == PISHVA_ID:
        # فقط مدیر ارشد: مرورِ سوابقِ چتِ مدیران با حریفِ هوش‌مصنوعی (chess_ai_history.py)
        rows.append([InlineKeyboardButton("🗂️ سوابق چت با هوش مصنوعی", callback_data="chess_ai_admlog_menu")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")])

    text = f"{box('♟️ شطرنج زنده')}\n\n"
    if opponents:
        text += "می‌توانید با هوش مصنوعی تمرین کنید یا با یکی از مدیران محترم/مدیر ارشد وارد یک بازی زنده شوید.\nحریف خود را انتخاب کنید:"
    else:
        text += "می‌توانید با هوش مصنوعی تمرین کنید. در حال حاضر مدیر دیگری برای دعوت به بازی پیدا نشد."
    if other_games:
        text += "\n\n👁 یا یکی از بازی‌های در حال انجام را زنده تماشا کنید:"

    return text, InlineKeyboardMarkup(rows), None


async def chess_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    await query.answer()

    chat_type = query.message.chat.type if query.message else "private"
    text, markup, reason = await _chess_menu_content(uid, chat_type, ctx.bot)
    if reason:
        await safe_edit_message_text(query, reason, reply_markup=_kb_back(), parse_mode=ParseMode.MARKDOWN)
        return

    await safe_edit_message_text(query, text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)


async def chess_menu_from_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """باز کردن منوی شطرنج زنده با کلمه‌ی کلیدی («بازی» یا «شطرنج») در پیام متنی
    (نه دکمه). پیامِ ارسال‌شده رو برمی‌گردونه تا در صورت نیاز (داخل گروه)
    مالکیتِ پنل براش ثبت بشه؛ اگه مسدود بود (و فقط متنِ ساده فرستاده شد)
    None برمی‌گردونه."""
    uid = update.effective_user.id
    chat_type = update.effective_chat.type if update.effective_chat else "private"
    text, markup, reason = await _chess_menu_content(uid, chat_type, ctx.bot)
    if reason:
        await update.message.reply_text(reason, parse_mode=ParseMode.MARKDOWN)
        return None
    return await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)


async def chess_challenge_target_from_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE, target_id: int, target_name: str = None):
    """وقتی کاربر روی یک نفر (یا روی پیامی حاوی آیدی) ریپلای می‌زنه و
    کلمه‌ی «بازی»/«شطرنج» رو می‌فرسته: به‌جای نمایش کل لیست حریف‌ها،
    مستقیم می‌ره سراغ انتخاب زمانِ بازی با همون شخص (معادل زدن دکمه‌ی
    chess_req_{target_id} در منوی معمولی) — دقیقاً همون بررسی‌هایی که
    chess_pick_time انجام می‌ده، ولی این‌جا با reply_text به‌جای
    ویرایشِ پیامِ کال‌بک.
    پیامِ ارسالی رو برمی‌گردونه تا مالکیتِ پنل (در گروه) براش ثبت بشه؛
    در صورتِ مسدود بودن/نامعتبر بودن، None برمی‌گردونه."""
    uid = update.effective_user.id

    reason = await _chess_block_reason(uid)
    if reason:
        await update.message.reply_text(reason, parse_mode=ParseMode.MARKDOWN)
        return None
    if target_id == uid:
        await update.message.reply_text("⛔ نمی‌توانید به خودتان درخواست بازی بدهید.")
        return None
    if target_id == CHESS_AI_ID:
        await update.message.reply_text("⛔ برای بازی با هوش مصنوعی از منوی «بازی» استفاده کنید.")
        return None
    # حریف باید پیشوا یا مدیرِ فعال باشه — همون فهرستِ مجازِ منوی معمولی
    eligible_ids = {oid for oid, _ in await _eligible_opponents(uid)}
    if target_id not in eligible_ids:
        await update.message.reply_text(
            "⛔ این کاربر مدیرِ فعال یا مدیر ارشد نیست و نمی‌توان به او درخواست بازی داد."
        )
        return None
    if await db.has_pending_chess_request(uid, target_id):
        await update.message.reply_text("⏳ درخواست قبلی برای همین شخص هنوز در انتظار پاسخ است.")
        return None
    active_game = await _active_game_for(uid)
    if active_game:
        await update.message.reply_text("⛔ شما یک بازی فعال دارید؛ ابتدا آن را تمام کنید.")
        return None

    name = target_name or await _display_name(target_id, ctx.bot)
    rows = [
        [InlineKeyboardButton(label, callback_data=f"chess_time_{target_id}_{secs}")]
        for secs, label in TIME_CONTROLS
    ]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="chess_menu")])
    return await update.message.reply_text(
        f"{box('♟️ شطرنج زنده')}\n\n"
        f"🎯 حریف: {name}\n"
        f"⏱ زمان فکر هر طرف را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.MARKDOWN,
    )


async def chess_active_games(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """بخشِ «📋 بازی‌های فعال»: همه‌ی بازی‌های شطرنجِ زنده‌ی در حال انجام
    را یک‌جا نشان می‌دهد — چه مدیر×مدیر، چه مدیر×پیشوا، چه هرکدام در
    مقابلِ هوش مصنوعی (برخلافِ لیستِ «تماشا»ی داخلِ منوی اصلی که فقط
    بازی‌های انسان×انسانِ دیگران را نشان می‌داد)."""
    query = update.callback_query
    uid = query.from_user.id
    await query.answer()

    chat_type = query.message.chat.type if query.message else "private"
    is_group = chat_type in ("group", "supergroup")
    bot_username = await _resolve_bot_username(ctx.bot) if is_group else None

    games = await db.get_all_active_chess_games()
    rows = []
    if not games:
        text = f"{box('📋 بازی‌های فعال')}\n\nدر حال حاضر هیچ بازیِ در جریانی وجود ندارد."
    else:
        text = f"{box('📋 بازی‌های فعال')}\n\n📌 همه‌ی بازی‌های شطرنجِ زنده‌ی در حال انجام:"
        for g in games:
            is_mine = str(uid) in (str(g["white_id"]), str(g["black_id"]))
            label = f"{'▶️ ادامه' if is_mine else '👁 تماشا'}: ⚪ {g['white_name']}  ×  ⚫ {g['black_name']}"
            btn = (_play_button if is_mine else _watch_button)(label, g["token"], is_group, bot_username)
            if btn:
                rows.append([btn])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="chess_menu")])

    await safe_edit_message_text(
        query, text, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN,
    )


async def chess_elo_board(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    reason = await _chess_block_reason(uid)
    if reason:
        await query.answer()
        await safe_edit_message_text(query, reason, reply_markup=_kb_back(), parse_mode=ParseMode.MARKDOWN)
        return
    await query.answer()
    from elo import ensure_chess_elo_table, get_chess_elo_leaderboard, get_elo_title

    await ensure_chess_elo_table()
    rows = await get_chess_elo_leaderboard(15)
    if not rows:
        text = f"{box('🏆 جدول Elo شطرنج زنده')}\n\nهنوز هیچ بازی‌ای ثبت نشده است."
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, r in enumerate(rows):
            medal = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{medal} {r['display_name']} — `{int(r['rating'])}` ({get_elo_title(r['rating'])})")
        text = f"{box('🏆 جدول Elo شطرنج زنده')}\n\n" + "\n".join(lines)

    await safe_edit_message_text(
        query, text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="chess_menu")]]),
        parse_mode=ParseMode.MARKDOWN,
    )


TIME_CONTROLS = [
    (180, "۳ دقیقه"),
    (300, "۵ دقیقه"),
    (600, "۱۰ دقیقه"),
    (900, "۱۵ دقیقه"),
    (1800, "۳۰ دقیقه"),
]
COLOR_LABELS = {"w": "⚪ سفید", "b": "⚫ سیاه", "r": "🎲 تصادفی"}


async def chess_pick_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """قدم اول بعد از انتخاب حریف: میزبان زمان بازی را انتخاب می‌کند."""
    query = update.callback_query
    uid = query.from_user.id
    target_id = int(query.data.split("_")[-1])

    reason = await _chess_block_reason(uid)
    if reason:
        await query.answer()
        await safe_edit_message_text(query, reason, reply_markup=_kb_back(), parse_mode=ParseMode.MARKDOWN)
        return
    if target_id == uid:
        await query.answer("نمی‌توانید به خودتان درخواست بدهید.", show_alert=True)
        return
    if await db.has_pending_chess_request(uid, target_id):
        await query.answer("درخواست قبلی هنوز در انتظار پاسخ است.", show_alert=True)
        return
    active_game = await _active_game_for(uid)
    if active_game:
        await query.answer("شما یک بازی فعال دارید؛ ابتدا آن را تمام کنید.", show_alert=True)
        return

    await query.answer()
    rows = [
        [InlineKeyboardButton(label, callback_data=f"chess_time_{target_id}_{secs}")]
        for secs, label in TIME_CONTROLS
    ]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="chess_menu")])
    await safe_edit_message_text(
        query,
        f"{box('♟️ شطرنج زنده')}\n\n⏱ زمان فکر هر طرف را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.MARKDOWN,
    )


async def chess_pick_color(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """قدم دوم: میزبان رنگ مهره‌های خودش را انتخاب می‌کند."""
    query = update.callback_query
    uid = query.from_user.id
    reason = await _chess_block_reason(uid)
    if reason:
        await query.answer()
        await safe_edit_message_text(query, reason, reply_markup=_kb_back(), parse_mode=ParseMode.MARKDOWN)
        return
    await query.answer()
    _, _, target_id, secs = query.data.split("_")
    rows = [
        [InlineKeyboardButton(COLOR_LABELS[c], callback_data=f"chess_go_{target_id}_{secs}_{c}")]
        for c in ("w", "b", "r")
    ]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="chess_menu")])
    await safe_edit_message_text(
        query,
        f"{box('♟️ شطرنج زنده')}\n\nبا کدام رنگ بازی می‌کنید؟",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.MARKDOWN,
    )


async def chess_send_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    _, _, target_id, secs, color = query.data.split("_")
    target_id = int(target_id)
    time_control = int(secs)

    if target_id == uid:
        await query.answer("نمی‌توانید به خودتان درخواست بدهید.", show_alert=True)
        return

    reason = await _chess_block_reason(uid)
    if reason:
        await query.answer()
        await safe_edit_message_text(query, reason, reply_markup=_kb_back(), parse_mode=ParseMode.MARKDOWN)
        return

    if await db.has_pending_chess_request(uid, target_id):
        await query.answer("درخواست قبلی هنوز در انتظار پاسخ است.", show_alert=True)
        return

    active_game = await _active_game_for(uid)
    if active_game:
        await query.answer("شما یک بازی فعال دارید؛ ابتدا آن را تمام کنید.", show_alert=True)
        return

    await query.answer("درخواست ارسال شد ✅")
    req_id = await db.create_chess_request(uid, target_id, time_control, color)
    requester_name = await _display_name(uid, ctx.bot)
    time_label = next((lbl for s, lbl in TIME_CONTROLS if s == time_control), f"{time_control // 60} دقیقه")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ قبول", callback_data=f"chess_acc_{req_id}"),
        InlineKeyboardButton("❌ رد", callback_data=f"chess_dec_{req_id}"),
    ]])
    try:
        await ctx.bot.send_message(
            chat_id=target_id,
            text=f"{box('♟️ درخواست بازی شطرنج')}\n\n"
                 f"{requester_name} از شما درخواست یک بازی شطرنج زنده دارد.\n"
                 f"⏱ زمان: {time_label}\n"
                 f"آیا قبول می‌کنید؟",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        logger.exception("Failed to deliver chess request to %s", target_id)

    await safe_edit_message_text(
        query,
        f"{box('♟️ شطرنج زنده')}\n\n"
        f"⏳ درخواست بازی برای {await _display_name(target_id, ctx.bot)} ارسال شد.\n"
        f"به محض پاسخ ایشان به شما اطلاع داده می‌شود.",
        reply_markup=_kb_back(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def chess_accept(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    req_id = int(query.data.split("_")[-1])
    req = await db.get_chess_request(req_id)

    if not req or req["status"] != "pending" or req["target_id"] != uid:
        await query.answer("این درخواست دیگر معتبر نیست.", show_alert=True)
        return

    reason = await _chess_block_reason(uid)
    if reason:
        await query.answer()
        await safe_edit_message_text(query, reason, reply_markup=_kb_back(), parse_mode=ParseMode.MARKDOWN)
        return

    requester_id = req["requester_id"]

    # جلوگیری از باگ «بعد از تمومِ یه بازی، هنوز میشه وارد بازی قبلی شد»:
    # اگر یکی از دو طرف از قبل یک بازی فعال دیگر داشته باشد (مثلاً همزمان چند
    # درخواست قبول شده)، دو ردیف active در chess_games ساخته می‌شد و
    # get_active_chess_game_for گاهی بازیِ قدیمی و تمام‌نشده را برمی‌گرداند.
    # پس قبل از ساخت بازی جدید، فعال نبودنِ بازی برای هر دو طرف را می‌سنجیم.
    if await _active_game_for(uid):
        await query.answer("شما یک بازی فعال دیگر دارید؛ ابتدا آن را تمام کنید.", show_alert=True)
        return
    if await _active_game_for(requester_id):
        await query.answer("درخواست‌دهنده در حال حاضر یک بازی فعال دیگر دارد.", show_alert=True)
        return

    await query.answer()
    await db.set_chess_request_status(req_id, "accepted")
    # هر درخواست pending دیگری بین همین دو نفر (در هر دو جهت) دیگر معنا ندارد؛
    # منقضی‌اش می‌کنیم تا برای همیشه به‌عنوان «در انتظار پاسخ» گیر نکند.
    await db.expire_other_chess_requests(requester_id, uid, req_id)

    requester_name = await _display_name(requester_id, ctx.bot)
    accepter_name = await _display_name(uid, ctx.bot)
    time_control = req["time_control"] or 300
    req_color = req["requester_color"] or "random"
    # دکمه‌ی «🎲 تصادفی» مقدار "r" را ذخیره می‌کند (نه "random")؛ قبلاً فقط
    # "random" چک می‌شد، برای همین "r" هیچ‌وقت قرعه‌کشی نمی‌شد و درخواست‌دهنده
    # همیشه مهره‌ی سیاه می‌گرفت.
    if req_color in ("random", "r"):
        req_color = random.choice(["w", "b"])
    if req_color == "w":
        white_id, black_id = requester_id, uid
        white_name, black_name = requester_name, accepter_name
    else:
        white_id, black_id = uid, requester_id
        white_name, black_name = accepter_name, requester_name

    token = new_game_token()
    await db.create_chess_game(token, white_id, black_id, white_name, black_name, START_FEN, time_control)

    requester_is_white = white_id == requester_id
    accepter_msg = await safe_edit_message_text(
        query,
        f"{box('♟️ بازی پذیرفته شد!')}\n\nبازی بین شما و {requester_name} آغاز شد. "
        f"شما با مهره‌های {'سیاه' if requester_is_white else 'سفید'} بازی می‌کنید.",
        reply_markup=_kb_play(token),
        parse_mode=ParseMode.MARKDOWN,
    )
    requester_msg = None
    try:
        requester_msg = await ctx.bot.send_message(
            chat_id=requester_id,
            text=f"{box('♟️ درخواست شما پذیرفته شد!')}\n\n"
                 f"{accepter_name} درخواست بازی شما را قبول کرد. "
                 f"شما با مهره‌های {'سفید' if requester_is_white else 'سیاه'} بازی می‌کنید"
                 f"{'، نوبت شماست.' if requester_is_white else '.'}",
            reply_markup=_kb_play(token),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        logger.exception("Failed to notify requester %s", requester_id)

    # ذخیره‌ی آی‌دیِ همین دو پیام (که دکمه‌ی «ورود به بازی» را دارند) تا وقتی
    # بازی تمام شد بشود آن‌ها را به یک پنل «بازی جدید / منوی اصلی» تازه
    # ویرایش کرد؛ رفعِ باگِ «دکمه‌ی ورود به بازیِ قدیمی برای همیشه در چت
    # می‌ماند و به بخش شروع بازی جدید آپدیت نمی‌شود».
    try:
        accepter_msg_id = accepter_msg.message_id if accepter_msg else None
        requester_msg_id = requester_msg.message_id if requester_msg else None
        if uid == white_id:
            white_msg_id, black_msg_id = accepter_msg_id, requester_msg_id
        else:
            white_msg_id, black_msg_id = requester_msg_id, accepter_msg_id
        await db.set_chess_game_messages(token, white_msg_id, black_msg_id)
    except Exception:
        logger.exception("Failed to store chess game message ids for %s", token)

    await _announce_game_to_others(ctx, requester_id, uid, white_name, black_name, token)


async def _announce_game_to_others(ctx, player1_id, player2_id, white_name, black_name, token):
    """به بقیه‌ی مدیران (و پیشوا، در صورتی که خودش بازیکن نباشد) خبر می‌دهد که
    یک بازی شطرنج زنده در جریان است و امکان تماشا و ارسال پیام را می‌دهد."""
    notify_ids = set()
    if not _boss(player1_id) and not _boss(player2_id):
        notify_ids.add(_boss_chat())
    admins = await db.get_active_admins()
    for a in admins:
        tid = a["telegram_id"]
        if tid not in (player1_id, player2_id):
            notify_ids.add(tid)

    if not notify_ids:
        return
    text = (
        f"{box('♟️ یک بازی شطرنج زنده در جریان است')}\n\n"
        f"⚪ {white_name}  در مقابل  ⚫ {black_name}\n\n"
        "می‌توانید بازی را زنده تماشا کنید و حتی در گفتگوی آن پیام بدهید."
    )
    for tid in notify_ids:
        try:
            await ctx.bot.send_message(
                chat_id=tid, text=text, reply_markup=_kb_spectate(token), parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            logger.exception("Failed to notify %s about ongoing chess game", tid)


async def chess_decline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    req_id = int(query.data.split("_")[-1])
    req = await db.get_chess_request(req_id)

    if not req or req["status"] != "pending" or req["target_id"] != uid:
        await query.answer("این درخواست دیگر معتبر نیست.", show_alert=True)
        return

    await query.answer("درخواست رد شد.")
    await db.set_chess_request_status(req_id, "declined")
    await safe_edit_message_text(
        query,
        f"{box('♟️ درخواست رد شد')}\n\nشما این درخواست بازی را رد کردید.",
        parse_mode=ParseMode.MARKDOWN,
    )
    try:
        await ctx.bot.send_message(
            chat_id=req["requester_id"],
            text=f"{box('♟️ درخواست رد شد')}\n\n{await _display_name(uid, ctx.bot)} درخواست بازی شما را رد کرد.",
        )
    except Exception:
        logger.exception("Failed to notify decline to %s", req["requester_id"])


async def chess_ai_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """دکمه‌ی «🤖 بازی با هوش مصنوعی» در پنل شطرنج زنده: انتخاب سطح سختی."""
    query = update.callback_query
    uid = query.from_user.id
    reason = await _chess_block_reason(uid)
    if reason:
        await query.answer()
        await safe_edit_message_text(query, reason, reply_markup=_kb_back(), parse_mode=ParseMode.MARKDOWN)
        return
    active_game = await _active_game_for(uid)
    if active_game:
        await query.answer("شما یک بازی فعال دارید؛ ابتدا آن را تمام کنید.", show_alert=True)
        return
    await query.answer()
    rows = [
        [InlineKeyboardButton(info["label"], callback_data=f"chessai_time_{level}")]
        for level, info in AI_LEVELS.items()
    ]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="chess_menu")])
    await safe_edit_message_text(
        query,
        f"{box('🤖 بازی با هوش مصنوعی')}\n\nسطح سختی هوش مصنوعی را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.MARKDOWN,
    )


async def chess_ai_pick_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """قدم دوم: زمان فکر انتخاب می‌شود."""
    query = update.callback_query
    uid = query.from_user.id
    reason = await _chess_block_reason(uid)
    if reason:
        await query.answer()
        await safe_edit_message_text(query, reason, reply_markup=_kb_back(), parse_mode=ParseMode.MARKDOWN)
        return
    await query.answer()
    level = query.data.split("_")[-1]
    rows = [
        [InlineKeyboardButton(label, callback_data=f"chessai_color_{level}_{secs}")]
        for secs, label in TIME_CONTROLS
    ]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="chessai_menu")])
    await safe_edit_message_text(
        query,
        f"{box('🤖 بازی با هوش مصنوعی')}\n\n⏱ زمان فکر خودتان را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.MARKDOWN,
    )


async def chess_ai_pick_color(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """قدم سوم: رنگِ مهره‌های کاربر انتخاب می‌شود."""
    query = update.callback_query
    uid = query.from_user.id
    reason = await _chess_block_reason(uid)
    if reason:
        await query.answer()
        await safe_edit_message_text(query, reason, reply_markup=_kb_back(), parse_mode=ParseMode.MARKDOWN)
        return
    await query.answer()
    _, _, level, secs = query.data.split("_")
    rows = [
        [InlineKeyboardButton(COLOR_LABELS[c], callback_data=f"chessai_go_{level}_{secs}_{c}")]
        for c in ("w", "b", "r")
    ]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="chessai_menu")])
    await safe_edit_message_text(
        query,
        f"{box('🤖 بازی با هوش مصنوعی')}\n\nبا کدام رنگ بازی می‌کنید؟",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.MARKDOWN,
    )


async def chess_ai_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """قدم آخر: بازی بلافاصله ساخته می‌شود (بدون نیاز به قبول‌کردن، چون
    طرف مقابل هوش مصنوعی است) و در صورتی که نوبت اول با هوش مصنوعی باشد،
    حرکت اول آن هم بلافاصله انجام می‌شود."""
    query = update.callback_query
    uid = query.from_user.id
    reason = await _chess_block_reason(uid)
    if reason:
        await query.answer()
        await safe_edit_message_text(query, reason, reply_markup=_kb_back(), parse_mode=ParseMode.MARKDOWN)
        return
    active_game = await _active_game_for(uid)
    if active_game:
        await query.answer("شما یک بازی فعال دارید؛ ابتدا آن را تمام کنید.", show_alert=True)
        return

    _, _, level, secs, color = query.data.split("_")
    time_control = int(secs)
    if color == "r":
        color = random.choice(["w", "b"])

    await query.answer("بازی ساخته شد ✅")
    human_name = await _display_name(uid, ctx.bot)
    ai_name = ai_display_name(level)
    if color == "w":
        white_id, black_id = uid, CHESS_AI_ID
        white_name, black_name = human_name, ai_name
    else:
        white_id, black_id = CHESS_AI_ID, uid
        white_name, black_name = ai_name, human_name

    token = new_game_token()
    await db.create_chess_game(token, white_id, black_id, white_name, black_name, START_FEN, time_control, ai_level=level)

    chat_type = query.message.chat.type if query.message else "private"
    is_group = chat_type in ("group", "supergroup")
    bot_username = await _resolve_bot_username(ctx.bot) if is_group else None

    msg = await safe_edit_message_text(
        query,
        f"{box('🤖 بازی با هوش مصنوعی شروع شد!')}\n\n"
        f"شما با مهره‌های {'سفید' if color == 'w' else 'سیاه'} در مقابل {ai_name} بازی می‌کنید.",
        reply_markup=_kb_play_for_chat(token, is_group, bot_username),
        parse_mode=ParseMode.MARKDOWN,
    )
    try:
        msg_id = msg.message_id if msg else None
        if color == "w":
            await db.set_chess_game_messages(token, white_msg_id=msg_id, black_msg_id=None)
        else:
            await db.set_chess_game_messages(token, white_msg_id=None, black_msg_id=msg_id)
    except Exception:
        logger.exception("Failed to store chess game message id for AI game %s", token)

    if white_id == CHESS_AI_ID:
        # نوبت اول با هوش مصنوعی است؛ حرکتش را همین الان انجام بده تا وقتی
        # کاربر وارد مینی‌اپ می‌شود، صفحه از قبل حرکتِ اول را نشان بدهد.
        await maybe_play_ai_move(token)

    # مثلِ بازیِ انسان×انسان، به بقیه‌ی مدیران/پیشوا هم خبر بده که این
    # بازی (این‌بار مقابلِ هوش مصنوعی) شروع شد — طبقِ درخواستِ کاربر که
    # این اعلان باید برای هر ترکیبی (مدیر/پیشوا × مدیر/پیشوا/هوش‌مصنوعی)
    # فرستاده شود، نه فقط بازی‌های انسان×انسان.
    await _announce_game_to_others(ctx, uid, CHESS_AI_ID, white_name, black_name, token)


def _kb_play(token: str) -> InlineKeyboardMarkup:
    if not WEBAPP_URL:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]])
    url = f"{WEBAPP_URL}/webapp/?token={token}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("♟️ ورود به بازی", web_app=WebAppInfo(url=url))],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ])


def _kb_resume(token: str) -> InlineKeyboardMarkup:
    return _kb_play(token)


def _kb_spectate(token: str) -> InlineKeyboardMarkup:
    if not WEBAPP_URL:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]])
    url = f"{WEBAPP_URL}/webapp/?token={token}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 تماشای بازی", web_app=WebAppInfo(url=url))],
    ])


_cached_bot_username = None  # کشِ سطحِ پروسه — یک‌بار از تلگرام می‌پرسیم و دیگه تکرار نمی‌کنیم


async def _resolve_bot_username(bot) -> str:
    """یوزرنیم ربات رو برمی‌گردونه (برای ساختِ لینکِ دیپ‌لینک به پیوی).
    اول از config.BOT_USERNAME می‌خونه، بعد از کشِ حافظه، و فقط اگه هیچ‌کدوم
    نبود یک بار از خودِ تلگرام می‌پرسه (get_me) و نتیجه رو کش می‌کنه.
    قبلاً این get_me هر بار (روی هر کلمه‌ی کلیدی توی گروه) صدا زده می‌شد
    که باعثِ کندیِ محسوسِ دکمه‌های کلمه‌ای می‌شد."""
    global _cached_bot_username
    if BOT_USERNAME:
        return BOT_USERNAME
    if _cached_bot_username:
        return _cached_bot_username
    if bot is None:
        return ""
    try:
        me = await bot.get_me()
        _cached_bot_username = me.username or ""
        return _cached_bot_username
    except Exception:
        logger.exception("Failed to resolve bot username")
        return ""


def _kb_play_for_chat(token: str, is_group: bool, bot_username: str = None) -> InlineKeyboardMarkup:
    """کیبورد «ورود به بازی» — توی پیوی مستقیم دکمه‌ی وب‌اپ، توی گروه/
    سوپرگروه (که تلگرام دکمه‌ی وب‌اپ رو قبول نمی‌کنه و Button_type_invalid
    می‌ده) به‌جاش یک دکمه‌ی لینک به پیوی ربات."""
    if not is_group:
        return _kb_play(token)
    if not bot_username:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔒 ورود به بازی (در پیوی)", url=f"https://t.me/{bot_username}?start=chess_enter_{token}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ])


def _kb_spectate_for_chat(token: str, is_group: bool, bot_username: str = None) -> InlineKeyboardMarkup:
    """کیبورد «تماشای بازی» — نسخه‌ی گروه‌سازگارِ _kb_spectate."""
    if not is_group:
        return _kb_spectate(token)
    if not bot_username:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔒 تماشای بازی (در پیوی)", url=f"https://t.me/{bot_username}?start=chess_watch_{token}")],
    ])


def _play_button(label: str, token: str, is_group: bool, bot_username: str = None):
    """دکمه‌ی «ادامه/ورود به بازیِ خودم» در لیستِ «📋 بازی‌های فعال» —
    نسخه‌ی تک‌دکمه‌ایِ _kb_play_for_chat، برای وقتی که این دکمه باید
    کنارِ دکمه‌های بازی‌های دیگران در یک لیست بیاید (نه به‌تنهایی یک
    کیبورد کامل)."""
    if is_group:
        if not bot_username:
            return None
        return InlineKeyboardButton(label, url=f"https://t.me/{bot_username}?start=chess_enter_{token}")
    if not WEBAPP_URL:
        return None
    url = f"{WEBAPP_URL}/webapp/?token={token}"
    return InlineKeyboardButton(label, web_app=WebAppInfo(url=url))


def _watch_button(label: str, token: str, is_group: bool, bot_username: str = None):
    """دکمه‌ی «تماشا»یِ یک بازیِ در حال انجام رو می‌سازه؛ توی گروه (که
    وب‌اپ مجاز نیست) به لینکِ پیوی تبدیل می‌شه. اگه توی گروه بودیم و
    یوزرنیمِ ربات هم در دسترس نبود، دکمه‌ای ساخته نمی‌شه (None)."""
    if is_group:
        if not bot_username:
            return None
        return InlineKeyboardButton(label, url=f"https://t.me/{bot_username}?start=chess_watch_{token}")
    if not WEBAPP_URL:
        return None
    url = f"{WEBAPP_URL}/webapp/?token={token}"
    return InlineKeyboardButton(label, web_app=WebAppInfo(url=url))


def _kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]])
