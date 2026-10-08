"""
panel_registry.py — فهرست کامل پنل‌ها، منوها و دکمه‌هایی که رهگشا می‌تونه زیر پیامش بازشون کنه
                    + تشخیصِ دقیقِ «منظور کاربر» از اسمِ پنل.

چرا این فایل هست؟
  قبلاً PANEL_MAP فقط ۱۵ پنل داشت و مدل باید حتماً یکی از ۱۴ کلید ثابت رو می‌فرستاد؛ هر پنلی که
  توی اون لیست نبود (مخابرات، وظایف، تقویم، تیم‌ها، تورنمنت‌ها، تنظیماتِ تک‌تک سوییچ‌ها، خلاصه صبحگاهی،
  بکاپ خودکار، ضدفلود، …) اصلاً قابل‌باز‌کردن نبود و مدل مجبور می‌شد حدس بزنه.

  حالا:
   ۱) هر دکمه/منویی که مستقل (بدون «حالتِ قبلی» مثل تأیید/لغو وسط یک فرایند) قابل‌فشردن باشه اینجاست.
   ۲) resolve() اسم/توضیحِ کاربر رو با کلید، برچسب و کلمه‌های هم‌معنی (aliases) تطبیق می‌ده:
        - تطبیقِ قطعی → باز می‌شه
        - مبهم → هیچ‌چیز باز نمی‌شه و گزینه‌های نزدیک برای پرسیدن از کاربر برمی‌گرده (حدس نمی‌زنه)
        - نقشِ کاربر مجاز نیست → خطای دسترسی (نه یک پنلِ دیگه‌ی نزدیک!)

kind:
   "menu"   = صفحه/منو/فهرست (فقط نمایش می‌ده یا مرحله‌ی بعدی رو نشون می‌ده)
   "action" = دکمه‌ای که با لمس‌شدن همان لحظه چیزی رو تغییر می‌ده (سوییچ، شروع/پایان، …). همیشه
              فقط دکمه‌ش آماده می‌شه و خودِ کاربر باید بزنه؛ رهگشا هرگز به‌جای کاربر نمی‌زنه.

نکته‌ی امنیتی: خطِ دفاعیِ دوم همون هندلرِ خودِ دکمه‌ست (مثلاً menu_pishva فقط PISHVA_ID رو راه می‌ده).
"""
import difflib
import re

from config import ROLE_PISHVA, ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER

_P = [ROLE_PISHVA]
_A = [ROLE_PISHVA, ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER]
_NP = [ROLE_TOURNAMENT_MANAGER, ROLE_SECURITY_MANAGER]
_TM = [ROLE_PISHVA, ROLE_TOURNAMENT_MANAGER]


class Entry:
    __slots__ = ("key", "label", "cb", "roles", "aliases", "kind", "group", "needs")

    def __init__(self, key, label, cb, roles, aliases="", kind="menu", group="", needs=None):
        self.key, self.label, self.cb, self.roles = key, label, cb, roles
        self.aliases = [a for a in aliases.split("|") if a.strip()]
        self.kind, self.group, self.needs = kind, group, needs  # needs: None | admin | player | team | class | tournament


def _e(*a, **k):
    return Entry(*a, **k)


