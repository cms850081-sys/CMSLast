"""
ai_tools_ext.py — ابزارهای اضافه‌ی دستیار هوشمند (CMS)

شامل:
  ۱) برتر 🌟 / ویژه ⚡ کردنِ دسته‌ای بازیکن‌ها
  ۲) نفرات برتر (خودکار/دستی)
  ۳) مدیریت کامل مدیران (افزودن، اخراج/احیا، دسترسی‌ها، درخواست‌ها، لاگ/وظایف)
  ۴) آب‌وهوا + تاریخ و ساعت (+ رویدادهای تقویم)
  ۵) تیم‌ها: گزارش، مقایسه‌ی قدرت، ساخت/ویرایش/عضویت/اخطار/حذف
  ۶) پرونده‌ی کاملِ بازیکن با همه‌ی اخطارهایش

این فایل عمداً به ai_tools.py ایمپورت نمی‌کنه (چرخه‌ی ایمپورت نشه)؛ ai_tools.py
این لیست‌ها رو با لیست‌های خودش ادغام می‌کنه و dispatch_ext رو صدا می‌زنه.
"""
import asyncio
import difflib
import json
import logging
import re
from datetime import datetime, timedelta

import turso_db
import database as db
from config import (DB_PATH, PISHVA_ID, ROLE_PISHVA, ROLE_TOURNAMENT_MANAGER,
                    ROLE_SECURITY_MANAGER)
from helpers import now_shamsi, pishva_display, TEHRAN_TZ

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None

logger = logging.getLogger(__name__)

ALL_ROLES = [ROLE_PISHVA, ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER]
MGR = [ROLE_PISHVA, ROLE_TOURNAMENT_MANAGER]
ONLY_PISHVA = [ROLE_PISHVA]

ROLE_LABELS = {
    ROLE_TOURNAMENT_MANAGER: "🏆 مدیر مسابقات",
    ROLE_SECURITY_MANAGER: "🛡️ مدیر امنیتی",
    ROLE_PISHVA: "👑 مدیر ارشد",
}

# ════════════════════════════════════════════════════════════════
# جدول دسترسی‌ها، دسته‌بندی، ست‌ها
# ════════════════════════════════════════════════════════════════
TOOL_PERMISSIONS_EXT = {
    "set_player_tiers": MGR,
    "list_player_tiers": ALL_ROLES,
    "get_top_players": ALL_ROLES,
    "set_top_players": ONLY_PISHVA,
    "add_admin": ONLY_PISHVA,
    "set_admin_active": ONLY_PISHVA,
    "set_admin_display_name": ONLY_PISHVA,
    "get_admin_permissions": ONLY_PISHVA,
    "set_admin_permissions": ONLY_PISHVA,
    "list_access_requests": ONLY_PISHVA,
    "resolve_access_request": ONLY_PISHVA,
    "get_admin_activity": ONLY_PISHVA,
    "get_weather_and_time": ALL_ROLES,
    "get_team_details": ALL_ROLES,
    "compare_teams": ALL_ROLES,
    "create_team": MGR,
    "edit_team": MGR,
    "manage_team_members": MGR,
    "warn_team": MGR,
    "delete_team": ONLY_PISHVA,
    "get_player_full_profile": ALL_ROLES,
    "list_warned_players": ALL_ROLES,
}

CATEGORIES_EXT = [
    ("player_tiers", "🌟 برتر/⚡ ویژه‌کردن بازیکنان", ["set_player_tiers", "list_player_tiers"]),
    ("top_players", "🏆 نفرات برتر", ["get_top_players", "set_top_players"]),
    ("admin_full", "🧑‍💼 مدیریت کامل مدیران",
     ["add_admin", "set_admin_active", "set_admin_display_name", "get_admin_permissions",
      "set_admin_permissions", "list_access_requests", "resolve_access_request", "get_admin_activity"]),
    ("teams", "🏅 تیم‌ها و مقایسه‌ی قدرت",
     ["get_team_details", "compare_teams", "create_team", "edit_team",
      "manage_team_members", "warn_team", "delete_team"]),
    ("player_profiles", "📋 پرونده‌ی بازیکن و اخطارها", ["get_player_full_profile", "list_warned_players"]),
    ("weather_time", "🌦️ آب‌وهوا و تاریخ/ساعت", ["get_weather_and_time"]),
]

# ── خلاصه صبحگاهی (ماژول morning_brief.py): قانون‌ها و روزهای ارسال ──
import morning_brief as _mb
TOOL_PERMISSIONS_EXT.update({_n: ONLY_PISHVA for _n in _mb.TOOL_PERMISSIONS})
CATEGORIES_EXT.append(_mb.CATEGORY)

# ابزارهایی که واقعاً چیزی رو تغییر می‌دن (گزارش سیستم + اطلاع به مدیر ارشد)
ACTION_TOOL_NAMES_EXT = frozenset({
    "set_player_tiers", "set_top_players", "add_admin", "set_admin_active",
    "set_admin_display_name", "set_admin_permissions", "resolve_access_request",
    "create_team", "edit_team", "manage_team_members", "warn_team", "delete_team",
    "brief_times_set",
})

SCHEDULABLE_TOOL_NAMES_EXT = frozenset(TOOL_PERMISSIONS_EXT.keys())

# ════════════════════════════════════════════════════════════════
# اعلان ابزارها برای Gemini
# ════════════════════════════════════════════════════════════════
_STR = {"type": "string"}


def _arr(desc, item=None):
    return {"type": "array", "description": desc, "items": item or _STR}


