# -*- coding: utf-8 -*-
"""
sheet_views.py — «نمای انسانی‌خوان» دیتابیس برای شیت متصل.

شیت متصل دیگر لاگِ خامِ رویدادها نیست؛ یک گزارشِ زنده و زیبا از «وضعیتِ فعلیِ مسابقات» است:
رده‌بندی، بازیکنان، بازی‌ها، تورنمنت‌ها، تیم‌ها، روند ایلو، مدیران، اخطارها، شطرنج زنده، رهگشا و …
با نام‌ها (نه شناسه‌ها)، تاریخ شمسی، رنگِ وضعیت‌ها، فونت و اندازه‌ی مخصوصِ عنوان/هدر/بدنه.

این فایل فقط داده را به «Grid» (جدولِ سلول‌دارِ استایل‌دار) تبدیل می‌کند و هیچ وابستگی‌ای به تلگرام/گوگل/دیتابیسِ
ربات ندارد؛ خواندنِ دیتابیس از طریق تابعِ `q(sql, args)` است که sheets_sync می‌دهد. نوشتن در گوگل‌شیت هم کارِ sheets_sync است.
"""
import hashlib
import json
import re

MAIN_ID = 1

# ───────────────────────── تاریخ شمسی (بدون وابستگی) ─────────────────────────
_G_D_M = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def g2j(gy: int, gm: int, gd: int):
    """میلادی → شمسی (الگوریتم استاندارد jalaali)."""
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd + _G_D_M[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?")


def fa_date(v, time=False) -> str:
    """'2026-11-05' یا '2026-11-05T18:30:00' → '1405/08/14' (با time=True: '1405/08/14 18:30')."""
    if v is None or v == "":
        return ""
    s = str(v).strip()
    m = _ISO.match(s)
    if not m:
        return s
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 1700:                       # از قبل شمسی ذخیره شده
        return f"{y:04d}/{mo:02d}/{d:02d}"
    try:
        jy, jm, jd = g2j(y, mo, d)
    except Exception:
        return s
    out = f"{jy:04d}/{jm:02d}/{jd:02d}"
    if time and m.group(4):
        out += f" {m.group(4)}:{m.group(5)}"
    return out


# ───────────────────────── رنگ و استایل ─────────────────────────
def mix(a: str, b: str, t: float) -> str:
    """ترکیبِ دو رنگِ hex: t=0 → a ، t=1 → b."""
    ca = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(ca, cb))


class Theme:
    """ظاهر: رنگِ اصلی (از تنظیماتِ /sheets)، فونت، اندازه، عرضِ ستون، رنگ‌بندیِ وضعیت‌ها."""

    def __init__(self, hdr="1E3A8A", font="Arial", fsize=10, cw=160, colors=True):
        self.hdr, self.font, self.fsize, self.cw, self.colors = hdr, font, int(fsize), int(cw), bool(colors)
        self.dark = mix(hdr, "000000", 0.28)      # نوارِ عنوان
        self.tint = mix(hdr, "FFFFFF", 0.90)      # زمینه‌ی روشنِ کارت‌ها/زیرعنوان
        self.tint2 = mix(hdr, "FFFFFF", 0.78)     # عنوانِ بخش‌ها
        self.zebra = mix(hdr, "FFFFFF", 0.95)     # ردیف‌های یکی‌درمیان

    def key(self):
        return [self.hdr, self.font, self.fsize, self.cw, self.colors]


CHIP = {
    "ok": ("DCFCE7", "166534"), "warn": ("FEF3C7", "92400E"), "bad": ("FEE2E2", "991B1B"),
    "mute": ("E5E7EB", "374151"), "info": ("DBEAFE", "1E40AF"),
    "white": ("F8FAFC", "0F172A"), "black": ("1F2937", "FFFFFF"),
    "gold": ("FDE68A", "78350F"), "silver": ("E2E8F0", "334155"), "bronze": ("FED7AA", "7C2D12"),
}


class Cell:
    __slots__ = ("v", "bg", "fg", "b", "i", "sz", "al", "nf")

    def __init__(self, v=None, bg=None, fg=None, b=False, i=False, sz=None, al=None, nf=None):
        self.v, self.bg, self.fg, self.b, self.i, self.sz, self.al, self.nf = v, bg, fg, b, i, sz, al, nf

    def sig(self):
        return [self.v, self.bg, self.fg, self.b, self.i, self.sz, self.al, self.nf]


class Grid:
    """یک تب: ردیف‌های سلول‌دار + ادغام‌ها + ارتفاعِ ردیف‌ها + عرضِ ستون‌ها (پیکسل، مبنای ۱۶۰)."""

    def __init__(self, ncols: int):
        self.ncols = ncols
        self.rows, self.heights, self.merges = [], {}, []
        self.widths = [160] * ncols
        self.freeze_rows = 0
        self.freeze_cols = 0
        self.body_start = 0          # اولین ردیفِ داده (برای تخمینِ ارتفاعِ متن‌های بلند)

    def add(self, cells, height=None):
        cells = list(cells)[: self.ncols]
        cells += [Cell()] * (self.ncols - len(cells))
        self.rows.append(cells)
        r = len(self.rows) - 1
        if height:
            self.heights[r] = height
        return r

    def merge(self, r0, c0, r1, c1):
        self.merges.append((r0, c0, r1, c1))

    def digest(self, theme: Theme) -> str:
        blob = json.dumps([[[c.sig() for c in row] for row in self.rows], self.merges, sorted(self.heights.items()),
                           self.widths, self.freeze_rows, self.freeze_cols, theme.key()],
                          ensure_ascii=False, default=str)
        return hashlib.md5(blob.encode("utf-8")).hexdigest()


# ───────────────────────── ساختارِ مشترکِ تب‌ها ─────────────────────────
def _banner(g: Grid, T: Theme, title: str, subtitle: str):
    n = g.ncols
    r = g.add([Cell(title, bg=T.dark, fg="FFFFFF", b=True, sz=T.fsize + 9, al="C")] + [Cell(None, bg=T.dark)] * (n - 1), 50)
    g.merge(r, 0, r, n - 1)
    r = g.add([Cell(subtitle, bg=T.tint, fg="475569", i=True, sz=T.fsize, al="C")] + [Cell(None, bg=T.tint)] * (n - 1), 28)
    g.merge(r, 0, r, n - 1)
    g.add([Cell()] * n, 8)


def _section(g: Grid, T: Theme, text: str):
    n = g.ncols
    r = g.add([Cell(text, bg=T.tint2, fg=T.dark, b=True, sz=T.fsize + 3, al="C")] + [Cell(None, bg=T.tint2)] * (n - 1), 36)
    g.merge(r, 0, r, n - 1)


