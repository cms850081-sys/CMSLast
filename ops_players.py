"""
ops_players.py — دستورات «مدیریت بازیکن» (فقط در تاپیکِ «مدیریت بازیکن» کار می‌کنند).

هر دستور هم با «/» کار می‌کند هم با کلمه‌ی فارسی. نام و متن را با «|» یا «:» جدا کنید:
    /warn علی رضایی | دیر آمد          ⇄   اخطار علی رضایی | دیر آمد
شناسه‌ی عددی هم قبول است:  /note #12 | ...
مجوزها همان مجوزهای پنلِ ربات است (issue_warning / request_ban)؛ مدیر ارشد همه‌چیز را دارد.
"""
import html
import json
import logging
import re
from datetime import datetime

import turso_db
import database as db
from config import DB_PATH, PISHVA_ID, ROLE_PISHVA

logger = logging.getLogger(__name__)

h = lambda x: html.escape(str(x if x is not None else ""), quote=False)   # noqa: E731
MAX_LIST = 25
NOTES_CAP = 2500

# نام‌های جایگزین → دستور
ALIASES = {
    "warn": ("warn", "ekhtar", "اخطار"),
    "unwarn": ("unwarn", "حذف_اخطار", "حذف اخطار"),
    "clearwarn": ("clearwarn", "پاکسازی_اخطار", "پاکسازی اخطار", "پاک_کردن_اخطارها"),
    "warnings": ("warnings", "warned", "اخطارها"),
    "history": ("history", "سابقه", "سابقه_اخطار"),
    "top": ("top", "topwarned", "پرخطرها"),
    "recent": ("recent", "اخیر", "اخطارهای_اخیر"),
    "note": ("note", "یادداشت"),
    "notes": ("notes", "یادداشت‌ها", "یادداشتها"),
    "clearnotes": ("clearnotes", "حذف_یادداشت‌ها", "پاک_کردن_یادداشت‌ها"),
    "player": ("player", "p", "پرونده", "بازیکن"),
    "find": ("find", "search", "who", "جستجو", "پیدا"),
    "suspend": ("suspend", "تعلیق"),
    "revive": ("revive", "unsuspend", "احیا"),
    "suspended": ("suspended", "تعلیقی‌ها", "تعلیق‌شده‌ها"),
    "watch": ("watch", "تحت_نظر"),
    "unwatch": ("unwatch", "رفع_نظارت"),
    "watchlist": ("watchlist", "لیست_نظارت", "تحت_نظرها"),
    "class": ("class", "کلاس"),
    "summary": ("summary", "stats", "آمار", "خلاصه"),
    "help": ("phelp", "help", "راهنما", "کمک"),
}
_LOOKUP = {a.lower(): k for k, v in ALIASES.items() for a in v}

HELP = """♟️ <b>راهنمای مدیریت بازیکن</b>
هر دستور با «/» یا با کلمه‌ی فارسی کار می‌کند. نام و متن را با | جدا کنید. به‌جای نام می‌شود #شناسه داد.

<b>⚠️ اخطار</b>
• <code>/warn نام | دلیل</code> — ثبتِ اخطار   (اخطار)
• <code>/unwarn نام [شماره]</code> — حذفِ یک اخطار (آخرین، یا شماره‌ی /history)
• <code>/clearwarn نام</code> — پاک‌کردنِ همه‌ی اخطارها (مدیر ارشد)
• <code>/history نام</code> — سابقه‌ی کاملِ اخطارها
• <code>/warnings</code> — بازیکن‌های دارای اخطار
• <code>/top</code> — پراخطارترین‌ها
• <code>/recent [تعداد]</code> — اخطارهای اخیر در کلِ باشگاه

<b>📝 یادداشت</b>
• <code>/note نام | متن</code> — افزودنِ یادداشت   (یادداشت)
• <code>/notes نام</code> — خواندنِ یادداشت‌ها
• <code>/clearnotes نام</code> — پاک‌کردنِ یادداشت‌ها (مدیر ارشد)

<b>👁 نظارتِ امنیتی</b>
• <code>/watch نام | دلیل</code> — گذاشتن «تحت نظر»   (تحت_نظر)
• <code>/unwatch نام</code> — برداشتنِ نظارت
• <code>/watchlist</code> — فهرستِ تحت‌نظرها

<b>🔎 پرونده و جست‌وجو</b>
• <code>/player نام</code> — پروفایلِ کامل   (پرونده)
• <code>/find متن</code> — جست‌وجوی نام/کلاس
• <code>/class نامِ‌کلاس</code> — بازیکن‌های یک کلاس
• <code>/summary</code> — آمارِ کلی

<b>⏸ وضعیت</b>
• <code>/suspend نام | دلیل</code> — تعلیق   (تعلیق)
• <code>/revive نام</code> — احیا (اخطارها صفر می‌شود)
• <code>/suspended</code> — فهرستِ تعلیق‌شده‌ها

مثال: <code>اخطار علی رضایی | بی‌احترامی در مسابقه</code>"""


