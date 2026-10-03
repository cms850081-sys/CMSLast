from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
import database as db
import keyboards as kb
from helpers import (safe_edit_message_text, box, separator, warning_bar_player, power_bar,
                     now_shamsi, notify_pishva, log_line, check_status_gate,
                     progress_bar, get_rank_label, check_perm)
from anomaly_alerts import record_destructive_action
from config import (PISHVA_ID, ST_CLASS_NAME, ST_PLAYER_CLASS_SELECT,
                    ST_PLAYER_NAME, ST_WARNING_REASON, ST_NOTE_TEXT,
                    ST_EDIT_PLAYER_NAME, ST_SEARCH_PLAYER)


# ─── Class Management ─────────────────────────────────────────
async def class_add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, 
        f"{box('✅ ثبت کلاس جدید')}\n\n📝 نام کلاس را وارد کنید (مثلاً ۹۰۱):",
        parse_mode="Markdown"
    )
    return ST_CLASS_NAME

async def class_add_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if ctx.user_data.get("editing_class"):
        cid = ctx.user_data.pop("editing_class")
        await db.rename_class(cid, name)
        await update.message.reply_text(f"✅ نام کلاس به *{name}* تغییر یافت.", parse_mode="Markdown",
                                         reply_markup=kb.kb_class_manage(is_pishva=(update.effective_user.id == PISHVA_ID)))
    else:
        await db.create_class(name)
        await db.log_action(update.effective_user.id, "create_class", f"ثبت کلاس: {name}")
        await update.message.reply_text(f"✅ کلاس *{name}* ثبت شد.", reply_markup=kb.kb_class_manage(is_pishva=(update.effective_user.id == PISHVA_ID)), parse_mode="Markdown")
    return ConversationHandler.END

async def class_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    classes = await db.get_all_classes()
    if not classes:
        await safe_edit_message_text(query, f"{box('🏫 کلاس‌ها')}\n\n❗ هیچ کلاسی ثبت نشده.",
                                       reply_markup=kb.kb_class_manage(is_pishva=(query.from_user.id == PISHVA_ID)), parse_mode="Markdown")
        return
    await safe_edit_message_text(query, f"{box('🏫 لیست کلاس‌ها')}\n\n📌 یک کلاس انتخاب کنید:",
                                   reply_markup=kb.kb_class_list(classes), parse_mode="Markdown")

async def class_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.split("_")[-1])
    c = await db.get_class(cid)
    players = await db.get_players_by_class(cid)
    active = sum(1 for p in players if p["status"] == "active")
    await safe_edit_message_text(query, 
        f"{box('🏫 کلاس ' + c['name'])}\n\n👥 تعداد بازیکنان: `{len(players)}`\n✅ فعال: `{active}`",
        reply_markup=kb.kb_class_actions(cid, is_pishva=(query.from_user.id == PISHVA_ID)), parse_mode="Markdown")

async def class_players(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.split("_")[-1])
    c = await db.get_class(cid)
    players = await db.get_players_by_class(cid)
    if not players:
        await safe_edit_message_text(query, f"👥 کلاس *{c['name']}*\n\n❗ بازیکنی ثبت نشده.",
                                       reply_markup=kb.kb_back("class_list"), parse_mode="Markdown")
        return
    lines = []
    for p in players:
        icon = "🟢" if p["status"] == "active" else "⛔" if p["status"] == "eliminated" else "🔴"
        lines.append(f"{icon} {p['full_name']} — W:{p['wins']} D:{p['draws']} L:{p['losses']}")
    await safe_edit_message_text(query, f"👥 بازیکنان کلاس *{c['name']}*:\n\n" + "\n".join(lines),
                                   reply_markup=kb.kb_back("class_list"), parse_mode="Markdown")

async def class_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.split("_")[-1])
    ctx.user_data["editing_class"] = cid
    c = await db.get_class(cid)
    await safe_edit_message_text(query, f"✏️ نام جدید برای کلاس *{c['name']}*:", parse_mode="Markdown")
    return ST_CLASS_NAME

async def class_harddelete_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ حذف کلاس فقط برای مدیر ارشد مجاز است.", show_alert=True)
        return
    await query.answer()
    cid = int(query.data.split("_")[-1])
    c = await db.get_class(cid)
    if not c:
        await query.answer("کلاس یافت نشد.", show_alert=True)
        return
    count = await db.get_class_player_count(cid)
    if count > 0:
        await safe_edit_message_text(query,
            f"❌ کلاس *{c['name']}* را نمی‌توان حذف کرد؛ `{count}` بازیکن هنوز در این کلاس هستند.\n"
            f"ابتدا بازیکنان را جابه‌جا یا حذف کنید.",
            reply_markup=kb.kb_class_actions(cid, is_pishva=True), parse_mode="Markdown")
        return
    await safe_edit_message_text(query,
        f"🗑 کلاس *{c['name']}* حذف می‌شود. این کار غیرقابل بازگشت است.\n\nمطمئنید؟",
        reply_markup=kb.kb_confirm(f"class_harddelete_go_{cid}", f"class_select_{cid}"),
        parse_mode="Markdown")