TOOL_DECLARATIONS_EXT = [
    {
        "name": "set_player_tiers",
        "description": (
            "برتر (🌟 بازیکن برتر، tier=elite) یا ویژه (⚡ نیروی ویژه، tier=special) کردن — یا برداشتنِ این "
            "وضعیت (enabled=false) — برای یک یا چندین بازیکن به‌صورت همزمان و یکجا (حتی لیست ۲۰ نفره). "
            "«فلانی رو برتر کن» = elite، «فلانی رو ویژه کن» = special. اگه لیست مخلوطه (بعضی برتر، بعضی ویژه) "
            "از assignments استفاده کن؛ اگه همه‌ی اسم‌ها یک نوع‌ان از names + tier. ویژه‌کردن فقط برای مدیر ارشده. "
            "این با «نفرات برتر» (لیست ۵ نفره‌ی رتبه‌بندی) فرق داره."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "assignments": _arr(
                    "لیست بازیکن‌ها با نوعِ هرکدوم",
                    {
                        "type": "object",
                        "properties": {
                            "full_name": {"type": "string", "description": "نام کامل بازیکن (اختیاری: «نام/کلاس»)"},
                            "tier": {"type": "string", "enum": ["elite", "special"], "description": "elite=برتر، special=ویژه"},
                            "enabled": {"type": "boolean", "description": "true (پیش‌فرض)=اعطا، false=برداشتن"},
                        },
                        "required": ["full_name", "tier"],
                    },
                ),
                "names": _arr("فقط نام‌ها، وقتی همه یک نوع‌ان (همراه با tier)"),
                "tier": {"type": "string", "enum": ["elite", "special"], "description": "برای حالت names"},
                "enabled": {"type": "boolean", "description": "برای حالت names؛ پیش‌فرض true"},
            },
        },
    },
    {
        "name": "list_player_tiers",
        "description": "لیست بازیکن‌های برتر 🌟 و/یا ویژه ⚡ فعلی.",
        "parameters": {
            "type": "object",
            "properties": {"tier": {"type": "string", "enum": ["elite", "special", "both"], "description": "پیش‌فرض both"}},
        },
    },
    {
        "name": "get_top_players",
        "description": (
            "نفرات برتر (رتبه‌بندی): حالت فعلی (خودکار یا دستی)، لیست دستیِ مدیر ارشد، و رتبه‌بندیِ خودکار بر اساس "
            "امتیاز مسابقات (برد=۱، مساوی=۰٫۵) در بازه‌ی week/month/all."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": ["week", "month", "all"], "description": "پیش‌فرض all"},
                "limit": {"type": "integer", "description": "تعداد ردیف رتبه‌بندی خودکار (پیش‌فرض ۱۰)"},
            },
        },
    },
    {
        "name": "set_top_players",
        "description": (
            "تغییر نفرات برتر: mode (auto/manual) و/یا لیست دستی (ranking = اسم‌ها به ترتیب رتبه ۱ تا ۵، حداکثر ۵ نفر، "
            "فقط بازیکن فعال) و/یا clear=true برای خالی‌کردن لیست دستی. اگه لیست دستی می‌ذاری و می‌خوای نمایش داده بشه "
            "mode=manual هم بفرست."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["auto", "manual"]},
                "ranking": _arr("نام بازیکن‌ها به ترتیب رتبه (اولی=رتبه ۱)"),
                "clear": {"type": "boolean"},
            },
        },
    },
    {
        "name": "add_admin",
        "description": "افزودن مدیر جدید (یا فعال‌کردن/تغییر نقشِ مدیرِ موجود). آیدی عددی تلگرام لازمه.",
        "parameters": {
            "type": "object",
            "properties": {
                "telegram_id": {"type": "string", "description": "آیدی عددی تلگرام"},
                "full_name": {"type": "string"},
                "username": {"type": "string", "description": "اختیاری"},
                "role": {"type": "string", "enum": [ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER]},
            },
            "required": ["telegram_id", "role"],
        },
    },
    {
        "name": "set_admin_active",
        "description": "اخراج (active=false) یا احیای (active=true) یک مدیر.",
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string", "description": "آیدی، یوزرنیم یا نام مدیر"},
                "active": {"type": "boolean"},
            },
            "required": ["identifier", "active"],
        },
    },
    {
        "name": "set_admin_display_name",
        "description": "تغییر نام نمایشی یک مدیر.",
        "parameters": {
            "type": "object",
            "properties": {"identifier": _STR, "display_name": _STR},
            "required": ["identifier", "display_name"],
        },
    },
    {
        "name": "get_admin_permissions",
        "description": "نمایش همه‌ی دسترسی‌های یک مدیر (✅/❌).",
        "parameters": {"type": "object", "properties": {"identifier": _STR}, "required": ["identifier"]},
    },
    {
        "name": "set_admin_permissions",
        "description": (
            "روشن/خاموش‌کردن دسترسی‌های یک مدیر. permission می‌تونه کلید انگلیسی یا نام فارسی باشه: "
            "notifications اعلان، news اخبار، match_management مسابقات، view_players بازیکنان، issue_warning اخطار، "
            "request_ban درخواست اخراج، direct_ban اخراج مستقیم، assign_task وظیفه، report گزارش، bot_active ربات فعال، "
            "settings_access تنظیمات، senior_admin ارشد، edit_delete_match ویرایش مسابقه، communications مخابرات، "
            "ai_access دسترسی هوش مصنوعی، chess_access شطرنج زنده، hub_access پنل من (Hub)، calendar_edit ویرایش تقویم."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": _STR,
                "changes": _arr("تغییرها", {
                    "type": "object",
                    "properties": {"permission": _STR, "enabled": {"type": "boolean"}},
                    "required": ["permission", "enabled"],
                }),
            },
            "required": ["identifier", "changes"],
        },
    },
    {
        "name": "list_access_requests",
        "description": "لیست درخواست‌های دسترسیِ در انتظار.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "resolve_access_request",
        "description": "تأیید (approve) یا رد (reject) یک درخواست دسترسی با شناسه‌ی #.",
        "parameters": {
            "type": "object",
            "properties": {
                "request_id": {"type": "integer"},
                "decision": {"type": "string", "enum": ["approve", "reject"]},
            },
            "required": ["request_id", "decision"],
        },
    },
    {
        "name": "get_admin_activity",
        "description": "جزئیات یک مدیر: اخطارها (با دلیل)، آخرین اقدامات ثبت‌شده، وظایف. kind: all/logs/tasks/warnings.",
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": _STR,
                "kind": {"type": "string", "enum": ["all", "logs", "tasks", "warnings"]},
                "period": {"type": "string", "enum": ["today", "week", "month", "all"], "description": "برای logs"},
                "limit": {"type": "integer", "description": "حداکثر ردیف هر بخش (پیش‌فرض ۱۰، سقف ۴۰)"},
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "get_weather_and_time",
        "description": (
            "آب‌وهوای زنده‌ی سرپل‌ذهاب (همون سیستمی که توی پنل خوش‌آمدگویی هست) + تاریخ و ساعت دقیق تهران "
            "(میلادی/شمسی/روز هفته/بخش روز/فاز ماه) + رویداد یا تعطیلیِ امروز و بعدی در تقویم مدرسه."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_team_details",
        "description": (
            "گزارش کامل یک تیم: اطلاعات، سرگروه، آمار تیمی، اخطارهای تیم، و همه‌ی اعضا هرکدوم با Elo، آمار، "
            "برتر/ویژه‌بودن و کاملِ اخطارهاشون (دلیل و تاریخ)."
        ),
        "parameters": {"type": "object", "properties": {"team": {"type": "string", "description": "نام یا کد تیم"}}, "required": ["team"]},
    },
    {
        "name": "compare_teams",
        "description": (
            "مقایسه‌ی قدرت و سطح تیم‌ها. teams خالی = همه‌ی تیم‌ها. رتبه‌بندی بر اساس «شاخص قدرت» (میانگین Elo اعضا + "
            "پاداش برتر/ویژه − جریمه اخطار) همراه با تمام معیارهای خام."
        ),
        "parameters": {"type": "object", "properties": {"teams": _arr("نام یا کد تیم‌ها (اختیاری)")}},
    },
    {
        "name": "create_team",
        "description": "ساخت تیم جدید (حالت تیمی باید فعال باشه). member_names اختیاری؛ فقط بازیکن‌های فعالِ بدون تیم.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": _STR, "slogan": _STR, "requester_name": _STR,
                "member_names": _arr("نام اعضا"),
                "captain_name": {"type": "string", "description": "باید یکی از member_names باشه"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "edit_team",
        "description": "تغییر نام یا شعار تیم.",
        "parameters": {
            "type": "object",
            "properties": {"team": _STR, "new_name": _STR, "new_slogan": _STR},
            "required": ["team"],
        },
    },
    {
        "name": "manage_team_members",
        "description": "افزودن/حذف عضو و تعیین سرگروه یک تیم (هر سه اختیاری، می‌تونن با هم بیان).",
        "parameters": {
            "type": "object",
            "properties": {
                "team": _STR,
                "add_names": _arr("بازیکن‌هایی که اضافه بشن"),
                "remove_names": _arr("بازیکن‌هایی که حذف بشن"),
                "captain_name": {"type": "string", "description": "سرگروه جدید (باید عضو تیم باشه)"},
            },
            "required": ["team"],
        },
    },
    {
        "name": "warn_team",
        "description": "ثبت اخطار برای یک تیم با دلیل.",
        "parameters": {"type": "object", "properties": {"team": _STR, "reason": _STR}, "required": ["team", "reason"]},
    },
    {
        "name": "delete_team",
        "description": "حذف یک تیم (فقط مدیر ارشد). فقط وقتی صریحاً خواسته شد.",
        "parameters": {"type": "object", "properties": {"team": _STR}, "required": ["team"]},
    },
    {
        "name": "get_player_full_profile",
        "description": "پرونده‌ی کامل یک بازیکن: آمار، Elo، برتر/ویژه، تیم، آخرین مسابقات، و کاملِ سابقه‌ی اخطارها (دلیل/تاریخ/ثبت‌کننده).",
        "parameters": {"type": "object", "properties": {"full_name": {"type": "string", "description": "نام کامل (اختیاری «نام/کلاس»)"}}, "required": ["full_name"]},
    },
    {
        "name": "list_warned_players",
        "description": "بازیکن‌هایی که اخطار دارن، با دلیلِ آخرین اخطارهاشون.",
        "parameters": {"type": "object", "properties": {"min_warnings": {"type": "integer", "description": "حداقل تعداد اخطار (پیش‌فرض ۱)"}}},
    },
]

# ════════════════════════════════════════════════════════════════
# کمکی‌ها: نرمال‌سازی، تطبیق نام
# ════════════════════════════════════════════════════════════════
_LETTER_FIX = str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه"})
_DIGIT_FIX = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_INVIS_RE = re.compile("[\u200c\u200d\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def norm(s) -> str:
    s = _s(s).translate(_LETTER_FIX).translate(_DIGIT_FIX)
    s = _INVIS_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _to_bool(v, default=True) -> bool:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    t = norm(v)
    if t in ("false", "0", "no", "n", "off", "خیر", "نه", "برداشتن", "بردار"):
        return False
    if t in ("true", "1", "yes", "y", "on", "بله", "آره"):
        return True
    return default


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return [x for x in v if x is not None]
    if isinstance(v, str):
        return [p.strip() for p in re.split(r"[\n،,؛;]+", v) if p.strip()]
    return []


def split_name_class(raw):
    raw = _s(raw)
    if "/" in raw:
        n, c = raw.split("/", 1)
        return n.strip(), c.strip()
    return raw, ""


def match_player(raw_name, players):
    """('ok', row) | ('ambiguous', [rows]) | ('none', [نزدیک‌ترین نام‌ها])"""
    name, cls = split_name_class(raw_name)
    q = norm(name)
    if not q:
        return "none", []
    qcls = norm(cls)

    def cls_ok(p):
        pc = norm(p["class_name"])
        return (not qcls) or qcls == pc or qcls in pc

    exact = [p for p in players if norm(p["full_name"]) == q and cls_ok(p)]
    if len(exact) == 1:
        return "ok", exact[0]
    if len(exact) > 1:
        return "ambiguous", exact
    qtok = q.split()
    partial = []
    for p in players:
        if not cls_ok(p):
            continue
        ptok = norm(p["full_name"]).split()
        if all(any(t == pt or (len(t) >= 3 and pt.startswith(t)) for pt in ptok) for t in qtok):
            partial.append(p)
    if len(partial) == 1:
        return "ok", partial[0]
    if len(partial) > 1:
        return "ambiguous", partial
    close = difflib.get_close_matches(name, [p["full_name"] for p in players], n=3, cutoff=0.6)
    return "none", close


def _problem_line(raw, kind, payload) -> str:
    if kind == "ambiguous":
        opts = "، ".join(f"{p['full_name']} (کلاس {p['class_name'] or '—'})" for p in payload[:5])
        return f"❌ «{raw}» چند نفره: {opts} — با «نام/کلاس» مشخصش کن"
    hint = f" (شاید منظورت «{'» یا «'.join(payload)}» بود؟)" if payload else ""
    return f"❌ «{raw}» پیدا نشد{hint}"


async def _find_admin(identifier):
    ident = norm(identifier).lstrip("@")
    if not ident:
        return None
    admins = await db.get_all_admins()
    for a in admins:
        if (a["username"] or "").lstrip("@").lower() == ident:
            return a
    if ident.isdigit():
        for a in admins:
            if a["telegram_id"] == int(ident):
                return a
    for a in admins:
        if ident in norm(a["full_name"]) or ident in norm(a["display_name"]):
            return a
    # اسمِ تایپ‌شده ممکنه با اسمِ ثبت‌شده کمی فرق داشته باشه (فاصله/نیم‌فاصله، حرف‌های شبیه، غلط املایی):
    # تطبیقِ تقریبی روی فشرده‌ی اسم‌ها (بدون فاصله)، فقط اگه یک نفر واضحاً نزدیک‌تر از بقیه باشه.
    squash = lambda t: norm(t).replace(" ", "")
    q = squash(ident)
    if len(q) >= 3:
        scored = []
        for a in admins:
            best = 0.0
            for nm in (a["full_name"], a["display_name"], (a["username"] or "")):
                c = squash(nm)
                if not c:
                    continue
                r = difflib.SequenceMatcher(None, q, c).ratio()
                if q in c:   # فقط «عبارتِ تایپ‌شده داخل اسمِ ثبت‌شده»؛ برعکسش (اسم کوتاه داخل عبارتِ بلند) ممکنه آدمِ اشتباه رو بگیره
                    r = max(r, 0.9)
                best = max(best, r)
            scored.append((best, a))
        scored.sort(key=lambda x: -x[0])
        if scored and scored[0][0] >= 0.75 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08):
            return scored[0][1]
    return None


def _admin_name(a) -> str:
    return (a["display_name"] or a["full_name"] or str(a["telegram_id"])) if a else "؟"


async def _who(uid, admin_map=None) -> str:
    if uid == PISHVA_ID:
        return await pishva_display()
    if admin_map is None:
        admin_map = {a["telegram_id"]: a for a in await db.get_all_admins()}
    a = admin_map.get(uid)
    return _admin_name(a) if a else str(uid)


async def _dm(ctx, chat_id, text):
    try:
        await ctx.bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        pass


def _short_date(v) -> str:
    return str(v or "")[:16].replace("T", " ")


# ════════════════════════════════════════════════════════════════
# ۱) برتر / ویژه
# ════════════════════════════════════════════════════════════════
def _parse_tier(v):
    t = norm(v)
    if t in ("elite", "برتر", "بازیکن برتر", "ستاره", "star"):
        return "elite"
    if t in ("special", "ویژه", "نیروی ویژه", "نیروی‌ ویژه"):
        return "special"
    return None


_TIER_COL = {"elite": "is_elite", "special": "is_special"}
_TIER_ON = {"elite": "🌟 برتر شد", "special": "⚡ ویژه شد"}
_TIER_OFF = {"elite": "➖ از برترها برداشته شد", "special": "➖ از ویژه‌ها برداشته شد"}


async def _bulk_set_flag(col, val, ids):
    assert col in ("is_elite", "is_special")
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        ph = ",".join("?" * len(chunk))
        async with turso_db.connect(DB_PATH) as conn:
            await conn.execute(f"UPDATE players SET {col}=? WHERE id IN ({ph})", [val, *chunk])
            await conn.commit()
    db._invalidate_players_cache()


async def _set_player_tiers(args, caller_id, caller_role):
    items = []
    default_enabled = args.get("enabled", True)
    for it in _as_list(args.get("assignments")):
        if isinstance(it, dict):
            items.append((_s(it.get("full_name")), it.get("tier", args.get("tier")),
                          it.get("enabled", default_enabled)))
        elif isinstance(it, str):
            items.append((it, args.get("tier"), default_enabled))
    for n in _as_list(args.get("names")):
        items.append((_s(n), args.get("tier"), default_enabled))
    if not items:
        return "❌ لیست بازیکن‌ها خالیه — اسم‌ها و اینکه «برتر» بشن یا «ویژه» رو بفرست."
    if len(items) > 300:
        return "❌ حداکثر ۳۰۰ نفر در هر بار. لیست رو تکه‌تکه کن."

    players = await db.get_all_players()
    problems, already, lines_on, lines_off = [], [], {"elite": [], "special": []}, {"elite": [], "special": []}
    targets = {}  # (col,val) -> {pid: name}
    seen = set()
    for raw, tier_raw, en_raw in items:
        tier = _parse_tier(tier_raw)
        if tier is None:
            problems.append(f"❌ «{raw}»: نوع مشخص نیست (برتر یا ویژه؟)")
            continue
        if tier == "special" and caller_role != ROLE_PISHVA:
            problems.append(f"⛔ «{raw}»: ویژه‌کردن فقط برای مدیر ارشده")
            continue
        kind, payload = match_player(raw, players)
        if kind != "ok":
            problems.append(_problem_line(raw, kind, payload))
            continue
        p = payload
        want = 1 if _to_bool(en_raw, True) else 0
        if (p["id"], tier) in seen:
            continue
        seen.add((p["id"], tier))
        col = _TIER_COL[tier]
        if int(p[col] or 0) == want:
            already.append(f"{p['full_name']} ({'برتر' if tier == 'elite' else 'ویژه'})")
            continue
        targets.setdefault((col, want), {})[p["id"]] = p["full_name"]
        (lines_on if want else lines_off)[tier].append(p["full_name"])

    changed = 0
    for (col, val), idmap in targets.items():
        await _bulk_set_flag(col, val, list(idmap.keys()))
        changed += len(idmap)

    out = []
    for tier in ("elite", "special"):
        if lines_on[tier]:
            out.append(f"{_TIER_ON[tier]}: " + "، ".join(lines_on[tier]))
        if lines_off[tier]:
            out.append(f"{_TIER_OFF[tier]}: " + "، ".join(lines_off[tier]))
    if already:
        out.append("ℹ️ از قبل در همین وضعیت بودن: " + "، ".join(already))
    out += problems

    if changed:
        await db.log_action(caller_id, "player_tiers", f"{changed} تغییر برتر/ویژه (دستیار هوشمند)")
        head = f"✅ {changed} تغییر انجام شد."
    elif already and not problems:
        head = "ℹ️ همه از قبل در همین وضعیت بودن؛ تغییری لازم نبود."
    else:
        head = "❌ هیچ تغییری انجام نشد."
    return head + "\n" + "\n".join(out)


async def _list_player_tiers(args):
    tier = norm(args.get("tier")) or "both"
    players = await db.get_all_players()
    out = []
    for key, label, col in (("elite", "🌟 بازیکنان برتر", "is_elite"), ("special", "⚡ نیروهای ویژه", "is_special")):
        if tier not in ("both", key):
            continue
        rows = [p for p in players if p[col]]
        if not rows:
            out.append(f"{label}: هیچ‌کس")
            continue
        out.append(f"{label} ({len(rows)}):")
        out += [f"- {p['full_name']} | کلاس {p['class_name'] or '—'} | وضعیت {p['status']}" for p in rows]
    return "\n".join(out)


# ════════════════════════════════════════════════════════════════
# ۲) نفرات برتر
# ════════════════════════════════════════════════════════════════
TOP_MODE_KEY = "top_players_mode"
TOP_MANUAL_KEY = "top_players_manual"


async def _top_mode() -> str:
    v = await db.get_setting(TOP_MODE_KEY, "auto")
    return v if v in ("auto", "manual") else "auto"


async def _manual_list() -> list:
    raw = await db.get_setting(TOP_MANUAL_KEY, "[]")
    try:
        data = json.loads(raw) or []
    except Exception:
        data = []
    out = []
    for it in data:
        try:
            out.append({"rank": int(it["rank"]), "player_id": int(it["player_id"])})
        except Exception:
            continue
    return sorted(out, key=lambda x: x["rank"])


async def _period_scores(period):
    where = "WHERE result IN ('white','black','draw')"
    params = []
    if period in ("week", "month"):
        days = 7 if period == "week" else 30
        where += " AND created_at >= ?"
        params.append((datetime.now() - timedelta(days=days)).isoformat())
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(
            f"SELECT white_player_id, black_player_id, result FROM matches {where}", params
        ) as cur:
            rows = await cur.fetchall()
    stats = {}

    def st(pid):
        return stats.setdefault(pid, {"games": 0, "wins": 0, "draws": 0, "losses": 0, "score": 0.0})

    for r in rows:
        w, b, res = r["white_player_id"], r["black_player_id"], r["result"]
        sw, sb = st(w), st(b)
        sw["games"] += 1
        sb["games"] += 1
        if res == "white":
            sw["wins"] += 1; sw["score"] += 1; sb["losses"] += 1
        elif res == "black":
            sb["wins"] += 1; sb["score"] += 1; sw["losses"] += 1
        else:
            sw["draws"] += 1; sb["draws"] += 1; sw["score"] += .5; sb["score"] += .5
    return stats


async def _get_top_players(args):
    period = norm(args.get("period")) or "all"
    if period not in ("week", "month", "all"):
        period = "all"
    try:
        limit = max(1, min(30, int(args.get("limit") or 10)))
    except (TypeError, ValueError):
        limit = 10
    mode = await _top_mode()
    players = await db.get_all_players()
    meta = {p["id"]: p for p in players}
    out = [f"🏆 حالت نفرات برتر: {'🖐️ دستی' if mode == 'manual' else '⚡ خودکار'}"]

    manual = await _manual_list()
    if manual:
        out.append("\nلیست دستیِ مدیر ارشد" + (" (همین الان نمایش داده می‌شه):" if mode == "manual" else " (فعلاً غیرفعال، چون حالت خودکاره):"))
        for it in manual:
            p = meta.get(it["player_id"])
            out.append(f"رتبه {it['rank']}: {p['full_name'] + ' (کلاس ' + (p['class_name'] or '—') + ')' if p else 'بازیکن حذف‌شده'}")
    elif mode == "manual":
        out.append("\nلیست دستی خالیه.")

    stats = await _period_scores(period)
    rows = sorted(stats.items(), key=lambda kv: (-kv[1]["score"], -kv[1]["wins"],
                                                  (meta.get(kv[0]) or {"full_name": ""})["full_name"] if meta.get(kv[0]) else ""))
    label = {"week": "۷ روز اخیر", "month": "۳۰ روز اخیر", "all": "کل دوران"}[period]
    out.append(f"\nرتبه‌بندی خودکار ({label}):" + ("" if mode == "auto" else " [برای مقایسه]"))
    if not rows:
        out.append("هنوز مسابقه‌ی دارای نتیجه‌ای نیست.")
    for i, (pid, s) in enumerate(rows[:limit], 1):
        p = meta.get(pid)
        out.append(f"{i}. {p['full_name'] if p else 'بازیکن حذف‌شده'} (کلاس {(p['class_name'] if p else None) or '—'}) — "
                   f"{s['score']:g} امتیاز | {s['wins']} برد، {s['draws']} مساوی، {s['losses']} باخت")
    return "\n".join(out)


async def _set_top_players(args, caller_id):
    mode = norm(args.get("mode"))
    ranking = args.get("ranking")
    clear = _to_bool(args.get("clear"), False)
    if mode not in ("", "auto", "manual"):
        return "❌ mode باید auto یا manual باشه."
    if not mode and ranking is None and not clear:
        return "❌ چیزی برای تغییر نفرستادی (mode / ranking / clear)."

    done = []
    if clear or ranking is not None:
        names = _as_list(ranking)
        if clear and not names:
            new_list = []
        else:
            if not names:
                return "❌ لیست ranking خالیه."
            if len(names) > 5:
                return "❌ نفرات برتر حداکثر ۵ نفره."
            players = [p for p in await db.get_all_players() if p["status"] == "active"]
            probs, chosen = [], []
            for raw in names:
                kind, payload = match_player(raw, players)
                if kind != "ok":
                    probs.append(_problem_line(raw, kind, payload))
                elif payload["id"] in [c["id"] for c in chosen]:
                    probs.append(f"❌ «{raw}» تکراریه")
                else:
                    chosen.append(payload)
            if probs:
                return "❌ لیست ذخیره نشد (فقط بازیکن‌های فعال قبوله):\n" + "\n".join(probs)
            new_list = [{"rank": i, "player_id": p["id"]} for i, p in enumerate(chosen, 1)]
            done.append("لیست دستی: " + "، ".join(f"{i}. {p['full_name']}" for i, p in enumerate(chosen, 1)))
        await db.set_setting(TOP_MANUAL_KEY, json.dumps(new_list, ensure_ascii=False))
        if not new_list:
            done.append("لیست دستی خالی شد")
    if mode:
        await db.set_setting(TOP_MODE_KEY, mode)
        done.append(f"حالت: {'دستی' if mode == 'manual' else 'خودکار'}")
    cur_mode = await _top_mode()
    await db.log_action(caller_id, "top_players", "؛ ".join(done) + " (دستیار هوشمند)")
    msg = "✅ نفرات برتر به‌روز شد — " + "؛ ".join(done)
    if (ranking is not None) and cur_mode == "auto":
        msg += "\nℹ️ حالت فعلی «خودکار»ه، پس لیست دستی تا وقتی حالت رو «دستی» نکنی نمایش داده نمی‌شه."
    return msg


# ════════════════════════════════════════════════════════════════
# ۳) مدیریت مدیران
# ════════════════════════════════════════════════════════════════
ADMIN_PERM_LABELS = {
    "notifications": "اعلان", "news": "اخبار", "match_management": "مسابقات", "view_players": "بازیکنان",
    "issue_warning": "اخطار", "request_ban": "درخواست اخراج", "direct_ban": "اخراج مستقیم",
    "assign_task": "وظیفه", "report": "گزارش", "bot_active": "ربات فعال", "settings_access": "تنظیمات",
    "senior_admin": "ارشد", "edit_delete_match": "ویرایش مسابقه", "communications": "مخابرات",
    "ai_access": "دسترسی هوش مصنوعی", "chess_access": "شطرنج زنده", "hub_access": "پنل من (Hub)",
    "calendar_edit": "ویرایش تقویم",
}
_PERM_DEFAULT_TRUE = {"ai_access", "chess_access", "hub_access"}
_PERM_BY_LABEL = {norm(v): k for k, v in ADMIN_PERM_LABELS.items()}


def _perm_key(raw):
    t = norm(raw).replace(" ", "_")
    if t in ADMIN_PERM_LABELS:
        return t
    return _PERM_BY_LABEL.get(norm(raw))


def _load_perms(a) -> dict:
    try:
        return json.loads(a["permissions"] or "{}")
    except Exception:
        return {}


async def _add_admin(args, caller_id, ctx):
    tid_s = norm(args.get("telegram_id"))
    if not re.fullmatch(r"\d{5,15}", tid_s):
        return "❌ آیدی عددی تلگرام لازمه (فقط رقم)."
    tid = int(tid_s)
    if tid == PISHVA_ID:
        return "❌ این آیدی مدیر ارشده و نیازی به ثبت نداره."
    role = _s(args.get("role"))
    if role not in (ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER):
        return "❌ نقش باید tournament_manager یا security_manager باشه."
    full_name = _s(args.get("full_name")) or f"مدیر {tid}"
    username = _s(args.get("username")).lstrip("@")
    username = f"@{username}" if username else ""
    existing = await db.get_admin(tid)
    if existing:
        await db.update_admin_role_active(tid, role)
        verb = "از قبل بود؛ نقشش به‌روز و فعال شد"
    else:
        await db.create_admin(tid, username, full_name, role)
        verb = "به‌عنوان مدیر جدید ثبت شد"
    await db.log_action(caller_id, "set_admin_keyword", f"{full_name} -> {role} (دستیار هوشمند)", tid)
    await _dm(ctx, tid, f"✅ دسترسی شما به ربات به‌عنوان «{ROLE_LABELS[role]}» فعال شد.\n/start بزنید.")
    return f"✅ {full_name} ({tid}) {verb}: {ROLE_LABELS[role]}."


async def _set_admin_active(args, caller_id, ctx):
    a = await _find_admin(args.get("identifier"))
    if not a:
        return f"❌ مدیری با مشخصات «{_s(args.get('identifier'))}» پیدا نشد."
    active = _to_bool(args.get("active"), True)
    tid = a["telegram_id"]
    name = _admin_name(a)
    if bool(a["is_active"]) == active:
        return f"ℹ️ {name} از قبل {'فعال' if active else 'غیرفعال'} بود."
    if active:
        await db.revive_admin(tid)
        await db.log_action(caller_id, "revive_admin", f"احیای مدیر: {a['full_name']} (دستیار هوشمند)", tid)
        await _dm(ctx, tid, "✅ دسترسی شما به ربات توسط مدیر ارشد دوباره فعال شد.\n/start بزنید.")
        return f"🔄 {name} احیا شد."
    await db.kick_admin(tid)
    await db.log_action(caller_id, "kick_admin", f"اخراج مدیر: {a['full_name']} (دستیار هوشمند)", tid)
    await _dm(ctx, tid, "🚫 دسترسی شما به ربات توسط مدیر ارشد لغو شد.")
    return f"🚫 {name} اخراج شد."


async def _set_admin_display_name(args, caller_id):
    a = await _find_admin(args.get("identifier"))
    if not a:
        return f"❌ مدیری با مشخصات «{_s(args.get('identifier'))}» پیدا نشد."
    new = _s(args.get("display_name"))
    if not new:
        return "❌ نام نمایشی نمی‌تونه خالی باشه."
    await db.update_admin_display_name(a["telegram_id"], new)
    await db.log_action(caller_id, "admin_display_name", f"{_admin_name(a)} -> {new}", a["telegram_id"])
    return f"✅ نام نمایشی {_admin_name(a)} شد «{new}»."


async def _get_admin_permissions(args):
    a = await _find_admin(args.get("identifier"))
    if not a:
        return f"❌ مدیری با مشخصات «{_s(args.get('identifier'))}» پیدا نشد."
    perms = _load_perms(a)
    lines = [f"🔐 دسترسی‌های {_admin_name(a)} ({ROLE_LABELS.get(a['role'], a['role'])}):"]
    for k, label in ADMIN_PERM_LABELS.items():
        on = perms.get(k, k in _PERM_DEFAULT_TRUE)
        lines.append(f"{'✅' if on else '❌'} {label} ({k})")
    return "\n".join(lines)


async def _set_admin_permissions(args, caller_id, ctx):
    a = await _find_admin(args.get("identifier"))
    if not a:
        return f"❌ مدیری با مشخصات «{_s(args.get('identifier'))}» پیدا نشد."
    changes = [c for c in _as_list(args.get("changes")) if isinstance(c, dict)]
    if not changes:
        return "❌ لیست تغییرها (changes) خالیه."
    tid = a["telegram_id"]
    ok, bad = [], []
    for c in changes:
        key = _perm_key(c.get("permission"))
        if not key:
            bad.append(f"❌ «{_s(c.get('permission'))}» دسترسی شناخته‌شده نیست")
            continue
        val = _to_bool(c.get("enabled"), True)
        await db.set_admin_permission(tid, key, val)
        await db.log_action(caller_id, "toggle_perm", f"{key}: {val} (دستیار هوشمند)", tid)
        ok.append((key, val))
    if ok:
        txt = "\n".join(f"{'⬆️' if v else '⬇️'} دسترسی «{ADMIN_PERM_LABELS[k]}» {'فعال ✅' if v else 'غیرفعال ❌'} شد." for k, v in ok)
        await _dm(ctx, tid, txt)
    head = f"✅ {len(ok)} دسترسی {_admin_name(a)} تغییر کرد: " + "، ".join(
        f"{ADMIN_PERM_LABELS[k]}={'✅' if v else '❌'}" for k, v in ok) if ok else "❌ هیچ دسترسی‌ای تغییر نکرد."
    return head + ("\n" + "\n".join(bad) if bad else "")


async def _list_access_requests():
    rows = await db.get_pending_requests()
    if not rows:
        return "هیچ درخواست دسترسیِ در انتظاری نیست."
    lines = [f"📥 {len(rows)} درخواست در انتظار:"]
    for r in rows:
        lines.append(f"- #{r['id']} | {r['full_name'] or '؟'} ({r['username'] or '—'}) | آیدی {r['telegram_id']} | "
                     f"{ROLE_LABELS.get(r['role'], r['role'])} | پیام: {r['message'] or '—'} | {_short_date(r['requested_at'])}")
    return "\n".join(lines)


async def _resolve_access_request(args, caller_id, ctx):
    try:
        rid = int(args.get("request_id"))
    except (TypeError, ValueError):
        return "❌ request_id باید عدد باشه (از list_access_requests بگیر)."
    decision = norm(args.get("decision"))
    if decision not in ("approve", "reject"):
        return "❌ decision باید approve یا reject باشه."
    req = await db.get_access_request(rid)
    if not req:
        return f"❌ درخواستی با شناسه‌ی #{rid} نیست."
    if req["status"] != "pending":
        return f"ℹ️ درخواست #{rid} قبلاً پردازش شده ({req['status']})."
    if decision == "approve":
        await db.update_access_request(rid, "approved")
        await db.create_admin(req["telegram_id"], req["username"], req["full_name"], req["role"])
        await db.close_other_open_requests(req["telegram_id"], rid)
        await _dm(ctx, req["telegram_id"], f"✅ دسترسی تأیید شد\n💼 {ROLE_LABELS.get(req['role'], req['role'])}\n\n/start بزنید.")
        await db.log_action(caller_id, "access_approve", f"تأیید درخواست #{rid} (دستیار هوشمند)", req["telegram_id"])
        return f"✅ درخواست #{rid} ({req['full_name']}) تأیید شد و مدیر ثبت گردید."
    await db.update_access_request(rid, "rejected")
    await _dm(ctx, req["telegram_id"], "❌ درخواست دسترسی شما رد شد.\nبرای اطلاعات بیشتر با مدیر ارشد تماس بگیرید.")
    await db.log_action(caller_id, "access_reject", f"رد درخواست #{rid} (دستیار هوشمند)", req["telegram_id"])
    return f"✅ درخواست #{rid} ({req['full_name']}) رد شد."


async def _get_admin_activity(args):
    a = await _find_admin(args.get("identifier"))
    if not a:
        return f"❌ مدیری با مشخصات «{_s(args.get('identifier'))}» پیدا نشد."
    kind = norm(args.get("kind")) or "all"
    period = norm(args.get("period")) or "all"
    if period not in ("today", "week", "month", "all"):
        period = "all"
    try:
        limit = max(1, min(40, int(args.get("limit") or 10)))
    except (TypeError, ValueError):
        limit = 10
    tid = a["telegram_id"]
    out = [f"👤 {_admin_name(a)} | {ROLE_LABELS.get(a['role'], a['role'])} | {'فعال' if a['is_active'] else 'غیرفعال'} | "
           f"اخطار فعلی: {a['warnings']} | آخرین فعالیت: {_short_date(a['last_active'])}"]
    if kind in ("all", "warnings"):
        async with turso_db.connect(DB_PATH) as conn:
            conn.row_factory = turso_db.Row
            async with conn.execute(
                "SELECT reason, issued_by, issued_at FROM warnings_log WHERE target_type='admin' AND target_id=? "
                "ORDER BY id DESC LIMIT ?", (tid, limit)) as cur:
                wrows = await cur.fetchall()
        out.append("\n⚠️ سابقه‌ی اخطارها:" if wrows else "\n⚠️ سابقه‌ی اخطاری ثبت نشده.")
        amap = {x["telegram_id"]: x for x in await db.get_all_admins()}
        for w in wrows:
            out.append(f"- {_short_date(w['issued_at'])} | {w['reason']} | توسط {await _who(w['issued_by'], amap)}")
    if kind in ("all", "logs"):
        rows, total = await db.get_action_logs(period, tid, 0, limit)
        out.append(f"\n📊 اقدامات ({total} مورد در این بازه؛ {len(rows)} آخرین):" if rows else "\n📊 اقدامی ثبت نشده.")
        for r in rows:
            out.append(f"- {_short_date(r['logged_at'])} | {r['action_type']} | {r['description'] or ''}")
    if kind in ("all", "tasks"):
        tasks = await db.get_tasks_for(tid)
        out.append(f"\n📋 وظایف ({len(tasks)}):" if tasks else "\n📋 وظیفه‌ای ندارد.")
        for t in tasks[:limit]:
            extra = f" | دلیل: {t['fail_reason']}" if t["fail_reason"] else ""
            out.append(f"- #{t['id']} {t['title']} | {t['status']} | {_short_date(t['assigned_at'])}{extra}")
    return "\n".join(out)


# ════════════════════════════════════════════════════════════════
# ۴) آب‌وهوا + تاریخ و ساعت
# ════════════════════════════════════════════════════════════════
_MOON_NAMES = {"🌑": "ماه نو", "🌒": "هلال رو به رشد", "🌓": "تربیع اول", "🌔": "محدب رو به رشد",
               "🌕": "بدر کامل", "🌖": "محدب رو به کاهش", "🌗": "تربیع آخر", "🌘": "هلال رو به کاهش"}


def _day_part(hour: int) -> str:
    if hour >= 23 or hour < 4:
        return "دل‌شب"
    if hour < 7:
        return "سحر"
    if hour < 11:
        return "صبح"
    if hour < 14:
        return "ظهر"
    if hour < 17:
        return "عصر"
    if hour < 19:
        return "غروب"
    return "شب"


async def _fetch_weather_raw(auth_mod):
    if httpx is None:
        return None
    async with httpx.AsyncClient(timeout=6.0) as client:
        r = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": auth_mod.SARPOL_LAT, "longitude": auth_mod.SARPOL_LON,
                "current_weather": "true",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
                "timezone": "Asia/Tehran", "forecast_days": 3,
            },
        )
        r.raise_for_status()
        return r.json()