# ═══════════════════════ جدولِ نظارت ═══════════════════════
_ok = False


async def _ensure():
    global _ok
    if _ok:
        return
    async with turso_db.connect(DB_PATH) as c:
        await c.execute("CREATE TABLE IF NOT EXISTS ops_watch (player_id INTEGER PRIMARY KEY, reason TEXT, by_id INTEGER, at TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS ops_commands (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, slash TEXT, "
                        "words TEXT, kind TEXT, base TEXT, template TEXT, with_player INTEGER DEFAULT 0, boss_only INTEGER DEFAULT 0, "
                        "help TEXT, active INTEGER DEFAULT 1, created_by INTEGER, created_at TEXT)")
        await c.commit()
    _ok = True


async def _q(sql, args=()):
    await _ensure()
    async with turso_db.connect(DB_PATH) as c:
        c.row_factory = turso_db.Row
        async with c.execute(sql, tuple(args)) as cur:
            return list(await cur.fetchall())


async def _x(sql, args=()):
    await _ensure()
    async with turso_db.connect(DB_PATH) as c:
        await c.execute(sql, tuple(args))
        await c.commit()


# ═══════════════════════ ابزارها ═══════════════════════
def _parse(text: str):
    """→ (command|None, arg)"""
    t = (text or "").strip()
    if not t:
        return None, ""
    first, _, rest = t.partition(" ")
    if first.startswith("/"):
        word = first[1:].split("@")[0].lower()
        return _LOOKUP.get(word), rest.strip()
    # کلمه‌ی فارسیِ دوکلمه‌ای (مثلاً «حذف اخطار»)
    two = " ".join(t.split()[:2]).lower()
    if two in _LOOKUP:
        return _LOOKUP[two], t[len(two):].strip()
    return _LOOKUP.get(first.lower()), rest.strip()


def _split(arg: str):
    for sep in ("|", ":", "：", "،"):
        if sep in arg:
            a, _, b = arg.partition(sep)
            return a.strip(), b.strip()
    return arg.strip(), ""


async def _perm(uid: int, role: str, perm: str) -> bool:
    if uid == PISHVA_ID:
        return True
    admin = await db.get_admin(uid)
    if not admin or not admin["is_active"]:
        return False
    try:
        return bool(json.loads(admin["permissions"]).get(perm, True))
    except Exception:
        return True


async def _gate(uid: int):
    """وضعیتِ سیستم (تعمیر/آپدیت/…) برای غیرِمدیرِ ارشد — همان روحیه‌ی check_status_gate."""
    if uid == PISHVA_ID:
        return None
    st = await db.get_setting("system_status", "normal")
    if st != "normal":
        return f"⛔ سیستم الان در حالتِ «{st}» است؛ فعلاً این عملیات بسته است."
    return None


async def _resolve(q: str):
    """→ (player|None, پیام‌خطا|None)"""
    q = (q or "").strip()
    if not q:
        return None, "نامِ بازیکن را بنویسید (یا #شناسه)."
    qid = q.lstrip("#")
    if qid.isdigit() and (q.startswith("#") or len(qid) <= 6):
        p = await db.get_player(int(qid))
        if p:
            return p, None
        if q.startswith("#"):
            return None, f"بازیکنی با شناسه‌ی {qid} نیست."
    rows = await db.search_players(q)
    if not rows:
        return None, f"بازیکنی با «{h(q)}» پیدا نشد."
    exact = [r for r in rows if (r["full_name"] or "").strip() == q]
    if len(exact) == 1:
        return exact[0], None
    if len(rows) == 1:
        return rows[0], None
    lst = "\n".join(f"• {h(r['full_name'])} — #{r['id']}" + (f" ({h(r['class_name'])})" if r["class_name"] else "")
                    for r in rows[:10])
    return None, f"❓ چند نفر پیدا شد؛ با #شناسه مشخص کنید:\n{lst}"


def _wbar(n: int, mx=3) -> str:
    n = max(0, int(n or 0))
    return "🟥" * min(n, mx) + "⬜️" * max(0, mx - n) + (f" (+{n - mx})" if n > mx else "")