# ═════════════════════════════════════════════════════════════════
# فهرست
# ═════════════════════════════════════════════════════════════════
ENTRIES = [
    # ── منوی اصلی و میان‌برها ─────────────────────────────────
    _e("own_main", "🏠 پنل شخصی من", "back_main", _A, "منوی اصلی|صفحه اصلی|پنل اصلی|خانه|پنل من|هوم|منوی شخصی", group="اصلی"),
    _e("pishva_main", "👑 پنل مدیر ارشد", "menu_pishva", _P, "پنل مدیر ارشد|پنل رییس|پنل پیشوا|پنل ارشد|پنل مدیریت ارشد|مدیر ارشد", group="اصلی"),
    _e("pishva_panel_p1", "👑 پنل مدیر ارشد — صفحه ۱", "pishva_panel_p0", _P, "صفحه اول پنل مدیر ارشد|پنل مدیر ارشد صفحه 1", group="اصلی"),
    _e("pishva_panel_p2", "👑 پنل مدیر ارشد — صفحه ۲", "pishva_panel_p1", _P, "صفحه دوم پنل مدیر ارشد|پنل مدیر ارشد صفحه 2", group="اصلی"),
    _e("pishva_panel_p3", "👑 پنل مدیر ارشد — صفحه ۳", "pishva_panel_p2", _P, "صفحه سوم پنل مدیر ارشد|پنل مدیر ارشد صفحه 3", group="اصلی"),
    _e("my_dashboard", "📊 داشبورد من", "dashboard_admin", _NP, "داشبورد من|داشبورد ادمین|داشبورد شخصی", group="اصلی"),
    _e("dashboard", "📊 داشبورد مدیر ارشد", "dashboard_pishva", _P, "داشبورد|داشبورد مدیر ارشد|آمار کلی|داشبورد من", group="اصلی"),
    _e("role_change", "🔄 تغییر نقش", "rolechg_menu", _NP, "تغییر نقش|عوض کردن نقش|عوض کردن سمت", group="اصلی"),
    _e("help_menu", "❓ راهنما", "menu_help", _A, "راهنما|منوی راهنما|کمک|فهرست راهنما|آموزش", group="راهنما"),
    _e("feedback_menu", "💡 انتقادات و پیشنهادات", "menu_feedback", _A, "انتقادات و پیشنهادات|انتقاد|پیشنهاد|فیدبک|بازخورد|نظرات", group="اصلی"),
    _e("calendar", "📅 تقویم", "menu_calendar", _A, "تقویم|کلندر|رویدادها|ایونت|تعطیلات", group="اصلی"),
    _e("chess_live", "♟️ شطرنج زنده", "chess_menu", _A, "شطرنج زنده|بازی شطرنج|شطرنج|بازی آنلاین|چالش شطرنج", group="شطرنج"),
    _e("chess_active", "📋 بازی‌های فعال شطرنج", "chess_active_games", _A, "بازی های فعال|بازیهای فعال شطرنج|بازی های در جریان", group="شطرنج"),
    _e("chess_elo_board", "🏆 جدول Elo شطرنج زنده", "chess_elo_board", _A, "جدول الو شطرنج زنده|الو شطرنج زنده|رتبه شطرنج زنده", group="شطرنج"),
    _e("chess_history", "📜 لیست بازی‌های شطرنج", "chess_history", _A, "لیست بازی ها|تاریخچه بازی های شطرنج|سوابق شطرنج زنده", group="شطرنج"),
    _e("chess_ai", "🤖 بازی با هوش مصنوعی", "chessai_menu", _A, "بازی با هوش مصنوعی|شطرنج با ربات|بازی با ربات|بازی با ای آی", group="شطرنج"),
    _e("ai_open", "🤖 رهگشا (چت جدید)", "ai_assistant_open", _A, "رهگشا|دستیار|دستیار هوشمند|چت با رهگشا", group="دستیار"),

    # ── مسابقات ───────────────────────────────────────────────
    _e("matches", "♟️ مدیریت مسابقات", "menu_matches", _A, "مسابقات|مدیریت مسابقات|منوی مسابقات|بخش مسابقات", group="مسابقات"),
    _e("match_add", "➕ ثبت مسابقه جدید", "match_add", _A, "ثبت مسابقه|مسابقه جدید|ساخت مسابقه|اضافه کردن مسابقه", group="مسابقات"),
    _e("match_result", "🏆 ثبت نتیجه", "match_result", _A, "ثبت نتیجه|نتیجه مسابقه|نتیجه بازی|ثبت برنده", group="مسابقات"),
    _e("match_scan", "📷 ثبت با عکس", "mscan_start", _A, "ثبت با عکس|اسکن مسابقه|عکس مسابقه|اسکن برگه", group="مسابقات"),
    _e("match_history", "🔍 تاریخچه مسابقات", "match_history", _A, "تاریخچه مسابقات|سوابق مسابقات|لیست مسابقات|مسابقات قبلی", group="مسابقات"),
    _e("match_history_today", "📅 مسابقات امروز", "mhist_today", _A, "مسابقات امروز|مسابقه های امروز", group="مسابقات"),
    _e("match_history_week", "📆 مسابقات این هفته", "mhist_week", _A, "مسابقات این هفته|مسابقه های هفته", group="مسابقات"),
    _e("match_history_month", "🗓️ مسابقات این ماه", "mhist_month", _A, "مسابقات این ماه|مسابقه های ماه", group="مسابقات"),
    _e("match_history_all", "📚 کل مسابقات", "mhist_all", _A, "کل مسابقات|همه مسابقات|تمام مسابقات", group="مسابقات"),
    _e("match_history_search", "🔍 جستجوی مسابقه", "mhist_search", _A, "جستجوی مسابقه|پیدا کردن مسابقه", group="مسابقات"),
    _e("match_panel", "📊 پنل مدیریت مسابقات", "match_panel", _A, "پنل مدیریت مسابقات|پنل مسابقات|مدیریت مسابقه ها", group="مسابقات"),
    _e("lottery", "🎲 قرعه‌کشی", "lottery_start", _A, "قرعه کشی|قرعه|بخت آزمایی", group="مسابقات"),
    _e("lottery_all", "🌐 قرعه‌کشی از همه کلاس‌ها", "lottery_all", _A, "قرعه کشی همه کلاس ها|قرعه از همه کلاس ها", group="مسابقات"),
    _e("lottery_class", "🏫 قرعه‌کشی فقط از یک کلاس", "lottery_class", _A, "قرعه کشی یک کلاس|قرعه کشی کلاسی", group="مسابقات"),
    _e("adv_lottery", "🎯 قرعه‌کشی پیشرفته", "adv_lottery_start", _A, "قرعه کشی پیشرفته", group="مسابقات"),
    _e("bracket", "📋 جدول مسابقات", "match_bracket", _A, "جدول مسابقات|براکت|جدول حذفی|جدول", group="مسابقات"),
    _e("elo_board", "📊 جدول Elo", "elo_leaderboard", _A, "جدول الو|الو|رتبه بندی الو|امتیاز الو|لیدربورد", group="مسابقات"),
    _e("elo_info", "❓ سیستم Elo چیست؟", "elo_info", _A, "الو چیست|سیستم الو|توضیح الو", group="مسابقات"),
    _e("elo_history", "📈 تاریخچه Elo بازیکن", "elo_my_history", _A, "تاریخچه الو|نمودار الو|سوابق الو", group="مسابقات"),
    _e("champions", "👑 قهرمانان", "champions", _A, "قهرمانان|قهرمان ها|جدول قهرمانان|برندگان", group="مسابقات"),

    # ── تورنمنت‌ها ────────────────────────────────────────────
    _e("tournaments", "🏅 تورنمنت‌ها", "menu_tournament", _A, "تورنمنت|تورنمنت ها|مدیریت تورنمنت ها|بخش تورنمنت", group="تورنمنت"),
    _e("tournament_add", "➕ افزودن تورنمنت", "tourn_add", _A, "افزودن تورنمنت|ساخت تورنمنت|تورنمنت جدید", group="تورنمنت"),
    _e("tournament_manage", "⚙️ مدیریت تورنمنت", "tourn_manage", _A, "مدیریت تورنمنت|تنظیم تورنمنت", group="تورنمنت"),
    _e("tournament_default", "📌 تورنمنت پیش‌فرض", "tourn_default", _A, "تورنمنت پیش فرض|تورنمنت اصلی", group="تورنمنت"),
    _e("tournament_details", "📊 جزئیات تورنمنت فعال", "tourn_details", _A, "جزئیات تورنمنت|تورنمنت فعال|وضعیت تورنمنت", group="تورنمنت"),
    _e("tournament_deleted", "🗂️ تورنمنت‌های حذف‌شده", "tourn_deleted", _A, "تورنمنت های حذف شده|سطل زباله تورنمنت", group="تورنمنت"),
    _e("tournament_view", "🏅 تورنمنت مشخص", "tourn_select_{id}", _A, "تورنمنت مشخص", group="تورنمنت", needs="tournament"),

    # ── تیم‌ها ────────────────────────────────────────────────
    _e("teams", "🏆 تیم‌ها", "teams_menu", _A, "تیم|تیم ها|منوی تیم ها|مدیریت تیم ها|بخش تیم", group="تیم"),
    _e("teams_list", "📋 فهرست تیم‌ها", "teams_list", _A, "فهرست تیم ها|لیست تیم ها|همه تیم ها|تیم ها لیست", group="تیم"),
    _e("teams_add", "➕ افزودن تیم", "teams_add", _A, "افزودن تیم|ساخت تیم|تیم جدید|ثبت تیم", group="تیم"),
    _e("teams_settings", "⚙️ تنظیمات تیم", "teams_settings", _A, "تنظیمات تیم|تنظیمات تیم ها", group="تیم"),
    _e("team_view", "🏆 تیم مشخص", "team_view_{id}", _A, "تیم مشخص|پنل تیم|پروفایل تیم", group="تیم", needs="team"),
    _e("team_members", "👥 بازیکنان یک تیم", "team_members_{id}", _A, "اعضای تیم|بازیکنان تیم|اعضا تیم", group="تیم", needs="team"),
    _e("team_warnings", "⚠️ اخطارهای یک تیم", "team_warnings_{id}", _A, "اخطار تیم|اخطارهای تیم", group="تیم", needs="team"),

    # ── بازیکنان و کلاس‌ها ────────────────────────────────────
    _e("players", "👤 مدیریت بازیکنان", "menu_players", _A, "بازیکنان|مدیریت بازیکنان|منوی بازیکنان|بخش بازیکنان|دانش آموزان", group="بازیکنان"),
    _e("player_add", "➕ ثبت‌نام بازیکن", "player_add", _A, "ثبت نام بازیکن|ثبت بازیکن|افزودن بازیکن|بازیکن جدید|ثبت نام", group="بازیکنان"),
    _e("bulk_register", "📋 ثبت‌نام گروهی", "bulk_register_start", _A, "ثبت نام گروهی|ثبت دسته جمعی|ثبت نام دسته جمعی|ثبت گروهی", group="بازیکنان"),
    _e("player_list", "👁️ مشاهده بازیکنان", "player_list", _A, "مشاهده بازیکنان|لیست بازیکنان|فهرست بازیکنان|همه بازیکنان", group="بازیکنان"),
    _e("player_search", "🔍 جستجوی بازیکن", "player_search", _A, "جستجوی بازیکن|پیدا کردن بازیکن|سرچ بازیکن", group="بازیکنان"),
    _e("player_continuing", "✅ ادامه‌دهندگان", "player_continuing", _A, "ادامه دهندگان|بازیکنان ادامه دهنده|بازیکنان فعال", group="بازیکنان"),
    _e("player_eliminated", "❌ حذف‌شدگان", "player_eliminated", _A, "حذف شدگان|بازیکنان حذف شده|حذفی ها", group="بازیکنان"),
    _e("player_elim_list", "⛔ شکست‌خورده‌ها", "player_list_elim", _A, "شکست خورده ها|بازنده ها", group="بازیکنان"),
    _e("player_kicked_list", "❌ اخراجی‌ها", "player_list_kicked", _A, "اخراجی ها|بازیکنان اخراج شده|اخراج شده ها", group="بازیکنان"),
    _e("player_elite", "🌟 بازیکنان برتر", "player_elite", _A, "بازیکنان برتر|برتر ها|ستاره ها", group="بازیکنان"),
    _e("player_special", "⚡ نیروهای ویژه", "player_special", _A, "نیروهای ویژه|بازیکنان ویژه|ویژه ها", group="بازیکنان"),
    _e("top5_overall", "🏆 ۵ نفر برتر", "ptop_overall", _A, "5 نفر برتر|پنج نفر برتر|نفرات برتر|نفرات اول|برترین ها", group="بازیکنان"),
    _e("top_classes", "🎓 برترین‌های کلاس‌ها", "ptop_classes", _A, "برترین های کلاس ها|نفرات برتر کلاس ها|برتر هر کلاس", group="بازیکنان"),
    _e("player_view", "👤 پروفایل بازیکن", "player_view_{id}", _A, "پروفایل بازیکن|پنل بازیکن|پرونده بازیکن|بازیکن مشخص", group="بازیکنان", needs="player"),
    _e("class_manage", "🏫 مدیریت کلاس‌ها", "class_manage", _A, "مدیریت کلاس ها|کلاس ها|بخش کلاس|منوی کلاس", group="کلاس"),
    _e("class_list", "👁️ مشاهده کلاس‌ها", "class_list", _A, "مشاهده کلاس ها|لیست کلاس ها|فهرست کلاس ها", group="کلاس"),
    _e("class_add", "✅ ثبت کلاس جدید", "class_add", _P, "ثبت کلاس|کلاس جدید|افزودن کلاس|ساخت کلاس", group="کلاس"),
    _e("class_colors", "🎨 رنگ کلاس‌ها", "cclr_list", _P, "رنگ کلاس ها|رنگ بندی کلاس ها|تغییر رنگ کلاس", group="کلاس"),
    _e("class_view", "🏫 کلاس مشخص", "class_select_{id}", _A, "کلاس مشخص|پنل کلاس", group="کلاس", needs="class"),
    _e("class_players", "👥 بازیکنان یک کلاس", "class_players_{id}", _A, "بازیکنان کلاس|اعضای کلاس|دانش آموزان کلاس", group="کلاس", needs="class"),
    _e("class_perf", "📈 عملکرد یک کلاس", "class_perf_{id}", _A, "عملکرد کلاس|آمار کلاس", group="کلاس", needs="class"),

    # ── مخابرات ───────────────────────────────────────────────
    _e("comms", "📡 مخابرات", "menu_comms", _A, "مخابرات|پیام ها|منوی مخابرات|ارتباطات|بخش مخابرات", group="مخابرات"),
    _e("comms_msg_admin", "💬 پیام به ادمین", "comms_msg_admin", _P, "پیام به ادمین|ارسال پیام به ادمین|پیام دادن به مدیر", group="مخابرات"),
    _e("comms_msg_pishva", "💬 پیام به مدیر ارشد", "comms_msg_pishva", _NP, "پیام به مدیر ارشد|پیام به رییس", group="مخابرات"),
    _e("comms_msg_other", "💬 پیام به ادمین دیگر", "comms_msg_other", _NP, "پیام به ادمین|پیام به همکار", group="مخابرات"),
    _e("comms_announce", "📢 ارسال بیانیه", "comms_announce", _P, "ارسال بیانیه|بیانیه|اطلاعیه|بیانیه جدید|اعلامیه", group="مخابرات"),
    _e("comms_news", "📰 ارسال خبر", "comms_news", _P, "ارسال خبر|خبر جدید|انتشار خبر", group="مخابرات"),
    _e("comms_inbox", "📨 پیام‌های دریافتی", "comms_inbox", _A, "پیام های دریافتی|صندوق ورودی|پیام های رسیده|اینباکس", group="مخابرات"),
    _e("comms_sent", "📤 پیام‌های ارسالی", "comms_sent_history", _A, "پیام های ارسالی|پیام های فرستاده شده|ارسال شده ها", group="مخابرات"),
    _e("comms_all_msgs", "👁️ پیام ادمین‌ها", "comms_all_msgs", _P, "پیام ادمین ها|پیام های مدیران|همه پیام ها", group="مخابرات"),
    _e("comms_notifs", "🔔 اعلانات اخیر", "comms_notifs", _P, "اعلانات اخیر|اعلانات|نوتیفیکیشن ها|اعلان ها", group="مخابرات"),
    _e("comms_reports", "📊 گزارشات", "comms_reports", _P, "گزارشات|گزارش ها|گزارش مخابرات", group="مخابرات"),
    _e("comms_ann_history", "📜 تاریخچه بیانیات", "comms_ann_history", _A, "تاریخچه بیانیات|سوابق بیانیه|بیانیه های قبلی|بیانیات", group="مخابرات"),
    _e("comms_news_list", "📰 اخبار", "comms_news_list", _NP, "اخبار|لیست اخبار|خبرها", group="مخابرات"),

    # ── وظایف ─────────────────────────────────────────────────
    _e("tasks", "📋 وظایف", "menu_tasks", _A, "وظایف|منوی وظایف|تسک ها|کارها|بخش وظایف", group="وظایف"),
    _e("task_assign", "📋 اعطای وظیفه", "task_assign", _P, "اعطای وظیفه|دادن وظیفه|وظیفه جدید|تعریف وظیفه|محول کردن وظیفه", group="وظایف"),
    _e("task_history", "📜 تاریخچه وظایف", "task_history", _P, "تاریخچه وظایف|سوابق وظایف|وظایف قبلی", group="وظایف"),
    _e("task_history_today", "📅 وظایف امروز", "thistory_today", _P, "وظایف امروز", group="وظایف"),
    _e("task_history_week", "📆 وظایف این هفته", "thistory_week", _P, "وظایف این هفته|وظایف هفته", group="وظایف"),
    _e("task_history_done", "✅ وظایف انجام‌شده", "thistory_done", _P, "وظایف انجام شده|کارهای انجام شده", group="وظایف"),
    _e("task_history_pending", "❌ وظایف انجام‌نشده", "thistory_pending", _P, "وظایف انجام نشده|وظایف باز|وظایف معوق", group="وظایف"),
    _e("task_track", "📌 پیگیری وظایف", "task_track", _NP, "پیگیری وظایف|وظایف من|کارهای من", group="وظایف"),

    # ── فیدبک ─────────────────────────────────────────────────
    _e("fb_critique", "📝 ثبت انتقاد", "fb_critique", _NP, "ثبت انتقاد|نوشتن انتقاد|انتقاد کردن", group="فیدبک"),
    _e("fb_suggestion", "💡 ثبت پیشنهاد", "fb_suggestion", _NP, "ثبت پیشنهاد|نوشتن پیشنهاد|پیشنهاد دادن", group="فیدبک"),
    _e("fb_praise", "🏆 ثبت تقدیر", "fb_praise", _NP, "ثبت تقدیر|تقدیر کردن|تشکر", group="فیدبک"),
    _e("fb_feature", "🔧 درخواست قابلیت", "fb_feature", _NP, "درخواست قابلیت|قابلیت جدید|درخواست ویژگی", group="فیدبک"),
    _e("fb_view_critique", "📝 انتقادات ثبت‌شده", "fb_view_critique", _P, "انتقادات ثبت شده|دیدن انتقادات|لیست انتقادات", group="فیدبک"),
    _e("fb_view_suggestion", "💡 پیشنهادات ثبت‌شده", "fb_view_suggestion", _P, "پیشنهادات ثبت شده|دیدن پیشنهادات|لیست پیشنهادات", group="فیدبک"),
    _e("fb_view_praise", "🏆 تقدیرهای ثبت‌شده", "fb_view_praise", _P, "تقدیرها|دیدن تقدیرها|لیست تقدیرها", group="فیدبک"),
    _e("fb_view_feature", "🔧 قابلیت‌های درخواستی", "fb_view_feature", _P, "قابلیت های درخواستی|دیدن قابلیت ها|درخواست های قابلیت", group="فیدبک"),
    _e("fb_view_all", "📋 همه موارد فیدبک", "fb_view_all", _P, "همه فیدبک ها|همه موارد|همه نظرات", group="فیدبک"),

    # ── راهنما ────────────────────────────────────────────────
    _e("help_tournament", "🏅 راهنمای تورنمنت", "help_tournament", _A, "راهنمای تورنمنت", group="راهنما"),
    _e("help_players", "👤 راهنمای بازیکنان", "help_players", _A, "راهنمای بازیکنان", group="راهنما"),
    _e("help_matches", "♟️ راهنمای مسابقات", "help_matches", _A, "راهنمای مسابقات", group="راهنما"),
    _e("help_comms", "📡 راهنمای مخابرات", "help_comms", _A, "راهنمای مخابرات", group="راهنما"),
    _e("help_warnings", "⚠️ راهنمای اخطار", "help_warnings", _A, "راهنمای اخطار", group="راهنما"),
    _e("help_tasks", "📋 راهنمای وظایف", "help_tasks", _A, "راهنمای وظایف", group="راهنما"),
    _e("help_faq", "❓ سوالات متداول", "help_faq", _A, "سوالات متداول|سوالات پرتکرار|اف ای کیو", group="راهنما"),
    _e("help_errors", "🛠️ خطاهای احتمالی", "help_errors", _A, "خطاهای احتمالی|راهنمای خطا|عیب یابی ربات", group="راهنما"),
    _e("htut_quick_start", "🚀 آموزش: شروع سریع", "htut_quick_start_0", _A, "شروع سریع|آموزش شروع سریع", group="آموزش"),
    _e("htut_players", "♟️ آموزش: مدیریت بازیکنان", "htut_players_0", _A, "آموزش بازیکنان|آموزش مدیریت بازیکنان", group="آموزش"),
    _e("htut_matches", "🏆 آموزش: مدیریت مسابقات", "htut_matches_0", _A, "آموزش مسابقات|آموزش مدیریت مسابقات", group="آموزش"),
    _e("htut_tasks", "📋 آموزش: وظایف", "htut_tasks_0", _A, "آموزش وظایف", group="آموزش"),
    _e("htut_teams", "👥 آموزش: مدیریت تیم‌ها", "htut_teams_0", _A, "آموزش تیم ها|آموزش مدیریت تیم ها", group="آموزش"),
    _e("htut_bulk", "📋 آموزش: ثبت‌نام گروهی", "htut_bulk_register_0", _A, "آموزش ثبت نام گروهی", group="آموزش"),
    _e("htut_champions", "🏆 آموزش: آمار، قهرمانان و Elo", "htut_champions_stats_0", _A, "آموزش آمار|آموزش قهرمانان|آموزش الو", group="آموزش"),
    _e("htut_feedback", "💡 آموزش: انتقادات و پیشنهادات", "htut_feedback_0", _A, "آموزش انتقادات|آموزش پیشنهادات", group="آموزش"),
    _e("htut_keywords", "🗣️ آموزش: دستورات کلمه‌ای", "htut_keywords_0", _A, "دستورات کلمه ای|آموزش دستورات کلمه ای|کلمات کلیدی", group="آموزش"),
    _e("htut_automation", "📡 آموزش: اعلانات و یادآورها", "htut_automation_0", _A, "آموزش اعلانات|آموزش یادآورها", group="آموزش"),
    _e("htut_ai", "🤖 آموزش: رهگشا", "htut_ai_assistant_0", _A, "آموزش رهگشا|آموزش دستیار|نحوه استفاده از رهگشا", group="آموزش"),
    _e("htut_troubleshoot", "🆘 آموزش: عیب‌یابی", "htut_troubleshoot_0", _A, "آموزش عیب یابی|مشکل دارم|عیب یابی", group="آموزش"),
    _e("htut_settings", "⚙️ آموزش: تنظیمات", "htut_settings_0", _P, "آموزش تنظیمات", group="آموزش"),
    _e("htut_backup", "💾 آموزش: پشتیبان‌گیری", "htut_backup_0", _P, "آموزش بکاپ|آموزش پشتیبان گیری", group="آموزش"),
    _e("htut_security", "🛡️ آموزش: امنیت و اورژانسی", "htut_security_aps_0", _P, "آموزش امنیت|آموزش APS|آموزش اورژانسی", group="آموزش"),

    # ── مدیران ────────────────────────────────────────────────
    _e("admins_list", "👥 مدیریت مدیران", "menu_admins", _P, "مدیریت مدیران|لیست مدیران|فهرست ادمین ها|مدیران|ادمین ها", group="مدیران"),
    _e("admin_profile", "👤 پروفایل یک مدیر", "admin_view_{id}", _P, "پروفایل ادمین|پنل ادمین|پنل مدیر|پروفایل مدیر", group="مدیران", needs="admin"),
    _e("admin_perms", "⬆️ دسترسی‌های یک مدیر", "admin_perms_{id}", _P, "دسترسی های ادمین|دسترسی های مدیر|پرمیشن ادمین|مجوزهای ادمین", group="مدیران", needs="admin"),
    _e("admin_logs_of", "🔍 اقدامات یک مدیر", "adminlogsmenu_{id}", _P, "اقدامات ادمین|لاگ ادمین|فعالیت های ادمین|سوابق ادمین", group="مدیران", needs="admin"),
    _e("admin_undo_of", "↩️ لغو اقدامات یک مدیر", "admin_undo_menu_{id}", _P, "لغو اقدامات ادمین|برگرداندن اقدامات ادمین|آندو ادمین", group="مدیران", needs="admin"),
    _e("role_pishva_button", "👑 تغییر نام مدیر ارشد", "identity_pishva", _P, "تغییر نام مدیر ارشد|تغییر اسم مدیر ارشد", group="مدیران", kind="action"),
    _e("identity_admin", "👥 تغییر نام مدیران", "identity_admin", _P, "تغییر نام مدیران|تغییر اسم ادمین ها|نام نمایشی مدیران", group="مدیران"),

    # ── پنل مدیر ارشد: صفحه ۱ ─────────────────────────────────
    _e("status", "🚦 مدیریت وضعیت", "pishva_status", _P, "مدیریت وضعیت|وضعیت سیستم|وضعیت ربات|وضعیت امنیتی|نرمال بد خطرناک", group="پنل مدیر ارشد"),
    _e("settings", "⚙️ تنظیمات ربات", "pishva_settings", _P, "تنظیمات|تنظیمات ربات|سوییچ ها|تنظیمات کلی", group="پنل مدیر ارشد"),
    _e("logs", "🔍 پیگیری اقدامات", "pishva_logs", _P, "پیگیری اقدامات|لاگ|لاگ ها|سوابق اقدامات|گزارش اقدامات|تاریخچه اقدامات", group="پنل مدیر ارشد"),
    _e("requests", "📥 درخواست‌های دسترسی", "pishva_requests", _P, "درخواست های دسترسی|درخواست دسترسی|درخواست ها|درخواست های ورود", group="پنل مدیر ارشد"),
    _e("kick_requests", "🚫 درخواست‌های اخراج", "pishva_kick_requests", _P, "درخواست های اخراج|درخواست اخراج", group="پنل مدیر ارشد"),
    _e("chess_games_log", "♟️ بازی‌های مدیران", "pishva_chess_games", _P, "بازی های مدیران|بازی های ادمین ها|شطرنج مدیران", group="پنل مدیر ارشد"),
    _e("scan_requests", "📷 درخواست‌های ثبت با عکس", "pishva_scan_requests", _P, "درخواست های ثبت با عکس|درخواست اسکن|درخواست های اسکن", group="پنل مدیر ارشد"),
    _e("backup", "💾 دریافت بکاپ", "pishva_backup", _P, "بکاپ|دریافت بکاپ|پشتیبان|پشتیبان گیری|فایل بکاپ|نسخه پشتیبان", group="پنل مدیر ارشد"),
    _e("workhours", "🕐 ساعت کاری", "pishva_workhours", _P, "ساعت کاری|ساعات کاری|شروع ساعت کاری|پایان ساعت کاری", group="پنل مدیر ارشد"),
    # ── صفحه ۲ ──
    _e("repair", "🔧 حالت تعمیر", "pishva_repair", _P, "حالت تعمیر|تعمیر|حالت نگهداری|تعمیرات", group="پنل مدیر ارشد"),
    _e("vault", "🏦 خزانه مدیر ارشد", "pishva_vault", _P, "خزانه|خزانه مدیر ارشد|گاوصندوق|رمزها", group="پنل مدیر ارشد"),
    _e("identity", "🪪 تغییر هویت", "pishva_identity", _P, "تغییر هویت|هویت|تغییر نام|تغییر اسم", group="پنل مدیر ارشد"),
    _e("newyear", "🎓 سال تحصیلی جدید", "pishva_newyear", _P, "سال تحصیلی جدید|سال جدید|شروع سال تحصیلی|ریست سال", group="پنل مدیر ارشد"),
    _e("update", "🔄 آپدیت ربات", "pishva_update", _P, "آپدیت ربات|آپدیت|به روزرسانی ربات|بروزرسانی", group="پنل مدیر ارشد"),
    _e("update_announce", "📢 اعلام آپدیت", "update_announce", _P, "اعلام آپدیت|اطلاع رسانی آپدیت|خبر آپدیت", group="پنل مدیر ارشد"),
    _e("update_sleep", "💤 خاموشی موقت برای آپدیت", "update_sleep", _P, "خاموشی موقت|خاموشی برای آپدیت|خواب ربات", group="پنل مدیر ارشد", kind="action"),
    _e("ann_group", "📡 گروه اعلانات", "pishva_group", _P, "گروه اعلانات|تنظیم گروه|گروه ربات", group="پنل مدیر ارشد"),
    _e("ann_channel", "🆔 تنظیم کانال اعلانات", "pishva_channel", _P, "کانال اعلانات|تنظیم کانال|کانال ربات", group="پنل مدیر ارشد"),
    _e("broadcast", "📡 پخش خودکار", "pishva_broadcast", _P, "پخش خودکار|برودکست|پخش اعلان خودکار|پخش اعلانات", group="پنل مدیر ارشد"),
    _e("chess_ai_broadcast_text", "✏️ ویرایش متن اعلان شطرنج با هوش مصنوعی", "pishva_chess_ai_broadcast_text", _P, "ویرایش متن اعلان|متن اعلان شطرنج", group="پنل مدیر ارشد"),
    # ── صفحه ۳ ──
    _e("security_panel", "🛡️ پنل امنیتی APS", "security_panel", _P, "پنل امنیتی|امنیت|پنل امنیتی aps|اپس|aps|امنیتی", group="امنیت"),
    _e("security_queue", "⏳ صف انتظار", "security_queue", _P, "صف انتظار|صف|در انتظار تایید", group="امنیت"),
    _e("security_blocked", "🚫 بلاک‌شده‌ها", "security_blocked", _P, "بلاک شده ها|لیست بلاک|مسدود شده ها|بلاک ها", group="امنیت"),
    _e("flood_menu", "🌊 ضدِ فلود", "security_flood_menu", _P, "ضد فلود|فلود|صف خودکار|جلوگیری از اسپم", group="امنیت"),
    _e("flood_toggle", "🔄 روشن/خاموش ضدِ فلود", "flood_toggle", _P, "روشن کردن ضد فلود|خاموش کردن ضد فلود|سوییچ ضد فلود", group="امنیت", kind="action"),
    _e("flood_reset", "♻️ ضدِ فلود: پیش‌فرض", "flood_reset", _P, "ریست ضد فلود|پیش فرض ضد فلود", group="امنیت", kind="action"),
    _e("sadel_panel", "🚨 هشدار حذف مشکوک", "sadel_panel", _P, "هشدار حذف مشکوک|حذف مشکوک|هشدار حذف|تنظیمات حذف مشکوک", group="امنیت"),
    _e("sadel_toggle", "🚨 روشن/خاموش هشدار حذف مشکوک", "sadel_toggle", _P, "روشن کردن هشدار حذف مشکوک|خاموش کردن هشدار حذف مشکوک", group="امنیت", kind="action"),
    _e("sadel_threshold", "🔢 آستانه‌ی حذف مشکوک", "sadel_threshold_menu", _P, "آستانه حذف مشکوک|آستانه هشدار", group="امنیت"),
    _e("sadel_window", "⏱️ بازه‌ی حذف مشکوک", "sadel_window_menu", _P, "بازه حذف مشکوک|بازه زمانی هشدار", group="امنیت"),
    _e("sadel_admins", "👤 حذف مشکوک: تنظیم هر ادمین", "sadel_admins_p0", _P, "تنظیم جداگانه ادمین حذف مشکوک|حذف مشکوک هر ادمین", group="امنیت"),
    _e("sadel_auto_toggle", "🤖 تصمیم‌گیری خودکار حذف مشکوک", "sadel_auto_toggle", _P, "تصمیم گیری خودکار|حذف مشکوک خودکار", group="امنیت", kind="action"),
    _e("sadel_auto_action", "⚙️ اقدام خودکار حذف مشکوک", "sadel_auto_action_menu", _P, "اقدام خودکار|اقدام خودکار حذف مشکوک", group="امنیت"),
    _e("ai_manage", "🧑‍💻 مدیریت دستیار", "ai_manage_menu_main", _P, "مدیریت دستیار|مدیریت رهگشا|تنظیمات دستیار|پنل دستیار", group="دستیار"),
    _e("ai_perms", "🛠️ اختیارات دستیار", "ai_perms_menu", _P, "اختیارات دستیار|دسترسی های دستیار|اختیارات رهگشا|ابزارهای دستیار", group="دستیار"),
    _e("ai_toggle_online", "🔌 روشن/خاموش هوش مصنوعی", "ai_manage_toggle_online", _P, "خاموش کردن هوش مصنوعی|روشن کردن هوش مصنوعی|آنلاین دستیار", group="دستیار", kind="action"),
    _e("ai_admin_logs", "🗂️ سوابق چت‌های دستیار", "ai_admlog_menu", _P, "سوابق ای آی ادمین ها|سوابق چت دستیار|سوابق رهگشا|چت های ادمین ها با رهگشا", group="دستیار"),
    _e("ai_admin_toggle", "🔕 خاموشی دستیار برای ادمین خاص", "ai_admtg_menu", _P, "خاموشی برای ادمین|قطع رهگشا برای ادمین|مسدود کردن رهگشا ادمین", group="دستیار"),
    _e("ai_history", "🕘 تاریخچه چت‌های من", "ai_hist_list", _A, "تاریخچه چت ها|چت های قبلی من|تاریخچه رهگشا", group="دستیار"),
    _e("ai_new_chat", "🆕 شروع چت جدید با رهگشا", "ai_new_start", _A, "چت جدید|شروع چت جدید|گفتگوی جدید", group="دستیار"),
    _e("ai_scheduled", "🤖 کارهای دستیار", "pishva_ai_scheduled", _P, "کارهای دستیار|کارهای زمان بندی شده|یادآورهای دستیار|کارهای رهگشا", group="دستیار"),
    _e("reminders", "⏰ یادآورها", "pishva_reminders", _P, "یادآورها|یادآور|ریمایندر|یادآوری ها", group="پنل مدیر ارشد"),
    _e("brief", "🌅 خلاصه صبحگاهی", "pishva_brief", _P, "خلاصه صبحگاهی|خلاصه صبح|منوی خلاصه صبحگاهی|تنظیمات خلاصه صبحگاهی|گزارش صبحگاهی|خلاصه روز", group="خلاصه صبحگاهی"),
    _e("brief_rules", "📜 قانون‌های رهگشا", "mb_rules", _P, "قانون های خلاصه صبحگاهی|قانون های رهگشا|نکته های خلاصه صبحگاهی", group="خلاصه صبحگاهی"),
    _e("brief_toggle", "🟢/🔴 روشن/خاموش خلاصه صبحگاهی", "mb_toggle", _P, "روشن کردن خلاصه صبحگاهی|خاموش کردن خلاصه صبحگاهی", group="خلاصه صبحگاهی", kind="action"),
    _e("brief_add_time", "➕ افزودن ساعت خلاصه صبحگاهی", "mb_add", _P, "افزودن ساعت خلاصه|ساعت جدید خلاصه صبحگاهی|اضافه کردن ساعت خلاصه", group="خلاصه صبحگاهی"),
    _e("brief_test", "📨 ارسال آزمایشی خلاصه صبحگاهی", "mb_test", _P, "ارسال آزمایشی|تست خلاصه صبحگاهی|آزمایشی خلاصه", group="خلاصه صبحگاهی", kind="action"),
    _e("brief_day", "✨ روز من رو خلاصه کن", "mb_day", _P, "روز من رو خلاصه کن|خلاصه روز من|خلاصه کامل روز", group="خلاصه صبحگاهی", kind="action"),
    _e("brief_alt_a", "📚 این هفته: سطر بالا (دینی)", "mb_alt_a", _P, "این هفته سطر بالا|هفته دینی|سطر بالا دینی", group="خلاصه صبحگاهی", kind="action"),
    _e("brief_alt_b", "📚 این هفته: سطر پایین (بیکار)", "mb_alt_b", _P, "این هفته سطر پایین|هفته بیکار|سطر پایین بیکار", group="خلاصه صبحگاهی", kind="action"),
    _e("dbstatus", "🗄️ وضعیت دیتابیس", "pishva_dbstatus", _P, "وضعیت دیتابیس|دیتابیس|پایگاه داده|اتصال دیتابیس", group="پنل مدیر ارشد"),
    _e("auto_backup", "🔄 تنظیمات بکاپ خودکار", "pishva_auto_backup", _P, "بکاپ خودکار|تنظیمات بکاپ خودکار|پشتیبان خودکار", group="بکاپ"),
    _e("auto_backup_toggle", "🔄 روشن/خاموش بکاپ خودکار", "abk_toggle", _P, "روشن کردن بکاپ خودکار|خاموش کردن بکاپ خودکار", group="بکاپ", kind="action"),
    _e("auto_backup_interval", "⏰ فاصله‌ی بکاپ خودکار", "abk_interval", _P, "فاصله بکاپ خودکار|هر چند ساعت بکاپ", group="بکاپ"),
    _e("auto_backup_fmt", "📁 فرمت بکاپ خودکار", "abk_fmt", _P, "فرمت بکاپ خودکار|اکسل یا ورد بکاپ", group="بکاپ", kind="action"),
    _e("auto_backup_period", "📊 بازه‌ی بکاپ خودکار", "abk_period", _P, "بازه بکاپ خودکار|بازه زمانی بکاپ", group="بکاپ", kind="action"),
    _e("restore", "📥 بازگردانی از فایل", "pishva_restore", _P, "بازگردانی|بازگردانی از فایل|ریستور|بازیابی بکاپ", group="بکاپ"),
    _e("backup_today", "📅 بکاپ امروز", "backup_period_today", _P, "بکاپ امروز", group="بکاپ"),
    _e("backup_week", "📆 بکاپ این هفته", "backup_period_week", _P, "بکاپ این هفته", group="بکاپ"),
    _e("backup_month", "🗓️ بکاپ این ماه", "backup_period_month", _P, "بکاپ این ماه", group="بکاپ"),
    _e("backup_all", "📚 بکاپ از ابتدا", "backup_period_all", _P, "بکاپ کامل|بکاپ از ابتدا|بکاپ همه", group="بکاپ"),
    _e("backup_excel", "📊 بکاپ Excel", "backup_fmt_excel", _P, "بکاپ اکسل|بکاپ excel", group="بکاپ"),
    _e("backup_word", "📄 بکاپ Word", "backup_fmt_word", _P, "بکاپ ورد|بکاپ word", group="بکاپ"),

    # ── ساعت کاری / تعمیر / وضعیت ─────────────────────────────
    _e("wh_start", "🟢 آغاز ساعت کاری", "wh_start", _P, "آغاز ساعت کاری|شروع ساعت کاری|باز کردن ساعت کاری", group="ساعت کاری", kind="action"),
    _e("wh_end", "🔴 پایان ساعت کاری", "wh_end", _P, "پایان ساعت کاری|بستن ساعت کاری|اتمام ساعت کاری", group="ساعت کاری", kind="action"),
    _e("wh_autoend", "⏱ پایان خودکار ساعت کاری", "wh_autoend_toggle", _P, "پایان خودکار ساعت کاری", group="ساعت کاری", kind="action"),
    _e("wh_reminder", "⏰ یادآور عدم پایان ساعت کاری", "wh_reminder_toggle", _P, "یادآور عدم پایان|یادآور ساعت کاری", group="ساعت کاری", kind="action"),
    _e("wh_reminder_minutes", "✏️ دقیقه‌ی یادآور ساعت کاری", "wh_reminder_set_minutes", _P, "دقیقه یادآور|دقیقه یادآور ساعت کاری", group="ساعت کاری"),
    _e("repair_on", "🔧 فعال‌سازی تعمیر", "repair_on", _P, "فعال کردن حالت تعمیر|روشن کردن تعمیر", group="تعمیر", kind="action"),
    _e("repair_off", "✅ غیرفعال‌سازی تعمیر", "repair_off", _P, "غیرفعال کردن حالت تعمیر|خاموش کردن تعمیر", group="تعمیر", kind="action"),
    _e("repair_reason", "📝 ثبت دلیل تعمیر", "repair_reason", _P, "دلیل تعمیر|ثبت دلیل تعمیر", group="تعمیر"),
    _e("status_normal", "🟢 وضعیت نرمال", "set_status_normal", _P, "وضعیت نرمال|برگرداندن وضعیت به نرمال", group="وضعیت", kind="action"),
    _e("status_bad", "🟡 وضعیت بد", "set_status_bad", _P, "وضعیت بد|وضعیت زرد", group="وضعیت", kind="action"),
    _e("status_danger", "🔴 وضعیت خطرناک", "set_status_danger", _P, "وضعیت خطرناک|وضعیت قرمز", group="وضعیت", kind="action"),
    _e("status_aps", "🪽 وضعیت APS", "set_status_aps", _P, "وضعیت aps|وضعیت اپس|وضعیت اورژانسی", group="وضعیت", kind="action"),
    _e("dbstatus_on", "🗄️ دیتابیس: روشن", "dbstatus_on", _P, "روشن کردن وضعیت دیتابیس", group="وضعیت", kind="action"),
    _e("dbstatus_off", "🗄️ دیتابیس: خاموش", "dbstatus_off", _P, "خاموش کردن وضعیت دیتابیس", group="وضعیت", kind="action"),

    # ── سوییچ‌های تنظیمات ربات (همه action) ────────────────────
    _e("set_notifications", "🔔 سوییچ اعلانات", "setting_notifications", _P, "سوییچ اعلانات|روشن کردن اعلانات|خاموش کردن اعلانات", group="سوییچ", kind="action"),
    _e("set_communications", "📡 سوییچ مخابرات", "setting_communications", _P, "سوییچ مخابرات|روشن کردن مخابرات|خاموش کردن مخابرات", group="سوییچ", kind="action"),
    _e("set_help", "❓ سوییچ راهنما", "setting_help", _P, "سوییچ راهنما|روشن کردن راهنما|خاموش کردن راهنما", group="سوییچ", kind="action"),
    _e("set_ai_online", "🤖 سوییچ هوش مصنوعی", "setting_ai_online", _P, "سوییچ هوش مصنوعی|روشن کردن ای آی|خاموش کردن ای آی", group="سوییچ", kind="action"),
    _e("set_match_reg", "♟️ سوییچ ثبت مسابقه", "setting_match_reg", _P, "سوییچ ثبت مسابقه|روشن کردن ثبت مسابقه|خاموش کردن ثبت مسابقه", group="سوییچ", kind="action"),
    _e("set_scan_enabled", "📷 سوییچ ثبت با عکس", "setting_scan_enabled", _P, "سوییچ ثبت با عکس|روشن کردن ثبت با عکس|خاموش کردن ثبت با عکس", group="سوییچ", kind="action"),
    _e("set_scan_mode", "📷 حالت ثبت با عکس", "setting_scan_mode", _P, "حالت ثبت با عکس|حالت ثبت مستقیم|حالت ثبت با تایید", group="سوییچ", kind="action"),
    _e("set_live_chess", "♟️ سوییچ شطرنج زنده", "setting_live_chess", _P, "سوییچ شطرنج زنده|روشن کردن شطرنج زنده|خاموش کردن شطرنج زنده", group="سوییچ", kind="action"),
    _e("set_hub", "🖥️ سوییچ پنل من (Hub)", "setting_hub", _P, "سوییچ هاب|پنل من هاب|روشن کردن هاب|خاموش کردن هاب", group="سوییچ", kind="action"),
    _e("set_team_mode", "🏆 سوییچ حالت تیمی", "setting_team_mode", _P, "سوییچ حالت تیمی|حالت تیمی|روشن کردن حالت تیمی", group="سوییچ", kind="action"),
    _e("set_team_reg", "📝 سوییچ ثبت‌نام با تیم", "setting_team_reg", _P, "ثبت نام با تیم|سوییچ ثبت نام با تیم", group="سوییچ", kind="action"),
    _e("set_mgr_team", "👤 سوییچ ساخت تیم توسط مدیر", "setting_mgr_team", _P, "ساخت تیم توسط مدیر|سوییچ ساخت تیم", group="سوییچ", kind="action"),
    _e("set_top_players_mode", "🏆 حالت نفرات برتر", "setting_top_players_mode", _P, "حالت نفرات برتر|دستی یا خودکار نفرات برتر", group="سوییچ", kind="action"),
    _e("set_admin_login", "🚪 سوییچ ورود ادمین", "setting_admin_login", _P, "سوییچ ورود ادمین|ورود ادمین|روشن کردن ورود ادمین|خاموش کردن ورود ادمین", group="سوییچ", kind="action"),
    _e("set_bot_active", "💤 سوییچ ربات برای ادمین‌ها", "setting_bot_active", _P, "ربات برای ادمین ها|سوییچ ربات برای ادمین ها|فعال بودن ربات برای ادمین", group="سوییچ", kind="action"),
    _e("set_admin_dashboard", "📊 سوییچ داشبورد ادمین‌ها", "setting_admin_dashboard", _P, "داشبورد ادمین ها|سوییچ داشبورد ادمین ها", group="سوییچ", kind="action"),
    _e("set_admin_direct_kick", "🚫 سوییچ اخراج مستقیم", "setting_admin_direct_kick", _P, "اخراج مستقیم|سوییچ اخراج مستقیم", group="سوییچ", kind="action"),
    _e("set_principal_panel", "🏫 سوییچ پنل مدیر مدرسه", "setting_principal_panel", _P, "سوییچ پنل مدیر مدرسه|روشن کردن پنل مدیر مدرسه|خاموش کردن پنل مدیر مدرسه", group="سوییچ", kind="action"),
    _e("set_admin_webpanel", "🌐 سوییچ پنل وب ادمین", "setting_admin_webpanel", _P, "پنل وب ادمین|سوییچ پنل وب|روشن کردن پنل وب|خاموش کردن پنل وب", group="سوییچ", kind="action"),
    _e("set_bug_report", "🚨 سوییچ گزارش باگ", "setting_bug_report", _P, "گزارش باگ|سوییچ گزارش باگ|روشن کردن گزارش باگ", group="سوییچ", kind="action"),

    # ── بازی‌های مدیران / لاگ‌ها (فیلتر زمان) ─────────────────
    _e("logs_today", "📅 اقدامات امروز", "logs_today", _P, "اقدامات امروز|لاگ امروز|لاگ های امروز|گزارش امروز", group="لاگ"),
    _e("logs_week", "📆 اقدامات این هفته", "logs_week", _P, "اقدامات این هفته|لاگ این هفته|لاگ هفته", group="لاگ"),
    _e("logs_month", "🗓️ اقدامات این ماه", "logs_month", _P, "اقدامات این ماه|لاگ این ماه|لاگ ماه", group="لاگ"),
    _e("logs_all", "📚 اقدامات کل تاریخ", "logs_all", _P, "اقدامات کل تاریخ|همه لاگ ها|کل لاگ ها", group="لاگ"),
    _e("logs_search", "🔍 جستجو در اقدامات", "logs_search", _P, "جستجو در اقدامات|جستجو در لاگ|سرچ لاگ", group="لاگ"),
    _e("chessgames_today", "📅 بازی‌های مدیران امروز", "chessgames_today", _P, "بازی های مدیران امروز", group="شطرنج"),
    _e("chessgames_week", "📆 بازی‌های مدیران این هفته", "chessgames_week", _P, "بازی های مدیران این هفته", group="شطرنج"),
    _e("chessgames_month", "🗓️ بازی‌های مدیران این ماه", "chessgames_month", _P, "بازی های مدیران این ماه", group="شطرنج"),
    _e("chessgames_all", "📚 کل بازی‌های مدیران", "chessgames_all", _P, "کل بازی های مدیران|همه بازی های مدیران", group="شطرنج"),

    # ── تقویم ────────────────────────────────────────────────
    _e("cal_event", "🟢 ثبت ایونت در تقویم", "calset_event", _P, "ثبت ایونت|ثبت رویداد|رویداد جدید", group="تقویم"),
    _e("cal_holiday", "🔴 ثبت تعطیلی در تقویم", "calset_holiday", _P, "ثبت تعطیلی|تعطیلی جدید", group="تقویم"),

    # ── یادآورها ─────────────────────────────────────────────
    _e("reminder_master", "⏰ سوییچ اصلی یادآورها", "reminder_toggle_master", _P, "سوییچ یادآورها|روشن کردن یادآورها|خاموش کردن یادآورها", group="یادآور", kind="action"),
]