async def _weather_and_time():
    import jdatetime
    import helpers
    now = datetime.now(TEHRAN_TZ)
    jd = jdatetime.datetime.fromgregorian(datetime=now)
    out = ["🕒 زمان دقیق (وقت رسمی تهران):",
           f"- میلادی: {now.strftime('%Y-%m-%d %H:%M:%S')} ({helpers.weekday_fa(now)})",
           f"- شمسی: {jd.strftime('%Y/%m/%d %H:%M:%S')}",
           f"- بخش روز: {_day_part(now.hour)}"]

    auth_mod = None
    try:
        import auth as auth_mod  # ماژول آب‌وهوای خودِ سیستم
        emoji = auth_mod.moon_phase_emoji()
        out.append(f"- فاز ماه: {emoji} {_MOON_NAMES.get(emoji, '')}")
    except Exception:
        logger.exception("moon phase failed")

    # تقویم مدرسه
    try:
        today_j = jd.strftime("%Y/%m/%d")
        day = await db.get_calendar_day(today_j)
        if day:
            out.append(f"- تقویم امروز: {'تعطیلی' if day['day_type'] == 'holiday' else 'رویداد'} — {day['title']}")
        else:
            out.append("- تقویم امروز: رویداد یا تعطیلیِ ثبت‌شده‌ای نیست")
        for typ, lab in (("holiday", "تعطیلیِ بعدی"), ("event", "رویدادِ بعدی")):
            nxt = await db.get_next_calendar_day(typ, today_j)
            if nxt and nxt["jdate"] != today_j:
                out.append(f"- {lab}: {nxt['jdate']} — {nxt['title']}")
    except Exception:
        logger.exception("calendar lookup failed")

    out.append("\n🌦️ آب‌وهوای سرپل‌ذهاب:")
    if auth_mod is None:
        out.append("(ماژول آب‌وهوا در دسترس نیست)")
        return "\n".join(out)
    try:
        import hub_weather  # منبعِ واحد: همان داده‌ی پنلِ هاب (بارشِ فعلی + ساعتی + چندمدلی)
        w = await hub_weather._get_weather()
        out.extend(hub_weather.report_lines(w))
        if w.get("stale"):
            out.append("(داده چند دقیقه قدیمی است؛ اتصال به سرویس هوا موقتاً مشکل دارد)")
    except Exception as e:
        logger.warning("weather via hub_weather failed: %r", e)
        try:
            data = await _fetch_weather_raw(auth_mod)
            cw = (data or {}).get("current_weather") or {}
            if cw.get("temperature") is not None:
                code = cw.get("weathercode")
                mood = (auth_mod._WEATHER_MOOD.get(code) or ["نامشخص"])[0]
                out.append(f"- الان: {cw['temperature']:.0f}°C، {mood}، باد {cw.get('windspeed', '؟')} km/h")
                out.append("- بارشِ لحظه‌ای/ساعتی در دسترس نیست؛ درباره‌ی بارشِ همین الان قطعی حرف نزن.")
        except Exception as e2:
            logger.warning("weather raw fetch failed: %r", e2)
    if len(out) and out[-1].startswith("\n🌦️"):
        out.append("(الان دریافت آب‌وهوا ممکن نشد)")
    return "\n".join(out)