def _spacer(g: Grid, h=14):
    g.add([Cell()] * g.ncols, h)


def _header(g: Grid, T: Theme, headers):
    g.add([Cell(h, bg=T.hdr, fg="FFFFFF", b=True, sz=T.fsize + 1, al="C") for h in headers], 38)


def _cellify(x):
    return x if isinstance(x, Cell) else Cell(x)


def _body(g: Grid, T: Theme, idx: int, row, left=()):
    out = []
    for ci, x in enumerate(row):
        c = _cellify(x)
        if c.bg is None and idx % 2 == 1:
            c.bg = T.zebra
        if c.al is None and ci not in left:
            c.al = "C"
        out.append(c)
    g.add(out)


def _empty(g: Grid, T: Theme, msg="هنوز داده‌ای ثبت نشده است."):
    n = g.ncols
    r = g.add([Cell(msg, fg="64748B", i=True, al="C", sz=T.fsize + 1)] + [Cell()] * (n - 1), 44)
    g.merge(r, 0, r, n - 1)


def chip(T: Theme, text, kind) -> Cell:
    if not T.colors or kind not in CHIP:
        return Cell(text, b=True)
    bg, fg = CHIP[kind]
    return Cell(text, bg=bg, fg=fg, b=True)


def table_grid(ctx, title, subtitle, headers, widths, rows, left=(), freeze_cols=0, empty="هنوز داده‌ای ثبت نشده است."):
    T = ctx.T
    g = Grid(len(headers))
    g.widths = list(widths)
    _banner(g, T, title, subtitle)
    _header(g, T, headers)
    g.body_start = len(g.rows)
    if not rows:
        _empty(g, T, empty)
    for i, r in enumerate(rows):
        _body(g, T, i, r, left)
    g.freeze_rows = 4
    g.freeze_cols = freeze_cols
    return g


# ───────────────────────── برچسب‌های فارسی ─────────────────────────
PLAYER_STATUS = {"active": ("فعال", "ok"), "suspended": ("تعلیق", "warn"), "kicked": ("اخراج", "bad"), "eliminated": ("حذف", "mute")}
ROLE_FA = {"pishva": "مدیر ارشد", "tournament_manager": "مسئول مسابقات", "security_manager": "مسئول انتظامات"}
TOUR_STATUS = {"active": ("فعال", "ok"), "archived": ("بایگانی", "mute"), "finished": ("پایان‌یافته", "info"), "ended": ("پایان‌یافته", "info")}
TASK_STATUS = {"pending": ("در انتظار", "warn"), "done": ("انجام‌شده", "ok"), "failed": ("ناموفق", "bad")}
TEAM_STATUS = {"active": ("فعال", "ok"), "inactive": ("غیرفعال", "mute"), "suspended": ("تعلیق", "warn"), "disbanded": ("منحل", "bad")}
WARN_TARGET = {"player": "بازیکن", "admin": "مدیر", "team": "تیم"}
GAME_STATUS = {
    "active": ("در حال بازی", "info"), "checkmate": ("کیش و مات", "ok"), "resigned": ("تسلیم", "warn"), "timeout": ("اتمام زمان", "warn"),
    "draw": ("تساوی", "mute"), "draw_agreement": ("تساوی با توافق", "mute"), "stalemate": ("پات", "mute"),
    "insufficient_material": ("تساوی (مهره ناکافی)", "mute"), "draw_75moves": ("تساوی (۷۵ حرکت)", "mute"),
    "draw_repetition": ("تساوی (تکرار)", "mute"), "abandoned": ("رها‌شده", "bad"),
}


def _tier(is_elite, is_special):
    return ("ویژه", "gold") if is_special else (("برتر", "info") if is_elite else ("عادی", None))


def _s(v):
    return "" if v is None else v


def _int(v, d=0):
    try:
        return int(v or d)
    except Exception:
        return d


def _clip(v, n=600):
    s = "" if v is None else str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


