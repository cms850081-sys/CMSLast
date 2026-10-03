"""
hub_caps.py — «چه کسی در پنل من چه کاری می‌تونه بکنه» و «کِی کلِ پنل بسته‌ست».

۱) قابلیت‌ها (capabilities)
   • مدیر ارشد: همه‌چیز + سه قابلیتِ مخصوصِ خودش (مدیریتِ مدیران، تنظیمات، پنلِ مدیر ارشد).
   • مسئول مسابقات و مسئول انتظامات: پیش‌فرضِ هر نقش در ROLE_DEFAULTS.
   • شخصی‌سازیِ هر مدیر: مدیر ارشد از داخلِ هاب برای هر مدیر هر قابلیت را جدا روشن/خاموش
     می‌کند؛ این انتخاب در admins.permissions زیرِ کلیدِ "hub_caps" ذخیره می‌شود و
     هر درخواستِ API دقیقاً از همین‌جا چک می‌شود (نه فقط مخفی‌شدنِ دکمه).
   • اگر برای یک قابلیت override ذخیره نشده باشد: پیش‌فرضِ نقش، و اگر معادلِ قدیمی‌اش
     در دسترسی‌های رباتِ تلگرام (issue_warning, edit_delete_match, ...) خاموش باشد،
     همان خاموش می‌ماند — یعنی تنظیماتِ فعلیِ مدیران بعد از این آپدیت عوض نمی‌شود.

۲) دروازه‌ی وضعیت (gate)
   وقتی حالت تعمیر / حالت آپدیت / وضعیتِ خطرناک / وضعیتِ APS / خاموشیِ ربات برای
   ادمین‌ها / بسته‌بودنِ ساعتِ کاری فعال باشد، هر مدیرِ غیرِ ارشد در هاب قفل می‌شود
   (دقیقاً همان قواعدی که در ربات هست). مدیر ارشد هیچ‌وقت قفل نمی‌شود.
"""

import asyncio
import json

import database as db
from config import ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER

# قابلیت‌های قابل‌شخصی‌سازی (کلید، عنوان فارسی، گروه)
CAPS = [
    ("players_view",    "مشاهده‌ی بازیکنان و پنلِ بازیکن", "بازیکنان"),
    ("player_register", "ثبت و ویرایشِ بازیکن",            "بازیکنان"),
    ("player_warn",     "اخطار به بازیکن",                  "بازیکنان"),
    ("player_kick",     "اخراج / درخواستِ اخراجِ بازیکن",   "بازیکنان"),
    ("match_create",    "ثبتِ مسابقه",                      "مسابقات"),
    ("match_edit",      "ویرایشِ مسابقه",                   "مسابقات"),
    ("match_delete",    "حذفِ مسابقه",                      "مسابقات"),
    ("match_scan",      "ثبت با عکس (مدیر مسابقات: با تأییدِ مدیر ارشد)", "مسابقات"),
    ("predictions",     "پیش‌بینی",                         "مسابقات"),
    ("elo",             "امتیازهای Elo",                    "مسابقات"),
    ("comms",           "مخابرات",                          "ارتباط"),
    ("classes",         "مدیریتِ کلاس‌ها",                  "ساختار"),
    ("teams",           "مدیریتِ تیم‌ها",                   "ساختار"),
    ("calendar",        "مشاهده‌ی تقویم",                   "تقویم"),
    ("calendar_edit",   "ویرایشِ تقویم (تعطیلی/رویداد)",     "تقویم"),
]
CAP_KEYS = [c[0] for c in CAPS]

# فقط مدیر ارشد؛ هیچ override‌ای هم اثر ندارد.
# player_delete (حذفِ کاملِ بازیکن) فقط مدیر ارشد: از CAPS برداشته شد تا نه نقشِ پیش‌فرضی بتواند داشته
# باشد و نه هیچ overrideی (حتی اگر قبلاً در permissions یک مدیر ذخیره شده باشد) اثری داشته باشد.
PISHVA_ONLY = ("admins_manage", "settings", "pishva_panel", "player_delete")