# ════════════════════════════════════════════════════════════════
# ۵) تیم‌ها
# ════════════════════════════════════════════════════════════════
async def _find_team(q):
    """(team_row, None) | (None, پیام خطا)"""
    teams = await db.get_all_teams()
    raw = _s(q)
    nq = norm(raw)
    if not nq:
        return None, "❌ نام تیم رو بگو."
    m = re.fullmatch(r"#?(\d+)", nq)
    if m and raw.startswith("#"):
        for t in teams:
            if t["id"] == int(m.group(1)):
                return t, None
    for t in teams:
        if norm(t["team_code"]) == nq:
            return t, None
    exact = [t for t in teams if norm(t["name"]) == nq]
    if len(exact) == 1:
        return exact[0], None
    part = [t for t in teams if nq in norm(t["name"])]
    if len(part) == 1:
        return part[0], None
    if len(part) > 1 or len(exact) > 1:
        return None, "❌ چند تیم با این نام هست: " + "، ".join(t["name"] for t in (exact or part)[:6])
    close = difflib.get_close_matches(raw, [t["name"] for t in teams], n=3, cutoff=0.6)
    return None, f"❌ تیمی به نام «{raw}» پیدا نشد." + (f" شاید منظورت: {'، '.join(close)}" if close else "")


async def _members_rich(team_ids):
    if not team_ids:
        return {}
    from elo import ensure_elo_table
    await ensure_elo_table()
    ph = ",".join("?" * len(team_ids))
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(
            f"""SELECT tm.team_id, tm.player_id, tm.is_reserve, p.full_name, p.status, p.warnings, p.wins, p.losses,
                       p.draws, p.is_elite, p.is_special, c.name AS class_name,
                       COALESCE(pe.rating, 1200) AS rating, COALESCE(pe.games_played, 0) AS elo_games
                FROM team_members tm
                JOIN players p ON p.id = tm.player_id
                LEFT JOIN classes c ON c.id = p.class_id
                LEFT JOIN player_elo pe ON pe.player_id = p.id
                WHERE tm.team_id IN ({ph})""", list(team_ids)) as cur:
            rows = await cur.fetchall()
    out = {tid: [] for tid in team_ids}
    for r in rows:
        out[r["team_id"]].append({k: r[k] for k in r.keys()})
    return out