# ───────────────────────── زمینه‌ی خواندن از دیتابیس ─────────────────────────
class Ctx:
    """q(sql,args) → list[dict]. tables: نامِ فیزیکیِ جدول‌های موجود. sid: سرور."""

    def __init__(self, sid, q, tables, theme: Theme, now_txt: str, today_g: str, server_name: str):
        self.sid, self.q, self.tables, self.T = int(sid), q, set(tables), theme
        self.now_txt, self.today_g, self.server_name = now_txt, today_g, server_name
        self._cols, self._m = {}, None

    def phys(self, logical):
        n = logical if self.sid == MAIN_ID else f"s{self.sid}_{logical}"
        return n if n in self.tables else None

    async def cols(self, logical):
        if logical not in self._cols:
            p = self.phys(logical)
            self._cols[logical] = [r["name"] for r in await self.q(f'PRAGMA table_info("{p}")')] if p else []
        return self._cols[logical]

    async def fetch(self, logical, want=None, where="", args=None, order="", limit=None):
        p = self.phys(logical)
        if not p:
            return []
        have = await self.cols(logical)
        sel = [c for c in (want or have) if c in have]
        if not sel:
            return []
        missing = [c for c in (want or []) if c not in have]      # ستونی که در این دیتابیس نیست → None (کد با KeyError نمی‌ترکد)
        sql = f'SELECT {",".join(chr(34) + c + chr(34) for c in sel)} FROM "{p}"'
        if where:
            sql += f" WHERE {where}"
        if order:
            sql += f" ORDER BY {order}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = await self.q(sql, args)
        if missing:
            for r in rows:
                for c in missing:
                    r[c] = None
        return rows

    async def count(self, logical, where="", args=None):
        p = self.phys(logical)
        if not p:
            return 0
        rows = await self.q(f'SELECT COUNT(*) AS n FROM "{p}"' + (f" WHERE {where}" if where else ""), args)
        return _int(rows[0]["n"]) if rows else 0

    def who(self, tid):
        """نامِ مدیر از روی شناسه‌ی تلگرام (یا خودِ عدد اگر پیدا نشد)."""
        if tid in (None, ""):
            return ""
        a = self._m["admins_tid"].get(_int(tid, -1)) if self._m else None
        if a:
            return a["display_name"] or a["full_name"] or a["username"] or str(tid)
        return str(tid)

    async def model(self):
        """داده‌های پایه‌ی مشترکِ بین تب‌ها (یک‌بار در هر ساختِ شیت)."""
        if self._m is not None:
            return self._m
        m = {}
        m["classes"] = {r["id"]: r for r in await self.fetch("classes", ["id", "name", "created_at"])}
        m["players"] = {r["id"]: r for r in await self.fetch(
            "players", ["id", "full_name", "class_id", "status", "warnings", "notes", "is_elite", "is_special",
                        "wins", "losses", "draws", "suspension_reason", "created_at"], order="full_name")}
        m["matches"] = await self.fetch(
            "matches", ["id", "white_player_id", "black_player_id", "result", "draw_reason", "match_date", "tournament_id",
                        "created_by", "created_at", "updated_by", "updated_at", "is_pinned"], order="created_at DESC, id DESC")
        m["elo"] = {r["player_id"]: r for r in await self.fetch(
            "player_elo", ["player_id", "rating", "peak_rating", "games_played", "last_updated"])}
        m["tours"] = {r["id"]: r for r in await self.fetch("tournaments", ["id", "name", "status", "is_default", "created_at", "ended_at"])}
        m["teams"] = {r["id"]: r for r in await self.fetch(
            "teams", ["id", "name", "slogan", "team_code", "requester_name", "captain_id", "created_by", "created_at", "status", "warnings"])}
        m["members"] = await self.fetch("team_members", ["id", "team_id", "player_id", "level", "is_reserve", "joined_at"], order="id")
        admins = await self.fetch("admins", ["id", "telegram_id", "username", "full_name", "display_name", "role", "is_active",
                                             "warnings", "joined_at", "last_active"], order="id")
        m["admins"] = admins
        m["admins_tid"] = {_int(a["telegram_id"], -1): a for a in admins}
        self._m = m

        # آمارِ واقعی از روی جدول matches (منبعِ حقیقت) — نه شمارنده‌های ذخیره‌شده در players
        st = {}

        def S(pid):
            return st.setdefault(pid, {"games": 0, "wins": 0, "draws": 0, "losses": 0, "score": 0.0, "opp_sum": 0.0})
        res = []
        for x in m["matches"]:
            r = x["result"]
            if r not in ("white", "black", "draw"):
                continue
            w, b = x["white_player_id"], x["black_player_id"]
            res.append((w, b))
            sw, sb = S(w), S(b)
            sw["games"] += 1
            sb["games"] += 1
            if r == "white":
                sw["wins"] += 1; sw["score"] += 1; sb["losses"] += 1
            elif r == "black":
                sb["wins"] += 1; sb["score"] += 1; sw["losses"] += 1
            else:
                sw["draws"] += 1; sb["draws"] += 1; sw["score"] += .5; sb["score"] += .5
        for w, b in res:
            st[w]["opp_sum"] += st[b]["score"]
            st[b]["opp_sum"] += st[w]["score"]
        m["stats"] = st
        return m

    def pname(self, pid):
        p = self._m["players"].get(pid)
        return (p["full_name"] if p else None) or (f"#{pid}" if pid not in (None, "") else "—")

    def stat(self, pid):
        return self._m["stats"].get(pid) or {"games": 0, "wins": 0, "draws": 0, "losses": 0, "score": 0.0, "opp_sum": 0.0}


async def _standings(ctx: Ctx):
    """رده‌بندیِ رسمی: دقیقاً همان ترتیبِ rankings.py (امتیاز ← مقام ← اخطار ← تعداد بازی ← سختی حریف)، فقط بازیکنان فعال."""
    m = await ctx.model()
    rows = []
    for pid, p in m["players"].items():
        if (p["status"] or "active") != "active":
            continue
        s = ctx.stat(pid)
        el, sp = bool(p["is_elite"]), bool(p["is_special"])
        warn = _int(p["warnings"])
        rows.append({"id": pid, "name": p["full_name"] or "", "class": (m["classes"].get(p["class_id"]) or {}).get("name") or "بدون کلاس",
                     "tier": 0 if sp else (1 if el else 2), "elite": el, "special": sp, "warn": warn, "warn_eff": 0 if sp else warn,
                     "games": s["games"], "wins": s["wins"], "draws": s["draws"], "losses": s["losses"], "score": s["score"],
                     "opp": (s["opp_sum"] / s["games"]) if s["games"] else 0.0})
    rows.sort(key=lambda r: (-r["score"], r["tier"], r["warn_eff"], -r["games"], -r["opp"], r["name"], r["id"]))
    for i, r in enumerate(rows, 1):
        r["pos"] = i
    return rows


def _medal(T, pos):
    if not T.colors:
        return None
    return {1: "gold", 2: "silver", 3: "bronze"}.get(pos)


def _rank_cell(T, pos):
    k = _medal(T, pos)
    if k:
        bg, fg = CHIP[k]
        return Cell(pos, bg=bg, fg=fg, b=True, sz=T.fsize + 2)
    return Cell(pos, b=True)


def _name_cell(T, name, pos=None):
    prefix = {1: "🥇 ", 2: "🥈 ", 3: "🥉 "}.get(pos, "") if pos else ""
    return Cell(prefix + (name or ""), b=bool(pos and pos <= 3), al="R")


def _result_chip(T, result):
    return {"white": chip(T, "برد سفید", "white"), "black": chip(T, "برد سیاه", "black"),
            "draw": chip(T, "تساوی", "warn")}.get(result) or chip(T, "بدون نتیجه", "mute")


def _winner(ctx, x):
    r = x["result"]
    if r == "white":
        return ctx.pname(x["white_player_id"])
    if r == "black":
        return ctx.pname(x["black_player_id"])
    return "—"


def _pct(wins, games):
    return (wins / games) if games else None


