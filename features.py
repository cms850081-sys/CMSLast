"""
features.py — قابلیت‌های جدید:
۱. جدول رتبه‌بندی Elo
۲. اعلان نتیجه به گروه
۳. جدول مسابقات (Bracket)
۴. قهرمان هفته/ماه
۵. پیش‌بینی نتیجه
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import database as db
import keyboards as kb
from helpers import box, separator, now_shamsi, today_shamsi, progress_bar, notify_pishva
from config import PISHVA_ID
import logging

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════
# ۱. جدول رتبه‌بندی Elo
# ══════════════════════════════════════════
async def show_elo_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    from elo import get_elo_leaderboard, get_elo_title, get_elo_bar
    leaders = await get_elo_leaderboard(15)
    if not leaders:
        await query.edit_message_text(
            f"{box('🏆 جدول رتبه‌بندی Elo')}\n\n"
            f"❗ هنوز هیچ مسابقه‌ای با نتیجه ثبت نشده.\n"
            f"پس از ثبت اولین نتیجه، جدول به‌روزرسانی می‌شود.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]
            ]),
            parse_mode="Markdown"
        )
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, p in enumerate(leaders):
        medal = medals[i] if i < 3 else f"`{i+1}.`"
        title = get_elo_title(p["rating"])
        lines.append(
            f"{medal} *{p['full_name']}* [{p['class_name'] or ''}]\n"
            f"   {title} — `{int(p['rating'])}` امتیاز\n"
            f"   ✅{p['elo_wins']} 🤝{p['elo_draws']} ❌{p['elo_losses']}"
        )
    text = (
        f"{box('🏆 جدول رتبه‌بندی Elo')}\n\n"
        f"📊 امتیازدهی بر اساس سیستم رسمی شطرنج\n"
        f"⏱️ آخرین به‌روزرسانی: `{now_shamsi()}`\n\n"
        f"{separator()}\n\n"
        + "\n\n".join(lines)
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📈 تاریخچه Elo بازیکن", callback_data="elo_my_history"),
            InlineKeyboardButton("❓ سیستم Elo چیست؟", callback_data="elo_info")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
        ]),
        parse_mode="Markdown"
    )

async def show_elo_history_picker(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """دکمه‌ی «📈 تاریخچه Elo بازیکن» توی جدول رتبه‌بندی — قبلاً به هیچ
    هندلری وصل نبود (فقط دکمه‌اش ساخته شده بود) و با زدنش هیچ اتفاقی
    نمی‌افتاد. اینجا لیستِ بازیکنان (بر اساس امتیاز) نشون داده می‌شه تا
    یکی انتخاب بشه و پنلِ Elo همون بازیکن (show_player_elo_panel) باز شه."""
    query = update.callback_query
    await query.answer()
    from elo import get_elo_leaderboard
    rows = await get_elo_leaderboard(30)
    if not rows:
        await query.edit_message_text(
            f"{box('📈 تاریخچه Elo')}\n\n❗ هنوز هیچ بازیکنی امتیاز Elo ندارد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="elo_leaderboard")]
            ]),
            parse_mode="Markdown"
        )
        return
    players = [
        {"id": r["player_id"], "full_name": r["full_name"], "class_name": r["class_name"]}
        for r in rows
    ]
    await query.edit_message_text(
        f"{box('📈 تاریخچه Elo')}\n\n📌 بازیکن موردنظر را انتخاب کنید:",
        reply_markup=kb.kb_player_select(players, prefix="elo_player", back="elo_leaderboard"),
        parse_mode="Markdown"
    )


async def show_elo_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        f"{box('❓ سیستم رتبه‌بندی Elo')}\n\n"
        f"♟️ *Elo Rating چیست؟*\n\n"
        f"سیستم Elo یک روش علمی برای محاسبه سطح بازیکنان شطرنج است "
        f"که توسط Arpad Elo ابداع شد و توسط فدراسیون جهانی شطرنج (FIDE) استفاده می‌شود.\n\n"
        f"{separator('📊 عناوین')}\n"
        f"♟️ گرندمستر: ۲۵۰۰+\n"
        f"🥇 استاد بین‌المللی: ۲۴۰۰+\n"
        f"🥈 استاد فیده: ۲۲۰۰+\n"
        f"🥉 کاندیدا استاد: ۲۰۰۰+\n"
        f"💎 متخصص: ۱۸۰۰+\n"
        f"🔵 کلاس A: ۱۶۰۰+\n"
        f"🟢 کلاس B: ۱۴۰۰+\n"
        f"🟡 کلاس C: ۱۲۰۰+\n"
        f"🔰 مبتدی: زیر ۱۲۰۰\n\n"
        f"{separator('⚙️ نحوه محاسبه')}\n"
        f"• امتیاز اولیه: *۱۲۰۰*\n"
        f"• برد مقابل قوی‌تر → امتیاز بیشتر\n"
        f"• باخت مقابل ضعیف‌تر → امتیاز بیشتر کم می‌شود\n"
        f"• تساوی هم امتیاز تغییر می‌دهد"
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت به جدول", callback_data="elo_leaderboard")]
        ]),
        parse_mode="Markdown"
    )

async def show_player_elo_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """نمایش Elo یک بازیکن خاص — از پنل بازیکن"""
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split("_")[-1])
    from elo import get_player_elo, get_elo_title, get_elo_bar, get_player_elo_history
    p = await db.get_player(pid)
    elo = await get_player_elo(pid)
    history = await get_player_elo_history(pid, 5)
    title = get_elo_title(elo["rating"])
    bar = get_elo_bar(elo["rating"])
    history_lines = []
    for h in history:
        icon = "✅" if h["result"] == "win" else "❌" if h["result"] == "loss" else "🤝"
        chg = h["change"]
        chg_txt = f"+{int(chg)}" if chg >= 0 else str(int(chg))
        history_lines.append(
            f"{icon} vs {h['opponent_name'] or '?'} → `{chg_txt}` (→{int(h['new_rating'])})"
        )
    text = (
        f"{box('📈 امتیاز Elo — ' + p['full_name'])}\n\n"
        f"🏆 عنوان: {title}\n"
        f"⭐ امتیاز فعلی: `{int(elo['rating'])}`\n"
        f"🔝 بالاترین امتیاز: `{int(elo['peak_rating'])}`\n"
        f"📊 {bar}\n\n"
        f"🎮 بازی‌ها: `{elo['games_played']}` | "
        f"✅{elo['elo_wins']} 🤝{elo['elo_draws']} ❌{elo['elo_losses']}\n\n"
        f"{separator('📜 آخرین تغییرات')}\n"
        + ("\n".join(history_lines) if history_lines else "_هنوز بازی‌ای ثبت نشده_")
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"player_view_{pid}")]
        ]),
        parse_mode="Markdown"
    )

# ══════════════════════════════════════════
# ۲. اعلان نتیجه به گروه/کانال
# ══════════════════════════════════════════
async def announce_match_result(bot, match_id: int, result: str,
                                  white_name: str, black_name: str,
                                  elo_change_w: int = 0, elo_change_b: int = 0):
    """ارسال نتیجه مسابقه به گروه اعلانات و کانال اعلانات (هرکدام که تنظیم و فعال باشد)

    FIX (کندیِ ثبت نتیجه): این ۴ کلید کاملاً مستقلن، ولی قبلاً با ۴ تا
    get_setting جدا (روی کشِ سرد، ۴ رفت‌وبرگشتِ شبکه‌ی پشتِ‌سرِهم) گرفته
    می‌شدن. با get_settings_bulk (دقیقاً همون کمکی‌ای که پنلِ تنظیمات
    استفاده می‌کنه) هر گروه با یک کوئریِ IN(...) میاد."""
    id_settings = await db.get_settings_bulk(
        ["announcement_group_id", "announcement_channel_id"], default=""
    )
    toggle_settings = await db.get_settings_bulk(
        ["broadcast_result_group_enabled", "broadcast_result_channel_enabled"], default="1"
    )
    group_id = id_settings["announcement_group_id"]
    channel_id = id_settings["announcement_channel_id"]
    group_on = toggle_settings["broadcast_result_group_enabled"]
    channel_on = toggle_settings["broadcast_result_channel_enabled"]
    send_targets = []
    if group_id and group_on == "1":
        send_targets.append(group_id)
    if channel_id and channel_on == "1":
        send_targets.append(channel_id)
    if not send_targets:
        return

    result_text = {
        "white": f"🥇 *{white_name}* (سفید) برنده شد!",
        "black": f"🥇 *{black_name}* (سیاه) برنده شد!",
        "draw": f"🤝 بازی مساوی شد!"
    }.get(result, "نتیجه ثبت شد")

    elo_text = ""
    if elo_change_w != 0 or elo_change_b != 0:
        w_sign = "+" if elo_change_w >= 0 else ""
        b_sign = "+" if elo_change_b >= 0 else ""
        elo_text = (
            f"\n\n📊 *تغییر امتیاز Elo:*\n"
            f"⬜ {white_name}: `{w_sign}{elo_change_w}`\n"
            f"⬛ {black_name}: `{b_sign}{elo_change_b}`"
        )

    text = (
        f"♟️ *نتیجه مسابقه*\n"
        f"╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼\n"
        f"⬜ {white_name} ⚔️ {black_name} ⬛\n\n"
        f"{result_text}"
        f"{elo_text}\n"
        f"╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼\n"
        f"⏱️ `{now_shamsi()}`"
    )

    for tid in send_targets:
        try:
            await bot.send_message(chat_id=int(tid), text=text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Could not announce match result to {tid}: {e}")

# ══════════════════════════════════════════
# ۳. پیش‌بینی نتیجه
# ══════════════════════════════════════════
async def show_prediction_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """انتخاب حریف برای پیش‌بینی"""
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split("_")[-1])
    p = await db.get_player(pid)
    players = await db.get_active_players()
    others = [pl for pl in players if pl["id"] != pid]
    if not others:
        await query.edit_message_text("❗ بازیکن دیگری وجود ندارد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data=f"player_view_{pid}")]]))
        return
    rows = []
    for i in range(0, len(others), 2):
        row = [InlineKeyboardButton(
            f"⚔️ {pl['full_name']}",
            callback_data=f"predict_{pid}_{pl['id']}"
        ) for pl in others[i:i+2]]
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"player_view_{pid}")])
    await query.edit_message_text(
        f"🔮 *پیش‌بینی برای {p['full_name']}*\n\nحریف را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown"
    )

async def show_prediction(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    white_id = int(parts[-2])
    black_id = int(parts[-1])
    from elo import get_player_elo, expected_score, get_elo_title
    wp = await db.get_player(white_id)
    bp = await db.get_player(black_id)
    w_elo = await get_player_elo(white_id)
    b_elo = await get_player_elo(black_id)
    prob_white = expected_score(w_elo["rating"], b_elo["rating"])
    prob_black = 1 - prob_white
    bar_len = 20
    w_bar = int(bar_len * prob_white)
    b_bar = bar_len - w_bar
    bar = "⬜" * w_bar + "⬛" * b_bar
    wh = await db.get_player_match_history(white_id)
    bh = await db.get_player_match_history(black_id)
    h2h_w = 0
    h2h_b = 0
    h2h_d = 0
    for m in wh:
        other = m["black_player_id"] if m["white_player_id"] == white_id else m["white_player_id"]
        if other == black_id:
            if m["result"] == "white":
                h2h_w += 1 if m["white_player_id"] == white_id else 0
                h2h_b += 1 if m["black_player_id"] == white_id else 0
            elif m["result"] == "black":
                h2h_b += 1 if m["white_player_id"] == white_id else 0
                h2h_w += 1 if m["black_player_id"] == white_id else 0
            elif m["result"] == "draw":
                h2h_d += 1
    h2h_text = (
        f"⚔️ سابقه رویارویی:\n"
        f"   {wp['full_name']}: {h2h_w} برد | {bp['full_name']}: {h2h_b} برد | {h2h_d} مساوی"
        if (h2h_w + h2h_b + h2h_d) > 0
        else "⚔️ اولین رویارویی این دو بازیکن!"
    )
    text = (
        f"{box('🔮 پیش‌بینی مسابقه')}\n\n"
        f"⬜ *{wp['full_name']}* vs ⬛ *{bp['full_name']}*\n\n"
        f"{separator('📊 امتیاز Elo')}\n"
        f"⬜ {wp['full_name']}: `{int(w_elo['rating'])}` — {get_elo_title(w_elo['rating'])}\n"
        f"⬛ {bp['full_name']}: `{int(b_elo['rating'])}` — {get_elo_title(b_elo['rating'])}\n\n"
        f"{separator('🎯 احتمال پیروزی')}\n"
        f"⬜ {int(prob_white*100)}٪ {bar} {int(prob_black*100)}٪ ⬛\n\n"
        f"{h2h_text}"
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت", callback_data="back_matches")]
        ]),
        parse_mode="Markdown"
    )

# ══════════════════════════════════════════
# ۴. قهرمان هفته/ماه
# ══════════════════════════════════════════
async def show_champions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    from datetime import datetime, timedelta
    now = datetime.now()
    week_ago = (now - timedelta(days=7)).isoformat()
    month_ago = (now - timedelta(days=30)).isoformat()
    weekly = await _get_best_player_since(week_ago)
    monthly = await _get_best_player_since(month_ago)
    most_active_week = await _get_most_active_since(week_ago)
    w_text = (
        f"🥇 *{weekly['name']}*\n"
        f"   ✅ {weekly['wins']} برد در این هفته"
        if weekly else "هنوز مسابقه‌ای این هفته ثبت نشده"
    )
    m_text = (
        f"🏆 *{monthly['name']}*\n"
        f"   ✅ {monthly['wins']} برد در این ماه"
        if monthly else "هنوز مسابقه‌ای این ماه ثبت نشده"
    )
    a_text = (
        f"⚡ *{most_active_week['name']}*\n"
        f"   ♟️ {most_active_week['games']} بازی این هفته"
        if most_active_week else "—"
    )
    text = (
        f"{box('🏆 قهرمانان')}\n\n"
        f"{separator('🌟 قهرمان هفته')}\n"
        f"{w_text}\n\n"
        f"{separator('👑 قهرمان ماه')}\n"
        f"{m_text}\n\n"
        f"{separator('⚡ فعال‌ترین این هفته')}\n"
        f"{a_text}\n\n"
        f"⏱️ آخرین به‌روزرسانی: `{now_shamsi()}`"
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 به‌روزرسانی", callback_data="champions"),
            InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
        ]),
        parse_mode="Markdown"
    )

async def _get_best_player_since(since: str) -> dict:
    import turso_db as aiosqlite
    from config import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT p.full_name as name,
                   SUM(CASE
                       WHEN (m.white_player_id=p.id AND m.result='white') OR
                            (m.black_player_id=p.id AND m.result='black') THEN 1
                       ELSE 0 END) as wins
            FROM players p
            JOIN matches m ON (m.white_player_id=p.id OR m.black_player_id=p.id)
            WHERE m.created_at >= ? AND m.result IS NOT NULL
            GROUP BY p.id
            ORDER BY wins DESC
            LIMIT 1
        """, (since,)) as cur:
            row = await cur.fetchone()
            if row and row["wins"] and row["wins"] > 0:
                return dict(row)
            return None

