"""
servers_panel.py — پنلِ «سرورها» برای مدیر ارشد (فقط روی سرور اصلی).

ورود: دکمه‌ی «🌐 سرورها» در پنل مدیر ارشد، یا کلمه‌ی «سرور / دنیا / ور / جهان».

بخش‌ها:
  • کلی: ساخت سرور، قابلیت‌های همه‌ی سرورها، ساعتِ کاری همه، رهگشا (پرامپتِ کلی)، درخواست‌های ثبت‌نام، بازیابی
  • هر سرور: روشن/خاموش، تعلیق، تغییر نام، قابلیت‌ها، تنظیماتِ ریز، اشخاص (+ شخصی‌سازیِ ریزِ دسترسی‌ها حتی
    برای خودِ مدیر مجموعه)، لاگ‌ها (با فیلتر)، آمار، ساعتِ کاری، لینکِ دعوت، رهگشا، حذف (۳ تأیید) و بازیابی

هر عملِ «داخلِ یک سرور» با srvctx.use(sid) روی دیتابیسِ همان سرور اجرا می‌شود؛ دیتای سرورها هرگز قاطی نمی‌شود.
"""
import html
import logging
import time
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (ApplicationHandlerStop, CallbackQueryHandler, ContextTypes,
                          MessageHandler, filters)

import database as db
import servers
import srvctx
import turso_db
from config import (BOT_USERNAME, DB_PATH, PISHVA_ID, ROLE_SECURITY_MANAGER,
                    ROLE_TOURNAMENT_MANAGER)
from helpers import TEHRAN_TZ, now_shamsi, safe_edit_message_text, safe_send_message

logger = logging.getLogger(__name__)

_WAIT_TTL = 180
ROLE_FA = {ROLE_TOURNAMENT_MANAGER: "🏆 مدیر مسابقات", ROLE_SECURITY_MANAGER: "🛡️ مدیر امنیتی"}