# ───────────────────────── تب‌ها ─────────────────────────
async def tab_home(ctx: Ctx):
    T, m = ctx.T, await ctx.model()
    rank = await _standings(ctx)
    n_live = await ctx.count("chess_games", "status='active'")
    today_n = sum(1 for x in m["matches"] if str(x["match_date"] or "")[:10] == ctx.today_g)
    kp = [("بازیکنان فعال", len(rank), "16A34A"), ("کل بازیکنان", len(m["players"]), "2563EB"),
          ("بازی‌های ثبت‌شده", len(m["matches"]), "7C3AED"), ("بازی‌های امروز", today_n, "D97706"),
          ("تورنمنت فعال", sum(1 for t in m["tours"].values() if t["status"] == "active"), "0891B2"),
          ("تیم‌های فعال", sum(1 for t in m["teams"].values() if (t["status"] or "active") == "active"), "DB2777"),
          ("مدیران فعال", sum(1 for a in m["admins"] if _int(a["is_active"], 1)), "475569"),
          ("بازی زنده‌ی جاری", n_live, "DC2626")]
    g = Grid(8)
    g.widths = [150] * 8
    _banner(g, T, f"♟ مرکز فرماندهی مسابقات — {ctx.server_name}", f"آخرین به‌روزرسانی: {ctx.now_txt} (به وقت تهران) · خودکار و زنده")
    g.add([Cell(l, bg=T.tint, fg="475569", b=True, sz=T.fsize, al="C") for l, _v, _c in kp], 30)
    g.add([Cell(v, bg=T.tint, fg=(c if T.colors else T.dark), b=True, sz=T.fsize + 14, al="C") for _l, v, c in kp], 60)
    _spacer(g)
    _section(g, T, "🏆 ده نفر برتر")
    _header(g, T, ["رتبه", "بازیکن", "کلاس", "امتیاز", "بازی", "برد", "مساوی", "باخت"])
    if not rank:
        _empty(g, T, "هنوز بازیکن فعالی نیست.")
    for i, r in enumerate(rank[:10]):
        _body(g, T, i, [_rank_cell(T, r["pos"]), _name_cell(T, r["name"], r["pos"]), r["class"], Cell(r["score"], b=True),
                        r["games"], r["wins"], r["draws"], r["losses"]], left=(1,))
    _spacer(g)
    _section(g, T, "🕒 ده بازیِ اخیر")
    _header(g, T, ["تاریخ", "سفید", "سیاه", "نتیجه", "برنده", "تورنمنت", "ثبت‌کننده", "زمان ثبت"])
    if not m["matches"]:
        _empty(g, T, "هنوز بازی‌ای ثبت نشده است.")
    for i, x in enumerate(m["matches"][:10]):
        _body(g, T, i, [fa_date(x["match_date"]), Cell(ctx.pname(x["white_player_id"]), al="R"), Cell(ctx.pname(x["black_player_id"]), al="R"),
                        _result_chip(T, x["result"]), Cell(_winner(ctx, x), b=True, al="R"),
                        (m["tours"].get(x["tournament_id"]) or {}).get("name") or "—", ctx.who(x["created_by"]),
                        fa_date(x["created_at"], True)], left=(1, 2, 4))
    return g


async def tab_rank(ctx: Ctx):
    T, m = ctx.T, await ctx.model()
    rank = await _standings(ctx)
    rows = []
    for r in rank:
        tier, tk = _tier(r["elite"], r["special"])
        e = m["elo"].get(r["id"])
        rows.append([_rank_cell(T, r["pos"]), _name_cell(T, r["name"], r["pos"]), r["class"], chip(T, tier, tk) if tk else tier,
                     Cell(r["score"], b=True, sz=T.fsize + 1), r["games"], Cell(r["wins"], fg="166534" if T.colors else None),
                     r["draws"], Cell(r["losses"], fg="991B1B" if T.colors else None),
                     Cell(_pct(r["wins"], r["games"]), nf="0%"), r["warn"] or "",
                     round(e["rating"]) if e and e["rating"] is not None else "", round(r["opp"], 1) if r["games"] else ""])
    return table_grid(ctx, "🏆 رده‌بندی رسمی بازیکنان",
                      f"{len(rank)} بازیکن فعال · ترتیب: امتیاز (برد ۱ · مساوی ½) ← مقام ← اخطار ← تعداد بازی ← سختیِ حریف · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["رتبه", "بازیکن", "کلاس", "مقام", "امتیاز", "بازی", "برد", "مساوی", "باخت", "درصد برد", "اخطار", "Elo", "سختیِ حریف"],
                      [70, 210, 130, 90, 90, 80, 80, 80, 80, 100, 80, 90, 110], rows, left=(1,), freeze_cols=2,
                      empty="هنوز بازیکن فعالی نیست.")


async def tab_players(ctx: Ctx):
    T, m = ctx.T, await ctx.model()
    teams_of = {}
    for mem in m["members"]:
        t = m["teams"].get(mem["team_id"])
        if t:
            teams_of.setdefault(mem["player_id"], []).append(t["name"] + (" (ذخیره)" if _int(mem["is_reserve"]) else ""))
    rows = []
    for pid, p in m["players"].items():
        s, e = ctx.stat(pid), m["elo"].get(pid)
        stl, stk = PLAYER_STATUS.get(p["status"], (p["status"] or "—", "mute"))
        tier, tk = _tier(p["is_elite"], p["is_special"])
        rows.append([pid, Cell(p["full_name"] or "", b=True, al="R"), (m["classes"].get(p["class_id"]) or {}).get("name") or "—",
                     chip(T, stl, stk), chip(T, tier, tk) if tk else tier, s["score"], s["games"], s["wins"], s["draws"], s["losses"],
                     Cell(_pct(s["wins"], s["games"]), nf="0%"),
                     round(e["rating"]) if e and e["rating"] is not None else "", round(e["peak_rating"]) if e and e["peak_rating"] is not None else "",
                     _int(p["warnings"]) or "", Cell("، ".join(teams_of.get(pid, [])), al="R"), Cell(_clip(p["suspension_reason"]), al="R"),
                     Cell(_clip(p["notes"]), al="R"), fa_date(p["created_at"])])
    return table_grid(ctx, "👤 بازیکنان — پروندهٔ کامل",
                      f"{len(rows)} بازیکن · آمارِ برد/مساوی/باخت زنده از روی بازی‌های ثبت‌شده محاسبه می‌شود · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["شناسه", "نام", "کلاس", "وضعیت", "مقام", "امتیاز", "بازی", "برد", "مساوی", "باخت", "درصد برد", "Elo", "اوج Elo",
                       "اخطار", "تیم", "علت تعلیق", "یادداشت", "تاریخ ثبت"],
                      [60, 200, 120, 90, 80, 80, 70, 70, 70, 70, 90, 80, 90, 70, 200, 220, 260, 110], rows,
                      left=(1, 14, 15, 16), freeze_cols=2, empty="هنوز بازیکنی ثبت نشده است.")


async def tab_classes(ctx: Ctx):
    m = await ctx.model()
    rank = await _standings(ctx)
    by_class = {}
    for pid, p in m["players"].items():
        by_class.setdefault(p["class_id"], []).append(pid)
    best = {}
    for r in rank:
        best.setdefault(r["class"], r)
    rows = []
    for cid, c in m["classes"].items():
        ids = by_class.get(cid, [])
        act = [x for x in rank if x["class"] == c["name"]]
        games = sum(ctx.stat(i)["games"] for i in ids)
        avg = round(sum(x["score"] for x in act) / len(act), 2) if act else ""
        b = best.get(c["name"])
        rows.append([cid, Cell(c["name"], b=True, al="R"), len(ids), len(act), avg, games, Cell(b["name"] if b else "—", al="R"),
                     b["score"] if b else "", fa_date(c["created_at"])])
    return table_grid(ctx, "🏫 کلاس‌ها", f"{len(rows)} کلاس · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["شناسه", "کلاس", "تعداد بازیکنان", "فعال", "میانگین امتیاز (فعال‌ها)", "مجموع بازی‌ها", "برترین بازیکن", "امتیاز او", "تاریخ ثبت"],
                      [60, 160, 120, 80, 170, 120, 200, 90, 110], rows, left=(1, 6), freeze_cols=2, empty="هنوز کلاسی ثبت نشده است.")