async def _get_most_active_since(since: str) -> dict:
    import turso_db as aiosqlite
    from config import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT p.full_name as name,
                   COUNT(m.id) as games
            FROM players p
            JOIN matches m ON (m.white_player_id=p.id OR m.black_player_id=p.id)
            WHERE m.created_at >= ?
            GROUP BY p.id
            ORDER BY games DESC
            LIMIT 1
        """, (since,)) as cur:
            row = await cur.fetchone()
            if row and row["games"] and row["games"] > 0:
                return dict(row)
            return None

# ══════════════════════════════════════════
# ۵. جدول مسابقات (Bracket View)
# ══════════════════════════════════════════
async def show_bracket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    matches = await db.get_matches_by_filter("all")
    done = [m for m in matches if m["result"]]
    pending = [m for m in matches if not m["result"]]
    if not matches:
        await query.edit_message_text(
            f"{box('📋 جدول مسابقات')}\n\n❗ هیچ مسابقه‌ای ثبت نشده.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="back_matches")]
            ]),
            parse_mode="Markdown"
        )
        return
    total = len(matches)
    pct = int(len(done) / total * 100) if total > 0 else 0
    bar = progress_bar(pct)
    recent_lines = []
    for m in done[-5:]:
        if m["result"] == "white":
            res = f"🥇 {m['white_name']}"
        elif m["result"] == "black":
            res = f"🥇 {m['black_name']}"
        else:
            res = "🤝 تساوی"
        recent_lines.append(f"⬜{m['white_name']} ⚔️ {m['black_name']}⬛ → {res}")
    pending_lines = []
    for m in pending[:5]:
        pending_lines.append(f"⏳ ⬜{m['white_name']} ⚔️ {m['black_name']}⬛")
    text = (
        f"{box('📋 جدول مسابقات')}\n\n"
        f"♟️ کل: `{total}` | ✅ انجام‌شده: `{len(done)}` | ⏳ در انتظار: `{len(pending)}`\n"
        f"🏁 پیشرفت: `{bar}`\n\n"
        f"{separator('✅ آخرین نتایج')}\n"
        + ("\n".join(recent_lines) if recent_lines else "_هنوز نتیجه‌ای ثبت نشده_") +
        f"\n\n{separator('⏳ در انتظار نتیجه')}\n"
        + ("\n".join(pending_lines) if pending_lines else "✅ همه مسابقات نتیجه دارند")
    )
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏆 جدول Elo", callback_data="elo_leaderboard"),
            InlineKeyboardButton("👑 قهرمانان", callback_data="champions")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="back_matches")],
        ]),
        parse_mode="Markdown"
    )

# ══════════════════════════════════════════
# Job: قهرمان هفتگی خودکار
# ══════════════════════════════════════════
async def weekly_champion_job(context):
    """هر هفته یک‌بار قهرمان هفته اعلام می‌شود"""
    from datetime import datetime, timedelta
    from helpers import broadcast_to_admins
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    champion = await _get_best_player_since(week_ago)
    if not champion:
        return
    group_id = await db.get_setting("announcement_group_id", "")
    channel_id = await db.get_setting("announcement_channel_id", "")
    text = (
        f"🏆 *قهرمان هفته*\n\n"
        f"╼╼╼╼╼╼ 🌟 ╾╾╾╾╾╾\n\n"
        f"👑 *{champion['name']}*\n"
        f"با {champion['wins']} برد در این هفته\n\n"
        f"تبریک! 🎉\n"
        f"╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼\n"
        f"⏱️ `{now_shamsi()}`"
    )
    await broadcast_to_admins(context.bot, text)
    try:
        import ops_group
        await ops_group.on_champion(text)
    except Exception:
        pass
    group_on = await db.get_setting("broadcast_champion_group_enabled", "1")
    channel_on = await db.get_setting("broadcast_champion_channel_enabled", "1")
    send_targets = []
    if group_id and group_on == "1":
        send_targets.append(group_id)
    if channel_id and channel_on == "1":
        send_targets.append(channel_id)
    for tid in send_targets:
        try:
            await context.bot.send_message(
                chat_id=int(tid), text=text, parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Weekly champion send to {tid} failed: {e}")
    await db.log_action(PISHVA_ID, "weekly_champion", f"قهرمان هفته: {champion['name']}")


def next_monday_9am(from_dt):
    """اولین دوشنبهٔ ساعت ۹:۰۰ بعد از from_dt رو برمی‌گردونه (لحظهٔ مطلق،
    برای لنگر زدنِ دقیق قهرمان هفتگی — نه یک بازهٔ نسبی که انحراف می‌گیره)."""
    from datetime import timedelta
    days_ahead = (0 - from_dt.weekday()) % 7  # Monday == 0
    candidate = (from_dt + timedelta(days=days_ahead)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    if candidate <= from_dt:
        candidate += timedelta(days=7)
    return candidate


async def weekly_champion_wrapper(context):
    """جابی که واقعاً روی جاب‌کیو می‌شینه: اول قهرمان هفته رو اعلام می‌کنه،
    بعد بلافاصله لحظهٔ دقیق هفتهٔ بعد رو زمان‌بندی می‌کنه — با جمع‌کردن
    ۷ روز به لنگر قبلی (نه به «الان»)، تا هیچ‌وقت انحراف جمع نشه."""
    import precise_scheduler as sched
    from datetime import timedelta, datetime
    from helpers import TEHRAN_TZ
    try:
        await weekly_champion_job(context)
    except Exception as e:
        logger.warning(f"weekly_champion_job failed: {e}")

    anchor, _ = await sched.load_target("weekly_champion")
    base = anchor if anchor else datetime.now(TEHRAN_TZ)
    next_target = base + timedelta(days=7)
    await sched.schedule_persistent(
        context.job_queue, weekly_champion_wrapper, next_target, "weekly_champion"
    )