ROLE_DEFAULTS = {
    ROLE_TOURNAMENT_MANAGER: {
        "players_view", "player_register", "player_warn", "player_kick",
        "match_create", "match_edit", "match_delete", "match_scan", "predictions", "elo",
        "comms", "classes", "teams", "calendar",
    },
    ROLE_SECURITY_MANAGER: {
        "players_view", "player_warn", "player_kick", "comms", "calendar",
    },
}

# معادل‌های قدیمیِ دسترسی‌های ربات: اگر صراحتاً False باشند، قابلیتِ هاب هم بسته می‌ماند.
_LEGACY_DENY = {
    "match_create": "match_management",
    "match_scan": "match_management",
    "match_edit": "edit_delete_match",
    "match_delete": "edit_delete_match",
    "player_warn": "issue_warning",
    "players_view": "view_players",
    "comms": "communications",
}


def parse_perms(admin) -> dict:
    try:
        p = json.loads(admin["permissions"] or "{}")
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}


def role_caps(role: str, perms: dict) -> dict:
    """{cap: bool} برای یک مدیرِ عادی، بدونِ اعمالِ سوییچ‌های کلیِ سیستم."""
    base = ROLE_DEFAULTS.get(role, set())
    overrides = perms.get("hub_caps") if isinstance(perms.get("hub_caps"), dict) else {}
    out = {}
    for k in CAP_KEYS:
        if k in overrides:
            out[k] = bool(overrides[k])
            continue
        v = k in base
        legacy = _LEGACY_DENY.get(k)
        if v and legacy and perms.get(legacy) is False:
            v = False
        if k == "player_kick" and v:
            v = bool(perms.get("request_ban", True) or perms.get("direct_ban", False))
        if k == "calendar_edit" and perms.get("calendar_edit") is True:
            v = True
        out[k] = v
    return out


# همه‌ی کلیدهایی که مسیرِ احرازِ هویت (gate_reason + compute_caps + hub_enabled) می‌خواند،
# با «همان پیش‌فرض‌هایی» که خودِ آن توابع به‌کار می‌برند.
_AUTH_SETTING_DEFAULTS = {
    "hub_enabled": "1",
    "repair_mode": "0", "bot_update_mode": "0", "system_status": "normal",
    "bot_active_for_admins": "1", "working_hours_active": "0", "working_hours_system_enabled": "0",
    "match_registration_enabled": "1", "communications_enabled": "1", "team_mode_enabled": "0",
    "managers_can_create_teams": "0", "admin_direct_kick_enabled": "1",
    "scan_enabled": "1", "scan_default_mode": "approval",
}


async def prewarm_auth_settings():
    """وقتی کش سرد است، ۱۲ کلید را با «یک» کوئری پر می‌کند؛ بعدش همه‌ی get_settingهای
    مسیرِ احراز از کش جواب می‌دهند (قبلاً چند موجِ جدا از رفت‌وبرگشتِ شبکه بود)."""
    await db.get_settings_with_defaults(_AUTH_SETTING_DEFAULTS)


async def compute_caps(is_pishva: bool, admin):
    """(caps: set[str], feats: dict). caps شاملِ همه‌ی سوییچ‌های کلیِ سیستم هم هست.
    پیش‌فرضِ هر کلید دقیقاً همان مقداری‌ست که ربات برای همان کلید به‌کار می‌برد."""
    match_reg, comms_on, team_mode, mgr_create, direct_global, scan_on = await asyncio.gather(
        db.get_setting("match_registration_enabled", "1"),
        db.get_setting("communications_enabled", "1"),
        db.get_setting("team_mode_enabled", "0"),
        db.get_setting("managers_can_create_teams", "0"),
        db.get_setting("admin_direct_kick_enabled", "1"),
        db.get_setting("scan_enabled", "1"),
    )
    team_on = team_mode == "1"
    feats = {
        "team_mode": team_on,
        "team_create": team_on and (is_pishva or mgr_create == "1"),
        "direct_kick": False,
    }
    if is_pishva:
        caps = set(CAP_KEYS) | set(PISHVA_ONLY)
        feats["direct_kick"] = True
        if not team_on:
            caps.discard("teams")
        return caps, feats

    perms = parse_perms(admin)
    caps = {k for k, v in role_caps(admin["role"], perms).items() if v}
    if match_reg != "1":
        caps.discard("match_create")
        caps.discard("match_scan")
    if scan_on != "1":
        caps.discard("match_scan")      # کلید کلیِ «ثبت با عکس» خاموش = برای همه‌ی مدیرانِ غیرِ ارشد بسته
    if comms_on != "1":
        caps.discard("comms")
    if not team_on:
        caps.discard("teams")
    feats["direct_kick"] = bool(perms.get("direct_ban", False)) and direct_global == "1"
    return caps, feats