# پنل‌هایی که قبلاً با این کلیدها کار می‌کردن؛ برای سازگاری با گفتگوها/کارهای زمان‌بندی‌شده‌ی قبلی
LEGACY_KEYS = {
    "dashboard": "dashboard", "matches": "matches", "players": "players",
    "ai_admin_logs": "ai_admin_logs", "admins_list": "admins_list", "admin_profile": "admin_profile",
}

BY_KEY = {e.key: e for e in ENTRIES}
assert len(BY_KEY) == len(ENTRIES), "کلید تکراری در panel_registry"

# ═════════════════════════════════════════════════════════════════
# نرمال‌سازی و امتیازدهی
# ═════════════════════════════════════════════════════════════════
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_STOP = {
    "رو", "را", "ره", "برام", "برای", "من", "ما", "لطفا", "لطفاً", "یه", "یک", "ی", "اون", "این", "همون",
    "باز", "کن", "کنی", "کنید", "بکن", "بزن", "بیار", "نشون", "بده", "بدی", "بدید", "نمایش", "برو", "ببر",
    "پنل", "منو", "منوی", "بخش", "صفحه", "صفحهی", "قسمت", "دکمه", "دکمهی", "گزینه", "به", "از", "در", "با", "و", "تو", "توی",
    "میخوام", "میخواهم", "میشه", "بتونی", "بتوانی", "ببینم", "ببینیم", "هست", "است", "الان", "همین", "حالا",
}