MATCH_CAP = 3000


async def tab_matches(ctx: Ctx):
    T, m = ctx.T, await ctx.model()
    allm = m["matches"]
    shown = allm[:MATCH_CAP]
    rows = []
    for x in shown:
        upd = ""
        if x["updated_at"]:
            upd = f"{fa_date(x['updated_at'], True)}" + (f" · {ctx.who(x['updated_by'])}" if x["updated_by"] else "")
        rows.append([x["id"], fa_date(x["match_date"]), (m["tours"].get(x["tournament_id"]) or {}).get("name") or "—",
                     Cell(ctx.pname(x["white_player_id"]), al="R"), Cell(ctx.pname(x["black_player_id"]), al="R"),
                     _result_chip(T, x["result"]), Cell(_winner(ctx, x), b=True, al="R"), Cell(_s(x["draw_reason"]), al="R"),
                     ctx.who(x["created_by"]), fa_date(x["created_at"], True), Cell(upd, al="R"), "📌" if _int(x["is_pinned"]) else ""])
    cap = f" · {MATCH_CAP} بازیِ آخر نمایش داده می‌شود (بقیه در بکاپ هست)" if len(allm) > MATCH_CAP else ""
    return table_grid(ctx, "♟ بازی‌ها و نتایج", f"{len(allm)} بازی ثبت‌شده · جدیدترین بالا{cap} · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["شناسه", "تاریخ بازی", "تورنمنت", "سفید", "سیاه", "نتیجه", "برنده", "علت تساوی", "ثبت‌کننده", "زمان ثبت", "ویرایش", "سنجاق"],
                      [60, 100, 150, 190, 190, 110, 190, 150, 140, 130, 220, 70], rows, left=(3, 4, 6, 7, 10), freeze_cols=1,
                      empty="هنوز بازی‌ای ثبت نشده است.")


async def tab_tournaments(ctx: Ctx):
    T, m = ctx.T, await ctx.model()
    cnt = {}
    for x in m["matches"]:
        cnt[x["tournament_id"]] = cnt.get(x["tournament_id"], 0) + 1
    rows = []
    for tid, t in sorted(m["tours"].items(), key=lambda kv: -kv[0]):
        stl, stk = TOUR_STATUS.get(t["status"], (t["status"] or "—", "mute"))
        rows.append([tid, Cell(t["name"] or "", b=True, al="R"), chip(T, stl, stk), "⭐" if _int(t["is_default"]) else "",
                     cnt.get(tid, 0), fa_date(t["created_at"]), fa_date(t["ended_at"])])
    return table_grid(ctx, "🏅 تورنمنت‌ها", f"{len(rows)} تورنمنت · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["شناسه", "نام", "وضعیت", "پیش‌فرض", "تعداد بازی‌ها", "تاریخ شروع", "تاریخ پایان"],
                      [60, 230, 100, 90, 110, 110, 110], rows, left=(1,), empty="هنوز تورنمنتی ثبت نشده است.")


ELO_CAP = 3000


async def tab_elo(ctx: Ctx):
    T, m = ctx.T, await ctx.model()
    total = await ctx.count("elo_history")
    hist = await ctx.fetch("elo_history", ["id", "player_id", "match_id", "old_rating", "new_rating", "change", "opponent_id", "result", "recorded_at"],
                           order="id DESC", limit=ELO_CAP)
    lab = {"win": ("برد", "ok"), "loss": ("باخت", "bad"), "draw": ("مساوی", "warn")}
    rows = []
    for h in hist:
        l, k = lab.get(h["result"], (h["result"] or "—", "mute"))
        ch = h["change"]
        col = None
        if T.colors and ch is not None:
            col = "166534" if ch > 0 else ("991B1B" if ch < 0 else None)
        rows.append([fa_date(h["recorded_at"], True), Cell(ctx.pname(h["player_id"]), b=True, al="R"), Cell(ctx.pname(h["opponent_id"]), al="R"),
                     chip(T, l, k), round(h["old_rating"]) if h["old_rating"] is not None else "",
                     round(h["new_rating"]) if h["new_rating"] is not None else "",
                     Cell(round(ch, 1) if ch is not None else "", fg=col, b=True, nf="+0.0;-0.0;0.0"), h["match_id"]])
    cap = f" · {ELO_CAP} تغییرِ آخر نمایش داده می‌شود" if total > ELO_CAP else ""
    return table_grid(ctx, "📈 روند Elo", f"{total} تغییرِ امتیاز{cap} · جدیدترین بالا · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["زمان", "بازیکن", "حریف", "نتیجه", "Elo قبل", "Elo بعد", "تغییر", "شماره بازی"],
                      [140, 200, 200, 100, 90, 90, 90, 100], rows, left=(1, 2), empty="هنوز تغییرِ Elo ثبت نشده است.")


async def tab_teams(ctx: Ctx):
    T, m = ctx.T, await ctx.model()
    mem_of = {}
    for mem in m["members"]:
        mem_of.setdefault(mem["team_id"], []).append(mem)
    rows = []
    for tid, t in m["teams"].items():
        mems = mem_of.get(tid, [])
        main = [x for x in mems if not _int(x["is_reserve"])]
        res = [x for x in mems if _int(x["is_reserve"])]
        names = []
        for x in main + res:
            nm = ctx.pname(x["player_id"])
            if x["player_id"] == t["captain_id"]:
                nm += " (کاپیتان)"
            if _int(x["is_reserve"]):
                nm += " (ذخیره)"
            names.append(nm)
        stl, stk = TEAM_STATUS.get(t["status"], (t["status"] or "—", "mute"))
        rows.append([tid, Cell(t["name"] or "", b=True, al="R"), Cell(_s(t["slogan"]), al="R"), _s(t["team_code"]),
                     Cell(ctx.pname(t["captain_id"]) if t["captain_id"] else "—", al="R"), chip(T, stl, stk), len(main), len(res),
                     Cell("، ".join(names), al="R"), _int(t["warnings"]) or "", Cell(_s(t["requester_name"]), al="R"), fa_date(t["created_at"])])
    return table_grid(ctx, "👥 تیم‌ها", f"{len(rows)} تیم · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["شناسه", "نام تیم", "شعار", "کد تیم", "کاپیتان", "وضعیت", "اعضای اصلی", "ذخیره", "اعضا", "اخطار", "درخواست‌دهنده", "تاریخ ایجاد"],
                      [60, 190, 190, 100, 170, 90, 100, 80, 420, 80, 160, 110], rows, left=(1, 2, 4, 8, 10), freeze_cols=2,
                      empty="هنوز تیمی ثبت نشده است.")