async def class_harddelete_go(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ حذف کلاس فقط برای مدیر ارشد مجاز است.", show_alert=True)
        return
    await query.answer()
    cid = int(query.data.split("_")[-1])
    c = await db.get_class(cid)
    if not c:
        await safe_edit_message_text(query, "کلاس قبلاً حذف شده.", reply_markup=kb.kb_back("class_list"))
        return
    name = c["name"]
    ok = await db.delete_class(cid)
    if not ok:
        await safe_edit_message_text(query,
            f"❌ کلاس *{name}* را نمی‌توان حذف کرد؛ بازیکنی به آن اضافه شده.",
            reply_markup=kb.kb_class_actions(cid, is_pishva=True), parse_mode="Markdown")
        return
    await db.log_action(query.from_user.id, "delete_class", f"حذف کلاس: {name}")
    await safe_edit_message_text(query, f"🗑 کلاس *{name}* حذف شد.",
                                   reply_markup=kb.kb_back("class_list"), parse_mode="Markdown")

async def class_perf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.split("_")[-1])
    c = await db.get_class(cid)
    players = await db.get_players_by_class(cid)
    total_w = sum(p["wins"] for p in players)
    total_d = sum(p["draws"] for p in players)
    total_l = sum(p["losses"] for p in players)
    bar = power_bar(total_w, total_l, total_d)
    await safe_edit_message_text(query, 
        f"{box('📈 عملکرد کلاس ' + c['name'])}\n\n"
        f"✅ برد: `{total_w}` | 🤝 مساوی: `{total_d}` | ❌ باخت: `{total_l}`\n\n"
        f"⚡ سطح قدرت:\n`{bar}`",
        reply_markup=kb.kb_back(f"class_select_{cid}"), parse_mode="Markdown")

# ─── رنگ دکمه‌ی کلاس‌ها (فقط مدیر ارشد) ─────────────────────────
async def _cclr_guard(query) -> bool:
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ تنظیم رنگ کلاس‌ها فقط برای مدیر ارشد مجاز است.", show_alert=True)
        return False
    return True

async def class_colors_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await _cclr_guard(query):
        return
    await query.answer()
    classes = await db.get_all_classes()
    if not classes:
        await safe_edit_message_text(query, f"{box('🎨 رنگ کلاس‌ها')}\n\n❗ هیچ کلاسی ثبت نشده.",
                                       reply_markup=kb.kb_class_manage(is_pishva=True), parse_mode="Markdown")
        return
    await safe_edit_message_text(query,
        f"{box('🎨 رنگ کلاس‌ها')}\n\n"
        f"📌 روی هر کلاس بزنید تا رنگ دکمه‌ی آن (در همه‌ی منوهای ربات) را تغییر دهید.\n"
        f"رنگ فعلی هر کلاس همین‌جا روی دکمه‌اش دیده می‌شود.",
        reply_markup=kb.kb_class_colors_list(classes), parse_mode="Markdown")

async def _show_class_color_picker(query, cid: int, src: str):
    c = await db.get_class(cid)
    if not c:
        await query.answer("کلاس یافت نشد.", show_alert=True)
        return
    current = kb.class_style_of(c)
    current_txt = kb.CLASS_STYLE_NAMES.get(current, "پیش‌فرض")
    await safe_edit_message_text(query,
        f"{box('🎨 رنگ کلاس ' + c['name'])}\n\n"
        f"🎯 رنگ فعلی: *{current_txt}*\n\n"
        f"📌 رنگ دکمه‌ی این کلاس را انتخاب کنید:",
        reply_markup=kb.kb_class_color_picker(cid, current, src), parse_mode="Markdown")

async def class_color_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await _cclr_guard(query):
        return
    await query.answer()
    parts = query.data.split("_")            # cclr_pick_{id}_{src}
    cid = int(parts[2])
    src = parts[3] if len(parts) > 3 else "l"
    await _show_class_color_picker(query, cid, src)

async def class_color_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await _cclr_guard(query):
        return
    parts = query.data.split("_")            # cclr_set_{id}_{style}_{src}
    cid, key = int(parts[2]), parts[3]
    src = parts[4] if len(parts) > 4 else "l"
    c = await db.get_class(cid)
    if not c:
        await query.answer("کلاس یافت نشد.", show_alert=True)
        return
    try:
        ok = await db.set_class_button_style(cid, key)
    except Exception:
        ok = False
    if not ok:
        await query.answer("❌ ذخیره‌ی رنگ انجام نشد. لطفاً دوباره تلاش کنید.", show_alert=True)
        return
    color_name = kb.CLASS_STYLE_NAMES[key]
    await db.log_action(query.from_user.id, "class_color", f"رنگ دکمه‌ی کلاس {c['name']}: {color_name}")
    await query.answer(f"✅ رنگ کلاس {c['name']} → {color_name}")
    await _show_class_color_picker(query, cid, src)

# ─── Player Registration ──────────────────────────────────────
async def player_add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    classes = await db.get_all_classes()
    if not classes:
        await safe_edit_message_text(query, "❗ ابتدا باید حداقل یک کلاس ثبت کنید.",
                                       reply_markup=kb.kb_class_manage(is_pishva=(query.from_user.id == PISHVA_ID)), parse_mode="Markdown")
        return ConversationHandler.END
    rows = []
    for i in range(0, len(classes), 2):
        row = [kb.class_btn(c, f"pclass_{c['id']}") for c in classes[i:i+2]]
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_players")])
    await safe_edit_message_text(query, 
        f"{box('➕ ثبت‌نام بازیکن')}\n\n📌 مرحله ۱/۲: کلاس بازیکن را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")
    return ST_PLAYER_CLASS_SELECT

async def player_class_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.split("_")[-1])
    ctx.user_data["player_class"] = cid
    c = await db.get_class(cid)
    await safe_edit_message_text(query, 
        f"{box('➕ ثبت‌نام بازیکن')}\n\n🏫 کلاس: *{c['name']}*\n\n📌 مرحله ۲/۲: نام و نام‌خانوادگی:",
        parse_mode="Markdown")
    return ST_PLAYER_NAME