_CSTOP = {"پنل", "منو", "منوی", "بخش", "صفحه", "قسمت", "دکمه"}


def norm(text: str) -> str:
    t = (text or "").translate(_DIGITS).lower()
    t = t.replace("ي", "ی").replace("ك", "ک").replace("ۀ", "ه").replace("ة", "ه")
    t = t.replace("\u200c", " ").replace("_", " ").replace("-", " ")
    t = re.sub(r"[\u064b-\u065f\u0640]", "", t)               # اعراب و کشیده
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)           # ایموجی/نشانه‌ها
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _tokens(text: str, drop_stop=True):
    toks = norm(text).split()
    if drop_stop:
        toks = [x for x in toks if x not in _STOP]
    return toks


def _cands(e: Entry):
    out = {norm(e.key), norm(e.label)}
    out.update(norm(a) for a in e.aliases)
    return {c for c in out if c}


def _score(q_full: str, q_toks, e: Entry) -> float:
    best = 0.0
    qset = set(q_toks)
    for c in _cands(e):
        c_toks = [x for x in c.split() if x not in _CSTOP] or c.split()
        cset = set(c_toks)
        if not cset:
            continue
        if c == q_full:
            best = max(best, 103.0)
            continue
        if c_toks == q_toks:
            best = max(best, 100.0)
            continue
        # همه‌ی کلمه‌های کاندید در درخواست آمده → مطمئن (هرچه کاندید طولانی‌تر، مطمئن‌تر)
        if cset <= qset:
            cov = len(cset) / max(len(qset), 1)
            best = max(best, 70 + 18 * cov + min(len(cset), 4) * 2)
            continue
        # همه‌ی کلمه‌های درخواست در کاندید آمده (درخواست مختصرتر)
        if qset and qset <= cset:
            cov = len(qset) / len(cset)
            best = max(best, 55 + 25 * cov)
            continue
        inter = len(qset & cset)
        if inter:
            jac = inter / len(qset | cset)
            best = max(best, 20 + 40 * jac)
        # غلط املایی/نیم‌فاصله‌ی متفاوت
        r = difflib.SequenceMatcher(None, "".join(q_toks), "".join(c_toks)).ratio()
        if r >= 0.8:
            best = max(best, 40 + 40 * (r - 0.8) / 0.2)
    return best