async def _warning_logs(target_type, ids):
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(
            f"SELECT target_id, reason, issued_by, issued_at FROM warnings_log WHERE target_type=? "
            f"AND target_id IN ({ph}) ORDER BY id", [target_type, *ids]) as cur:
            rows = await cur.fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["target_id"], []).append({k: r[k] for k in r.keys()})
    return out


def _metrics(team, members, tstats):
    from elo import get_elo_title
    base = [m for m in members if m["status"] == "active"] or members
    n = len(members)
    ratings = sorted((m["rating"] for m in base), reverse=True)
    avg = sum(ratings) / len(ratings) if ratings else None
    top3 = sum(ratings[:3]) / len(ratings[:3]) if ratings else None
    w = sum(m["wins"] or 0 for m in members)
    l = sum(m["losses"] or 0 for m in members)
    d = sum(m["draws"] or 0 for m in members)
    games = w + l + d
    elites = sum(1 for m in members if m["is_elite"])
    specials = sum(1 for m in members if m["is_special"])
    mem_warn = sum(m["warnings"] or 0 for m in members)
    team_warn = team["warnings"] or 0
    power = None
    if n and avg is not None:
        power = avg + 100 * elites / n + 50 * specials / n - 20 * team_warn - 5 * mem_warn / n
    best = max(base, key=lambda m: m["rating"]) if base else None
    return {
        "n": n, "active": sum(1 for m in members if m["status"] == "active"),
        "reserve": sum(1 for m in members if m["is_reserve"]),
        "avg": avg, "top3": top3, "W": w, "L": l, "D": d,
        "winrate": (w / games) if games else None, "games": games,
        "elites": elites, "specials": specials, "mem_warn": mem_warn, "team_warn": team_warn,
        "power": power, "level": get_elo_title(avg) if avg is not None else "—",
        "best": f"{best['full_name']} ({best['rating']:.0f})" if best else "—",
        "tstats": tstats,
    }