def _when(ts) -> str:
    try:
        import jdatetime
        return jdatetime.datetime.fromgregorian(datetime=datetime.fromisoformat(str(ts)[:19])).strftime("%Y/%m/%d %H:%M")
    except Exception:
        return str(ts)[:16]


async def _name_of(uid):
    if uid == PISHVA_ID:
        return "مدیر ارشد"
    a = await db.get_admin(int(uid)) if uid else None
    return (a["display_name"] or a["full_name"]) if a else str(uid)


# ═══════════════════════ هندلر ═══════════════════════
async def handle(update, ctx, text: str, role: str):
    m, uid = update.effective_message, update.effective_user.id
    cmd, arg = _parse(text)
    if cmd is None:
        custom = await _find_custom(text)
        if custom:
            row, carg = custom
            try:
                out = await _run_custom(ctx, uid, role, row, carg)
                await db.log_action(uid, "ops_custom_command", f"{row['name']}: {(text or '')[:120]}")
            except Exception:
                logger.warning("ops_players: custom %s failed", row["name"], exc_info=True)
                out = "❌ اجرای این دستور خطا داد."
            if out:
                await m.reply_text(out[:4000], parse_mode="HTML")
        elif (text or "").startswith("/"):
            await m.reply_text("❔ دستورِ ناشناخته. راهنما: /phelp")
        return
    if cmd == "help":
        await m.reply_text(HELP + await _custom_help(), parse_mode="HTML")
        return
    try:
        out = await _run(ctx, uid, role, cmd, arg)
    except Exception:
        logger.warning("ops_players: %s failed", cmd, exc_info=True)
        out = "❌ خطایی پیش آمد؛ دوباره امتحان کنید."
    if out:
        await m.reply_text(out[:4000], parse_mode="HTML")