# ─── دروازه‌ی وضعیتِ سیستم ────────────────────────────────────────
GATE_MESSAGES = {
    "repair": ("🔧 ربات در حالت تعمیر است", "مدیر ارشد حالت تعمیر را فعال کرده. پنل تا پایانِ تعمیر بسته است."),
    "update": ("🔄 ربات در حال آپدیت است", "پنل تا پایانِ آپدیت بسته است؛ چند دقیقه‌ی دیگر دوباره امتحان کنید."),
    "danger": ("🔴 وضعیتِ خطرناک", "سیستم در وضعیتِ خطرناک است و تمام عملیاتِ مدیران متوقف شده."),
    "aps": ("🪽 وضعیتِ امنیتیِ APS", "امنیت به سیستمِ APS واگذار شده و دسترسیِ مدیران موقتاً قطع است."),
    "off": ("💤 ربات خاموش است", "ربات توسطِ مدیر ارشد برای مدیران خاموش شده است."),
    "hours": ("🕐 ساعتِ کاری بسته است", "تا بازشدنِ ساعتِ کاری توسطِ مدیر ارشد، پنل در دسترس نیست."),
}


async def gate_reason():
    """None اگر همه‌چیز باز است، وگرنه یکی از کلیدهای GATE_MESSAGES.
    ترتیب دقیقاً مثلِ check_status_gate در helpers.py."""
    repair, update, status, bot_active, hours_on, hours_sys = await asyncio.gather(
        db.get_setting("repair_mode", "0"),
        db.get_setting("bot_update_mode", "0"),
        db.get_setting("system_status", "normal"),
        db.get_setting("bot_active_for_admins", "1"),
        db.get_setting("working_hours_active", "0"),
        db.get_setting("working_hours_system_enabled", "0"),
    )
    if repair == "1":
        return "repair"
    if update == "1":
        return "update"
    if bot_active != "1":
        return "off"
    if hours_on == "0" and hours_sys == "1":
        return "hours"
    if status == "aps":
        return "aps"
    if status == "danger":
        return "danger"
    return None


async def system_status() -> str:
    return await db.get_setting("system_status", "normal")


# ─── «ثبت با عکس»: مستقیم یا با تأییدِ مدیر ارشد؟ ───────────────────
# مدیر ارشد همیشه مستقیم ثبت می‌کند. برای بقیه:
#   ۱) تنظیمِ اختصاصیِ همان مدیر (permissions["scan_mode"] = "direct" | "approval")
#   ۲) وگرنه تنظیمِ کلی (scan_default_mode؛ پیش‌فرض "approval" = با تأیید)
SCAN_MODES = ("direct", "approval")


def scan_mode_override(perms: dict):
    m = perms.get("scan_mode")
    return m if m in SCAN_MODES else None


async def scan_needs_approval(is_pishva: bool, admin) -> bool:
    """True = ثبتِ نهایی فقط «درخواست» می‌سازد و باید مدیر ارشد تأیید کند."""
    if is_pishva:
        return False
    own = scan_mode_override(parse_perms(admin)) if admin else None
    if own:
        return own == "approval"
    return (await db.get_setting("scan_default_mode", "approval")) != "direct"