class Resolution:
    """status: ok | ambiguous | forbidden | unknown"""
    def __init__(self, status, entry=None, options=None, note=""):
        self.status, self.entry, self.options, self.note = status, entry, options or [], note


def resolve(text: str, role: str) -> Resolution:
    """متنِ کاربر/کلید → یک ورودی؛ مبهم = هیچ‌چیز باز نشه."""
    raw = (text or "").strip()
    if not raw:
        return Resolution("unknown", note="نام پنل خالیه.")
    # کلید دقیق (قدیمی یا جدید)
    k = BY_KEY.get(raw) or BY_KEY.get(raw.lower())
    if k is not None:
        if role in k.roles:
            return Resolution("ok", k)
        return Resolution("forbidden", k)
    q_full = norm(raw)
    q_toks = _tokens(raw)
    if not q_toks:
        q_toks = q_full.split()
    scored = sorted(((_score(q_full, q_toks, e), e) for e in ENTRIES), key=lambda x: -x[0])
    scored = [(s, e) for s, e in scored if s >= 40]
    if not scored:
        return Resolution("unknown", note=f"پنلی شبیه «{raw}» پیدا نشد.")

    allowed = [(s, e) for s, e in scored if role in e.roles]
    top_all = scored[0]
    if not allowed or (top_all[0] - allowed[0][0] >= 15 and top_all[0] >= 80):
        # بهترین تطبیقِ قطعی مالِ نقشِ دیگه‌ست؛ پنلِ «نزدیک» رو جایگزینش نکن
        return Resolution("forbidden", top_all[1])

    top = allowed[0]
    # ورودی‌های هم‌ارزِ همین نقش (مثلاً دو دکمه با یک callback) یکی حساب می‌شن
    margin = 0 if top[0] >= 103 else 12
    rivals = [(s, e) for s, e in allowed[1:] if e.cb != top[1].cb and s >= top[0] - margin]
    if top[0] >= 95 and not rivals:
        return Resolution("ok", top[1])
    if top[0] >= 72 and not rivals:
        return Resolution("ok", top[1])
    opts = [top[1]] + [e for _, e in rivals]
    if len(opts) == 1:
        opts += [e for _, e in allowed[1:4] if e.cb != top[1].cb]
    return Resolution("ambiguous", options=opts[:5])


def catalog_for_prompt(role: str, with_actions: bool = False) -> str:
    """فهرست فشرده‌ی کلیدها (به‌تفکیک گروه) برای توضیح ابزار؛ فقط ورودی‌های مجاز برای نقش."""
    groups = {}
    for e in ENTRIES:
        if role in e.roles and (with_actions or e.kind == "menu"):
            groups.setdefault(e.group or "سایر", []).append(e.key)
    return "؛ ".join(f"{g}: {', '.join(keys)}" for g, keys in groups.items())


def all_keys() -> list:
    return [e.key for e in ENTRIES]


def catalog_all() -> str:
    """فهرست فشرده‌ی همه‌ی کلیدها به‌تفکیک گروه (برای توضیح ابزار؛ نقش‌ها بعداً در سرور چک می‌شن)."""
    groups = {}
    for e in ENTRIES:
        groups.setdefault(e.group or "سایر", []).append(e.key)
    return "؛ ".join(f"{g}: {', '.join(keys)}" for g, keys in groups.items())
