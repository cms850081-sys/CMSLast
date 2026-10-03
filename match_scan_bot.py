"""
match_scan_bot.py — «ثبت نتیجه با عکسِ برگه» از داخلِ دکمه‌های ربات (مدیریت مسابقات ← 📷 ثبت با عکس).

جریان (مدیر ارشد و مدیر مسابقات):
  ۱) ارسالِ عکسِ برگه  ۲) خواندنِ خودکار  ۳) پیش‌نمایشِ ردیف‌ها (هر ردیف قابلِ ویرایش/رد)
  ۴) تغییرِ تاریخ/تورنمنت  ۵) تأییدِ نهایی یا لغو.

تفاوتِ نقش‌ها:
  • مدیر ارشد: «ثبتِ نهایی» مستقیم مسابقه‌ها را می‌سازد.
  • مدیر مسابقات: «ثبتِ نهایی» فقط یک درخواست برای مدیر ارشد می‌سازد (از ربات یا از هاب قابلِ تأیید/رد).
    تا تأیید نشود هیچ مسابقه‌ای ثبت نمی‌شود؛ با رد، کلاً کنار گذاشته می‌شود.

منطقِ ثبت/تأیید در match_scan_service.py است (مشترک با هاب).
"""

import html
import json
import logging

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup, Update
from telegram.ext import ContextTypes, ConversationHandler

import database as db
import hub_caps
import match_scan_service as svc
from config import PISHVA_ID, ST_SCAN_PHOTO, ST_SCAN_REVIEW, ST_SCAN_SEARCH, ST_SCAN_DATE
from helpers import (check_status_gate, date_label_fa, days_ago_gregorian, now_shamsi,
                     parse_admin_undo_date, safe_edit_message_text, today_gregorian)

logger = logging.getLogger(__name__)

ROWS_PER_PAGE = 5
RES_LABEL = {"white": "برد سفید", "black": "برد سیاه", "draw": "تساوی", None: "بدون نتیجه"}
_E = html.escape


# ───────────────────────── دسترسی ─────────────────────────
async def _can_scan(uid: int) -> bool:
    if uid == PISHVA_ID:
        return True
    admin = await db.get_admin(uid)
    if not admin or not admin["is_active"]:
        return False
    caps, _feats = await hub_caps.compute_caps(False, admin)
    return "match_scan" in caps


async def _needs_approval(uid: int) -> bool:
    """True = ثبتِ نهایی فقط درخواست می‌سازد. مدیر ارشد همیشه مستقیم؛ بقیه طبقِ تنظیمِ همان مدیر/تنظیمِ کلی."""
    if uid == PISHVA_ID:
        return False
    return await hub_caps.scan_needs_approval(False, await db.get_admin(uid))


async def _admin_name(uid: int) -> str:
    if uid == PISHVA_ID:
        return "مدیر ارشد"
    a = await db.get_admin(uid)
    return (a["display_name"] or a["full_name"] or str(uid)) if a else str(uid)


# ───────────────────────── حالتِ جلسه ─────────────────────────
def _sc(ctx):
    return ctx.user_data.get("scan")


def _ready(r) -> bool:
    return bool(r["on"] and r["w"] and r["b"] and r["w"]["id"] != r["b"]["id"])


def _icon(r) -> str:
    if not r["on"]:
        return "🚫"
    if not r["w"] or not r["b"]:
        return "⚠️"
    if r["w"]["id"] == r["b"]["id"]:
        return "⛔"
    return "🔁" if r["dup"] else "✅"


def _name(side, raw):
    return _E(side["name"]) if side else f"❓ {_E(raw)}" if raw else "❓ نامشخص"


def _row_block(k, r) -> str:
    lines = [f"{_icon(r)} <b>{k + 1}.</b> ⬜ {_name(r['w'], r['w_raw'])}  ⚔️  ⬛ {_name(r['b'], r['b_raw'])}"]
    res = f"🏆 {RES_LABEL[r['res']]}"
    if r["raw_res"]:
        res += f"  (روی برگه: {_E(r['raw_res'])})"
    lines.append("      " + res)
    if r["dup"]:
        lines.append("      🔁 امروز با همین رنگ ثبت شده (احتمالاً تکراری)")
    if r["w"] and r["b"] and r["w"]["id"] == r["b"]["id"]:
        lines.append("      ⛔ سفید و سیاه یک نفر است")
    if r["note"]:
        lines.append(f"      ℹ️ {_E(r['note'])}")
    return "\n".join(lines)