async def _run(ctx, uid, role, cmd, arg):
    write = cmd in ("warn", "unwarn", "clearwarn", "note", "clearnotes", "watch", "unwatch", "suspend", "revive")
    if write:
        g = await _gate(uid)
        if g:
            return g

    # ── خواندنی‌های بدونِ نام ──
    if cmd == "warnings":
        rows = await _q("SELECT id, full_name, warnings, status FROM players WHERE warnings>0 ORDER BY warnings DESC, full_name LIMIT ?", (MAX_LIST,))
        if not rows:
            return "✅ الان هیچ بازیکنی اخطار ندارد."
        return "⚠️ <b>بازیکن‌های دارای اخطار</b>\n" + "\n".join(
            f"{_wbar(r['warnings'])} <b>{h(r['full_name'])}</b> #{r['id']}" for r in rows)
    if cmd == "top":
        rows = await _q("SELECT id, full_name, warnings FROM players WHERE warnings>0 ORDER BY warnings DESC LIMIT 10")
        if not rows:
            return "✅ هیچ بازیکنی اخطار ندارد."
        medals = ["🥇", "🥈", "🥉"] + ["▫️"] * 7
        return "🚨 <b>پراخطارترین‌ها</b>\n" + "\n".join(
            f"{medals[i]} {h(r['full_name'])} — {r['warnings']} اخطار (#{r['id']})" for i, r in enumerate(rows))
    if cmd == "recent":
        try:
            n = max(1, min(20, int(arg))) if arg else 10
        except ValueError:
            n = 10
        rows = await _q("SELECT w.reason, w.issued_by, w.issued_at, p.full_name FROM warnings_log w "
                        "LEFT JOIN players p ON p.id=w.target_id WHERE w.target_type='player' ORDER BY w.id DESC LIMIT ?", (n,))
        if not rows:
            return "📭 اخطاری ثبت نشده."
        out = []
        for r in rows:
            out.append(f"⚠️ <b>{h(r['full_name'] or '؟')}</b> — {h(r['reason'])}\n   👤 {h(await _name_of(r['issued_by']))} · {_when(r['issued_at'])}")
        return "🕘 <b>اخطارهای اخیر</b>\n" + "\n".join(out)
    if cmd == "suspended":
        rows = await _q("SELECT id, full_name, suspension_reason FROM players WHERE status='suspended' ORDER BY full_name LIMIT ?", (MAX_LIST,))
        if not rows:
            return "✅ بازیکنِ تعلیق‌شده‌ای نیست."
        return "⏸ <b>تعلیق‌شده‌ها</b>\n" + "\n".join(
            f"• {h(r['full_name'])} #{r['id']}" + (f" — {h(r['suspension_reason'])}" if r["suspension_reason"] else "") for r in rows)
    if cmd == "watchlist":
        rows = await _q("SELECT w.player_id, w.reason, w.at, p.full_name FROM ops_watch w LEFT JOIN players p ON p.id=w.player_id ORDER BY w.at DESC LIMIT ?", (MAX_LIST,))
        if not rows:
            return "👁 فهرستِ نظارت خالی است."
        return "👁 <b>تحت نظر</b>\n" + "\n".join(
            f"• <b>{h(r['full_name'] or '؟')}</b> #{r['player_id']} — {h(r['reason'] or '—')} ({_when(r['at'])})" for r in rows)
    if cmd == "summary":
        r = (await _q("SELECT COUNT(*) AS n, SUM(status='active') AS a, SUM(status='suspended') AS s, SUM(status='kicked') AS k, "
                      "SUM(warnings>0) AS w, SUM(warnings>=3) AS w3 FROM players"))[0]
        wl = (await _q("SELECT COUNT(*) AS n FROM ops_watch"))[0]["n"]
        return (f"📊 <b>آمارِ بازیکنان</b>\n━━━━━━━━━━\n👥 کل: {r['n']}\n🟢 فعال: {r['a'] or 0}\n⏸ تعلیق: {r['s'] or 0}\n"
                f"🚫 اخراجی: {r['k'] or 0}\n⚠️ دارای اخطار: {r['w'] or 0}\n🔴 سه اخطار یا بیشتر: {r['w3'] or 0}\n👁 تحت نظر: {wl}")

    # ── دستورهای دارای نام ──
    name, rest = _split(arg)
    if cmd == "find":
        rows = await db.search_players(arg)
        if not rows:
            return f"بازیکنی با «{h(arg)}» پیدا نشد."
        return f"🔎 <b>نتیجه‌ی «{h(arg)}»</b>\n" + "\n".join(
            f"• {h(r['full_name'])} #{r['id']}" + (f" — {h(r['class_name'])}" if r["class_name"] else "")
            + (f" — ⚠️{r['warnings']}" if r["warnings"] else "") for r in rows[:MAX_LIST])
    if cmd == "class":
        rows = [r for r in await db.search_players(arg) if arg and r["class_name"] and arg in r["class_name"]]
        if not rows:
            return f"کلاسی با «{h(arg)}» یا بازیکنی در آن پیدا نشد."
        return f"🏫 <b>کلاسِ {h(rows[0]['class_name'])}</b> ({len(rows)} نفر)\n" + "\n".join(
            f"• {h(r['full_name'])} #{r['id']}" + (f" — ⚠️{r['warnings']}" if r["warnings"] else "") for r in rows[:MAX_LIST])

    if cmd == "unwarn" and not rest:           # «/unwarn علی رضایی 2» بدونِ جداکننده
        tk = name.split()
        if len(tk) > 1 and tk[-1].isdigit():
            rest, name = tk[-1], " ".join(tk[:-1])
    p, err = await _resolve(name)
    if not p:
        return err
    pid, pname = p["id"], p["full_name"]

    if cmd == "player":
        cls = await _q("SELECT name FROM classes WHERE id=?", (p["class_id"],)) if p["class_id"] else []
        wl = await _q("SELECT reason FROM ops_watch WHERE player_id=?", (pid,))
        wlogs = await db.get_warnings_log("player", pid, 3)
        st = {"active": "🟢 فعال", "suspended": "⏸ تعلیق", "kicked": "🚫 اخراج"}.get(p["status"], h(p["status"]))
        lines = [f"♟️ <b>{h(pname)}</b>  #{pid}", f"🏫 {h(cls[0]['name']) if cls else '—'}  |  {st}",
                 f"📈 برد {p['wins'] or 0} · باخت {p['losses'] or 0} · مساوی {p['draws'] or 0}",
                 f"⚠️ اخطار: {_wbar(p['warnings'])}"]
        if wl:
            lines.append(f"👁 <b>تحت نظر:</b> {h(wl[0]['reason'] or '—')}")
        if p["status"] == "suspended" and p["suspension_reason"]:
            lines.append(f"⏸ دلیلِ تعلیق: {h(p['suspension_reason'])}")
        for w in wlogs:
            lines.append(f"   ▫️ {h(w['reason'])} · {_when(w['issued_at'])}")
        if p["notes"]:
            lines.append(f"\n📝 <b>یادداشت‌ها</b>\n{h(str(p['notes'])[-900:])}")
        return "\n".join(lines)

    if cmd == "history":
        logs = await db.get_warnings_log("player", pid, 30)
        if not logs:
            return f"✅ {h(pname)} سابقه‌ی اخطار ندارد."
        out = []
        for i, w in enumerate(logs, 1):
            out.append(f"{i}. {h(w['reason'])}\n   👤 {h(await _name_of(w['issued_by']))} · {_when(w['issued_at'])}")
        return f"📜 <b>سابقه‌ی اخطارِ {h(pname)}</b>  ({p['warnings'] or 0} فعال)\n" + "\n".join(out)

    if cmd == "notes":
        return (f"📝 <b>یادداشت‌های {h(pname)}</b>\n{h(p['notes'])}" if p["notes"] else f"📭 برای {h(pname)} یادداشتی نیست.")

    # ── نوشتنی‌ها ──
    if cmd == "warn":
        if not await _perm(uid, role, "issue_warning"):
            return "⛔ مجوزِ «صدورِ اخطار» را ندارید."
        if not rest:
            return "دلیلِ اخطار را هم بنویسید:  <code>/warn نام | دلیل</code>"
        await db.add_player_warning(pid, rest, uid)
        pu = await db.get_player(pid)
        await db.log_action(uid, "player_warning", f"اخطار به {pname}: {rest}", pid)
        if (pu["warnings"] or 0) >= 3:
            try:
                from helpers import notify_pishva, now_shamsi
                await notify_pishva(ctx.bot, f"🔴 بازیکن *{pname}* به ۳ اخطار رسید!\n📋 دلیل: {rest}\n⏱️ `{now_shamsi()}`")
            except Exception:
                pass
        return f"⚠️ اخطار برای <b>{h(pname)}</b> ثبت شد.\n📋 {h(rest)}\n{_wbar(pu['warnings'])}"

    if cmd == "unwarn":
        if not await _perm(uid, role, "issue_warning"):
            return "⛔ مجوزِ «صدورِ اخطار» را ندارید."
        logs = await db.get_warnings_log("player", pid, 30)
        if not logs:
            return f"{h(pname)} اخطاری ندارد."
        idx = int(rest.strip()) if rest.strip().isdigit() else 1
        if not 1 <= idx <= len(logs):
            return f"شماره‌ی اخطار باید بین ۱ و {len(logs)} باشد (/history)."
        w = logs[idx - 1]
        if uid != PISHVA_ID and w["issued_by"] != uid:
            return "⛔ فقط اخطارهایی را می‌توانید حذف کنید که خودتان ثبت کرده‌اید."
        done = await db.remove_player_warning(pid, w["id"])
        if not done:
            return "این اخطار قبلاً حذف شده."
        await db.log_action(uid, "player_warning_removed", f"حذفِ اخطار {pname}: {w['reason']}", pid)
        return f"✅ اخطارِ «{h(w['reason'])}» از {h(pname)} حذف شد."

    if cmd == "clearwarn":
        if uid != PISHVA_ID:
            return "⛔ پاک‌کردنِ همه‌ی اخطارها فقط با مدیر ارشد است."
        await db.clear_player_warnings(pid)
        await db.log_action(uid, "player_warnings_cleared", f"پاک‌سازیِ همه‌ی اخطارهای {pname}", pid)
        return f"🧹 همه‌ی اخطارهای {h(pname)} پاک شد."

    if cmd == "note":
        if not await _perm(uid, role, "issue_warning"):
            return "⛔ مجوزِ لازم را ندارید."
        if not rest:
            return "متنِ یادداشت را هم بنویسید:  <code>/note نام | متن</code>"
        line = f"[{_when(datetime.now().isoformat())} — {await _name_of(uid)}] {rest}"
        merged = ((p["notes"] + "\n") if p["notes"] else "") + line
        await db.update_player(pid, notes=merged[-NOTES_CAP:])
        await db.log_action(uid, "player_note", f"یادداشت برای {pname}: {rest}", pid)
        return f"📝 یادداشت برای <b>{h(pname)}</b> ثبت شد."

    if cmd == "clearnotes":
        if uid != PISHVA_ID:
            return "⛔ فقط مدیر ارشد."
        await db.update_player(pid, notes="")
        await db.log_action(uid, "player_notes_cleared", f"پاک‌سازیِ یادداشت‌های {pname}", pid)
        return f"🧹 یادداشت‌های {h(pname)} پاک شد."

    if cmd == "watch":
        if not await _perm(uid, role, "issue_warning"):
            return "⛔ مجوزِ لازم را ندارید."
        await _x("INSERT OR REPLACE INTO ops_watch(player_id, reason, by_id, at) VALUES (?,?,?,?)",
                 (pid, rest or "—", uid, datetime.now().isoformat()))
        await db.log_action(uid, "player_watch", f"تحت‌نظر: {pname} — {rest}", pid)
        return f"👁 <b>{h(pname)}</b> تحت نظر قرار گرفت." + (f"\n📋 {h(rest)}" if rest else "")

    if cmd == "unwatch":
        if not await _perm(uid, role, "issue_warning"):
            return "⛔ مجوزِ لازم را ندارید."
        await _x("DELETE FROM ops_watch WHERE player_id=?", (pid,))
        await db.log_action(uid, "player_unwatch", f"رفعِ نظارت: {pname}", pid)
        return f"✅ نظارت بر {h(pname)} برداشته شد."

    if cmd == "suspend":
        if not await _perm(uid, role, "request_ban"):
            return "⛔ مجوزِ «تعلیق/اخراج» را ندارید."
        if p["status"] == "suspended":
            return f"{h(pname)} از قبل تعلیق است."
        fields = {"status": "suspended"}
        if rest:
            fields["suspension_reason"] = rest
        await db.update_player(pid, **fields)
        await db.log_action(uid, "suspend_player", f"تعلیق: {pname}" + (f" — {rest}" if rest else ""), pid)
        try:
            from anomaly_alerts import record_destructive_action
            await record_destructive_action(ctx.bot, uid, "suspend_player")
        except Exception:
            pass
        return f"⏸ <b>{h(pname)}</b> تعلیق شد." + (f"\n📋 {h(rest)}" if rest else "")

    if cmd == "revive":
        if not await _perm(uid, role, "request_ban"):
            return "⛔ مجوزِ «تعلیق/اخراج» را ندارید."
        await db.update_player(pid, status="active", warnings=0)
        await db.log_action(uid, "revive_player", f"احیا: {pname}", pid)
        return f"🔄 <b>{h(pname)}</b> احیا شد و اخطارهایش صفر شد."
    return None


