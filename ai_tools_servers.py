"""
ai_tools_servers.py — ابزارهای رهگشا برای پرسش درباره‌ی «سرورها» (فقط مدیر ارشد، فقط از سرور اصلی).

رهگشا با این ابزارها درباره‌ی هر مجموعه از اشخاص تا بازیکن‌ها، مسابقات، لاگ‌ها و بازخوردها
(انتقادات/باگ‌ها) اطلاعاتِ کامل می‌گیرد. هر ابزار با srvctx.use(sid) روی دیتابیسِ همان سرور اجرا می‌شود؛
هیچ داده‌ای بینِ سرورها قاطی نمی‌شود. اگر مدیر ارشد از پنلِ سرورها «پرسش از رهگشا» را زده باشد،
سرورِ پیش‌فرض همان است (ai_focus_server).
"""
from datetime import datetime, timedelta

import servers
import srvctx
import turso_db
from config import DB_PATH, ROLE_SECURITY_MANAGER, ROLE_TOURNAMENT_MANAGER

_STR = {"type": "string"}
_SRV = {"type": "string", "description": "شناسه‌ی عددیِ سرور (مثلاً 2) یا بخشی از نامِ آن. اگر خالی باشد، سرورِ انتخاب‌شده در پنلِ سرورها."}

TOOL_PERMISSIONS = {n: ["pishva"] for n in (
    "list_servers", "server_overview", "server_people", "server_players",
    "server_matches", "server_logs", "server_feedback")}

CATEGORY = ("servers", "🌐 سرورها (مجموعه‌ها)",
            ["list_servers", "server_overview", "server_people", "server_players",
             "server_matches", "server_logs", "server_feedback"])

TOOL_DECLARATIONS = [
    {"name": "list_servers",
     "description": "فهرست همه‌ی سرورها (اصلی و مجموعه‌ها) با وضعیت و مدیر مجموعه. برای «چند سرور داریم / وضعیت سرورها».",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "server_overview",
     "description": "گزارش کاملِ یک سرور: وضعیت، مدیر مجموعه، تعدادِ مدیران/بازیکنان/مسابقات، ساعت کاری، قابلیت‌های خاموش، فعالیتِ اخیر.",
     "parameters": {"type": "object", "properties": {"server": _SRV}}},
    {"name": "server_people",
     "description": "همه‌ی اشخاصِ یک سرور با نقش، وضعیت، اخطار و آخرین فعالیت.",
     "parameters": {"type": "object", "properties": {"server": _SRV}}},
    {"name": "server_players",
     "description": "بازیکن‌های یک سرور (با جست‌وجوی نام): کلاس، وضعیت، اخطار، برد/باخت/مساوی.",
     "parameters": {"type": "object", "properties": {"server": _SRV, "query": _STR, "limit": {"type": "integer"}}}},
    {"name": "server_matches",
     "description": "آخرین مسابقات یک سرور (با نتیجه).",
     "parameters": {"type": "object", "properties": {"server": _SRV, "limit": {"type": "integer"}}}},
    {"name": "server_logs",
     "description": "آخرین لاگ‌های اقدامات یک سرور؛ قابل‌فیلتر بر اساس ساعتِ اخیر، نامِ مدیر یا نوعِ اقدام.",
     "parameters": {"type": "object", "properties": {"server": _SRV, "hours": {"type": "integer"},
                                                      "admin": _STR, "action": _STR, "limit": {"type": "integer"}}}},
    {"name": "server_feedback",
     "description": "انتقادات/پیشنهادات/گزارش باگِ ثبت‌شده در یک سرور.",
     "parameters": {"type": "object", "properties": {"server": _SRV, "limit": {"type": "integer"}}}},
]

ROLE_FA = {ROLE_TOURNAMENT_MANAGER: "مدیر مسابقات", ROLE_SECURITY_MANAGER: "مدیر امنیتی"}