async def player_add_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    full_name = update.message.text.strip()
    cid = ctx.user_data.get("player_class")
    if not cid or not full_name:
        await update.message.reply_text("❌ خطا. دوباره از ابتدا شروع کنید.")
        return ConversationHandler.END
    pid = await db.create_player(full_name, cid)
    await db.log_action(update.effective_user.id, "create_player", f"ثبت بازیکن: {full_name}", pid)
    c = await db.get_class(cid)
    team_mode = await db.get_setting("team_mode_enabled", "0")
    team_reg = await db.get_setting("team_registration_enabled", "1")
    if team_mode == "1" and team_reg == "1":
        teams = await db.get_all_teams()
        if teams:
            ctx.user_data["new_player_id"] = pid
            rows = []
            for i in range(0, len(teams), 2):
                row = [InlineKeyboardButton(f"🏆 {t['name']}", callback_data=f"player_jointeam_{pid}_{t['id']}") for t in teams[i:i+2]]
                rows.append(row)
            rows.append([InlineKeyboardButton("⏭️ رد کردن", callback_data=f"player_noteam_{pid}")])
            await update.message.reply_text(
                f"✅ بازیکن *{full_name}* (کلاس {c['name']}) ثبت شد.\n\nآیا به تیمی ملحق شود؟",
                reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")
            return ConversationHandler.END
    await update.message.reply_text(
        f"✅ بازیکن *{full_name}* در کلاس *{c['name']}* ثبت شد.\n🆔 شناسه: `{pid}`",
        reply_markup=kb.kb_players_menu("pishva"), parse_mode="Markdown")
    return ConversationHandler.END

async def player_join_team(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    pid = int(parts[-2])
    tid = int(parts[-1])
    await db.add_team_member(tid, pid)
    p = await db.get_player(pid)
    t = await db.get_team(tid)
    await safe_edit_message_text(query, f"✅ *{p['full_name']}* به تیم *{t['name']}* اضافه شد.",
                                   reply_markup=kb.kb_players_menu("pishva"), parse_mode="Markdown")

async def player_no_team(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit_message_text(query, "✅ بازیکن بدون تیم ثبت شد.", reply_markup=kb.kb_players_menu("pishva"))

# ─── Player List ──────────────────────────────────────────────

# FIX: عنوان و منبعِ داده‌ی هر «context» توی یک‌جا نگه داشته می‌شه تا
# صفحه‌بندی و جستجو بتونن دقیقاً همون لیستی که کاربر توش بود رو دوباره بسازن،
# به‌جای این‌که همیشه بریزن روی لیست کل بازیکنان.
_PLAYER_LIST_TITLES = {
    "all": "👤 لیست بازیکنان",
    "continuing": "✅ بازیکنان ادامه‌دهنده",
    "kicked": "❌ اخراجی‌ها",
    "elim": "⛔ شکست‌خورده‌ها",
    "elite": "🌟 بازیکنان برتر",
    "special": "⚡ نیروهای ویژه",
}

async def _get_players_by_context(context: str):
    if context == "continuing":
        return await db.get_continuing_players()
    if context == "kicked":
        players = await db.get_all_players()
        return [p for p in players if p["status"] == "kicked"]
    if context == "elim":
        players = await db.get_all_players()
        return [p for p in players if p["status"] == "eliminated"]
    if context == "elite":
        players = await db.get_all_players()
        return [p for p in players if p["is_elite"]]
    if context == "special":
        players = await db.get_all_players()
        return [p for p in players if p["is_special"]]
    return await db.get_all_players()

async def player_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    players = await db.get_all_players()
    if not players:
        await safe_edit_message_text(query, f"{box('👤 لیست بازیکنان')}\n\n❗ هیچ بازیکنی ثبت نشده.",
                                       reply_markup=kb.kb_back("players"), parse_mode="Markdown")
        return
    await safe_edit_message_text(query, 
        f"{box('👤 لیست بازیکنان')}\n\n👥 تعداد کل: `{len(players)}`\n\n📌 یک بازیکن انتخاب کنید:",
        reply_markup=kb.kb_player_list(players, context="all"), parse_mode="Markdown")

async def player_list_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # callback_data: "player_list_page_{context}_{page}"
    parts = query.data.split("_")
    page = int(parts[-1])
    context = parts[3] if len(parts) > 4 else "all"
    players = await _get_players_by_context(context)
    title = _PLAYER_LIST_TITLES.get(context, "👤 لیست بازیکنان")
    await safe_edit_message_text(query, 
        f"{box(title)}\n\n👥 تعداد: `{len(players)}`",
        reply_markup=kb.kb_player_list(players, page, context=context), parse_mode="Markdown")

async def player_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split("_")[-1])
    p = await db.get_player(pid)
    if not p:
        await query.answer("بازیکن یافت نشد.", show_alert=True)
        return

    role = "pishva" if query.from_user.id == PISHVA_ID else "admin"
    total = p["wins"] + p["losses"] + p["draws"]
    warn_bar = warning_bar_player(p["warnings"])
    pw = power_bar(p["wins"], p["losses"], p["draws"])
    rank = get_rank_label(p["wins"], total)
    win_pct = int(p["wins"] / total * 100) if total > 0 else 0
    win_bar = progress_bar(win_pct)

    status_map = {
        "active": "🟢 فعال", "suspended": "⏸️ تعلیق",
        "kicked": "❌ اخراج", "eliminated": "⛔ حذف در مسابقه"
    }

    # Best opponent
    history = await db.get_player_match_history(pid)
    opponent_wins = {}
    for m in history:
        if m["result"] == "white" and m["white_player_id"] == pid:
            opp = m["black_name"]
        elif m["result"] == "black" and m["black_player_id"] == pid:
            opp = m["white_name"]
        else:
            continue
        opponent_wins[opp] = opponent_wins.get(opp, 0) + 1
    best_opp = max(opponent_wins, key=opponent_wins.get) if opponent_wins else "—"
    hardest_opp_wins = {}
    for m in history:
        if m["result"] == "black" and m["white_player_id"] == pid:
            opp = m["black_name"]
        elif m["result"] == "white" and m["black_player_id"] == pid:
            opp = m["white_name"]
        else:
            continue
        hardest_opp_wins[opp] = hardest_opp_wins.get(opp, 0) + 1
    hardest = max(hardest_opp_wins, key=hardest_opp_wins.get) if hardest_opp_wins else "—"

    elite_tag = "  🌟 بازیکن برتر" if p["is_elite"] else ""
    special_tag = "  ⚡ نیروی ویژه" if p["is_special"] else ""

    text = (
        f"{box('👤 ' + p['full_name'])}\n\n"
        f"🏫 کلاس: *{(p['class_name'] or '—')}*\n"
        f"📊 وضعیت: {status_map.get(p['status'], p['status'])}\n"
        f"🏆 رتبه: {rank}{elite_tag}{special_tag}\n\n"
        f"{separator('📊 آمار عملکرد')}\n"
        f"✅ برد: `{p['wins']}` | 🤝 مساوی: `{p['draws']}` | ❌ باخت: `{p['losses']}`\n"
        f"📈 مجموع: `{total}` بازی\n"
        f"🎯 درصد برد:\n`{win_bar}`\n"
        f"⚡ سطح قدرت:\n`{pw}`\n\n"
        f"{separator('⚔️ اطلاعات رقابتی')}\n"
        f"🏅 بهترین حریف (بیشترین برد مقابل): {best_opp}\n"
        f"💀 سخت‌ترین حریف (بیشترین باخت مقابل): {hardest}\n\n"
        f"{separator('⚠️ اخطار')}\n"
        f"{warn_bar}\n"
        f"{'📂 یادداشت: _' + p['notes'] + '_' if p['notes'] else ''}"
    )
    await safe_edit_message_text(query, text, reply_markup=kb.kb_player_actions(pid, role, p["status"], p["is_elite"], p["is_special"], p["warnings"] or 0), parse_mode="Markdown")

# ─── Player Actions ───────────────────────────────────────────
async def player_warn_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await check_status_gate(query, "warning"):
        return
    if await check_perm(query, "issue_warning"):
        return
    await query.answer()
    pid = int(query.data.split("_")[-1])
    ctx.user_data["warning_player"] = pid
    p = await db.get_player(pid)
    await safe_edit_message_text(query, f"⚠️ دلیل اخطار برای *{p['full_name']}*:",
                                   reply_markup=kb.kb_cancel("back_players"), parse_mode="Markdown")
    return ST_WARNING_REASON

async def player_warn_reason(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    pid = ctx.user_data.get("warning_player")
    uid = update.effective_user.id
    if not pid:
        return ConversationHandler.END
    p = await db.get_player(pid)
    await db.add_player_warning(pid, reason, uid)
    p_updated = await db.get_player(pid)
    warn_bar = warning_bar_player(p_updated["warnings"])
    await update.message.reply_text(
        f"⚠️ اخطار برای *{p['full_name']}* ثبت شد.\n📋 دلیل: {reason}\n\n{warn_bar}",
        reply_markup=kb.kb_back("player_list"), parse_mode="Markdown")
    await db.log_action(uid, "player_warning", f"اخطار به {p['full_name']}: {reason}", pid)
    if p_updated["warnings"] >= 3:
        await notify_pishva(update.get_bot(),
            f"🔴 بازیکن *{p['full_name']}* به ۳ اخطار رسید!\n📋 دلیل: {reason}\n⏱️ `{now_shamsi()}`")
    return ConversationHandler.END

# ─── حذفِ اخطارِ بازیکن ───────────────────────────────────────
# مدیر ارشد: هر اخطاری + «پاک‌کردنِ همه». مدیرِ دیگر (با دسترسیِ اخطار): فقط اخطارهایی که «خودش» ثبت کرده.
async def _warn_gate(query) -> bool:
    """True = بلاک شد."""
    if await check_status_gate(query, "warning"):
        return True
    return bool(await check_perm(query, "issue_warning"))


async def _render_warnlist(query, pid: int):
    from html import escape as _e
    from telegram import InlineKeyboardButton as _B, InlineKeyboardMarkup as _M
    from helpers import date_label_fa
    p = await db.get_player(pid)
    if not p:
        await query.answer("بازیکن یافت نشد.", show_alert=True)
        return
    uid = query.from_user.id
    is_pishva = uid == PISHVA_ID
    logs = list(await db.get_warnings_log("player", pid, 15))
    lines, rows = [], []
    for i, r in enumerate(logs, 1):
        by = r["issuer_name"] or ("مدیر ارشد" if r["issued_by"] == PISHVA_ID else "؟")
        lines.append(f"{i}. {_e(str(r['reason'] or '')[:80])}\n    👤 {_e(by)} • {_e(date_label_fa(str(r['issued_at'] or '')[:10]))}")
        if is_pishva or r["issued_by"] == uid:
            rows.append([_B(f"🗑 حذفِ اخطار {i}: {str(r['reason'] or '')[:22]}", callback_data=f"pwd_{pid}_{r['id']}", style="danger")])
    count = p["warnings"] or 0
    text = f"🧹 <b>اخطارهای {_e(p['full_name'])}</b>\nتعداد: {count}\n\n"
    text += "\n".join(lines) if lines else "سابقه‌ی ثبت‌شده‌ای نیست."
    if logs and not rows:
        text += "\n\nℹ️ فقط اخطارهایی که خودتان ثبت کرده‌اید قابلِ حذف است."
    if is_pishva and (count or logs):
        rows.append([_B("🧹 پاک‌کردنِ همه‌ی اخطارها", callback_data=f"pwc_{pid}", style="danger")])
    rows.append([_B("🔙 بازگشت به پنلِ بازیکن", callback_data=f"player_view_{pid}", style="primary")])
    await safe_edit_message_text(query, text, reply_markup=_M(rows), parse_mode="HTML")


async def player_warnlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await _warn_gate(query):
        return
    await query.answer()
    await _render_warnlist(query, int(query.data.split("_")[-1]))


async def player_warn_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """pwd_{pid}_{logid}"""
    query = update.callback_query
    if await _warn_gate(query):
        return
    uid = query.from_user.id
    _p, pid_s, log_s = query.data.split("_")
    pid, log_id = int(pid_s), int(log_s)
    p = await db.get_player(pid)
    if not p:
        await query.answer("بازیکن یافت نشد.", show_alert=True)
        return
    if uid != PISHVA_ID:
        mine = [r for r in await db.get_warnings_log("player", pid, 100) if r["id"] == log_id]
        if not mine or mine[0]["issued_by"] != uid:
            await query.answer("⛔ فقط اخطارهایی که خودتان ثبت کرده‌اید قابلِ حذف است.", show_alert=True)
            return
    row = await db.remove_player_warning(pid, log_id)
    if not row:
        await query.answer("این اخطار قبلاً حذف شده.", show_alert=True)
    else:
        await query.answer("✅ اخطار حذف شد")
        await db.log_action(uid, "player_warning_removed",
                            f"حذفِ اخطارِ {p['full_name']}: {str(row['reason'] or '')[:80]}", pid)
    await _render_warnlist(query, pid)


async def player_warn_clear_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    from telegram import InlineKeyboardButton as _B, InlineKeyboardMarkup as _M
    pid = int(query.data.split("_")[-1])
    p = await db.get_player(pid)
    if not p:
        await query.answer("بازیکن یافت نشد.", show_alert=True)
        return
    await safe_edit_message_text(
        query, f"🧹 همه‌ی اخطارهای *{p['full_name']}* پاک شود؟\nاین کار برگشت ندارد.",
        reply_markup=_M([[_B("✅ بله، همه پاک شود", callback_data=f"pwcy_{pid}", style="danger")],
                         [_B("🔙 انصراف", callback_data=f"pwl_{pid}", style="primary")]]),
        parse_mode="Markdown")


async def player_warn_clear_go(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    pid = int(query.data.split("_")[-1])
    p = await db.get_player(pid)
    if not p:
        await query.answer("بازیکن یافت نشد.", show_alert=True)
        return
    await db.clear_player_warnings(pid)
    await db.log_action(PISHVA_ID, "player_warnings_cleared", f"پاک‌شدنِ همه‌ی اخطارهای {p['full_name']}", pid)
    await query.answer("✅ همه‌ی اخطارها پاک شد")
    await _render_warnlist(query, pid)


async def player_kick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """FIX: قبلاً این تابع فقط permission «request_ban» رو چک می‌کرد و
    همیشه بازیکن رو مستقیم اخراج می‌کرد — یعنی permission «اخراج مستقیم»
    (direct_ban) و کلیدِ کلیِ «اخراجِ مستقیمِ مدیران» هیچ‌وقت واقعاً چک
    نمی‌شدن؛ حتی وقتی این‌ها خاموش بودن، ادمین بازم می‌تونست مستقیم اخراج
    کنه. حالا: اگه هم permission اختصاصیِ همین ادمین («اخراج مستقیم») روشنه
    و هم کلیدِ کلی روشنه، اخراج مثل قبل فوری انجام می‌شه؛ وگرنه به‌جای
    اخراجِ فوری، یک درخواست برای مدیر ارشد ساخته می‌شه و لیستِ درخواست‌های
    اخراج (توی پنل مدیر ارشد) به‌روز می‌شه."""
    query = update.callback_query
    if await check_status_gate(query, "ban_player"):
        return
    if await check_perm(query, "request_ban"):
        return
    uid = query.from_user.id
    pid = int(query.data.split("_")[-1])
    p = await db.get_player(pid)
    if not p:
        await query.answer("بازیکن یافت نشد.", show_alert=True)
        return

    admin = await db.get_admin(uid)
    import json as _json
    try:
        perms = _json.loads(admin["permissions"]) if admin else {}
    except Exception:
        perms = {}
    direct_allowed = bool(perms.get("direct_ban", False))
    global_allowed = (await db.get_setting("admin_direct_kick_enabled", "1")) == "1"

    # FIX: تنظیمِ «اخراجِ مستقیمِ مدیران» فقط مخصوصِ ادمین‌هاست. قبلاً چون
    # مدیر ارشد رکورد/دسترسیِ direct_ban توی جدولِ ادمین‌ها نداره، وقتی این
    # تنظیم خاموش بود، مدیر ارشد هم به‌جای اخراجِ مستقیم می‌رفت توی مسیرِ
    # «درخواست برای مدیر ارشد» — یعنی عملاً برای خودش درخواست می‌ساخت و
    # باید اخراجِ خودش رو توی پنلش تایید/رد می‌کرد. حالا مدیر ارشد همیشه
    # مستقیم اخراج می‌کنه، مستقل از این تنظیم.
    if uid == PISHVA_ID or (direct_allowed and global_allowed):
        await query.answer()
        if p["is_elite"] or p["is_special"]:
            icon = "🌟" if p["is_elite"] else "⚡"
            await query.answer(f"⚠️ این بازیکن {icon} است! برای تأیید دوباره بزنید.", show_alert=True)
        await db.update_player(pid, status="kicked")
        await db.log_action(uid, "kick_player", f"اخراج: {p['full_name']}", pid)
        await record_destructive_action(ctx.bot, uid, "kick_player")
        await safe_edit_message_text(query, f"🚫 *{p['full_name']}* اخراج شد.",
                                       reply_markup=kb.kb_back("player_list"), parse_mode="Markdown")
        return

    # ─── دسترسیِ اخراجِ مستقیم خاموشه — به‌جای اخراج، درخواست بساز ───
    # FIX: قبلاً همین‌جا یک popup («show_alert=True») هم نشون داده می‌شد که
    # دقیقاً همون پیامِ متنیِ پایینِ تابع (بعد از ساختِ درخواست) رو تکرار
    # می‌کرد؛ یعنی کاربر هم توی متنِ پیام و هم توی یک پاپ‌آپِ جدا همین خبر رو
    # می‌دید. حالا فقط همون callback رو تایید می‌کنیم (بدون پاپ‌آپ) و خبر
    # فقط یک‌بار، توی متنِ ویرایش‌شده‌ی پیام، نشون داده می‌شه.
    await query.answer()
    req_id = await db.create_kick_request(uid, pid)
    admin_name = (admin["display_name"] or admin["full_name"]) if admin else str(uid)
    await notify_pishva(
        ctx.bot,
        f"{box('🚫 درخواست اخراج بازیکن')}\n\n"
        f"👤 ادمینِ درخواست‌دهنده: *{admin_name}*\n"
        f"♟️ بازیکن: *{p['full_name']}*\n"
        f"⏱️ `{now_shamsi()}`\n\n"
        f"📌 تایید یا رد کنید:",
        reply_markup=kb.kb_kick_request(req_id)
    )
    await db.log_action(uid, "request_kick_player", f"درخواست اخراج: {p['full_name']}", pid)
    await safe_edit_message_text(
        query,
        f"⏳ درخواستِ اخراجِ *{p['full_name']}* برای تاییدِ مدیر ارشد ارسال شد.",
        reply_markup=kb.kb_back("player_list"), parse_mode="Markdown"
    )


async def pishva_kick_requests(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    reqs = await db.get_pending_kick_requests()
    await safe_edit_message_text(
        query,
        f"{box('🚫 درخواست‌های اخراج')}\n\n📌 درخواست‌های در انتظارِ تایید:",
        reply_markup=kb.kb_kick_requests_list(reqs),
        parse_mode="Markdown"
    )


async def kick_request_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer()
    req_id = int(query.data[len("kickreq_view_"):])
    r = await db.get_kick_request(req_id)
    if not r or r["status"] != "pending":
        await safe_edit_message_text(query, "این درخواست دیگر معتبر نیست.", reply_markup=kb.kb_back("pishva_panel"))
        return
    admin = await db.get_admin(r["admin_id"])
    p = await db.get_player(r["player_id"])
    admin_name = (admin["display_name"] or admin["full_name"]) if admin else str(r["admin_id"])
    player_name = p["full_name"] if p else str(r["player_id"])
    await safe_edit_message_text(
        query,
        f"{box('🚫 درخواست اخراج بازیکن')}\n\n"
        f"👤 ادمینِ درخواست‌دهنده: *{admin_name}*\n"
        f"♟️ بازیکن: *{player_name}*\n"
        f"⏱️ `{str(r['requested_at'])[:16]}`\n\n"
        f"📌 تایید یا رد کنید:",
        reply_markup=kb.kb_kick_request(req_id),
        parse_mode="Markdown"
    )


async def kick_request_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer("✅ اخراج شد.")
    req_id = int(query.data[len("kickreq_approve_"):])
    r = await db.get_kick_request(req_id)
    if not r or r["status"] != "pending":
        await safe_edit_message_text(query, "این درخواست دیگر معتبر نیست.", reply_markup=kb.kb_back("pishva_panel"))
        return
    p = await db.get_player(r["player_id"])
    await db.update_kick_request(req_id, "approved")
    if p:
        await db.update_player(r["player_id"], status="kicked")
        await db.log_action(PISHVA_ID, "kick_player",
                             f"تاییدِ درخواستِ اخراج: {p['full_name']}", r["player_id"])
        await record_destructive_action(ctx.bot, r["admin_id"], "kick_player")
    old_text = query.message.text or ""
    try:
        await ctx.bot.send_message(chat_id=r["admin_id"],
            text=f"✅ درخواستِ اخراجِ *{p['full_name'] if p else ''}* توسط مدیر ارشد تایید شد.",
            parse_mode="Markdown")
    except Exception:
        pass
    await safe_edit_message_text(query, f"{old_text}\n\n✅ *تایید شد و بازیکن اخراج گردید.*",
                                   reply_markup=kb.kb_back("pishva_panel"))


async def kick_request_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return
    await query.answer("❌ رد شد.")
    req_id = int(query.data[len("kickreq_reject_"):])
    r = await db.get_kick_request(req_id)
    if not r or r["status"] != "pending":
        await safe_edit_message_text(query, "این درخواست دیگر معتبر نیست.", reply_markup=kb.kb_back("pishva_panel"))
        return
    p = await db.get_player(r["player_id"])
    await db.update_kick_request(req_id, "rejected")
    try:
        await ctx.bot.send_message(chat_id=r["admin_id"],
            text=f"❌ درخواستِ اخراجِ *{p['full_name'] if p else ''}* توسط مدیر ارشد رد شد.",
            parse_mode="Markdown")
    except Exception:
        pass
    old_text = query.message.text or ""
    await safe_edit_message_text(query, f"{old_text}\n\n❌ *درخواست رد شد.*",
                                   reply_markup=kb.kb_back("pishva_panel"))

async def player_suspend(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await check_status_gate(query, "ban_player"):
        return
    if await check_perm(query, "request_ban"):
        return
    await query.answer()
    pid = int(query.data.split("_")[-1])
    p = await db.get_player(pid)
    await db.update_player(pid, status="suspended")
    await db.log_action(query.from_user.id, "suspend_player", f"تعلیق: {p['full_name']}", pid)
    await record_destructive_action(ctx.bot, query.from_user.id, "suspend_player")
    await safe_edit_message_text(query, f"⏸️ *{p['full_name']}* تعلیق شد.",
                                   reply_markup=kb.kb_back("player_list"), parse_mode="Markdown")

async def player_revive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if await check_status_gate(query, "ban_player"):
        return
    if await check_perm(query, "request_ban"):
        return
    await query.answer()
    pid = int(query.data.split("_")[-1])
    p = await db.get_player(pid)
    await db.update_player(pid, status="active", warnings=0)
    await db.log_action(query.from_user.id, "revive_player", f"احیا: {p['full_name']}", pid)
    await safe_edit_message_text(query, f"🔄 *{p['full_name']}* احیا شد و به لیست فعال بازگشت.",
                                   reply_markup=kb.kb_back("player_list"), parse_mode="Markdown")

async def player_harddelete_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """قدم اول حذف کامل: فقط مدیر ارشد، و فقط با تأیید — چون برخلاف
    اخراج/تعلیق، این عمل غیرقابل‌بازگشته و سابقه‌ی مسابقات بازیکن هم پاک می‌شه."""
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد می‌تواند حذف کامل انجام دهد.", show_alert=True)
        return
    await query.answer()
    pid = int(query.data.split("_")[-1])
    p = await db.get_player(pid)
    if not p:
        await query.answer("بازیکن یافت نشد.", show_alert=True)
        return
    await safe_edit_message_text(query,
        f"🗑 *{p['full_name']}* برای همیشه از دیتابیس حذف می‌شود.\n"
        f"⚠️ این کار غیرقابل بازگشت است و سابقه‌ی مسابقات این بازیکن هم پاک می‌شود.\n\n"
        f"مطمئنید؟",
        reply_markup=kb.kb_confirm(f"player_harddelete_go_{pid}", f"player_view_{pid}"),
        parse_mode="Markdown")

async def player_harddelete_go(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد می‌تواند حذف کامل انجام دهد.", show_alert=True)
        return
    await query.answer()
    pid = int(query.data.split("_")[-1])
    p = await db.get_player(pid)
    if not p:
        await safe_edit_message_text(query, "بازیکن قبلاً حذف شده.", reply_markup=kb.kb_back("player_list"))
        return
    name = p["full_name"]
    await db.delete_player_hard(pid)
    await db.log_action(query.from_user.id, "delete_player_hard", f"حذف کامل: {name}", pid)
    await record_destructive_action(ctx.bot, query.from_user.id, "delete_player_hard")
    await safe_edit_message_text(query, f"🗑 *{name}* برای همیشه حذف شد.",
                                   reply_markup=kb.kb_back("player_list"), parse_mode="Markdown")

async def player_note_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split("_")[-1])
    ctx.user_data["note_player"] = pid
    p = await db.get_player(pid)
    await safe_edit_message_text(query, f"📝 یادداشت برای *{p['full_name']}*:", parse_mode="Markdown")
    return ST_NOTE_TEXT

async def player_note_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    note = update.message.text.strip()
    pid = ctx.user_data.get("note_player")
    if pid:
        await db.update_player(pid, notes=note)
        await update.message.reply_text("📝 یادداشت ذخیره شد.", reply_markup=kb.kb_back("player_list"))
    return ConversationHandler.END

async def player_elite_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split("_")[-1])
    p = await db.get_player(pid)
    new_val = 0 if p["is_elite"] else 1
    await db.update_player(pid, is_elite=new_val)
    label = "🌟 به برترین‌ها اضافه شد" if new_val else "از برترین‌ها حذف شد"
    await safe_edit_message_text(query, f"✅ {p['full_name']} — {label}",
                                   reply_markup=kb.kb_back("player_list"), parse_mode="Markdown")

async def player_special_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PISHVA_ID:
        await query.answer("⛔ فقط مدیر ارشد می‌تواند نیروی ویژه تعیین کند.", show_alert=True)
        return
    await query.answer()
    pid = int(query.data.split("_")[-1])
    p = await db.get_player(pid)
    new_val = 0 if p["is_special"] else 1
    await db.update_player(pid, is_special=new_val)
    label = "⚡ نیروی ویژه شد" if new_val else "از نیروهای ویژه حذف شد"
    await safe_edit_message_text(query, f"✅ {p['full_name']} — {label}",
                                   reply_markup=kb.kb_back("player_list"), parse_mode="Markdown")

async def player_editname_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split("_")[-1])
    ctx.user_data["edit_player"] = pid
    p = await db.get_player(pid)
    await safe_edit_message_text(query, f"✏️ نام جدید برای *{p['full_name']}*:", parse_mode="Markdown")
    return ST_EDIT_PLAYER_NAME

async def player_editname_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    pid = ctx.user_data.get("edit_player")
    if pid and new_name:
        await db.update_player(pid, full_name=new_name)
        await update.message.reply_text(f"✅ نام به *{new_name}* تغییر یافت.", parse_mode="Markdown")
    return ConversationHandler.END

async def player_editclass_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split("_")[-1])
    ctx.user_data["editclass_player"] = pid
    classes = await db.get_all_classes()
    rows = []
    for i in range(0, len(classes), 2):
        row = [kb.class_btn(c, f"setclass_{pid}_{c['id']}") for c in classes[i:i+2]]
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"player_view_{pid}")])
    await safe_edit_message_text(query, "🏫 کلاس جدید را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(rows))

async def player_setclass(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    pid = int(parts[-2])
    cid = int(parts[-1])
    await db.update_player(pid, class_id=cid)
    c = await db.get_class(cid)
    p = await db.get_player(pid)
    await safe_edit_message_text(query, f"✅ کلاس *{p['full_name']}* به *{c['name']}* تغییر یافت.",
                                   reply_markup=kb.kb_back("player_list"), parse_mode="Markdown")

async def player_search_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # FIX: دکمه‌ی جستجوی داخل هر لیست، context اون لیست رو با خودش می‌آره
    # ("player_search_ctx_continuing" مثلاً)؛ این‌جا ذخیره‌ش می‌کنیم تا وقتی
    # کاربر عبارت جستجو رو می‌فرسته، فقط داخل همون لیست جستجو بشه، نه کل بازیکنان.
    data = query.data
    prefix = "player_search_ctx_"
    search_context = data[len(prefix):] if data.startswith(prefix) else "all"
    ctx.user_data["player_search_ctx"] = search_context
    await safe_edit_message_text(query, f"{box('🔍 جستجو بازیکن')}\n\nنام، نام‌خانوادگی یا کلاس را وارد کنید:",
                                   reply_markup=kb.kb_cancel("back_players"), parse_mode="Markdown")
    return ST_SEARCH_PLAYER

async def player_search_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.message.text.strip()
    search_context = ctx.user_data.pop("player_search_ctx", "all")
    if search_context == "all":
        results = await db.search_players(q)
    else:
        base = await _get_players_by_context(search_context)
        needle = q.casefold()
        results = [
            p for p in base
            if needle in (p["full_name"] or "").casefold()
            or needle in (p["class_name"] or "").casefold()
        ]
    if not results:
        await update.message.reply_text("❗ نتیجه‌ای یافت نشد.", reply_markup=kb.kb_back("players"))
        return ConversationHandler.END
    await update.message.reply_text(f"🔍 نتایج جستجو برای «{q}»:",
                                     reply_markup=kb.kb_player_list(results, context=search_context), parse_mode="Markdown")
    return ConversationHandler.END

async def player_continuing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    players = await db.get_continuing_players()
    if not players:
        await safe_edit_message_text(query, f"{box('✅ بازیکنان ادامه‌دهنده')}\n\n❗ هیچ بازیکن ادامه‌دهنده‌ای وجود ندارد.",
                                       reply_markup=kb.kb_back("players"), parse_mode="Markdown")
        return
    await safe_edit_message_text(query, 
        f"{box('✅ بازیکنان ادامه‌دهنده')}\n\n👥 تعداد: `{len(players)}`",
        reply_markup=kb.kb_player_list(players, context="continuing"), parse_mode="Markdown")

async def player_eliminated(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    players = await db.get_all_players()
    kicked = [p for p in players if p["status"] == "kicked"]
    eliminated = [p for p in players if p["status"] == "eliminated"]
    rows = [
        [InlineKeyboardButton(f"❌ اخراجی‌ها ({len(kicked)})", callback_data="player_list_kicked"),
         InlineKeyboardButton(f"⛔ شکست‌خورده‌ها ({len(eliminated)})", callback_data="player_list_elim")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_players")],
    ]
    await safe_edit_message_text(query, f"{box('❌ بازیکنان حذف‌شده')}\n\n📌 نوع را انتخاب کنید:",
                                   reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")

async def player_list_kicked(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    players = await db.get_all_players()
    kicked = [p for p in players if p["status"] == "kicked"]
    if not kicked:
        await safe_edit_message_text(query, "❗ هیچ بازیکن اخراجی وجود ندارد.", reply_markup=kb.kb_back("player_eliminated"))
        return
    await safe_edit_message_text(query, f"❌ اخراجی‌ها ({len(kicked)}):", reply_markup=kb.kb_player_list(kicked, context="kicked"))

async def player_list_elim(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    players = await db.get_all_players()
    elim = [p for p in players if p["status"] == "eliminated"]
    if not elim:
        await safe_edit_message_text(query, "❗ هیچ بازیکن شکست‌خورده‌ای وجود ندارد.", reply_markup=kb.kb_back("player_eliminated"))
        return
    await safe_edit_message_text(query, f"⛔ شکست‌خورده‌ها ({len(elim)}):", reply_markup=kb.kb_player_list(elim, context="elim"))

async def player_elite_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    players = await db.get_all_players()
    elite = [p for p in players if p["is_elite"]]
    if not elite:
        await safe_edit_message_text(query, "🌟 هیچ بازیکن برتری تعیین نشده.", reply_markup=kb.kb_back("players"))
        return
    await safe_edit_message_text(query, f"🌟 بازیکنان برتر ({len(elite)}):", reply_markup=kb.kb_player_list(elite, context="elite"))

async def player_special_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    players = await db.get_all_players()
    special = [p for p in players if p["is_special"]]
    if not special:
        await safe_edit_message_text(query, "⚡ هیچ نیروی ویژه‌ای تعیین نشده.", reply_markup=kb.kb_back("players"))
        return
    await safe_edit_message_text(query, f"⚡ نیروهای ویژه ({len(special)}):", reply_markup=kb.kb_player_list(special, context="special"))