# ═══════════════════════ دستورهای سفارشی (ساخته‌شده توسط رهگشا) ═══════════════════════
# سه نوع:
#   reply — جوابِ ثابت (متن با {args} {name} {rest} {me})
#   alias — میان‌بُر برای یک دستورِ اصلی (مثلاً «/late علی» = اخطارِ «تأخیر»)؛ از همان مجوزهای دستورِ اصلی رد می‌شود
#   ai    — پرسش از رهگشا با قالبِ دلخواه (اختیاری: همراهِ پرونده‌ی بازیکن)
# هر دستور می‌تواند هم «/اسم» داشته باشد هم «کلمه»‌ی فارسی. نمی‌تواند دستورهای اصلی را بپوشاند.
SAFE_BASES = {"warn", "unwarn", "note", "notes", "watch", "unwatch", "player", "history", "find", "class", "warnings",
              "top", "recent", "summary", "suspended", "watchlist"}
BOSS_BASES = {"suspend", "revive", "clearwarn", "clearnotes"}        # میان‌بُر به این‌ها همیشه فقط برای مدیر ارشد
KINDS = ("reply", "alias", "ai")
_TRIG_RE = re.compile(r"^[\w\u0600-\u06FF]{2,32}$", re.UNICODE)
_cmd_cache = {"t": 0.0, "rows": []}