_BOARD = {"p1": ("برد بازیکن ۱", "ok"), "player1": ("برد بازیکن ۱", "ok"), "1": ("برد بازیکن ۱", "ok"), "win1": ("برد بازیکن ۱", "ok"),
          "white": ("برد بازیکن ۱", "ok"), "p2": ("برد بازیکن ۲", "bad"), "player2": ("برد بازیکن ۲", "bad"), "2": ("برد بازیکن ۲", "bad"),
          "win2": ("برد بازیکن ۲", "bad"), "black": ("برد بازیکن ۲", "bad"), "draw": ("مساوی", "warn"), "0.5": ("مساوی", "warn")}


async def tab_tmatches(ctx: Ctx):
    T, m = ctx.T, await ctx.model()
    tms = await ctx.fetch("team_matches", ["id", "team1_id", "team2_id", "match_type", "scoring_method", "round_num", "created_by", "created_at", "status", "result"],
                          order="id DESC")
    boards = await ctx.fetch("team_match_boards", ["team_match_id"])
    nb = {}
    for b in boards:
        nb[b["team_match_id"]] = nb.get(b["team_match_id"], 0) + 1
    tn = lambda i: (m["teams"].get(i) or {}).get("name") or (f"#{i}" if i else "—")
    rows = []
    for x in tms:
        rows.append([x["id"], x["round_num"] if x["round_num"] is not None else "", Cell(tn(x["team1_id"]), b=True, al="R"),
                     Cell(tn(x["team2_id"]), b=True, al="R"), _s(x["match_type"]), _s(x["scoring_method"]), nb.get(x["id"], 0),
                     chip(T, _s(x["status"]) or "—", {"pending": "warn", "done": "ok", "finished": "ok", "completed": "ok"}.get(x["status"], "mute")),
                     Cell(_s(x["result"]), al="R"), ctx.who(x["created_by"]), fa_date(x["created_at"], True)])
    return table_grid(ctx, "⚔️ مسابقات تیمی", f"{len(rows)} مسابقه‌ی تیمی · جدیدترین بالا · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["شناسه", "دور", "تیم ۱", "تیم ۲", "نوع", "روش امتیازدهی", "تعداد میز", "وضعیت", "نتیجه", "ثبت‌کننده", "زمان ثبت"],
                      [60, 70, 190, 190, 110, 130, 100, 100, 170, 140, 130], rows, left=(2, 3, 8), empty="هنوز مسابقه‌ی تیمی ثبت نشده است.")


async def tab_tboards(ctx: Ctx):
    T, m = ctx.T, await ctx.model()
    tms = {r["id"]: r for r in await ctx.fetch("team_matches", ["id", "team1_id", "team2_id", "round_num"])}
    bs = await ctx.fetch("team_match_boards", ["id", "team_match_id", "board_num", "player1_id", "player2_id", "result"],
                         order="team_match_id DESC, board_num", limit=3000)
    tn = lambda i: (m["teams"].get(i) or {}).get("name") or (f"#{i}" if i else "—")
    rows = []
    for b in bs:
        tm = tms.get(b["team_match_id"]) or {}
        l, k = _BOARD.get(str(b["result"]).lower() if b["result"] is not None else "", (_s(b["result"]) or "—", "mute"))
        rows.append([b["team_match_id"], tm.get("round_num") if tm.get("round_num") is not None else "",
                     Cell(f"{tn(tm.get('team1_id'))}  ⚔  {tn(tm.get('team2_id'))}", al="R"), b["board_num"],
                     Cell(ctx.pname(b["player1_id"]), al="R"), Cell(ctx.pname(b["player2_id"]), al="R"), chip(T, l, k)])
    return table_grid(ctx, "🎯 میزهای مسابقات تیمی", f"{len(rows)} میز · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["مسابقه", "دور", "تیم‌ها", "میز", "بازیکن ۱", "بازیکن ۲", "نتیجه"],
                      [80, 70, 400, 70, 200, 200, 130], rows, left=(2, 4, 5), empty="هنوز میزی ثبت نشده است.")


async def tab_admins(ctx: Ctx):
    T, m = ctx.T, await ctx.model()
    rows = []
    for a in m["admins"]:
        act = bool(_int(a["is_active"], 1))
        rows.append([a["id"], Cell(a["display_name"] or a["full_name"] or "", b=True, al="R"), Cell(_s(a["full_name"]), al="R"),
                     ("@" + a["username"]) if a["username"] else "", ROLE_FA.get(a["role"], a["role"] or "—"),
                     chip(T, "فعال" if act else "غیرفعال", "ok" if act else "mute"), _int(a["warnings"]) or "",
                     fa_date(a["joined_at"]), fa_date(a["last_active"], True)])
    return table_grid(ctx, "🛡 مدیران", f"{len(rows)} مدیر · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["شناسه", "نام نمایشی", "نام کامل", "یوزرنیم", "نقش", "وضعیت", "اخطار", "تاریخ عضویت", "آخرین فعالیت"],
                      [60, 200, 200, 150, 140, 100, 80, 110, 140], rows, left=(1, 2), empty="هنوز مدیری ثبت نشده است.")


async def tab_warnings(ctx: Ctx):
    T, m = ctx.T, await ctx.model()
    ws = await ctx.fetch("warnings_log", ["id", "target_type", "target_id", "reason", "issued_by", "issued_at"], order="id DESC", limit=3000)
    rows = []
    for w in ws:
        t = w["target_type"]
        if t == "player":
            nm = ctx.pname(w["target_id"])
        elif t == "team":
            nm = (m["teams"].get(w["target_id"]) or {}).get("name") or f"#{w['target_id']}"
        elif t == "admin":
            nm = ctx.who(w["target_id"])
        else:
            nm = str(_s(w["target_id"]))
        rows.append([fa_date(w["issued_at"], True), chip(T, WARN_TARGET.get(t, t or "—"), "warn"), Cell(nm, b=True, al="R"),
                     Cell(_clip(w["reason"]), al="R"), ctx.who(w["issued_by"])])
    return table_grid(ctx, "⚠️ اخطارها", f"{len(rows)} اخطار · جدیدترین بالا · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["زمان", "نوع", "دریافت‌کننده", "دلیل", "صادرکننده"], [140, 100, 220, 420, 150], rows, left=(2, 3),
                      empty="اخطاری ثبت نشده است.")