async def _gather_teams(teams):
    ids = [t["id"] for t in teams]
    members, stats_list = await asyncio.gather(
        _members_rich(ids), asyncio.gather(*[db.get_team_stats(i) for i in ids]))
    stats = dict(zip(ids, stats_list))
    return {t["id"]: _metrics(t, members.get(t["id"], []), stats[t["id"]]) for t in teams}, members


def _fmt_metrics_line(rank, t, m):
    wr = f"{m['winrate'] * 100:.0f}٪" if m["winrate"] is not None else "—"
    power = f"{m['power']:.0f}" if m["power"] is not None else "—"
    avg = f"{m['avg']:.0f}" if m["avg"] is not None else "—"
    top3 = f"{m['top3']:.0f}" if m["top3"] is not None else "—"
    ts = m["tstats"]
    return (f"#{rank} «{t['name']}» | قدرت {power} | سطح {m['level']} | اعضا {m['n']} (فعال {m['active']}، ذخیره {m['reserve']}) | "
            f"میانگین Elo {avg} (۳ نفر برتر {top3}) | بهترین: {m['best']} | آمار فردی: {m['W']} برد {m['D']} مساوی {m['L']} باخت (نرخ برد {wr}) | "
            f"مسابقات تیمی: {ts['wins']} برد {ts['draws']} مساوی {ts['losses']} باخت | 🌟{m['elites']} ⚡{m['specials']} | "
            f"اخطار تیم {m['team_warn']} | مجموع اخطار اعضا {m['mem_warn']}")