def _norm_trig(x):
    t = str(x or "").strip().lstrip("/").lower().replace(" ", "_").replace("\u200c", "_")
    return t if _TRIG_RE.match(t) else None


def _jl(v):
    try:
        return json.loads(v) if v else []
    except Exception:
        return []


async def _custom_rows(force=False):
    import time
    if not force and time.monotonic() - _cmd_cache["t"] < 30:
        return _cmd_cache["rows"]
    rows = [dict(r) for r in await _q("SELECT * FROM ops_commands WHERE active=1")]
    for r in rows:
        r["slash_l"], r["words_l"] = _jl(r["slash"]), _jl(r["words"])
    _cmd_cache["rows"], _cmd_cache["t"] = rows, time.monotonic()
    return rows


async def _find_custom(text: str):
    t = (text or "").strip()
    if not t:
        return None
    rows = await _custom_rows()
    first, _, rest = t.partition(" ")
    if first.startswith("/"):
        w = first[1:].split("@")[0].lower()
        for r in rows:
            if w in r["slash_l"]:
                return r, rest.strip()
        return None
    f = first.lower().replace("\u200c", "_")
    toks = t.split()
    two = "_".join(toks[:2]).lower() if len(toks) > 1 else None
    if two:                                     # کلمه‌ی دوبخشی اولویت دارد («قوانین باشگاه» روی «قوانین»)
        for r in rows:
            if two in r["words_l"]:
                return r, " ".join(toks[2:]).strip()
    for r in rows:
        if f in r["words_l"]:
            return r, rest.strip()
    return None