async def tab_tasks(ctx: Ctx):
    T, m = ctx.T, await ctx.model()
    ts = await ctx.fetch("tasks", ["id", "assigned_to", "assigned_by", "title", "description", "status", "fail_reason", "assigned_at", "completed_at"],
                         order="id DESC", limit=3000)
    rows = []
    for t in ts:
        l, k = TASK_STATUS.get(t["status"], (t["status"] or "—", "mute"))
        rows.append([t["id"], Cell(_s(t["title"]), b=True, al="R"), Cell(_clip(t["description"]), al="R"), ctx.who(t["assigned_to"]),
                     ctx.who(t["assigned_by"]), chip(T, l, k), Cell(_clip(t["fail_reason"]), al="R"), fa_date(t["assigned_at"], True),
                     fa_date(t["completed_at"], True)])
    return table_grid(ctx, "📋 وظایف", f"{len(rows)} وظیفه · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["شناسه", "عنوان", "شرح", "مسئول", "واگذارکننده", "وضعیت", "دلیل ناکامی", "تاریخ واگذاری", "تاریخ پایان"],
                      [60, 220, 300, 150, 150, 100, 220, 130, 130], rows, left=(1, 2, 6), empty="وظیفه‌ای ثبت نشده است.")


async def tab_calendar(ctx: Ctx):
    T = ctx.T
    cs = await ctx.fetch("calendar_days", ["id", "jdate", "day_type", "title", "created_by", "created_at"], order="jdate DESC", limit=3000)
    rows = []
    for c in cs:
        hol = c["day_type"] == "holiday"
        rows.append([_s(c["jdate"]), chip(T, "تعطیل" if hol else "ایونت", "bad" if hol else "ok"), Cell(_s(c["title"]), b=True, al="R"),
                     ctx.who(c["created_by"]), fa_date(c["created_at"], True)])
    return table_grid(ctx, "📅 تقویم", f"{len(rows)} روز ثبت‌شده · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["تاریخ (شمسی)", "نوع", "عنوان", "ثبت‌کننده", "زمان ثبت"], [120, 100, 320, 150, 130], rows, left=(2,),
                      empty="هنوز روزی در تقویم ثبت نشده است.")


async def tab_live(ctx: Ctx):
    T = ctx.T
    await ctx.model()
    total = await ctx.count("chess_games")
    gs = await ctx.fetch("chess_games", ["id", "white_id", "black_id", "white_name", "black_name", "status", "winner_id", "white_elo_change",
                                         "black_elo_change", "created_at", "finished_at"], order="id DESC", limit=1000)
    rows = []
    for x in gs:
        l, k = GAME_STATUS.get(x["status"], (x["status"] or "—", "mute"))
        win = "—"
        if x["winner_id"] is not None:
            win = x["white_name"] if x["winner_id"] == x["white_id"] else (x["black_name"] if x["winner_id"] == x["black_id"] else str(x["winner_id"]))

        def ec(v):
            if v is None:
                return Cell("")
            return Cell(v, fg=("166534" if v > 0 else "991B1B" if v < 0 else None) if T.colors else None, b=True, nf="+0;-0;0")
        rows.append([x["id"], Cell(_s(x["white_name"]), al="R"), Cell(_s(x["black_name"]), al="R"), chip(T, l, k), Cell(win, b=True, al="R"),
                     ec(x["white_elo_change"]), ec(x["black_elo_change"]), fa_date(x["created_at"], True), fa_date(x["finished_at"], True)])
    cap = " · ۱۰۰۰ بازیِ آخر نمایش داده می‌شود" if total > 1000 else ""
    return table_grid(ctx, "🔴 شطرنج زنده", f"{total} بازی{cap} · جدیدترین بالا · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["شناسه", "سفید", "سیاه", "وضعیت", "برنده", "تغییر Elo سفید", "تغییر Elo سیاه", "شروع", "پایان"],
                      [60, 190, 190, 150, 190, 120, 120, 130, 130], rows, left=(1, 2, 4), empty="هنوز بازیِ زنده‌ای انجام نشده است.")


async def tab_live_elo(ctx: Ctx):
    rs = await ctx.fetch("chess_elo", ["player_id", "display_name", "rating", "peak_rating", "games_played", "wins", "losses", "draws"], order="rating DESC")
    rows = [[i, Cell(_s(r["display_name"]) or f"#{r['player_id']}", b=True, al="R"), round(r["rating"] or 0), round(r["peak_rating"] or 0),
             _int(r["games_played"]), _int(r["wins"]), _int(r["draws"]), _int(r["losses"])] for i, r in enumerate(rs, 1)]
    return table_grid(ctx, "🏁 رده‌بندی شطرنج زنده (Elo)", f"{len(rows)} بازیکن · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["رتبه", "بازیکن", "Elo", "اوج Elo", "بازی", "برد", "مساوی", "باخت"], [70, 220, 90, 100, 80, 80, 80, 80], rows,
                      left=(1,), empty="هنوز بازیکنی در شطرنج زنده رتبه ندارد.")


async def tab_ai_chats(ctx: Ctx):
    await ctx.model()
    ss = await ctx.fetch("ai_chat_sessions", ["id", "user_id", "role", "title", "started_at", "last_message_at"], order="last_message_at DESC", limit=2000)
    counts = {}
    p = ctx.phys("ai_chat_messages")
    if p:
        for r in await ctx.q(f'SELECT session_id, COUNT(*) AS n FROM "{p}" GROUP BY session_id'):
            counts[r["session_id"]] = _int(r["n"])
    rows = [[x["id"], Cell(("مدیر مدرسه" if x["role"] == "principal" else ctx.who(x["user_id"])), b=True, al="R"),
             "مدیر مدرسه" if x["role"] == "principal" else "ادمین", Cell(_clip(x["title"], 120), al="R"), counts.get(x["id"], 0),
             fa_date(x["started_at"], True), fa_date(x["last_message_at"], True)] for x in ss]
    return table_grid(ctx, "🤖 رهگشا — گفتگوها", f"{len(rows)} گفتگو · (متنِ پیام‌ها فقط در بکاپ هست) · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["شناسه", "کاربر", "نقش", "عنوان", "تعداد پیام", "شروع", "آخرین پیام"], [60, 200, 110, 360, 100, 130, 130], rows,
                      left=(1, 3), empty="هنوز گفتگویی با رهگشا انجام نشده است.")