def _resolve(arg, ctx):
    """سرور را از شناسه/نام (یا تمرکزِ پنل) پیدا می‌کند → (sid, error)."""
    arg = str(arg or "").strip()
    if not arg and ctx is not None:
        try:
            focus = ctx.user_data.get("ai_focus_server")
            if focus:
                return int(focus), None
        except Exception:
            pass
    if not arg:
        return None, "کدام سرور؟ شناسه یا نام را بگو (یا list_servers را ببین)."
    if arg.isdigit():
        sid = int(arg)
        return (sid, None) if servers.get(sid) else (None, f"سرور {sid} وجود ندارد.")
    for s in servers.all_servers(True):
        if arg in (s.get("name") or "") or arg in (s.get("school") or ""):
            return int(s["id"]), None
    return None, f"سروری با نامِ «{arg}» پیدا نشد."


async def _q(sql, params=()):
    async with turso_db.connect(DB_PATH) as c:
        async with c.execute(sql, tuple(params)) as cur:
            return await cur.fetchall()


async def dispatch_tool(name: str, args: dict, job_queue=None, ctx=None):
    if srvctx.current() != srvctx.MAIN_ID:
        return "⛔ این ابزار فقط برای مدیر ارشد در سرور اصلی است."
    if name == "list_servers":
        lines = []
        for s in servers.all_servers(True):
            sid = int(s["id"])
            owner = f" | مدیر مجموعه: {s.get('owner_tid')}" if sid != 1 else ""
            lines.append(f"- سرور {sid}: «{s.get('name')}» — {servers.STATUS_FA.get(s.get('status'), '')}{owner}")
        return "سرورها:\n" + "\n".join(lines)

    sid, err = _resolve((args or {}).get("server"), ctx)
    if err:
        return err
    s = servers.get(sid)
    if not s:
        return "سرور پیدا نشد."
    head = f"[سرور {sid}: «{s.get('name')}»]"
    try:
        with srvctx.use(sid):
            if name == "server_overview":
                admins = await _q("SELECT telegram_id, role, is_active FROM admins")
                act = [a for a in admins if a[2]]
                pc = (await _q("SELECT COUNT(*) FROM players WHERE status='active'"))[0][0]
                mt = (await _q("SELECT COUNT(*) FROM matches"))[0][0]
                pend = (await _q("SELECT COUNT(*) FROM matches WHERE result IS NULL"))[0][0]
                wh = (await _q("SELECT value FROM system_settings WHERE key='working_hours_active'"))
                since = (datetime.now() - timedelta(days=7)).isoformat()
                acts = (await _q("SELECT COUNT(*) FROM action_logs WHERE logged_at >= ?", (since,)))[0][0]
                off = [fa for k, fa in servers.FEATURES if not servers.feature_on(sid, k)] if sid != 1 else []
                return (f"{head}\nوضعیت: {servers.STATUS_FA.get(s.get('status'))}"
                        f"{(' — ' + s['suspend_reason']) if s.get('suspend_reason') else ''}\n"
                        f"مدرسه/مکان: {s.get('school') or '—'} | مدیر مجموعه: {s.get('owner_tid') or '—'}\n"
                        f"مدیران فعال: {len(act)} از {len(admins)} | بازیکنان فعال: {pc}\n"
                        f"مسابقات: {mt} (بدون نتیجه: {pend}) | ساعت کاری: {'باز' if wh and wh[0][0] == '1' else 'بسته'}\n"
                        f"اقداماتِ ۷ روز اخیر: {acts}\nقابلیت‌های خاموش: {', '.join(off) if off else 'ندارد'}")
            if name == "server_people":
                rows = await _q("SELECT telegram_id, full_name, display_name, role, is_active, warnings, last_active, username FROM admins ORDER BY is_active DESC")
                out = []
                for r in rows:
                    role = "مدیر مجموعه" if servers.is_owner(int(r[0]), sid) else ROLE_FA.get(r[3], r[3])
                    out.append(f"- {r[2] or r[1]} ({r[7] or ''}) id={r[0]} | {role} | {'فعال' if r[4] else 'غیرفعال'} | اخطار {r[5]} | آخرین فعالیت {str(r[6] or '—')[:16]}")
                return head + "\nاشخاص:\n" + ("\n".join(out) or "—")
            if name == "server_players":
                lim = max(1, min(int(args.get("limit") or 30), 80))
                qy = (args.get("query") or "").strip()
                where, params = ("WHERE p.full_name LIKE ?", (f"%{qy}%",)) if qy else ("", ())
                rows = await _q(f"SELECT p.full_name, c.name, p.status, p.warnings, p.wins, p.losses, p.draws, p.is_elite, p.is_special, p.notes "
                                f"FROM players p LEFT JOIN classes c ON c.id=p.class_id {where} ORDER BY p.id DESC LIMIT ?", params + (lim,))
                out = [f"- {r[0]} | کلاس {r[1] or '—'} | {r[2]} | اخطار {r[3]} | {r[4]}برد {r[5]}باخت {r[6]}مساوی"
                       f"{' | برتر' if r[7] else ''}{' | ویژه' if r[8] else ''}{(' | یادداشت: ' + r[9][:60]) if r[9] else ''}" for r in rows]
                return head + f"\nبازیکن‌ها ({len(rows)}):\n" + ("\n".join(out) or "—")
            if name == "server_matches":
                lim = max(1, min(int(args.get("limit") or 15), 50))
                rows = await _q("SELECT w.full_name, b.full_name, m.result, m.match_date FROM matches m "
                                "LEFT JOIN players w ON w.id=m.white_player_id LEFT JOIN players b ON b.id=m.black_player_id "
                                "ORDER BY m.id DESC LIMIT ?", (lim,))
                out = [f"- {r[0]} در برابر {r[1]} | نتیجه: {r[2] or 'ثبت نشده'} | {str(r[3] or '')[:16]}" for r in rows]
                return head + "\nآخرین مسابقات:\n" + ("\n".join(out) or "—")
            if name == "server_logs":
                lim = max(1, min(int(args.get("limit") or 30), 100))
                cond, params = [], []
                if args.get("hours"):
                    cond.append("logged_at >= ?"); params.append((datetime.now() - timedelta(hours=int(args["hours"]))).isoformat())
                if args.get("action"):
                    cond.append("action_type LIKE ?"); params.append(f"%{args['action']}%")
                names = {int(r[0]): (r[2] or r[1]) for r in await _q("SELECT telegram_id, full_name, display_name FROM admins")}
                if args.get("admin"):
                    ids = [i for i, n in names.items() if args["admin"] in (n or "")]
                    if not ids:
                        return head + f"\nمدیری با نام «{args['admin']}» نیست."
                    cond.append(f"admin_id IN ({','.join('?' * len(ids))})"); params += ids
                where = ("WHERE " + " AND ".join(cond)) if cond else ""
                rows = await _q(f"SELECT admin_id, action_type, description, logged_at FROM action_logs {where} ORDER BY id DESC LIMIT ?",
                                tuple(params) + (lim,))
                out = [f"- {str(r[3])[:16]} | {names.get(int(r[0]), r[0]) if r[0] else 'سیستم'} | {r[1]}: {str(r[2])[:120]}" for r in rows]
                return head + "\nلاگ‌ها:\n" + ("\n".join(out) or "—")
            if name == "server_feedback":
                lim = max(1, min(int(args.get("limit") or 15), 50))
                rows = await _q("SELECT fb_type, title, content, sent_at, reply FROM feedback ORDER BY id DESC LIMIT ?", (lim,))
                out = [f"- [{r[0]}] {r[1]}: {str(r[2])[:140]} ({str(r[3])[:10]}){' ✔پاسخ داده شده' if r[4] else ''}" for r in rows]
                return head + "\nبازخوردها/باگ‌ها:\n" + ("\n".join(out) or "—")
    except Exception as ex:
        return f"{head}\n❌ خطا در خواندنِ اطلاعات: {ex}"
    return "ابزار ناشناخته."