def _taken(rows, slash, words, skip_name=None):
    for r in rows:
        if r["name"] == skip_name:
            continue
        for x in slash:
            if x in r["slash_l"]:
                return f"«/{x}» قبلاً برای دستورِ «{r['name']}» است."
        for x in words:
            if x in r["words_l"]:
                return f"کلمه‌ی «{x}» قبلاً برای دستورِ «{r['name']}» است."
    for x in list(slash) + list(words):
        if x in _LOOKUP:
            return f"«{x}» یکی از دستورهای اصلی است و نمی‌شود رویش نوشت."
    return None


async def save_command(*, name, kind=None, template=None, base=None, slash=None, words=None, with_player=None,
                       boss_only=None, help=None, active=None, created_by=0, update=False):
    """ساخت یا ویرایشِ دستور. → (ok, پیام)"""
    nm = _norm_trig(name)
    if not nm:
        return False, "نامِ دستور باید ۲ تا ۳۲ حرف/عدد/زیرخط باشد (بدونِ فاصله)."
    rows = await _custom_rows(force=True)
    cur = next((r for r in rows if r["name"] == nm), None)
    if update and not cur:
        all_rows = await _q("SELECT * FROM ops_commands WHERE name=?", (nm,))
        if not all_rows:
            return False, f"دستوری با نامِ «{nm}» نیست."
        cur = dict(all_rows[0])
        cur["slash_l"], cur["words_l"] = _jl(cur["slash"]), _jl(cur["words"])
    if not update and cur:
        return False, f"دستوری با نامِ «{nm}» از قبل هست؛ برای تغییر از ویرایش استفاده کنید."
    if not update:
        if (await _q("SELECT 1 FROM ops_commands WHERE name=?", (nm,))):
            return False, f"دستوری با نامِ «{nm}» از قبل هست (غیرفعال)؛ ویرایشش کنید."

    k = kind or (cur["kind"] if cur else None)
    if k not in KINDS:
        return False, "kind باید reply | alias | ai باشد."
    tpl = template if template is not None else (cur["template"] if cur else "")
    b = base if base is not None else (cur["base"] if cur else None)
    if slash is None and words is None and not cur:
        slash = [nm]
    sl = [_norm_trig(x) for x in (slash if slash is not None else (cur["slash_l"] if cur else []))]
    wd = [_norm_trig(x) for x in (words if words is not None else (cur["words_l"] if cur else []))]
    if None in sl or None in wd:
        return False, "هر نامِ دستور/کلمه باید ۲ تا ۳۲ حرف/عدد/زیرخط باشد."
    sl, wd = list(dict.fromkeys(sl)), list(dict.fromkeys(wd))
    if not sl and not wd:
        return False, "حداقل یک «/دستور» یا یک «کلمه» لازم است."
    bad = _taken(rows, sl, wd, skip_name=nm)
    if bad:
        return False, bad
    bo = bool(boss_only if boss_only is not None else (cur["boss_only"] if cur else 0))
    if k == "alias":
        b = (b or "").strip().lower()
        b = _LOOKUP.get(b, b)
        if b not in SAFE_BASES | BOSS_BASES:
            return False, "برای alias، base باید یکی از این‌ها باشد: " + "، ".join(sorted(SAFE_BASES | BOSS_BASES))
        if b in BOSS_BASES:
            bo = True
        tpl = tpl or "{args}"
    else:
        b = None
        if not (tpl or "").strip():
            return False, "برای این نوع، template (متنِ جواب/دستورِ رهگشا) لازم است."
    if len(tpl) > 1500:
        return False, "template بیش از ۱۵۰۰ نویسه است."
    wp = int(bool(with_player if with_player is not None else (cur["with_player"] if cur else 0)))
    hp = (help if help is not None else (cur["help"] if cur else "")) or ""
    act = int(bool(active if active is not None else (cur["active"] if cur else 1)))
    now = datetime.now().isoformat()
    if update:
        await _x("UPDATE ops_commands SET slash=?, words=?, kind=?, base=?, template=?, with_player=?, boss_only=?, help=?, active=? WHERE name=?",
                 (json.dumps(sl), json.dumps(wd, ensure_ascii=False), k, b, tpl, wp, int(bo), hp[:200], act, nm))
    else:
        await _x("INSERT INTO ops_commands(name, slash, words, kind, base, template, with_player, boss_only, help, active, created_by, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
                 (nm, json.dumps(sl), json.dumps(wd, ensure_ascii=False), k, b, tpl, wp, int(bo), hp[:200], created_by, now))
    _cmd_cache["t"] = 0.0
    trig = "، ".join([f"/{x}" for x in sl] + wd)
    return True, f"✅ دستورِ «{nm}» {'ویرایش' if update else 'ساخته'} شد — فراخوانی: {trig}" + (" (فقط مدیر ارشد)" if bo else "")