async def tab_ai_memory(ctx: Ctx):
    await ctx.model()
    ms = await ctx.fetch("ai_memory", ["id", "subject", "content", "visibility", "created_by", "created_at"], order="id DESC", limit=2000)
    rows = [[x["id"], Cell(_clip(x["subject"], 150), b=True, al="R"), Cell(_clip(x["content"], 800), al="R"), _s(x["visibility"]),
             ctx.who(x["created_by"]) if str(x["created_by"] or "").lstrip("-").isdigit() else _s(x["created_by"]), fa_date(x["created_at"], True)]
            for x in ms]
    return table_grid(ctx, "🧠 رهگشا — حافظه", f"{len(rows)} یادداشت · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["شناسه", "موضوع", "محتوا", "نمایان برای", "ثبت‌کننده", "تاریخ"], [60, 220, 520, 110, 160, 130], rows, left=(1, 2),
                      empty="حافظه‌ی رهگشا خالی است.")


async def tab_servers(ctx: Ctx):
    T = ctx.T
    await ctx.model()
    ss = await ctx.fetch("servers", ["id", "name", "school", "status", "created_at", "note", "suspend_reason", "suspend_until", "deleted_at"], order="id")
    stm = {"active": ("فعال", "ok"), "suspended": ("تعلیق", "warn"), "deleted": ("حذف‌شده", "bad")}
    rows = []
    for s in ss:
        l, k = stm.get(s["status"], (s["status"] or "—", "mute"))
        rows.append([s["id"], Cell(_s(s["name"]), b=True, al="R"), Cell(_s(s["school"]), al="R"), chip(T, l, k), Cell(_s(s["note"]), al="R"),
                     Cell(_s(s["suspend_reason"]), al="R"), _s(s["suspend_until"]), fa_date(s["created_at"])])
    return table_grid(ctx, "🖥 سرورها (مجموعه‌ها)", f"{len(rows)} سرور · آخرین به‌روزرسانی: {ctx.now_txt}",
                      ["شناسه", "نام", "مدرسه", "وضعیت", "یادداشت", "علت تعلیق", "تعلیق تا", "تاریخ ایجاد"],
                      [60, 200, 200, 100, 260, 220, 110, 110], rows, left=(1, 2, 4, 5), empty="سروری ثبت نشده است.")


# ───────────────────────── فهرستِ تب‌ها ─────────────────────────
CATEGORIES = ["۱ داشبورد و رده‌بندی", "۲ بازیکنان و کلاس‌ها", "۳ بازی‌ها و Elo", "۴ تیم‌ها", "۵ مدیران و نظارت", "۶ شطرنج زنده", "۷ رهگشا", "۸ سیستم"]
CAT_COLORS = ["2563EB", "16A34A", "D97706", "7C3AED", "DB2777", "DC2626", "0891B2", "475569"]


class TabDef:
    __slots__ = ("key", "title", "cat", "deps", "build", "main_only")

    def __init__(self, key, title, cat, deps, build, main_only=False):
        self.key, self.title, self.cat, self.deps, self.build, self.main_only = key, title, cat, set(deps), build, main_only

    @property
    def color(self):
        return CAT_COLORS[CATEGORIES.index(self.cat)]


_C = CATEGORIES
TABS = [
    TabDef("home", "🏠 خلاصه", _C[0], {"players", "matches", "tournaments", "teams", "admins", "chess_games", "classes", "player_elo"}, tab_home),
    TabDef("rank", "🏆 رده‌بندی", _C[0], {"players", "matches", "classes", "player_elo"}, tab_rank),
    TabDef("players", "👤 بازیکنان", _C[1], {"players", "classes", "matches", "player_elo", "teams", "team_members"}, tab_players),
    TabDef("classes", "🏫 کلاس‌ها", _C[1], {"classes", "players", "matches"}, tab_classes),
    TabDef("matches", "♟ بازی‌ها", _C[2], {"matches", "players", "tournaments", "admins"}, tab_matches),
    TabDef("tours", "🏅 تورنمنت‌ها", _C[2], {"tournaments", "matches"}, tab_tournaments),
    TabDef("elo", "📈 روند Elo", _C[2], {"elo_history", "players"}, tab_elo),
    TabDef("teams", "👥 تیم‌ها", _C[3], {"teams", "team_members", "players"}, tab_teams),
    TabDef("tmatches", "⚔️ مسابقات تیمی", _C[3], {"team_matches", "team_match_boards", "teams", "admins"}, tab_tmatches),
    TabDef("tboards", "🎯 میزهای تیمی", _C[3], {"team_matches", "team_match_boards", "teams", "players"}, tab_tboards),
    TabDef("admins", "🛡 مدیران", _C[4], {"admins"}, tab_admins),
    TabDef("warnings", "⚠️ اخطارها", _C[4], {"warnings_log", "players", "teams", "admins"}, tab_warnings),
    TabDef("tasks", "📋 وظایف", _C[4], {"tasks", "admins"}, tab_tasks),
    TabDef("calendar", "📅 تقویم", _C[4], {"calendar_days", "admins"}, tab_calendar),
    TabDef("live", "🔴 شطرنج زنده", _C[5], {"chess_games"}, tab_live),
    TabDef("live_elo", "🏁 Elo شطرنج زنده", _C[5], {"chess_elo"}, tab_live_elo),
    TabDef("ai_chats", "🤖 رهگشا — گفتگوها", _C[6], {"ai_chat_sessions", "admins"}, tab_ai_chats),
    TabDef("ai_mem", "🧠 رهگشا — حافظه", _C[6], {"ai_memory", "admins"}, tab_ai_memory),
    TabDef("servers", "🖥 سرورها", _C[7], {"servers"}, tab_servers, main_only=True),
]
TAB_BY_KEY = {t.key: t for t in TABS}

# جدول‌هایی که تغییرشان باید شیت را به‌روز کند (فقط روی همین‌ها تریگر نصب می‌شود)
WATCHED = set().union(*[t.deps for t in TABS])
# ستون‌هایی که تغییرِ تنهایشان شیت را «کثیف» نمی‌کند (مثلاً آخرین فعالیتِ مدیر که با هر کلیک عوض می‌شود)؛ هر ساعت یک‌بار تازه می‌شود
IGNORE_UPDATE_COLS = {"admins": {"last_active"}}
