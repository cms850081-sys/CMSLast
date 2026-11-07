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
        if (text or "").startswith("/"):
            await m.reply_text("❔ دستورِ ناشناخته. راهنما: /phelp")
        return
    if cmd == "help":
        await m.reply_text(HELP, parse_mode="HTML")
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