def _pages(sc) -> int:
    return max(1, (len(sc["items"]) + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)


def _header(sc) -> str:
    n_ready = sum(1 for r in sc["items"] if _ready(r))
    h = (f"📷 <b>بازبینیِ نتایجِ برگه</b>\n"
         f"خوانده‌شده: {len(sc['items'])} • آماده‌ی ثبت: {n_ready}\n"
         f"📅 {_E(date_label_fa(sc['date']))}   🏅 {_E(sc['tname'])}")
    if sc.get("date_text"):
        h += f"\nتاریخِ روی برگه: {_E(sc['date_text'])}"
    if sc.get("sheet_note"):
        h += f"\nℹ️ {_E(sc['sheet_note'])}"
    return h


def _review_view(sc, direct: bool):
    page = min(max(sc.get("page", 0), 0), _pages(sc) - 1)
    sc["page"] = page
    lo = page * ROWS_PER_PAGE
    chunk = list(enumerate(sc["items"]))[lo:lo + ROWS_PER_PAGE]
    text = _header(sc) + "\n\n" + "\n\n".join(_row_block(k, r) for k, r in chunk)
    text += "\n\n✅ آماده  ⚠️ نیازمندِ انتخابِ بازیکن  🔁 تکراری  🚫 ردشده"
    rows = []
    for k, r in chunk:
        rows.append([Btn(f"✏️ ویرایش {k + 1}", callback_data=f"mscan_e_{k}", style="primary"),
                     Btn(f"↩️ بازگردانی {k + 1}" if not r["on"] else f"🚫 رد {k + 1}",
                         callback_data=f"mscan_x_{k}", style="danger" if r["on"] else "success")])
    if _pages(sc) > 1:
        nav = []
        if page > 0:
            nav.append(Btn("◀️ قبلی", callback_data=f"mscan_p_{page - 1}", style="primary"))
        nav.append(Btn(f"صفحه {page + 1}/{_pages(sc)}", callback_data="noop_label", style="primary"))
        if page < _pages(sc) - 1:
            nav.append(Btn("بعدی ▶️", callback_data=f"mscan_p_{page + 1}", style="primary"))
        rows.append(nav)
    rows.append([Btn("📅 تغییر تاریخ", callback_data="mscan_date", style="primary"),
                 Btn("🏅 تورنمنت", callback_data="mscan_tour", style="primary")])
    n_ready = sum(1 for r in sc["items"] if _ready(r))
    if n_ready:
        label = f"✅ ثبتِ نهایی ({n_ready})" if direct else f"📤 ارسال برای تأییدِ مدیر ارشد ({n_ready})"
        rows.append([Btn(label, callback_data="mscan_go", style="success")])
    rows.append([Btn("❌ لغو", callback_data="mscan_cancel", style="danger")])
    return text, Markup(rows)


def _edit_view(sc, k):
    r = sc["items"][k]
    text = (f"✏️ <b>ویرایشِ ردیف {k + 1}</b>\n\n{_row_block(k, r)}\n\n"
            f"بازیکن یا نتیجه را تغییر دهید:")

    def res_btn(code, label):
        mark = "✓ " if r["res"] == {"w": "white", "b": "black", "d": "draw", "n": None}[code] else ""
        return Btn(mark + label, callback_data=f"mscan_r_{k}_{code}", style="primary")
    kbd = Markup([
        [Btn("⬜ تغییر سفید", callback_data=f"mscan_pw_{k}", style="primary"),
         Btn("⬛ تغییر سیاه", callback_data=f"mscan_pb_{k}", style="primary")],
        [Btn("🔄 جابه‌جاییِ سفید و سیاه", callback_data=f"mscan_sw_{k}", style="primary")],
        [res_btn("w", "برد سفید"), res_btn("b", "برد سیاه")],
        [res_btn("d", "تساوی"), res_btn("n", "بدون نتیجه")],
        [Btn("🔙 بازگشت به فهرست", callback_data="mscan_back", style="danger")],
    ])
    return text, kbd


# ───────────────────────── ارسالِ پیام/ویرایش ─────────────────────────
async def _show(update: Update, text: str, markup):
    q = update.callback_query
    if q:
        await safe_edit_message_text(q, text, reply_markup=markup, parse_mode="HTML")
    else:
        await update.effective_message.reply_text(text, reply_markup=markup, parse_mode="HTML")


async def _show_review(update, ctx):
    sc = _sc(ctx)
    text, markup = _review_view(sc, not await _needs_approval(update.effective_user.id))
    await _show(update, text, markup)


def _cancel_kb():
    return Markup([[Btn("❌ لغو", callback_data="mscan_cancel", style="danger")]])


# ───────────────────────── شروع و دریافتِ عکس ─────────────────────────
async def scan_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if await check_status_gate(query, "match_registration"):
        return ConversationHandler.END
    if not await _can_scan(uid):
        await query.answer("⛔ دسترسیِ «ثبت با عکس» برای شما فعال نیست.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    ctx.user_data.pop("scan", None)
    note = ("\n\n🔐 ثبتِ نهایی فقط بعد از <b>تأییدِ مدیر ارشد</b> انجام می‌شود."
            if await _needs_approval(uid) else "")
    await safe_edit_message_text(
        query,
        "📷 <b>ثبت نتیجه با عکس</b>\n\n"
        "عکسِ برگه‌ی نتایج را بفرستید. هوش مصنوعی ردیف‌ها را می‌خواند و شما قبل از ثبت "
        "پیش‌نمایش می‌بینید و هر ردیف را می‌توانید ویرایش یا رد کنید.\n\n"
        "💡 عکسِ صاف و روشن از کلِ برگه بگیرید. برای دقتِ بیشتر، عکس را به‌صورت «فایل» "
        "(Send as File) بفرستید تا فشرده نشود." + note,
        reply_markup=_cancel_kb(), parse_mode="HTML")
    return ST_SCAN_PHOTO


async def scan_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = update.effective_user.id
    if not await _can_scan(uid):
        await msg.reply_text("⛔ دسترسیِ «ثبت با عکس» برای شما فعال نیست.")
        return ConversationHandler.END
    if msg.photo:
        tg_file, mime = await msg.photo[-1].get_file(), "image/jpeg"
    else:
        doc = msg.document
        tg_file, mime = await doc.get_file(), (doc.mime_type or "image/jpeg")
    if tg_file.file_size and tg_file.file_size > 6 * 1024 * 1024:
        await msg.reply_text("❌ حجمِ عکس زیاد است (حداکثر ۶ مگابایت). عکسِ کم‌حجم‌تری بفرستید.",
                             reply_markup=_cancel_kb())
        return ST_SCAN_PHOTO
    wait = await msg.reply_text("⏳ در حال خواندنِ برگه… (تا یک دقیقه)")
    try:
        raw = bytes(await tg_file.download_as_bytearray())
        review = await svc.scan_sheet(raw, mime, uid)
    except svc.ScanError as e:
        # خطای زیرساخت (کلید/سهمیه/شلوغی/تایم‌اوت) ربطی به وضوحِ عکس ندارد؛ پیشنهادِ «عکسِ واضح‌تر» گمراه‌کننده است
        infra = e.code in ("ai_no_key", "ai_auth", "ai_model", "ai_quota", "ai_overloaded", "ai_timeout", "busy")
        tail = ("\n\nچند لحظه بعد همین عکس را دوباره بفرستید یا لغو کنید." if infra
                else "\n\nعکسِ دیگری بفرستید یا لغو کنید.")
        text = f"❌ {e.message}{tail}"
        if uid == PISHVA_ID and getattr(e, "detail", ""):
            text += f"\n\n🔧 جزئیاتِ فنی (فقط برای شما):\n{e.detail[:600]}"
        await wait.edit_text(text, reply_markup=_cancel_kb())
        return ST_SCAN_PHOTO
    except Exception:
        logger.exception("scan_photo failed")
        await wait.edit_text("❌ خطا در پردازشِ عکس. دوباره امتحان کنید.", reply_markup=_cancel_kb())
        return ST_SCAN_PHOTO

    items = []
    for it in review["items"]:
        items.append({
            "i": it["i"],
            "w": {"id": it["w"]["id"], "name": it["w"]["name"]} if it["w"]["id"] else None,
            "b": {"id": it["b"]["id"], "name": it["b"]["name"]} if it["b"]["id"] else None,
            "wc": it["w"].get("cands") or [], "bc": it["b"].get("cands") or [],
            "w_raw": it["w_raw"], "b_raw": it["b_raw"],
            "res": it["res"] if it["res"] in ("white", "black", "draw") else None,
            "raw_res": it["raw_result"], "note": it["note"], "dup": bool(it.get("dup")),
            "on": not it["issues"] and not it.get("dup"),
        })
    ctx.user_data["scan"] = {"items": items, "date": review["today"], "tid": None, "tname": "تورنمنتِ پیش‌فرض",
                             "page": 0, "date_text": review.get("date_text", ""),
                             "sheet_note": review.get("sheet_note", ""), "pick": None}
    try:
        await wait.delete()
    except Exception:
        pass
    await _show_review(update, ctx)
    return ST_SCAN_REVIEW


# ───────────────────────── ناوبری و ویرایش ─────────────────────────
async def _need_session(update, ctx):
    if _sc(ctx):
        return False
    await update.callback_query.answer("این جلسه تمام شده؛ دوباره از «ثبت با عکس» شروع کنید.", show_alert=True)
    return True


async def scan_back(update, ctx):
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    await q.answer()
    await _show_review(update, ctx)
    return ST_SCAN_REVIEW


async def scan_page(update, ctx):
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    await q.answer()
    _sc(ctx)["page"] = int(q.data.rsplit("_", 1)[1])
    await _show_review(update, ctx)
    return ST_SCAN_REVIEW


async def scan_toggle(update, ctx):
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    k = int(q.data.rsplit("_", 1)[1])
    sc = _sc(ctx)
    if not 0 <= k < len(sc["items"]):
        await q.answer()
        return ST_SCAN_REVIEW
    sc["items"][k]["on"] = not sc["items"][k]["on"]
    await q.answer("🚫 ردیف حذف شد" if not sc["items"][k]["on"] else "↩️ ردیف بازگشت")
    await _show_review(update, ctx)
    return ST_SCAN_REVIEW


async def scan_edit(update, ctx):
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    await q.answer()
    k = int(q.data.rsplit("_", 1)[1])
    sc = _sc(ctx)
    if not 0 <= k < len(sc["items"]):
        return ST_SCAN_REVIEW
    text, markup = _edit_view(sc, k)
    await _show(update, text, markup)
    return ST_SCAN_REVIEW


async def scan_swap(update, ctx):
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    k = int(q.data.rsplit("_", 1)[1])
    r = _sc(ctx)["items"][k]
    r["w"], r["b"] = r["b"], r["w"]
    r["wc"], r["bc"] = r["bc"], r["wc"]
    r["w_raw"], r["b_raw"] = r["b_raw"], r["w_raw"]
    # نتیجه نسبت به رنگ است؛ با جابه‌جاییِ رنگ‌ها، برد سفید ↔ برد سیاه می‌شود
    r["res"] = {"white": "black", "black": "white"}.get(r["res"], r["res"])
    await q.answer("🔄 جابه‌جا شد")
    text, markup = _edit_view(_sc(ctx), k)
    await _show(update, text, markup)
    return ST_SCAN_REVIEW


async def scan_result(update, ctx):
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    _p, ks, code = q.data.split("_")[1:]
    k = int(ks)
    r = _sc(ctx)["items"][k]
    r["res"] = {"w": "white", "b": "black", "d": "draw", "n": None}[code]
    await q.answer("✅")
    text, markup = _edit_view(_sc(ctx), k)
    await _show(update, text, markup)
    return ST_SCAN_REVIEW


# ───────────────────────── انتخابِ بازیکن ─────────────────────────
def _player_buttons(k, side, players):
    rows = []
    for p in players[:8]:
        label = f"{p['name']} [{p['cls']}]" if p.get("cls") else p["name"]
        rows.append([Btn(label[:60], callback_data=f"mscan_s_{k}_{side}_{p['id']}", style="primary")])
    return rows


async def scan_pick(update, ctx):
    """mscan_pw_{k} / mscan_pb_{k}: پیشنهادهای هوش مصنوعی + جستجو."""
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    await q.answer()
    side = "w" if q.data.startswith("mscan_pw_") else "b"
    k = int(q.data.rsplit("_", 1)[1])
    r = _sc(ctx)["items"][k]
    raw = r[side + "_raw"]
    rows = _player_buttons(k, side, r[side + "c"])
    rows.append([Btn("🔍 جستجوی اسم", callback_data=f"mscan_q_{k}_{side}", style="primary")])
    rows.append([Btn("🔙 بازگشت", callback_data=f"mscan_e_{k}", style="danger")])
    label = "⬜ سفید" if side == "w" else "⬛ سیاه"
    txt = f"{label} — ردیف {k + 1}\n"
    txt += f"روی برگه: «{_E(raw)}»\n\n" if raw else "\n"
    txt += ("گزینه‌های پیشنهادی:" if r[side + "c"] else "پیشنهادی نداریم؛ اسم را جستجو کنید.")
    await _show(update, txt, Markup(rows))
    return ST_SCAN_REVIEW


async def scan_search_start(update, ctx):
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    await q.answer()
    _p, ks, side = q.data.split("_")[1:]
    _sc(ctx)["pick"] = {"k": int(ks), "side": side}
    await safe_edit_message_text(
        q, "🔍 بخشی از اسمِ بازیکن را بنویسید (مثلاً «رضایی»):",
        reply_markup=Markup([[Btn("🔙 بازگشت", callback_data=f"mscan_e_{ks}", style="danger")]]),
        parse_mode="HTML")
    return ST_SCAN_SEARCH


async def scan_search_text(update, ctx):
    import match_vision as mv
    sc = _sc(ctx)
    pick = sc and sc.get("pick")
    if not pick:
        await update.message.reply_text("این جلسه تمام شده؛ دوباره از «ثبت با عکس» شروع کنید.")
        return ConversationHandler.END
    qn = mv.normalize_name(update.message.text)
    if not qn:
        await update.message.reply_text("اسم را به‌صورت متن بنویسید.")
        return ST_SCAN_SEARCH
    roster = mv.make_roster([p for p in await db.get_all_players() if (p["status"] or "active") == "active"])
    scored = []
    for r in roster:
        s = mv._score(qn, r["norm"])
        if qn in r["norm"]:
            s = max(s, 0.8)
        if s >= 0.4:
            scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    k, side = pick["k"], pick["side"]
    rows = _player_buttons(k, side, [r for _s, r in scored])
    rows.append([Btn("🔍 جستجوی دوباره", callback_data=f"mscan_q_{k}_{side}", style="primary")])
    rows.append([Btn("🔙 بازگشت", callback_data=f"mscan_e_{k}", style="danger")])
    txt = f"نتیجه‌ی جستجو برای «{_E(update.message.text.strip()[:30])}»:" if scored else "❌ بازیکنِ فعالی پیدا نشد."
    await update.message.reply_text(txt, reply_markup=Markup(rows), parse_mode="HTML")
    return ST_SCAN_REVIEW


async def scan_select(update, ctx):
    """mscan_s_{k}_{side}_{pid}"""
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    _p, ks, side, pid = q.data.split("_")[1:]
    k, pid = int(ks), int(pid)
    sc = _sc(ctx)
    r = sc["items"][k]
    p = await db.get_player(pid)
    if not p or (p["status"] or "active") != "active":
        await q.answer("این بازیکن فعال نیست.", show_alert=True)
        return ST_SCAN_REVIEW
    other = r["b" if side == "w" else "w"]
    if other and other["id"] == pid:
        await q.answer("سفید و سیاه نمی‌توانند یک نفر باشند.", show_alert=True)
        return ST_SCAN_REVIEW
    r[side] = {"id": pid, "name": p["full_name"]}
    r["on"] = True
    sc["pick"] = None
    await q.answer("✅ انتخاب شد")
    text, markup = _edit_view(sc, k)
    await _show(update, text, markup)
    return ST_SCAN_REVIEW


# ───────────────────────── تاریخ و تورنمنت ─────────────────────────
async def scan_date_start(update, ctx):
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    await q.answer()
    await safe_edit_message_text(
        q,
        "📅 <b>تاریخِ ثبت</b>\n\nتاریخ را بفرستید: شمسی (مثل <code>1405/07/11</code>)، میلادی، "
        "یا یک عدد = «چند روز قبل» (مثلاً <code>1</code> = دیروز). یا یکی از دکمه‌ها:",
        reply_markup=Markup([
            [Btn("📅 امروز", callback_data="mscan_dset_0", style="success"),
             Btn("دیروز", callback_data="mscan_dset_1", style="primary")],
            [Btn("🔙 بازگشت", callback_data="mscan_back", style="danger")]]),
        parse_mode="HTML")
    return ST_SCAN_DATE


async def scan_date_set(update, ctx):
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    await q.answer()
    _sc(ctx)["date"] = days_ago_gregorian(int(q.data.rsplit("_", 1)[1]))
    await _show_review(update, ctx)
    return ST_SCAN_REVIEW


async def scan_date_text(update, ctx):
    sc = _sc(ctx)
    if not sc:
        await update.message.reply_text("این جلسه تمام شده؛ دوباره از «ثبت با عکس» شروع کنید.")
        return ConversationHandler.END
    d = parse_admin_undo_date(update.message.text)
    if not d:
        await update.message.reply_text("❌ تاریخ نامعتبر است. مثلاً 1405/07/11 یا 2026-10-03 یا یک عدد.")
        return ST_SCAN_DATE
    sc["date"] = d
    await _show_review(update, ctx)
    return ST_SCAN_REVIEW


async def scan_tour_start(update, ctx):
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    await q.answer()
    ts = [t for t in await db.get_all_tournaments() if (t["status"] or "active") == "active"]
    rows = [[Btn("🏅 تورنمنتِ پیش‌فرض", callback_data="mscan_tset_0", style="success")]]
    rows += [[Btn(t["name"][:50], callback_data=f"mscan_tset_{t['id']}", style="primary")] for t in ts[:15]]
    rows.append([Btn("🔙 بازگشت", callback_data="mscan_back", style="danger")])
    await safe_edit_message_text(q, "🏅 <b>تورنمنت</b> را انتخاب کنید:", reply_markup=Markup(rows), parse_mode="HTML")
    return ST_SCAN_REVIEW


async def scan_tour_set(update, ctx):
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    tid = int(q.data.rsplit("_", 1)[1])
    sc = _sc(ctx)
    if tid:
        t = await db.get_tournament(tid)
        if not t:
            await q.answer("تورنمنت پیدا نشد.", show_alert=True)
            return ST_SCAN_REVIEW
        sc["tid"], sc["tname"] = t["id"], t["name"]
    else:
        sc["tid"], sc["tname"] = None, "تورنمنتِ پیش‌فرض"
    await q.answer("✅")
    await _show_review(update, ctx)
    return ST_SCAN_REVIEW


# ───────────────────────── تأییدِ نهایی / لغو ─────────────────────────
async def scan_go(update, ctx):
    """مرحله‌ی تأیید قبل از کارِ برگشت‌ناپذیر."""
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    sc = _sc(ctx)
    n = sum(1 for r in sc["items"] if _ready(r))
    if not n:
        await q.answer("مسابقه‌ای برای ثبت انتخاب نشده.", show_alert=True)
        return ST_SCAN_REVIEW
    await q.answer()
    pishva = not await _needs_approval(q.from_user.id)      # True = ثبتِ مستقیم
    skipped = len(sc["items"]) - n
    text = (f"{'✅ <b>ثبتِ نهایی</b>' if pishva else '📤 <b>ارسال برای تأییدِ مدیر ارشد</b>'}\n\n"
            f"♟️ {n} مسابقه • 📅 {_E(date_label_fa(sc['date']))} • 🏅 {_E(sc['tname'])}\n")
    if skipped:
        text += f"({skipped} ردیف ردشده/ناقص ثبت نمی‌شود)\n"
    text += ("\nبا تأیید، مسابقه‌ها همین حالا ثبت می‌شوند." if pishva else
             "\nتا وقتی مدیر ارشد تأیید نکند هیچ مسابقه‌ای ثبت نمی‌شود.")
    await safe_edit_message_text(q, text, parse_mode="HTML", reply_markup=Markup([
        [Btn("✅ بله، ثبت کن" if pishva else "📤 بله، ارسال کن", callback_data="mscan_ok", style="success")],
        [Btn("🔙 ادامه‌ی ویرایش", callback_data="mscan_back", style="primary")]]))
    return ST_SCAN_REVIEW


async def scan_commit(update, ctx):
    q = update.callback_query
    if await _need_session(update, ctx):
        return ConversationHandler.END
    uid = q.from_user.id
    sc = _sc(ctx)
    if not await _can_scan(uid):
        await q.answer("⛔ دسترسی شما غیرفعال شده.", show_alert=True)
        ctx.user_data.pop("scan", None)
        return ConversationHandler.END
    todo = [r for r in sc["items"] if _ready(r)]
    if not todo:
        await q.answer("مسابقه‌ای برای ثبت انتخاب نشده.", show_alert=True)
        return ST_SCAN_REVIEW
    ctx.user_data.pop("scan", None)          # فوراً؛ تا دوبار-زدن دوبار ثبت نکند
    payload = [{"i": r["i"], "white_id": r["w"]["id"], "black_id": r["b"]["id"], "result": r["res"]} for r in todo]
    back = Markup([[Btn("♟️ بازگشت به مسابقات", callback_data="back_matches", style="primary")]])
    await q.answer()

    if await _needs_approval(uid):
        try:
            req_id = await svc.submit_for_approval(uid, payload, sc["date"], sc["tid"])
        except ValueError as e:
            await safe_edit_message_text(q, f"❌ {_E(str(e))}", reply_markup=back, parse_mode="HTML")
            return ConversationHandler.END
        await db.log_action(uid, "scan_request", f"درخواستِ ثبت با عکس: {len(payload)} مسابقه", req_id)
        await notify_pishva_new_request(ctx.bot, req_id, await _admin_name(uid))
        await safe_edit_message_text(
            q, f"📤 <b>ارسال شد</b>\n\n{len(payload)} مسابقه برای تأییدِ مدیر ارشد فرستاده شد.\n"
               f"بعد از تأیید ثبت می‌شوند و نتیجه به شما اطلاع داده می‌شود. (درخواست #{req_id})",
            reply_markup=back, parse_mode="HTML")
        return ConversationHandler.END

    await safe_edit_message_text(q, "⏳ در حال ثبت…", parse_mode="HTML")
    try:
        md = svc.valid_date(sc["date"])
        tid = await svc.resolve_tournament(sc["tid"])
    except ValueError as e:
        await safe_edit_message_text(q, f"❌ {_E(str(e))}", reply_markup=back, parse_mode="HTML")
        return ConversationHandler.END
    created, failed = await svc.commit_items(payload, md, tid, uid)
    if created:
        await db.log_action(uid, "scan_commit", f"ثبت با عکس: {created} مسابقه" + ("" if uid == PISHVA_ID else " (مستقیم)"))
        if uid != PISHVA_ID:
            await notify_pishva_direct_commit(ctx.bot, await _admin_name(uid), created)
    text = f"✅ <b>{created} مسابقه ثبت شد.</b>"
    if failed:
        text += f"\n\n⚠️ {len(failed)} ردیف ثبت نشد:\n" + "\n".join(
            f"• ردیف {(f['i'] + 1) if isinstance(f['i'], int) else '؟'}: {_E(f['message'])}" for f in failed[:15])
    await safe_edit_message_text(q, text, reply_markup=back, parse_mode="HTML")
    return ConversationHandler.END


async def scan_cancel(update, ctx):
    q = update.callback_query
    ctx.user_data.pop("scan", None)
    await q.answer("لغو شد")
    await safe_edit_message_text(
        q, "❌ ثبت با عکس لغو شد؛ هیچ مسابقه‌ای ثبت نشد.", parse_mode="HTML",
        reply_markup=Markup([[Btn("♟️ بازگشت به مسابقات", callback_data="back_matches", style="primary")]]))
    return ConversationHandler.END


# ═══════════════ درخواست‌ها برای مدیر ارشد (خارج از conversation) ═══════════════
def _request_kb(req_id):
    return Markup([[Btn("✅ تأیید و ثبت", callback_data=f"scanreq_approve_{req_id}", style="success"),
                    Btn("❌ رد", callback_data=f"scanreq_reject_{req_id}", style="danger")]])


async def _request_text(req, who: str) -> str:
    rows = await svc.request_rows(req)
    lines = []
    for k, x in enumerate(rows):
        lines.append(f"{k + 1}. ⬜ {_E(x['w'])} ⚔️ ⬛ {_E(x['b'])} — {RES_LABEL.get(x['res'], 'بدون نتیجه')}")
    body, used = [], 0
    for ln in lines:
        if used + len(ln) > 2800:
            body.append(f"… و {len(lines) - len(body)} ردیفِ دیگر")
            break
        body.append(ln)
        used += len(ln) + 1
    tname = ""
    if req["tournament_id"]:
        t = await db.get_tournament(req["tournament_id"])
        tname = t["name"] if t else ""
    return (f"📷 <b>درخواستِ ثبت با عکس</b>  #{req['id']}\n\n"
            f"👤 درخواست‌دهنده: <b>{_E(who)}</b>\n"
            f"♟️ تعداد: {req['item_count'] or len(rows)} مسابقه\n"
            f"📅 {_E(date_label_fa(req['match_date'] or ''))}" + (f"   🏅 {_E(tname)}" if tname else "") + "\n"
            f"⏱️ <code>{_E(now_shamsi())}</code>\n\n" + "\n".join(body) +
            "\n\n📌 با تأیید، همه ثبت می‌شوند؛ با رد، هیچ‌چیز ثبت نمی‌شود:")


async def notify_pishva_new_request(bot, req_id: int, who: str):
    """به مدیر ارشد پیام می‌دهد (از ربات یا هاب صدا زده می‌شود). خطا لاگ می‌شود، جریان را نمی‌شکند."""
    if bot is None:
        return
    try:
        req = await db.get_scan_request(req_id)
        if not req:
            return
        await bot.send_message(chat_id=PISHVA_ID, text=await _request_text(req, who),
                               reply_markup=_request_kb(req_id), parse_mode="HTML")
    except Exception:
        logger.warning("notify_pishva_new_request failed", exc_info=True)


async def _pishva_only(q) -> bool:
    if q.from_user.id != PISHVA_ID:
        await q.answer("⛔ فقط مدیر ارشد.", show_alert=True)
        return False
    return True


async def pishva_scan_requests(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _pishva_only(q):
        return
    await q.answer()
    reqs = await db.get_pending_scan_requests()
    rows = []
    if not reqs:
        rows.append([Btn("📭 درخواستِ در انتظاری نیست", callback_data="noop_label", style="primary")])
    for r in reqs:
        rows.append([Btn(f"👁️ #{r['id']} — {r['item_count']} مسابقه — {await _admin_name(r['admin_id'])}"[:60],
                         callback_data=f"scanreq_view_{r['id']}", style="primary")])
    rows.append([Btn("🔙 بازگشت", callback_data="menu_pishva", style="danger")])
    await safe_edit_message_text(q, "📷 <b>درخواست‌های ثبت با عکس</b>\n\nدرخواست‌های در انتظارِ تأیید:",
                                 reply_markup=Markup(rows), parse_mode="HTML")


async def scanreq_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _pishva_only(q):
        return
    await q.answer()
    req = await db.get_scan_request(int(q.data.rsplit("_", 1)[1]))
    if not req or req["status"] != "pending":
        await safe_edit_message_text(q, "این درخواست دیگر معتبر نیست.",
                                     reply_markup=Markup([[Btn("🔙 بازگشت", callback_data="pishva_scan_requests", style="danger")]]))
        return
    await safe_edit_message_text(q, await _request_text(req, await _admin_name(req["admin_id"])),
                                 reply_markup=_request_kb(req["id"]), parse_mode="HTML")


async def scanreq_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _pishva_only(q):
        return
    req_id = int(q.data.rsplit("_", 1)[1])
    ok, created, failed, req = await svc.approve_scan_request(req_id, q.from_user.id)
    if not ok:
        await q.answer("این درخواست قبلاً بررسی شده.", show_alert=True)
        await safe_edit_message_text(q, (q.message.text or "") + "\n\nℹ️ قبلاً بررسی شده بود.", parse_mode=None)
        return
    await q.answer("✅ ثبت شد")
    msg = f"✅ درخواستِ ثبت با عکسِ شما توسط مدیر ارشد تایید شد: {created} مسابقه ثبت شد."
    if failed:
        msg += f"\n⚠️ {len(failed)} ردیف ثبت نشد."
    try:
        await ctx.bot.send_message(chat_id=req["admin_id"], text=msg)
    except Exception:
        logger.warning("scanreq_approve: notify requester failed", exc_info=True)
    out = f"✅ تأیید شد: {created} مسابقه ثبت شد."
    if failed:
        out += f"\n⚠️ {len(failed)} ردیف ثبت نشد:\n" + "\n".join(
            f"• ردیف {(f['i'] + 1) if isinstance(f['i'], int) else '؟'}: {f['message']}" for f in failed[:10])
    await safe_edit_message_text(q, (q.message.text or "") + "\n\n" + out, parse_mode=None,
                                 reply_markup=Markup([[Btn("🔙 بازگشت", callback_data="menu_pishva", style="danger")]]))


async def scanreq_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await _pishva_only(q):
        return
    req_id = int(q.data.rsplit("_", 1)[1])
    ok, req = await svc.reject_scan_request(req_id, q.from_user.id)
    if not ok:
        await q.answer("این درخواست قبلاً بررسی شده.", show_alert=True)
        await safe_edit_message_text(q, (q.message.text or "") + "\n\nℹ️ قبلاً بررسی شده بود.", parse_mode=None)
        return
    await q.answer("❌ رد شد")
    try:
        await ctx.bot.send_message(chat_id=req["admin_id"],
                                   text="❌ درخواستِ ثبت با عکسِ شما توسط مدیر ارشد رد شد؛ هیچ مسابقه‌ای ثبت نشد.")
    except Exception:
        logger.warning("scanreq_reject: notify requester failed", exc_info=True)
    await safe_edit_message_text(q, (q.message.text or "") + "\n\n❌ رد شد؛ چیزی ثبت نشد.", parse_mode=None,
                                 reply_markup=Markup([[Btn("🔙 بازگشت", callback_data="menu_pishva", style="danger")]]))


async def notify_pishva_direct_commit(bot, who: str, created: int):
    """مدیرِ دارای دسترسیِ مستقیم ثبت کرد؛ فقط خبر به مدیر ارشد (تأیید لازم نیست)."""
    if bot is None or not created:
        return
    try:
        await bot.send_message(chat_id=PISHVA_ID,
                               text=f"📷 {who} با «ثبت با عکس» {created} مسابقه را مستقیم ثبت کرد.")
    except Exception:
        logger.warning("notify_pishva_direct_commit failed", exc_info=True)


# ═══════════════ تنظیمِ اختصاصیِ هر مدیر (از پنلِ دسترسی‌های مدیر) ═══════════════
# دو دکمه: «ثبت با عکس» (پیش‌فرض ← روشن ← خاموش) و «حالتِ ثبت» (کلی ← مستقیم ← با تأیید).
async def scanperm_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """scanperm_{tid}_access  |  scanperm_{tid}_mode"""
    from helpers import box
    import keyboards as kb
    q = update.callback_query
    if not await _pishva_only(q):
        return
    _p, tid_s, what = q.data.split("_")
    tid = int(tid_s)
    admin = await db.get_admin(tid)
    if not admin:
        await q.answer("مدیر یافت نشد.", show_alert=True)
        return
    perms = hub_caps.parse_perms(admin)
    if what == "access":
        over = dict(perms.get("hub_caps") or {}) if isinstance(perms.get("hub_caps"), dict) else {}
        cur = over.get("match_scan")
        if cur is None:
            over["match_scan"] = True
        elif cur:
            over["match_scan"] = False
        else:
            over.pop("match_scan", None)
        await db.set_admin_permission(tid, "hub_caps", over)
        txt = {True: "روشن ✅", False: "خاموش ❌", None: "پیش‌فرضِ نقش"}[over.get("match_scan")]
    else:
        nxt = {None: "direct", "direct": "approval", "approval": None}[hub_caps.scan_mode_override(perms)]
        await db.set_admin_permission(tid, "scan_mode", nxt)
        txt = {"direct": "مستقیم ⚡", "approval": "با تأییدِ من 🔐", None: "تنظیمِ کلی"}[nxt]
    await db.log_action(PISHVA_ID, "admin_permission", f"ثبت با عکس ({'دسترسی' if what == 'access' else 'حالت'}): {txt}", tid)
    await q.answer(txt)
    admin2 = await db.get_admin(tid)
    name = admin2["display_name"] or admin2["full_name"]
    await safe_edit_message_text(
        q, f"{box('⬆️ دسترسی‌های ' + name)}\n\n📌 دسترسی‌ها را تغییر دهید:",
        reply_markup=kb.kb_admin_permissions(tid, hub_caps.parse_perms(admin2)), parse_mode="Markdown")