async def delete_command(name):
    nm = _norm_trig(name)
    if not nm or not (await _q("SELECT 1 FROM ops_commands WHERE name=?", (nm,))):
        return False, f"دستوری با نامِ «{name}» نیست."
    await _x("DELETE FROM ops_commands WHERE name=?", (nm,))
    _cmd_cache["t"] = 0.0
    return True, f"🗑️ دستورِ «{nm}» حذف شد."


async def list_commands():
    rows = [dict(r) for r in await _q("SELECT * FROM ops_commands ORDER BY id")]
    for r in rows:
        r["slash_l"], r["words_l"] = _jl(r["slash"]), _jl(r["words"])
    return rows


async def _custom_help() -> str:
    rows = await _custom_rows(force=True)
    if not rows:
        return ""
    out = ["\n\n🧩 <b>دستورهای سفارشی</b>"]
    for r in rows:
        trig = " ".join([f"/{x}" for x in r["slash_l"]] + r["words_l"])
        out.append(f"• <code>{h(trig)}</code> — {h(r['help'] or r['name'])}" + (" 👑" if r["boss_only"] else ""))
    return "\n".join(out)


def _fill(tpl: str, arg: str, name: str, rest: str, me: str) -> str:
    return tpl.replace("{args}", arg).replace("{name}", name).replace("{rest}", rest).replace("{me}", me)


async def _run_custom(ctx, uid, role, r, arg):
    if r["boss_only"] and uid != PISHVA_ID:
        return "⛔ این دستور فقط برای مدیر ارشد است."
    name, rest = _split(arg)
    me = await _name_of(uid)
    kind = r["kind"]
    if kind == "reply":
        return h(_fill(r["template"], arg, name, rest, me))
    if kind == "alias":
        built = _fill(r["template"] or "{args}", arg, name, rest, me).strip()
        cmd2, arg2 = _parse(f"/{r['base']} {built}")
        if cmd2 is None:
            return "❌ دستورِ پایه‌ی این میان‌بر دیگر معتبر نیست."
        return await _run(ctx, uid, role, cmd2, arg2)
    if kind == "ai":
        import ai_assistant as aia
        if not await aia._is_ai_online():
            return "⚠️ رهگشا فعلاً خاموش است."
        if not await aia._can_use_ai(uid, role):
            return "⛔ دسترسیِ شما به رهگشا بسته است."
        card = ""
        if r["with_player"]:
            p, err = await _resolve(name)
            if not p:
                return err
            card = re.sub(r"<[^>]+>", "", await _run(ctx, uid, role, "player", f"#{p['id']}") or "")
            card += "\n" + re.sub(r"<[^>]+>", "", await _run(ctx, uid, role, "history", f"#{p['id']}") or "")
        prompt = (f"تو «رهگشا» هستی و در تاپیکِ مدیریتِ بازیکنانِ یک باشگاهِ شطرنجِ مدرسه جواب می‌دهی.\n"
                  f"درخواست: {_fill(r['template'], arg, name, rest, me)}\n"
                  + (f"\nداده‌ی بازیکن:\n{card}\n" if card else "")
                  + "\nقوانین: فقط از داده‌ی بالا استفاده کن و چیزی نساز؛ فارسی؛ حداکثر ۱۲ خط؛ بدونِ مارک‌داون.")
        data = await aia._call_gemini([{"role": "user", "parts": [{"text": prompt}]}], None)
        txt = "".join(x.get("text", "") for x in aia._extract_parts(data)).strip()
        return ("🤖 " + h(txt[:3000])) if txt else "⚠️ رهگشا جوابی نداد."
    return None