# دسترسی‌های ریزِ هر مدیر (کلیدها همان‌هایی‌اند که create_admin می‌سازد)
PERMS = [
    ("notifications", "دریافت اعلان‌ها"), ("news", "خبر"), ("match_management", "مدیریت مسابقات"),
    ("view_players", "دیدن بازیکنان"), ("issue_warning", "اخطار به بازیکن"), ("request_ban", "درخواست اخراج"),
    ("direct_ban", "اخراج مستقیم"), ("assign_task", "اعطای وظیفه"), ("report", "گزارش"),
    ("bot_active", "دسترسی به ربات"), ("settings_access", "تنظیمات"), ("senior_admin", "مدیر ارشد-یار"),
    ("edit_delete_match", "ویرایش/حذف مسابقه"), ("communications", "مخابرات"), ("ai_access", "رهگشا"),
    ("chess_access", "شطرنج زنده"), ("hub_access", "پنل هاب"),
]
# تنظیماتِ ریزِ هر سرور (کلید در system_settings همان سرور، عنوان، پیش‌فرض)
CFG = [
    ("notifications_enabled", "اعلان‌ها", "1"), ("communications_enabled", "مخابرات", "1"),
    ("help_enabled", "مرکز راهنما", "1"), ("match_registration_enabled", "ثبتِ مسابقه", "1"),
    ("admin_login_enabled", "ورودِ مدیرانِ تازه", "1"), ("bot_active_for_admins", "ربات برای مدیران", "1"),
    ("ai_online", "رهگشا آنلاین", "1"), ("live_chess_enabled", "شطرنج زنده", "1"),
    ("hub_enabled", "پنل هاب", "1"), ("team_mode_enabled", "حالتِ تیمی", "0"),
    ("admin_dashboard_enabled", "داشبوردِ مدیران", "1"), ("admin_webpanel_enabled", "پنلِ وبِ مدیران", "1"),
    ("repair_mode", "حالتِ تعمیر", "0"),
]


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _b(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def _kb(rows):
    return InlineKeyboardMarkup(rows)


async def _guard(q) -> bool:
    if q.from_user.id != PISHVA_ID:
        await q.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return False
    return True


async def _show(q, text, rows, parse_mode="HTML"):
    await safe_edit_message_text(q, text, reply_markup=_kb(rows), parse_mode=parse_mode)


def _set_wait(ctx, kind, **kw):
    ctx.user_data["srv_wait"] = dict(kind=kind, at=time.monotonic(), **kw)


def _srv_title(s) -> str:
    return f"{servers.STATUS_FA.get(s.get('status'), '')} {s.get('name') or '—'} (سرور {s['id']})"


# ═════════════════════════ صفحه‌ی اصلی ═════════════════════════
async def home(q, ctx, note=""):
    subs = servers.sub_servers(include_deleted=False)
    pend = len(await servers.list_requests("pending", 50))
    trash = len([s for s in servers.sub_servers(True) if s.get("status") == servers.ST_DELETED])
    lines = ["🌐 <b>پنلِ سرورها</b>", f"🛰️ <code>{_e(now_shamsi())}</code>", "",
             f"🏠 سرور اصلی: 🟢 روشن (این پنل)",
             f"🏫 مجموعه‌ها: <b>{len(subs)}</b> از {servers.MAX_SERVERS - 1} ظرفیت"]
    if note:
        lines += ["", note]
    rows = []
    for s in subs:
        rows.append([_b(_srv_title(s), f"srv_v_{s['id']}")])
    rows.append([_b("➕ ایجادِ سرور", "srv_new"), _b(f"📥 درخواست‌ها ({pend})", "srv_reqs")])
    rows.append([_b("🎛 قابلیت‌های همه", "srv_gf"), _b("🕐 ساعتِ کاریِ همه", "srv_gw")])
    rows.append([_b("🤖 رهگشا (کلی)", "srv_gai"), _b(f"♻️ بازیابی ({trash})", "srv_trash")])
    rows.append([_b("🔙 بازگشت", "back_main")])
    await _show(q, "\n".join(lines), rows)


# ═════════════════════════ جزئیاتِ یک سرور ═════════════════════════
async def _counts(sid):
    out = {"admins": "—", "players": "—", "matches": "—"}
    try:
        with srvctx.use(sid):
            out["admins"] = len(await db.get_active_admins())
            out["players"] = len(await db.get_continuing_players())
            out["matches"] = len(await db.get_matches_by_filter("today"))
    except Exception:
        logger.exception("counts %s", sid)
    return out


async def detail(q, ctx, sid, note=""):
    s = servers.get(sid)
    if not s:
        await q.answer("سرور پیدا نشد.", show_alert=True)
        return
    c = await _counts(sid)
    wh = "—"
    try:
        with srvctx.use(sid):
            wh = "🟢 باز" if (await db.get_setting("working_hours_active", "0")) == "1" else "🔴 بسته"
    except Exception:
        pass
    owner = s.get("owner_tid")
    text = (f"🏫 <b>{_e(s.get('name'))}</b>  (سرور {sid})\n"
            f"📍 مدرسه/مکان: {_e(s.get('school') or '—')}\n"
            f"📡 وضعیت: {servers.STATUS_FA.get(s.get('status'))}\n"
            + (f"📝 دلیلِ تعلیق: {_e(s.get('suspend_reason'))}\n" if s.get("status") == servers.ST_SUSPENDED and s.get("suspend_reason") else "")
            + f"👑 مدیر مجموعه: <code>{owner}</code>\n"
            f"🕐 ساعت کاری: {wh}\n"
            f"👥 مدیران: <b>{c['admins']}</b> · 👤 بازیکنان: <b>{c['players']}</b> · ♟️ مسابقاتِ امروز: <b>{c['matches']}</b>\n"
            f"🗄 دیتابیس: <code>{_e(s.get('db_name'))}</code> (جدا و مستقل)")
    if note:
        text += f"\n\n{note}"
    st = s.get("status")
    rows = []
    if st in (servers.ST_ACTIVE, servers.ST_OFF):
        rows.append([_b("⚫ خاموش‌کردن" if st == servers.ST_ACTIVE else "🟢 روشن‌کردن", f"srv_t_{sid}"),
                     _b("🟠 تعلیق", f"srv_s_{sid}")])
    elif st == servers.ST_SUSPENDED:
        rows.append([_b("🟢 رفعِ تعلیق", f"srv_u_{sid}")])
    rows += [
        [_b("👥 اشخاص و دسترسی‌ها", f"srv_p_{sid}"), _b("📜 لاگ‌ها", f"srv_l_{sid}")],
        [_b("📈 آمار فعالیت", f"srv_st_{sid}"), _b("🕐 ساعت کاری", f"srv_w_{sid}")],
        [_b("🎛 قابلیت‌ها", f"srv_f_{sid}"), _b("⚙️ تنظیماتِ ریز", f"srv_cfg_{sid}")],
        [_b("🤖 رهگشا", f"srv_ai_{sid}"), _b("🔗 لینکِ دعوت", f"srv_inv_{sid}")],
        [_b("✏️ تغییرِ نام", f"srv_rn_{sid}"), _b("🗑 حذف", f"srv_d_{sid}")],
        [_b("🔙 فهرستِ سرورها", "srv_home")],
    ]
    await _show(q, text, rows)


# ═════════════════════════ قابلیت‌ها ═════════════════════════
async def features_page(q, sid):
    s = servers.get(sid)
    rows, lines = [], [f"🎛 <b>قابلیت‌های {_e(s['name'])}</b>", "هر دکمه یک قابلیت را برای همین سرور روشن/خاموش می‌کند.",
                       "(قابلیت‌های خاموش‌شده‌ی «همه‌ی سرورها» همیشه اولویت دارند.)", ""]
    for key, fa in servers.FEATURES:
        on = (s.get("features") or {}).get(key, True)
        g = servers._GLOBAL_FEATURES.get(key, True)
        mark = "🟢" if on and g else ("🔒" if not g else "🔴")
        rows.append([_b(f"{mark} {fa}", f"srv_ft_{sid}_{key}")])
    rows.append([_b("🔙 بازگشت", f"srv_v_{sid}")])
    await _show(q, "\n".join(lines), rows)


async def global_features(q):
    rows = [[_b(f"{'🟢' if servers._GLOBAL_FEATURES.get(k, True) else '🔴'} {fa}", f"srv_gft_{k}")] for k, fa in servers.FEATURES]
    rows.append([_b("🔙 بازگشت", "srv_home")])
    await _show(q, "🎛 <b>قابلیت‌های «همه‌ی سرورها»</b>\nخاموش‌کردنِ یک قابلیت، آن را برای همه‌ی مجموعه‌ها قطع می‌کند.", rows)


async def cfg_page(q, sid):
    s = servers.get(sid)
    rows = []
    with srvctx.use(sid):
        for i, (k, fa, d) in enumerate(CFG):
            v = await db.get_setting(k, d)
            rows.append([_b(f"{'🟢' if v == '1' else '🔴'} {fa}", f"srv_ct_{sid}_{i}")])
    rows.append([_b("🔙 بازگشت", f"srv_v_{sid}")])
    await _show(q, f"⚙️ <b>تنظیماتِ ریزِ {_e(s['name'])}</b>\n(مستقیماً روی تنظیماتِ همین سرور اثر می‌گذارد)", rows)


# ═════════════════════════ اشخاص ═════════════════════════
async def people(q, sid):
    s = servers.get(sid)
    with srvctx.use(sid):
        admins = await db.get_all_admins()
    rows = []
    for a in admins[:40]:
        tid = int(a["telegram_id"])
        crown = "👑 " if servers.is_owner(tid, sid) else ""
        name = a["display_name"] or a["full_name"] or str(tid)
        role = "مدیر مجموعه" if servers.is_owner(tid, sid) else ROLE_FA.get(a["role"], a["role"])
        rows.append([_b(f"{'🟢' if a['is_active'] else '⚫'} {crown}{name} — {role}", f"srv_pp_{sid}_{tid}")])
    rows.append([_b("🔙 بازگشت", f"srv_v_{sid}")])
    await _show(q, f"👥 <b>اشخاصِ {_e(s['name'])}</b>\nهر شخص را انتخاب کنید تا همه‌چیزش قابلِ کنترل باشد.", rows)


async def person(q, sid, tid, note=""):
    with srvctx.use(sid):
        a = await db.get_admin(tid)
        perms = {}
        if a:
            import json
            try:
                perms = json.loads(a["permissions"] or "{}")
            except Exception:
                perms = {}
    if not a:
        await q.answer("پیدا نشد.", show_alert=True)
        return
    is_o = servers.is_owner(tid, sid)
    name = a["display_name"] or a["full_name"] or str(tid)
    text = (f"👤 <b>{_e(name)}</b> {'👑 (مدیر مجموعه)' if is_o else ''}\n🆔 <code>{tid}</code> {_e(a['username'] or '')}\n"
            f"🎖 مقام: {'مدیر مجموعه' if is_o else ROLE_FA.get(a['role'], a['role'])}\n"
            f"📌 {'🟢 فعال' if a['is_active'] else '⚫ غیرفعال'} · ⚠️ اخطار: {a['warnings'] or 0}")
    if note:
        text += f"\n\n{note}"
    rows = []
    # دسترسی‌ها دو در هر ردیف
    row = []
    for i, (k, fa) in enumerate(PERMS):
        on = perms.get(k, k not in ("direct_ban", "assign_task", "settings_access", "senior_admin"))
        row.append(_b(f"{'🟢' if on else '🔴'} {fa}", f"srv_pt_{sid}_{tid}_{i}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    if not is_o:
        other = ROLE_SECURITY_MANAGER if a["role"] == ROLE_TOURNAMENT_MANAGER else ROLE_TOURNAMENT_MANAGER
        rows.append([_b(f"🔄 مقام ← {ROLE_FA[other]}", f"srv_pr_{sid}_{tid}"),
                     _b("🚪 اخراج" if a["is_active"] else "♻️ بازگرداندن", f"srv_pk_{sid}_{tid}")])
    rows.append([_b("🔙 اشخاص", f"srv_p_{sid}")])
    await _show(q, text, rows)


# ═════════════════════════ لاگ‌ها ═════════════════════════
_PERIOD_FA = {"a": "همه", "t": "امروز", "w": "۷ روز", "m": "۳۰ روز"}
_ROLE_FILTER_FA = {"x": "همه‌ی نقش‌ها", "tm": "🏆 مسابقات", "sc": "🛡️ امنیتی", "ow": "👑 مدیر مجموعه"}


async def logs_menu(q, sid):
    s = servers.get(sid)
    rows = []
    for rk, rfa in _ROLE_FILTER_FA.items():
        rows.append([_b(f"{rfa} · {pfa}", f"srv_lg_{sid}_{pk}_{rk}_0") for pk, pfa in _PERIOD_FA.items()][:2])
        rows.append([_b(f"{rfa} · {pfa}", f"srv_lg_{sid}_{pk}_{rk}_0") for pk, pfa in list(_PERIOD_FA.items())[2:]])
    rows.append([_b("🔙 بازگشت", f"srv_v_{sid}")])
    await _show(q, f"📜 <b>لاگ‌های {_e(s['name'])}</b>\nنقش و بازه را انتخاب کنید:", rows)


async def logs_view(q, sid, period, role, page):
    page = max(int(page), 0)
    size = 8
    cond, params = [], []
    now = datetime.now(TEHRAN_TZ).replace(tzinfo=None)
    if period == "t":
        cond.append("logged_at LIKE ?"); params.append(now.strftime("%Y-%m-%d") + "%")
    elif period == "w":
        cond.append("logged_at >= ?"); params.append((now - timedelta(days=7)).isoformat())
    elif period == "m":
        cond.append("logged_at >= ?"); params.append((now - timedelta(days=30)).isoformat())
    names = {}
    with srvctx.use(sid):
        admins = await db.get_all_admins()
        for a in admins:
            names[int(a["telegram_id"])] = a["display_name"] or a["full_name"] or str(a["telegram_id"])
        if role != "x":
            if role == "ow":
                ids = [servers.owner_tid(sid)]
            else:
                want = ROLE_TOURNAMENT_MANAGER if role == "tm" else ROLE_SECURITY_MANAGER
                ids = [int(a["telegram_id"]) for a in admins if a["role"] == want and not servers.is_owner(int(a["telegram_id"]), sid)]
            ids = [i for i in ids if i]
            if not ids:
                cond.append("1=0")
            else:
                cond.append(f"admin_id IN ({','.join('?' * len(ids))})"); params += ids
        where = ("WHERE " + " AND ".join(cond)) if cond else ""
        async with turso_db.connect(DB_PATH) as c:
            async with c.execute(f"SELECT COUNT(*) FROM action_logs {where}", tuple(params)) as cur:
                total = (await cur.fetchone())[0]
            async with c.execute(
                    f"SELECT admin_id, action_type, description, logged_at FROM action_logs {where} "
                    f"ORDER BY id DESC LIMIT ? OFFSET ?", tuple(params) + (size, page * size)) as cur:
                rows_ = await cur.fetchall()
    lines = [f"📜 <b>لاگ — {_ROLE_FILTER_FA[role]} · {_PERIOD_FA[period]}</b>  ({total} مورد)", ""]
    if not rows_:
        lines.append("— موردی نیست —")
    for r in rows_:
        who = names.get(int(r[0]), str(r[0])) if r[0] else "سیستم"
        lines.append(f"• <code>{_e(str(r[3])[5:16])}</code> <b>{_e(who)}</b>\n  {_e(r[1])}: {_e(str(r[2])[:140])}")
    nav = []
    if page > 0:
        nav.append(_b("◀️ قبلی", f"srv_lg_{sid}_{period}_{role}_{page - 1}"))
    if (page + 1) * size < total:
        nav.append(_b("بعدی ▶️", f"srv_lg_{sid}_{period}_{role}_{page + 1}"))
    rows = ([nav] if nav else []) + [[_b("🔙 فیلترها", f"srv_l_{sid}")]]
    await _show(q, "\n".join(lines), rows)


# ═════════════════════════ آمار ═════════════════════════
async def stats(q, sid):
    s = servers.get(sid)
    since = (datetime.now(TEHRAN_TZ).replace(tzinfo=None) - timedelta(days=7)).isoformat()
    lines = [f"📈 <b>آمارِ {_e(s['name'])}</b>", ""]
    c = await _counts(sid)
    lines.append(f"👥 مدیران: {c['admins']} · 👤 بازیکنان: {c['players']} · ♟️ مسابقاتِ امروز: {c['matches']}")
    with srvctx.use(sid):
        names = {int(a["telegram_id"]): (a["display_name"] or a["full_name"] or str(a["telegram_id"]))
                 for a in await db.get_all_admins()}
        async with turso_db.connect(DB_PATH) as cn:
            async with cn.execute("SELECT COUNT(*) FROM action_logs WHERE logged_at >= ?", (since,)) as cur:
                tot = (await cur.fetchone())[0]
            async with cn.execute(
                    "SELECT admin_id, COUNT(*) c FROM action_logs WHERE logged_at >= ? GROUP BY admin_id ORDER BY c DESC LIMIT 10",
                    (since,)) as cur:
                top = await cur.fetchall()
            async with cn.execute(
                    "SELECT action_type, COUNT(*) c FROM action_logs WHERE logged_at >= ? GROUP BY action_type ORDER BY c DESC LIMIT 8",
                    (since,)) as cur:
                kinds = await cur.fetchall()
    lines += ["", f"🧾 اقداماتِ ۷ روز اخیر: <b>{tot}</b>", "", "🏅 فعال‌ترین‌ها:"]
    for r in top:
        lines.append(f"• {_e(names.get(int(r[0]), r[0]) if r[0] else 'سیستم')}: {r[1]}")
    lines += ["", "🔧 پرتکرارترین اقدام‌ها:"]
    for r in kinds:
        lines.append(f"• {_e(r[0])}: {r[1]}")
    await _show(q, "\n".join(lines), [[_b("🔄 تازه‌سازی", f"srv_st_{sid}"), _b("🔙 بازگشت", f"srv_v_{sid}")]])


# ═════════════════════════ ساعت کاری ═════════════════════════
async def workhours_page(q, sid, note=""):
    s = servers.get(sid)
    with srvctx.use(sid):
        act = (await db.get_setting("working_hours_active", "0")) == "1"
    text = f"🕐 <b>ساعت کاریِ {_e(s['name'])}</b>\nوضعیت: {'🟢 باز' if act else '🔴 بسته'}"
    if note:
        text += f"\n\n{note}"
    await _show(q, text, [[_b("🟢 آغاز", f"srv_wo_{sid}"), _b("🔴 پایان", f"srv_wc_{sid}")],
                          [_b("🔙 بازگشت", f"srv_v_{sid}")]])


async def _wh_apply(ctx, sid, start: bool):
    import workhours as wh
    with srvctx.use(sid):
        if start:
            await wh._do_start(ctx.bot, ctx.application.job_queue)
        else:
            await wh._do_end(ctx.bot, ctx.application.job_queue, reason="manual")


async def global_workhours(q, note=""):
    await _show(q, "🕐 <b>ساعتِ کاریِ همه‌ی مجموعه‌های فعال</b>\n" + (note or "یک عملیات را برای همه اجرا کنید:"),
                [[_b("🟢 آغازِ همه", "srv_gwo"), _b("🔴 پایانِ همه", "srv_gwc")], [_b("🔙 بازگشت", "srv_home")]])


# ═════════════════════════ لینکِ دعوت ═════════════════════════
async def invite_page(q, sid, ctx):
    s = servers.get(sid)
    code = s.get("invite_code") or ""
    uname = BOT_USERNAME or (ctx.bot.username or "")
    text = f"🔗 <b>لینکِ دعوتِ {_e(s['name'])}</b>\n\n"
    text += (f"<code>https://t.me/{_e(uname)}?start=join_{_e(code)}</code>" if code else "غیرفعال است.")
    await _show(q, text, [[_b("♻️ لینکِ تازه", f"srv_invn_{sid}"), _b("🚫 غیرفعال", f"srv_invo_{sid}")],
                          [_b("🔙 بازگشت", f"srv_v_{sid}")]])


# ═════════════════════════ رهگشا ═════════════════════════
async def ai_page(q, sid):
    s = servers.get(sid)
    with srvctx.use(sid):
        online = (await db.get_setting("ai_online", "1")) == "1"
        prompt = await db.get_setting("ai_server_prompt", "")
    text = (f"🤖 <b>رهگشا — {_e(s['name'])}</b>\nوضعیت: {'🟢 آنلاین' if online else '🔴 آفلاین'}\n\n"
            f"📝 پرامپتِ اختصاصیِ این سرور:\n<i>{_e(prompt[:600]) if prompt else '— ندارد —'}</i>")
    await _show(q, text, [[_b("🔴 آفلاین‌کردن" if online else "🟢 آنلاین‌کردن", f"srv_aio_{sid}")],
                          [_b("✏️ تنظیمِ پرامپتِ اختصاصی", f"srv_aip_{sid}"), _b("🧹 پاک‌کردن", f"srv_aic_{sid}")],
                          [_b("💬 پرسش از رهگشا درباره‌ی این سرور", f"srv_ask_{sid}")],
                          [_b("🔙 بازگشت", f"srv_v_{sid}")]])


async def global_ai(q):
    g = await _get_meta("ai_global_prompt")
    text = ("🤖 <b>رهگشا — کنترلِ کلی</b>\nاین پرامپت به رهگشای «همه‌ی مجموعه‌ها» اضافه می‌شود "
            "(شیوه‌ی فعالیت و محدودیت‌های کلی).\n\n"
            f"<i>{_e(g[:800]) if g else '— ندارد —'}</i>")
    await _show(q, text, [[_b("✏️ تنظیمِ پرامپتِ کلی", "srv_gaip"), _b("🧹 پاک‌کردن", "srv_gaic")],
                          [_b("🔙 بازگشت", "srv_home")]])


async def _get_meta(key, default=""):
    with srvctx.use(srvctx.MAIN_ID):
        async with turso_db.connect(DB_PATH) as c:
            async with c.execute("SELECT value FROM server_meta WHERE key=?", (key,)) as cur:
                r = await cur.fetchone()
    return r[0] if r else default


async def _set_meta(key, value):
    await servers._exec("INSERT OR REPLACE INTO server_meta(key,value) VALUES (?,?)", (key, value))
    servers.META_CACHE[key] = value


# ═════════════════════════ حذف / بازیابی ═════════════════════════
async def delete_step(q, sid, step):
    s = servers.get(sid)
    name = _e(s["name"])
    if step == 1:
        text = (f"🗑 <b>حذفِ سرورِ «{name}»</b>\n\nمرحله‌ی ۱ از ۳\nاعضای این سرور فوراً دسترسی‌شان را از دست می‌دهند.\n"
                "دیتابیس پاک <b>نمی‌شود</b> و از «♻️ بازیابی» برمی‌گردد.")
        rows = [[_b("ادامه ⏭", f"srv_d2_{sid}"), _b("❌ انصراف", f"srv_v_{sid}")]]
    elif step == 2:
        text = f"🗑 <b>حذفِ «{name}»</b>\n\nمرحله‌ی ۲ از ۳ — مطمئنید؟"
        rows = [[_b("بله، ادامه ⏭", f"srv_d3_{sid}"), _b("❌ انصراف", f"srv_v_{sid}")]]
    else:
        text = f"🗑 <b>حذفِ «{name}»</b>\n\nمرحله‌ی ۳ از ۳ — آخرین تأیید."
        rows = [[_b("🗑 حذفِ نهایی", f"srv_d4_{sid}"), _b("❌ انصراف", f"srv_v_{sid}")]]
    await _show(q, text, rows)


async def trash(q):
    gone = [s for s in servers.sub_servers(True) if s.get("status") == servers.ST_DELETED]
    rows = [[_b(f"♻️ {s['name']} (سرور {s['id']})", f"srv_rec_{s['id']}")] for s in gone]
    rows.append([_b("🔙 بازگشت", "srv_home")])
    await _show(q, "♻️ <b>بازیابیِ سرورهای حذف‌شده</b>\n" + ("یکی را انتخاب کنید:" if gone else "موردی نیست."), rows)


# ═════════════════════════ درخواست‌های ثبت‌نام ═════════════════════════
def request_text(r) -> str:
    return ("🏫 <b>درخواستِ ثبتِ مدیر مجموعه</b>\n\n"
            f"👤 نام و نام‌خانوادگی: {_e(r['full_name'])}\n"
            f"🆔 آیدی: {_e(r['username'])}  (<code>{r['telegram_id']}</code>)\n"
            f"🏫 مدرسه/مکانِ فعالیت: {_e(r['school'])}\n"
            f"📅 تاریخِ موردنیازِ سرویس: {_e(r['need_date'])}\n"
            f"📞 تلفن: {_e(r['phone'])}\n"
            f"🎓 مقطع/رشته: {_e(r['grade_field'])}")


def request_kb(rid):
    return _kb([[_b("✅ تأیید و ساختِ سرور", f"sreq_ok_{rid}"), _b("❌ رد", f"sreq_no_{rid}")],
                [_b("🚫 بلاک دائم", f"sreq_bl_{rid}")]])


async def requests_page(q):
    reqs = await servers.list_requests("pending", 20)
    rows = [[_b(f"{r['school']} — {r['full_name']}", f"srv_rq_{r['id']}")] for r in reqs]
    rows.append([_b("🔙 بازگشت", "srv_home")])
    await _show(q, "📥 <b>درخواست‌های ثبتِ مدیر مجموعه</b>\n" + ("" if reqs else "درخواستِ بازی نیست."), rows)


async def approve_request(q, ctx, rid):
    r = await servers.get_request(rid)
    if not r or r["status"] != "pending":
        await q.answer("این درخواست قبلاً بررسی شده.", show_alert=True)
        return
    await q.answer("⏳ در حال ساختِ سرور…")
    try:
        sid = await servers.create_server(r["school"], r["school"], int(r["telegram_id"]), r["username"], r["full_name"])
    except Exception as ex:
        logger.exception("create_server")
        await safe_edit_message_text(q, f"❌ ساختِ سرور ناموفق بود:\n\n{_e(ex)}", reply_markup=_kb([[_b("🔙 بازگشت", "srv_home")]]),
                                     parse_mode="HTML")
        return
    await servers.decide_request(rid, "approved", sid)
    await db.log_action(PISHVA_ID, "server_create", f"سرور {sid} برای «{r['school']}» ساخته شد", int(r["telegram_id"]))
    try:
        await ctx.bot.send_message(int(r["telegram_id"]),
                                   f"🎉 <b>درخواستِ شما تأیید شد!</b>\n\n🏫 مجموعه‌ی «{_e(r['school'])}» ساخته شد و شما مدیر مجموعه هستید.\n"
                                   "برای شروع /start را بزنید.", parse_mode="HTML")
    except Exception:
        pass
    await safe_edit_message_text(q, f"✅ سرور {sid} برای «{_e(r['school'])}» ساخته شد و به مدیر مجموعه اطلاع داده شد.",
                                 reply_markup=_kb([[_b("🏫 مشاهده‌ی سرور", f"srv_v_{sid}"), _b("🔙 فهرست", "srv_home")]]),
                                 parse_mode="HTML")


async def reject_request(q, ctx, rid, block=False):
    r = await servers.get_request(rid)
    if not r or r["status"] != "pending":
        await q.answer("این درخواست قبلاً بررسی شده.", show_alert=True)
        return
    await servers.decide_request(rid, "rejected")
    await servers.remove_member(int(r["telegram_id"]))
    if block:
        try:
            await db.block_user(int(r["telegram_id"]), r["username"] or "", r["full_name"] or "", "بلاک توسط مدیر ارشد (درخواست مدیر مجموعه)", PISHVA_ID)
        except Exception:
            logger.exception("block")
    try:
        await ctx.bot.send_message(int(r["telegram_id"]), "❌ متأسفانه درخواستِ ثبتِ مجموعه‌ی شما رد شد.")
    except Exception:
        pass
    await safe_edit_message_text(q, "❌ درخواست رد شد." + (" و فرد بلاک شد." if block else ""),
                                 reply_markup=_kb([[_b("🔙 بازگشت", "srv_home")]]))


# ═════════════════════════ روتر کالبک‌ها ═════════════════════════
async def router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if srvctx.current() != srvctx.MAIN_ID or not await _guard(q):
        return
    d = q.data
    ctx.user_data.pop("srv_wait", None)
    try:
        if d == "srv_home":
            await q.answer(); return await home(q, ctx)
        if d == "srv_new":
            await q.answer(); _set_wait(ctx, "new_name")
            return await _show(q, "➕ <b>ایجادِ سرور</b>\nنامِ مجموعه/مدرسه را بفرستید:", [[_b("❌ انصراف", "srv_home")]])
        if d == "srv_reqs":
            await q.answer(); return await requests_page(q)
        if d == "srv_trash":
            await q.answer(); return await trash(q)
        if d == "srv_gf":
            await q.answer(); return await global_features(q)
        if d.startswith("srv_gft_"):
            k = d[8:]
            await servers.set_global_feature(k, not servers._GLOBAL_FEATURES.get(k, True))
            await q.answer("✅"); return await global_features(q)
        if d == "srv_gw":
            await q.answer(); return await global_workhours(q)
        if d in ("srv_gwo", "srv_gwc"):
            await q.answer("⏳ در حال اجرا…")
            n = 0
            for sid in servers.active_ids():
                if sid == srvctx.MAIN_ID:
                    continue
                try:
                    await _wh_apply(ctx, sid, d == "srv_gwo"); n += 1
                except Exception:
                    logger.exception("global workhours %s", sid)
            return await global_workhours(q, f"✅ برای {n} سرور انجام شد.")
        if d == "srv_gai":
            await q.answer(); return await global_ai(q)
        if d == "srv_gaip":
            await q.answer(); _set_wait(ctx, "g_ai")
            return await _show(q, "✏️ پرامپتِ کلیِ رهگشا را بفرستید:", [[_b("❌ انصراف", "srv_gai")]])
        if d == "srv_gaic":
            await _set_meta("ai_global_prompt", ""); await q.answer("🧹"); return await global_ai(q)
        if d.startswith("srv_rq_"):
            r = await servers.get_request(int(d[7:])); await q.answer()
            if not r:
                return
            return await _show(q, request_text(r), request_kb(r["id"]).inline_keyboard)
        if d.startswith("srv_rec_"):
            sid = int(d[8:]); await q.answer()
            return await _show(q, f"♻️ بازیابیِ «{_e(servers.get(sid)['name'])}»؟",
                               [[_b("✅ بازیابی", f"srv_rec2_{sid}"), _b("❌ انصراف", "srv_trash")]])
        if d.startswith("srv_rec2_"):
            sid = int(d[9:])
            await servers.set_status(sid, servers.ST_ACTIVE)
            await db.log_action(PISHVA_ID, "server_restore", f"سرور {sid} بازیابی شد")
            await q.answer("✅ بازیابی شد"); return await detail(q, ctx, sid)

        # ─── مسیرهای دارای شناسه‌ی سرور ───
        parts = d.split("_")           # srv, <op>, <sid>, ...
        op, sid = parts[1], int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        if sid is None or not servers.get(sid):
            return await q.answer("سرور پیدا نشد.", show_alert=True)
        s = servers.get(sid)
        if op == "v":
            await q.answer(); return await detail(q, ctx, sid)
        if op == "t":
            await servers.set_status(sid, servers.ST_OFF if s["status"] == servers.ST_ACTIVE else servers.ST_ACTIVE)
            await db.log_action(PISHVA_ID, "server_toggle", f"سرور {sid} → {servers.get(sid)['status']}")
            await q.answer("✅"); return await detail(q, ctx, sid)
        if op == "s":
            await q.answer(); _set_wait(ctx, "suspend", sid=sid)
            return await _show(q, "🟠 دلیلِ تعلیق را بفرستید (برای اعضا نمایش داده می‌شود):", [[_b("❌ انصراف", f"srv_v_{sid}")]])
        if op == "u":
            await servers.set_status(sid, servers.ST_ACTIVE)
            await db.log_action(PISHVA_ID, "server_unsuspend", f"تعلیقِ سرور {sid} برداشته شد")
            await q.answer("✅"); return await detail(q, ctx, sid)
        if op == "rn":
            await q.answer(); _set_wait(ctx, "rename", sid=sid)
            return await _show(q, "✏️ نامِ جدیدِ سرور را بفرستید:", [[_b("❌ انصراف", f"srv_v_{sid}")]])
        if op == "f":
            await q.answer(); return await features_page(q, sid)
        if op == "ft":
            key = "_".join(parts[3:])
            cur = (s.get("features") or {}).get(key, True)
            await servers.set_feature(sid, key, not cur)
            await q.answer("✅"); return await features_page(q, sid)
        if op == "cfg":
            await q.answer(); return await cfg_page(q, sid)
        if op == "ct":
            k, fa, dflt = CFG[int(parts[3])]
            with srvctx.use(sid):
                v = await db.get_setting(k, dflt)
                await db.set_setting(k, "0" if v == "1" else "1")
            await db.log_action(PISHVA_ID, "server_setting", f"سرور {sid}: {k}")
            await q.answer("✅"); return await cfg_page(q, sid)
        if op == "p":
            await q.answer(); return await people(q, sid)
        if op == "pp":
            await q.answer(); return await person(q, sid, int(parts[3]))
        if op == "pt":
            tid, pi = int(parts[3]), int(parts[4])
            key = PERMS[pi][0]
            with srvctx.use(sid):
                import json
                a = await db.get_admin(tid)
                perms = json.loads(a["permissions"] or "{}") if a else {}
                cur = perms.get(key, key not in ("direct_ban", "assign_task", "settings_access", "senior_admin"))
                await db.set_admin_permission(tid, key, not cur)
            await db.log_action(PISHVA_ID, "server_perm", f"سرور {sid}: {key} برای {tid}", tid)
            await q.answer("✅"); return await person(q, sid, tid)
        if op == "pr":
            tid = int(parts[3])
            with srvctx.use(sid):
                a = await db.get_admin(tid)
                new = ROLE_SECURITY_MANAGER if a["role"] == ROLE_TOURNAMENT_MANAGER else ROLE_TOURNAMENT_MANAGER
                await db.set_admin_role(tid, new)
            await q.answer("✅"); return await person(q, sid, tid)
        if op == "pk":
            tid = int(parts[3])
            if servers.is_owner(tid, sid):
                return await q.answer("برای مدیر مجموعه از «حذف سرور» استفاده کنید.", show_alert=True)
            with srvctx.use(sid):
                a = await db.get_admin(tid)
                if a["is_active"]:
                    await db.kick_admin(tid)
                else:
                    await db.revive_admin(tid)
            await q.answer("✅"); return await person(q, sid, tid)
        if op == "l":
            await q.answer(); return await logs_menu(q, sid)
        if op == "lg":
            await q.answer(); return await logs_view(q, sid, parts[3], parts[4], parts[5])
        if op == "st":
            await q.answer(); return await stats(q, sid)
        if op == "w":
            await q.answer(); return await workhours_page(q, sid)
        if op in ("wo", "wc"):
            await _wh_apply(ctx, sid, op == "wo")
            await q.answer("✅"); return await workhours_page(q, sid, "✅ انجام شد.")
        if op == "inv":
            await q.answer(); return await invite_page(q, sid, ctx)
        if op == "invn":
            await servers.regenerate_invite(sid); await q.answer("✅"); return await invite_page(q, sid, ctx)
        if op == "invo":
            await servers.disable_invite(sid); await q.answer("✅"); return await invite_page(q, sid, ctx)
        if op == "ai":
            await q.answer(); return await ai_page(q, sid)
        if op == "aio":
            with srvctx.use(sid):
                v = await db.get_setting("ai_online", "1")
                await db.set_setting("ai_online", "0" if v == "1" else "1")
            await q.answer("✅"); return await ai_page(q, sid)
        if op == "aip":
            await q.answer(); _set_wait(ctx, "s_ai", sid=sid)
            return await _show(q, "✏️ پرامپتِ اختصاصیِ رهگشای این سرور را بفرستید:", [[_b("❌ انصراف", f"srv_ai_{sid}")]])
        if op == "aic":
            with srvctx.use(sid):
                await db.set_setting("ai_server_prompt", "")
            await q.answer("🧹"); return await ai_page(q, sid)
        if op == "ask":
            ctx.user_data["ai_focus_server"] = sid
            await q.answer()
            return await _show(q, f"💬 حالا در رهگشا (دکمه‌ی 🤖 رهگشا در منوی اصلی) هر پرسشی درباره‌ی «{_e(s['name'])}» بپرسید؛ "
                                  "رهگشا درباره‌ی اشخاص، بازیکن‌ها، لاگ‌ها و آمارِ همین سرور اطلاعاتِ کامل دارد.",
                               [[_b("🤖 باز کردنِ رهگشا", "ai_assistant_open"), _b("🔙 بازگشت", f"srv_v_{sid}")]])
        if op == "d":
            await q.answer(); return await delete_step(q, sid, 1)
        if op == "d2":
            await q.answer(); return await delete_step(q, sid, 2)
        if op == "d3":
            await q.answer(); return await delete_step(q, sid, 3)
        if op == "d4":
            await servers.set_status(sid, servers.ST_DELETED)
            await db.log_action(PISHVA_ID, "server_delete", f"سرور {sid} حذف شد (قابل بازیابی)")
            await q.answer("🗑 حذف شد"); return await home(q, ctx, "🗑 سرور حذف شد؛ از «♻️ بازیابی» برمی‌گردد.")
    except ApplicationHandlerStop:
        raise
    except Exception:
        logger.exception("servers_panel router: %s", d)
        try:
            await q.answer("❌ خطا در اجرای عملیات", show_alert=True)
        except Exception:
            pass


async def request_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if srvctx.current() != srvctx.MAIN_ID or not await _guard(q):
        return
    d = q.data
    rid = int(d.rsplit("_", 1)[1])
    if d.startswith("sreq_ok_"):
        return await approve_request(q, ctx, rid)
    if d.startswith("sreq_no_"):
        return await reject_request(q, ctx, rid)
    if d.startswith("sreq_bl_"):
        return await reject_request(q, ctx, rid, block=True)


# ═════════════════════════ ورودیِ متنی ═════════════════════════
async def text_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    w = ctx.user_data.get("srv_wait")
    if not w or update.effective_user.id != PISHVA_ID or srvctx.current() != srvctx.MAIN_ID:
        return
    if time.monotonic() - w["at"] > _WAIT_TTL:
        ctx.user_data.pop("srv_wait", None)
        return
    ctx.user_data.pop("srv_wait", None)
    text = (update.message.text or "").strip()
    kind = w["kind"]
    if kind == "new_name":
        _set_wait(ctx, "new_owner", name=text[:60])
        await update.message.reply_text("🆔 حالا «آیدیِ عددیِ تلگرام» مدیر مجموعه را بفرستید\n(او باید حداقل یک‌بار ربات را /start کرده باشد):")
    elif kind == "new_owner":
        if not text.lstrip("-").isdigit():
            _set_wait(ctx, "new_owner", name=w["name"])
            return await update.message.reply_text("❌ آیدی باید عدد باشد. دوباره بفرستید:")
        owner = int(text)
        await update.message.reply_text("⏳ در حال ساختِ سرور…")
        try:
            sid = await servers.create_server(w["name"], w["name"], owner, "", "")
        except Exception as ex:
            logger.exception("manual create_server")
            await update.message.reply_text(f"❌ ساخته نشد:\n{ex}")
            raise ApplicationHandlerStop
        await db.log_action(PISHVA_ID, "server_create", f"سرور {sid} («{w['name']}») دستی ساخته شد", owner)
        try:
            await ctx.bot.send_message(owner, f"🎉 مجموعه‌ی «{w['name']}» برای شما ساخته شد و شما مدیر مجموعه هستید.\n/start را بزنید.")
            extra = "و به مدیر مجموعه اطلاع داده شد."
        except Exception:
            extra = "(پیام به مدیر مجموعه نرسید؛ باید ربات را /start کند.)"
        await update.message.reply_text(f"✅ سرور {sid} ساخته شد {extra}\nبرای مشاهده کلمه‌ی «سرور» را بفرستید.")
    elif kind == "rename":
        await servers.rename(w["sid"], text)
        await update.message.reply_text("✅ نام تغییر کرد.")
    elif kind == "suspend":
        await servers.set_status(w["sid"], servers.ST_SUSPENDED, text[:300])
        await db.log_action(PISHVA_ID, "server_suspend", f"سرور {w['sid']} تعلیق شد: {text[:200]}")
        await update.message.reply_text("🟠 سرور تعلیق شد.")
    elif kind == "s_ai":
        with srvctx.use(w["sid"]):
            await db.set_setting("ai_server_prompt", text[:4000])
        await update.message.reply_text("✅ پرامپتِ اختصاصی ذخیره شد.")
    elif kind == "g_ai":
        await _set_meta("ai_global_prompt", text[:4000])
        await update.message.reply_text("✅ پرامپتِ کلی ذخیره شد.")
    raise ApplicationHandlerStop


def register(app):
    app.add_handler(CallbackQueryHandler(router, pattern=r"^srv_"))
    app.add_handler(CallbackQueryHandler(request_router, pattern=r"^sreq_(ok|no|bl)_\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input), group=-4)


async def open_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """ورود با کلمه‌ی «سرور/دنیا/ور/جهان» (از keyword_commands)."""
    if update.effective_user.id != PISHVA_ID or srvctx.current() != srvctx.MAIN_ID:
        return
    subs = servers.sub_servers(include_deleted=False)
    pend = len(await servers.list_requests("pending", 50))
    rows = [[_b(_srv_title(s), f"srv_v_{s['id']}")] for s in subs]
    rows.append([_b("➕ ایجادِ سرور", "srv_new"), _b(f"📥 درخواست‌ها ({pend})", "srv_reqs")])
    rows.append([_b("🎛 قابلیت‌های همه", "srv_gf"), _b("🕐 ساعتِ کاریِ همه", "srv_gw")])
    rows.append([_b("🤖 رهگشا (کلی)", "srv_gai"), _b("♻️ بازیابی", "srv_trash")])
    await update.message.reply_text(
        f"🌐 <b>پنلِ سرورها</b>\n🏫 مجموعه‌ها: <b>{len(subs)}</b> از {servers.MAX_SERVERS - 1} ظرفیت",
        parse_mode="HTML", reply_markup=_kb(rows))