async def _compare_teams(args):
    names = _as_list(args.get("teams"))
    all_teams = await db.get_all_teams()
    if not all_teams:
        return "هیچ تیم فعالی ثبت نشده."
    if names:
        teams, errs = [], []
        for n in names:
            t, err = await _find_team(n)
            if err:
                errs.append(err)
            elif t["id"] not in [x["id"] for x in teams]:
                teams.append(t)
        if not teams:
            return "\n".join(errs)
    else:
        teams, errs = all_teams, []
    metrics, _members = await _gather_teams(teams)
    ranked = sorted(teams, key=lambda t: (-(metrics[t["id"]]["power"] if metrics[t["id"]]["power"] is not None else -9999),
                                          -(metrics[t["id"]]["winrate"] or 0)))
    out = [f"🏅 مقایسه‌ی {len(ranked)} تیم (به ترتیب قدرت):"]
    out += [_fmt_metrics_line(i, t, metrics[t["id"]]) for i, t in enumerate(ranked, 1)]
    out.append("\nفرمول شاخص قدرت: میانگین Elo اعضای فعال + ۱۰۰×(نسبت اعضای برتر) + ۵۰×(نسبت اعضای ویژه) − ۲۰×(اخطار تیم) − ۵×(میانگین اخطار هر عضو). "
               "سطح = عنوان Elo میانگین. اگه بازیکن‌ها هنوز بازی Elo‌دار ندارن، همه ۱۲۰۰ حساب می‌شن و تفاوت اصلی از برتر/ویژه/اخطار میاد؛ نرخ برد رو هم کنارش ببین.")
    out += errs
    return "\n".join(out)


async def _get_team_details(args):
    t, err = await _find_team(args.get("team"))
    if err:
        return err
    metrics, members_map = await _gather_teams([t])
    m, members = metrics[t["id"]], members_map.get(t["id"], [])
    amap = {a["telegram_id"]: a for a in await db.get_all_admins()}
    captain = "تعیین نشده"
    if t["captain_id"]:
        p = await db.get_player(t["captain_id"])
        captain = p["full_name"] if p else "تعیین نشده"
    out = [f"🏆 تیم «{t['name']}» | کد {t['team_code']} | شعار: {t['slogan'] or '—'} | ثبت: {str(t['created_at'])[:10]} | "
           f"درخواست‌دهنده: {t['requester_name'] or '—'} | سرگروه: {captain}",
           _fmt_metrics_line(1, t, m).split("|", 1)[1].strip()]

    tw = (await _warning_logs("team", [t["id"]])).get(t["id"], [])
    out.append(f"\n⚠️ اخطارهای تیم (فعلی {t['warnings']}، سابقه {len(tw)}):" if tw else f"\n⚠️ اخطار تیم: {t['warnings']} (سابقه‌ای در لاگ نیست)")
    for w in tw:
        out.append(f"- {_short_date(w['issued_at'])} | {w['reason']} | توسط {await _who(w['issued_by'], amap)}")

    plogs = await _warning_logs("player", [x["player_id"] for x in members])
    out.append(f"\n👥 اعضا ({len(members)}):")
    for x in sorted(members, key=lambda z: -z["rating"]):
        tags = ("🌟" if x["is_elite"] else "") + ("⚡" if x["is_special"] else "") + ("🪑ذخیره" if x["is_reserve"] else "")
        out.append(f"- {x['full_name']} {tags} | کلاس {x['class_name'] or '—'} | {x['status']} | Elo {x['rating']:.0f} | "
                   f"{x['wins'] or 0}برد {x['draws'] or 0}مساوی {x['losses'] or 0}باخت | اخطار فعلی {x['warnings'] or 0}")
        for w in plogs.get(x["player_id"], []):
            out.append(f"    ⚠️ {_short_date(w['issued_at'])} | {w['reason']} | توسط {await _who(w['issued_by'], amap)}")

    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(
            """SELECT tm.id, t1.name AS n1, t2.name AS n2, tm.result, tm.status, tm.created_at
               FROM team_matches tm LEFT JOIN teams t1 ON t1.id=tm.team1_id LEFT JOIN teams t2 ON t2.id=tm.team2_id
               WHERE tm.team1_id=? OR tm.team2_id=? ORDER BY tm.id DESC LIMIT 5""", [t["id"], t["id"]]) as cur:
            tms = await cur.fetchall()
    if tms:
        res_fa = {"team1": "برد تیم اول", "team2": "برد تیم دوم", "draw": "مساوی"}
        out.append("\n🤝 آخرین مسابقات تیمی:")
        for r in tms:
            out.append(f"- #{r['id']} {r['n1']} ⚔️ {r['n2']} → {res_fa.get(r['result'], r['result'] or r['status'])}")
    return "\n".join(out)


def _can_create_team_msg(caller_role, team_mode, mgr_flag):
    if team_mode != "1":
        return "❌ حالت تیمی غیرفعاله. اول از تنظیمات (یا toggle_bot_setting با کلید team_mode_enabled) فعالش کن."
    if caller_role != ROLE_PISHVA and mgr_flag != "1":
        return "⛔ فقط مدیر ارشد می‌تونه تیم بسازه (ساخت تیم برای مدیران خاموشه)."
    return None


async def _create_team(args, caller_id, caller_role):
    team_mode, mgr_flag = await asyncio.gather(
        db.get_setting("team_mode_enabled", "0"), db.get_setting("managers_can_create_teams", "0"))
    msg = _can_create_team_msg(caller_role, team_mode, mgr_flag)
    if msg:
        return msg
    name = _s(args.get("name"))
    if not name:
        return "❌ نام تیم لازمه."
    if any(norm(t["name"]) == norm(name) for t in await db.get_all_teams()):
        return f"❌ تیمی با نام «{name}» از قبل هست."
    players = [p for p in await db.get_all_players() if p["status"] == "active"]
    taken = await db.get_players_with_team()
    chosen, notes = [], []
    for raw in _as_list(args.get("member_names")):
        kind, payload = match_player(raw, players)
        if kind != "ok":
            notes.append(_problem_line(raw, kind, payload))
        elif payload["id"] in taken:
            notes.append(f"⚠️ {payload['full_name']} از قبل عضو تیم دیگه‌ایه؛ اضافه نشد")
        elif payload["id"] not in [c["id"] for c in chosen]:
            chosen.append(payload)
    captain = None
    cap_raw = _s(args.get("captain_name"))
    if cap_raw:
        kind, payload = match_player(cap_raw, chosen)
        if kind == "ok":
            captain = payload
        else:
            notes.append(f"⚠️ سرگروه «{cap_raw}» بین اعضای انتخابی نبود؛ تعیین نشد")
    requester = _s(args.get("requester_name")) or "—"
    tid = await db.create_team(name, _s(args.get("slogan")), requester, caller_id)
    for p in chosen:
        await db.add_team_member(tid, p["id"])
    if captain:
        await db.update_team(tid, captain_id=captain["id"])
    team = await db.get_team(tid)
    await db.log_action(caller_id, "create_team", f"ثبت تیم: {name} (دستیار هوشمند)", tid)
    res = f"✅ تیم «{name}» ساخته شد | کد {team['team_code']} | {len(chosen)} عضو" + (f" | سرگروه {captain['full_name']}" if captain else "")
    return res + ("\n" + "\n".join(notes) if notes else "")


async def _edit_team(args, caller_id):
    t, err = await _find_team(args.get("team"))
    if err:
        return err
    upd = {}
    new_name, new_slogan = _s(args.get("new_name")), args.get("new_slogan")
    if new_name:
        upd["name"] = new_name
    if new_slogan is not None and _s(new_slogan):
        upd["slogan"] = _s(new_slogan)
    if not upd:
        return "❌ نام یا شعار جدیدی نفرستادی."
    await db.update_team(t["id"], **upd)
    await db.log_action(caller_id, "edit_team", f"ویرایش تیم {t['name']}: {upd} (دستیار هوشمند)", t["id"])
    return f"✅ تیم «{t['name']}» ویرایش شد: " + "، ".join(f"{'نام' if k == 'name' else 'شعار'} → {v}" for k, v in upd.items())


async def _manage_team_members(args, caller_id):
    t, err = await _find_team(args.get("team"))
    if err:
        return err
    tid = t["id"]
    adds, rems, cap_raw = _as_list(args.get("add_names")), _as_list(args.get("remove_names")), _s(args.get("captain_name"))
    if not (adds or rems or cap_raw):
        return "❌ چیزی برای انجام نفرستادی (add_names / remove_names / captain_name)."
    out = []
    if adds:
        players = [p for p in await db.get_all_players() if p["status"] == "active"]
        taken = await db.get_players_with_team()
        cur_ids = {m["player_id"] for m in await db.get_team_members(tid)}
        for raw in adds:
            kind, payload = match_player(raw, players)
            if kind != "ok":
                out.append(_problem_line(raw, kind, payload))
            elif payload["id"] in cur_ids:
                out.append(f"ℹ️ {payload['full_name']} از قبل عضو همین تیمه")
            elif payload["id"] in taken:
                out.append(f"⚠️ {payload['full_name']} عضو تیم دیگه‌ایه؛ اضافه نشد")
            else:
                await db.add_team_member(tid, payload["id"])
                cur_ids.add(payload["id"])
                out.append(f"➕ {payload['full_name']} اضافه شد")
    if rems:
        members = await db.get_team_members(tid)
        for raw in rems:
            kind, payload = match_player(raw, [{"id": m["player_id"], "full_name": m["full_name"], "class_name": m["class_name"]} for m in members])
            if kind != "ok":
                out.append(_problem_line(raw, kind, payload))
                continue
            await db.remove_team_member(tid, payload["id"])
            line = f"🗑️ {payload['full_name']} از تیم حذف شد"
            if t["captain_id"] == payload["id"]:
                await db.update_team(tid, captain_id=None)
                line += " (سرگروه بود؛ سرگروهی برداشته شد)"
            out.append(line)
    if cap_raw:
        members = await db.get_team_members(tid)
        kind, payload = match_player(cap_raw, [{"id": m["player_id"], "full_name": m["full_name"], "class_name": m["class_name"]} for m in members])
        if kind != "ok":
            out.append(_problem_line(cap_raw, kind, payload) + " (بین اعضای تیم)")
        else:
            await db.update_team(tid, captain_id=payload["id"])
            out.append(f"👑 {payload['full_name']} سرگروه شد")
    await db.log_action(caller_id, "team_members", f"تیم {t['name']}: " + " | ".join(out)[:300] + " (دستیار هوشمند)", tid)
    ok = any(x.startswith(("➕", "🗑️", "👑")) for x in out)
    return (f"✅ تیم «{t['name']}» به‌روز شد:\n" if ok else f"❌ تغییری روی تیم «{t['name']}» انجام نشد:\n") + "\n".join(out)


async def _warn_team(args, caller_id):
    t, err = await _find_team(args.get("team"))
    if err:
        return err
    reason = _s(args.get("reason"))
    if not reason:
        return "❌ دلیل اخطار لازمه."
    await db.add_team_warning(t["id"], reason, caller_id)
    t2 = await db.get_team(t["id"])
    await db.log_action(caller_id, "team_warning", f"اخطار به تیم {t['name']}: {reason}", t["id"])
    return f"⚠️ به تیم «{t['name']}» اخطار ثبت شد (مجموع {t2['warnings']}). دلیل: {reason}"


async def _delete_team(args, caller_id):
    t, err = await _find_team(args.get("team"))
    if err:
        return err
    await db.delete_team(t["id"])
    await db.log_action(caller_id, "delete_team", f"حذف تیم: {t['name']} (دستیار هوشمند)", t["id"])
    return f"🗑️ تیم «{t['name']}» حذف شد."


# ════════════════════════════════════════════════════════════════
# ۶) پرونده‌ی بازیکن
# ════════════════════════════════════════════════════════════════
async def _get_player_full_profile(args):
    players = await db.get_all_players()
    raw = _s(args.get("full_name"))
    kind, payload = match_player(raw, players)
    if kind != "ok":
        return _problem_line(raw, kind, payload)
    p = payload
    from elo import get_player_elo, get_elo_title, ensure_elo_table
    await ensure_elo_table()
    elo = await get_player_elo(p["id"])
    amap = {a["telegram_id"]: a for a in await db.get_all_admins()}
    logs = (await _warning_logs("player", [p["id"]])).get(p["id"], [])
    async with turso_db.connect(DB_PATH) as conn:
        conn.row_factory = turso_db.Row
        async with conn.execute(
            "SELECT t.name, tm.is_reserve FROM team_members tm JOIN teams t ON t.id=tm.team_id "
            "WHERE tm.player_id=? AND t.status='active'", [p["id"]]) as cur:
            teams = await cur.fetchall()
    games = (p["wins"] or 0) + (p["losses"] or 0) + (p["draws"] or 0)
    out = [f"👤 {p['full_name']} | کلاس {p['class_name'] or '—'} | وضعیت: {p['status']}"
           + (f" ({p['suspension_reason']})" if p["suspension_reason"] else ""),
           f"{'🌟 برتر' if p['is_elite'] else 'برتر: خیر'} | {'⚡ ویژه' if p['is_special'] else 'ویژه: خیر'}",
           f"آمار: {p['wins'] or 0} برد، {p['draws'] or 0} مساوی، {p['losses'] or 0} باخت ({games} بازی)",
           f"Elo: {elo['rating']:.0f} ({get_elo_title(elo['rating'])}) | اوج {elo['peak_rating']:.0f} | {elo['games_played']} بازی Elo‌دار",
           "تیم: " + ("، ".join(t["name"] + (" (ذخیره)" if t["is_reserve"] else "") for t in teams) if teams else "بدون تیم"),
           f"یادداشت: {p['notes'] or '—'}",
           f"\n⚠️ اخطار فعلی: {p['warnings'] or 0} | سابقه‌ی ثبت‌شده در لاگ: {len(logs)} مورد"
           + (" (ممکنه بعضی با «احیا» صفر شده باشن)" if len(logs) != (p['warnings'] or 0) else "")]
    for w in logs:
        out.append(f"- {_short_date(w['issued_at'])} | {w['reason']} | توسط {await _who(w['issued_by'], amap)}")
    hist = await db.get_player_match_history(p["id"])
    if hist:
        out.append("\n♟️ آخرین مسابقات:")
        for m in hist[:5]:
            res = m["result"]
            if res in ("white", "black"):
                won = (res == "white") == (m["white_player_id"] == p["id"])
                r_fa = "برد" if won else "باخت"
            else:
                r_fa = "مساوی" if res == "draw" else "بدون نتیجه"
            opp = m["black_name"] if m["white_player_id"] == p["id"] else m["white_name"]
            out.append(f"- #{m['id']} مقابل {opp or '؟'} → {r_fa}")
    return "\n".join(out)


async def _list_warned_players(args):
    try:
        mn = max(1, int(args.get("min_warnings") or 1))
    except (TypeError, ValueError):
        mn = 1
    rows = [p for p in await db.get_all_players() if (p["warnings"] or 0) >= mn]
    if not rows:
        return f"بازیکنی با {mn} اخطار یا بیشتر نیست."
    rows.sort(key=lambda p: -(p["warnings"] or 0))
    rows = rows[:40]
    logs = await _warning_logs("player", [p["id"] for p in rows])
    amap = {a["telegram_id"]: a for a in await db.get_all_admins()}
    out = [f"⚠️ {len(rows)} بازیکن با ≥{mn} اخطار:"]
    for p in rows:
        out.append(f"- {p['full_name']} (کلاس {p['class_name'] or '—'}) | {p['warnings']} اخطار | {p['status']}")
        for w in logs.get(p["id"], [])[-3:]:
            out.append(f"    ⚠️ {_short_date(w['issued_at'])} | {w['reason']} | توسط {await _who(w['issued_by'], amap)}")
    return "\n".join(out)


# ════════════════════════════════════════════════════════════════
# دیسپچر — None یعنی این ابزار مال این ماژول نیست
# ════════════════════════════════════════════════════════════════
TOOL_DECLARATIONS_EXT = TOOL_DECLARATIONS_EXT + _mb.TOOL_DECLARATIONS


async def dispatch_ext(name, args, caller_id, caller_role, ctx):
    if name not in TOOL_PERMISSIONS_EXT:
        return None
    args = args or {}
    if name in _mb.TOOL_PERMISSIONS:
        return await _mb.dispatch_tool(name, args, getattr(ctx, "job_queue", None), ctx)
    if name == "set_player_tiers":
        return await _set_player_tiers(args, caller_id, caller_role)
    if name == "list_player_tiers":
        return await _list_player_tiers(args)
    if name == "get_top_players":
        return await _get_top_players(args)
    if name == "set_top_players":
        return await _set_top_players(args, caller_id)
    if name == "add_admin":
        return await _add_admin(args, caller_id, ctx)
    if name == "set_admin_active":
        return await _set_admin_active(args, caller_id, ctx)
    if name == "set_admin_display_name":
        return await _set_admin_display_name(args, caller_id)
    if name == "get_admin_permissions":
        return await _get_admin_permissions(args)
    if name == "set_admin_permissions":
        return await _set_admin_permissions(args, caller_id, ctx)
    if name == "list_access_requests":
        return await _list_access_requests()
    if name == "resolve_access_request":
        return await _resolve_access_request(args, caller_id, ctx)
    if name == "get_admin_activity":
        return await _get_admin_activity(args)
    if name == "get_weather_and_time":
        return await _weather_and_time()
    if name == "get_team_details":
        return await _get_team_details(args)
    if name == "compare_teams":
        return await _compare_teams(args)
    if name == "create_team":
        return await _create_team(args, caller_id, caller_role)
    if name == "edit_team":
        return await _edit_team(args, caller_id)
    if name == "manage_team_members":
        return await _manage_team_members(args, caller_id)
    if name == "warn_team":
        return await _warn_team(args, caller_id)
    if name == "delete_team":
        return await _delete_team(args, caller_id)
    if name == "get_player_full_profile":
        return await _get_player_full_profile(args)
    if name == "list_warned_players":
        return await _list_warned_players(args)
    return None
