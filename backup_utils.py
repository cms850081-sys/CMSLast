"""
backup_utils.py — سیستم پشتیبان‌گیری کامل
شامل: تولید Excel، تولید Word، بکاپ خودکار با تایمر
"""
import io
import os
import re
import logging
from datetime import datetime, timedelta

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from docx import Document

import database as db
from helpers import now_shamsi, today_shamsi

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# جمع‌آوری داده برای بکاپ
# ──────────────────────────────────────────────────────────────
async def gather_data(period: str = "all") -> dict:
    from datetime import timedelta
    now = datetime.now()

    # بدون سقفِ ۵۰ تایی: بکاپ باید همه‌ی بازی‌های بازه را داشته باشد (قبلاً فقط ۵۰ بازیِ آخر می‌آمد)
    matches = await db.get_matches_by_filter(period, limit=None)
    all_matches = matches if period == "all" else await db.get_matches_by_filter("all", limit=None)
    players_raw = await db.get_all_players()
    tournaments = await db.get_all_tournaments()
    classes = await db.get_all_classes()
    admins = await db.get_all_admins()

    # برد/مساوی/باخت از روی جدول matches (منبعِ حقیقت) و روی «همه‌ی» بازی‌ها محاسبه می‌شود، نه شمارنده‌های ذخیره‌شده
    # در players که با حذف/ویرایشِ بازی‌ها از واقعیت جدا می‌افتند. (بازه‌ی بکاپ فقط روی فهرستِ بازی‌ها اثر دارد.)
    st = {}
    for m in all_matches:
        r = m["result"]
        if r not in ("white", "black", "draw"):
            continue
        w, b = st.setdefault(m["white_player_id"], [0, 0, 0]), st.setdefault(m["black_player_id"], [0, 0, 0])
        if r == "white":
            w[0] += 1; b[2] += 1
        elif r == "black":
            b[0] += 1; w[2] += 1
        else:
            w[1] += 1; b[1] += 1
    players = []
    for pr in players_raw:
        d = dict(pr)
        w, dr, l = st.get(d["id"], [0, 0, 0])
        d["wins"], d["draws"], d["losses"] = w, dr, l
        players.append(d)

    return {
        "players": players,
        "matches": matches,
        "tournaments": tournaments,
        "classes": classes,
        "admins": admins,
        "generated_at": now_shamsi(),
        "period": period,
        "total_players": len(players),
        "total_matches": len(matches),
    }


# ──────────────────────────────────────────────────────────────
# ابزارهای استایل Excel
# ──────────────────────────────────────────────────────────────
def _header_style():
    return {
        "font": Font(bold=True, color="FFFFFF", size=11, name="Arial"),
        "fill": PatternFill("solid", fgColor="1F3864"),
        "alignment": Alignment(horizontal="center", vertical="center", wrap_text=True),
        "border": Border(
            left=Side(style="thin", color="AAAAAA"),
            right=Side(style="thin", color="AAAAAA"),
            top=Side(style="thin", color="AAAAAA"),
            bottom=Side(style="thin", color="AAAAAA"),
        )
    }

def _row_style(even: bool):
    return {
        "fill": PatternFill("solid", fgColor="EBF1FF" if even else "FFFFFF"),
        "alignment": Alignment(horizontal="right", vertical="center", wrap_text=True),
        "border": Border(
            left=Side(style="thin", color="DDDDDD"),
            right=Side(style="thin", color="DDDDDD"),
            top=Side(style="thin", color="DDDDDD"),
            bottom=Side(style="thin", color="DDDDDD"),
        )
    }

def _apply_style(cell, style: dict):
    for attr, val in style.items():
        setattr(cell, attr, val)

def _write_header(ws, headers: list):
    style = _header_style()
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        _apply_style(cell, style)
    ws.row_dimensions[1].height = 22

def _write_row(ws, row_num: int, values: list):
    style = _row_style(row_num % 2 == 0)
    for col, v in enumerate(values, 1):
        cell = ws.cell(row=row_num, column=col, value=str(v) if v is not None else "")
        if isinstance(cell.value, str) and cell.value.startswith("="):
            cell.data_type = "s"          # متنی که با «=» شروع می‌شود (مثلاً یک نام) فرمول حساب نشود
        _apply_style(cell, style)

def _auto_width(ws, min_width=12, max_width=35):
    for col in ws.columns:
        max_len = min_width
        col_letter = col[0].column_letter
        for cell in col:
            try:
                val = str(cell.value or "")
                max_len = min(max(max_len, len(val) + 2), max_width)
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = max_len



# ──────────────────────────────────────────────────────────────
# عکسِ کاملِ دیتابیس (همان چیزی که قبلاً فقط در شیتِ متصل بود، حالا در هر بکاپ)
# ──────────────────────────────────────────────────────────────
SNAP_ROW_CAP = 30000      # سقفِ ردیفِ هر جدول در فایل (جدیدترین‌ها)؛ برای جلوگیری از پُر شدنِ حافظه‌ی ربات
SNAP_PAGE = 2000
_XL_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_COL_FA = {
    "id": "شناسه", "telegram_id": "شناسه تلگرام", "username": "یوزرنیم", "full_name": "نام کامل", "display_name": "نام نمایشی",
    "role": "نقش", "is_active": "فعال؟", "warnings": "اخطار", "joined_at": "تاریخ عضویت", "last_active": "آخرین فعالیت",
    "permissions": "دسترسی‌ها", "name": "نام", "created_at": "زمان ایجاد", "class_id": "شناسه کلاس", "status": "وضعیت",
    "notes": "یادداشت", "is_elite": "برتر؟", "is_special": "ویژه؟", "wins": "برد", "losses": "باخت", "draws": "مساوی",
    "suspension_reason": "علت تعلیق", "white_player_id": "شناسه سفید", "black_player_id": "شناسه سیاه", "result": "نتیجه",
    "draw_reason": "علت تساوی", "match_date": "تاریخ مسابقه", "tournament_id": "شناسه تورنمنت", "created_by": "ثبت‌کننده",
    "updated_by": "ویرایش‌کننده", "updated_at": "زمان ویرایش", "is_pinned": "سنجاق؟", "is_default": "پیش‌فرض؟",
    "ended_at": "زمان پایان", "target_type": "نوعِ هدف", "target_id": "شناسه هدف", "reason": "دلیل", "issued_by": "صادرکننده",
    "issued_at": "زمان صدور", "text": "متن", "sent_at": "زمان ارسال", "title": "عنوان", "description": "شرح", "assigned_to": "مسئول",
    "assigned_by": "واگذارکننده", "assigned_at": "زمان واگذاری", "completed_at": "زمان انجام", "fail_reason": "دلیل ناکامی",
    "slogan": "شعار", "team_code": "کد تیم", "captain_id": "شناسه کاپیتان", "team_id": "شناسه تیم", "player_id": "شناسه بازیکن",
    "level": "سطح", "is_reserve": "ذخیره؟", "team1_id": "شناسه تیم ۱", "team2_id": "شناسه تیم ۲", "match_type": "نوع مسابقه",
    "scoring_method": "روش امتیازدهی", "round_num": "دور", "team_match_id": "شناسه مسابقه تیمی", "board_num": "شماره میز",
    "player1_id": "شناسه بازیکن ۱", "player2_id": "شناسه بازیکن ۲", "rating": "Elo", "peak_rating": "اوج Elo",
    "games_played": "تعداد بازی", "old_rating": "Elo قبل", "new_rating": "Elo بعد", "change": "تغییر", "opponent_id": "شناسه حریف",
    "match_id": "شناسه بازی", "recorded_at": "زمان ثبت", "jdate": "تاریخ شمسی", "day_type": "نوع روز", "white_id": "شناسه سفید",
    "black_id": "شناسه سیاه", "white_name": "نام سفید", "black_name": "نام سیاه", "winner_id": "شناسه برنده", "pgn": "PGN", "fen": "FEN",
    "finished_at": "زمان پایان", "token": "توکن بازی", "action_type": "نوع اقدام", "logged_at": "زمان ثبت", "key": "کلید", "value": "مقدار",
}


def _xl_val(v):
    if v is None:
        return ""
    if isinstance(v, (bytes, bytearray)):
        return "[blob]"
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    return _XL_BAD.sub("", str(v))[:32000]


def _xl_append(ws, vals):
    """افزودنِ ردیف؛ متنی که با «=» شروع می‌شود فرمول حساب نشود."""
    if any(isinstance(v, str) and v.startswith("=") for v in vals):
        r = ws._current_row + 1
        for ci, v in enumerate(vals, 1):
            c = ws.cell(row=r, column=ci, value=v)
            if isinstance(v, str) and v.startswith("="):
                c.data_type = "s"
    else:
        ws.append(vals)


def _xl_title(base: str, used: set) -> str:
    t = re.sub(r"[\[\]\*\?/\\:]", "-", base)[:31]
    n, out = 1, t
    while out in used:
        n += 1
        out = t[:31 - len(str(n)) - 1] + "~" + str(n)
    used.add(out)
    return out


async def _read_table(ss, physical, cols, logical):
    """ردیف‌های یک جدول (جدیدترین SNAP_ROW_CAP تا) به‌ترتیبِ rowid. برمی‌گرداند: (rows, total)."""
    sel = ",".join(f'CASE WHEN typeof("{c}")=\'blob\' THEN \'[blob]\' ELSE "{c}" END AS "{c}"' for c in cols)
    cond = ss._SECRET_KEY_SQL.replace("NEW.", "") if logical == "system_settings" else ""
    wh = f" WHERE {cond}" if cond else ""
    total = int((await ss._x(f'SELECT COUNT(*) FROM "{physical}"{wh}')).rows[0][0])
    start = 0
    if total > SNAP_ROW_CAP:
        r = (await ss._x(f'SELECT rowid FROM "{physical}"{wh} ORDER BY rowid LIMIT 1 OFFSET {total - SNAP_ROW_CAP}')).rows
        start = int(r[0][0]) - 1 if r else 0
    rows, last = [], start
    while True:
        q = f'SELECT rowid AS "__rid", {sel} FROM "{physical}" WHERE {cond + " AND " if cond else ""}rowid > ? ORDER BY rowid LIMIT {SNAP_PAGE}'
        page = (await ss._x(q, [last])).rows
        if not page:
            break
        rows.extend(r[1:] for r in page)
        last = int(page[-1][0])
        if len(page) < SNAP_PAGE:
            break
    return rows, total


async def _add_snapshot_sheets(wb, ws_index):
    """برای هر جدولِ دیتابیس (همان‌هایی که شیتِ متصل پوشش می‌داد) یک تب با «وضعیتِ فعلیِ کاملِ» جدول. توکن/رمزها کنار گذاشته می‌شوند.
    اگر یک جدول خطا بدهد فقط همان جدول در فهرست با خطا ثبت می‌شود و بکاپ ادامه پیدا می‌کند."""
    import sheets_sync as ss
    import srvctx
    sid = srvctx.current()
    used = set(wb.sheetnames)
    index = []
    for physical, logical in await ss._tables_of(sid):
        title = _xl_title(ss._tab_title(logical), used)
        try:
            cols = ss._vis(await ss._columns(physical), logical)
            if not cols:
                used.discard(title)
                continue
            rows, total = await _read_table(ss, physical, cols, logical)
            ws = wb.create_sheet(title)
            ws.sheet_view.rightToLeft = True
            _write_header(ws, [f"{_COL_FA[c]} ({c})" if c in _COL_FA else c for c in cols])
            for r in rows:
                _xl_append(ws, [_xl_val(v) for v in r])
            ws.freeze_panes = "A2"
            for ci, c in enumerate(cols, 1):
                w = len(_COL_FA.get(c, c)) + len(c) + 4 if c in _COL_FA else len(c) + 4
                for r in rows[:200]:
                    v = r[ci - 1]
                    w = max(w, min(len(str(v)) if v is not None else 0, 50) + 2)
                ws.column_dimensions[ws.cell(row=1, column=ci).column_letter].width = min(max(w, 10), 55)
            note = "" if len(rows) >= total else f"فقط {len(rows)} ردیفِ آخر از {total} در این فایل است"
            index.append((title, logical, total, len(rows), note))
        except Exception as e:
            logger.warning("backup: snapshot of %s failed: %s", physical, e)
            index.append((title, logical, "؟", 0, f"خطا: {str(e)[:120]}"))
            if title in wb.sheetnames and wb[title].max_row <= 1:
                del wb[title]
    _write_header(ws_index, ["تب", "جدول", "تعداد ردیف در دیتابیس", "ردیف در این فایل", "توضیح"])
    for i, r in enumerate(index, 1):
        _write_row(ws_index, i + 1, list(r))
    _auto_width(ws_index)
    return index

# ──────────────────────────────────────────────────────────────
# تولید فایل Excel
# ──────────────────────────────────────────────────────────────
async def generate_excel_backup(period: str = "all") -> io.BytesIO:
    data = await gather_data(period)
    wb = openpyxl.Workbook()

    # ─── شیت اطلاعات ─────────────────────────────────────────
    ws_info = wb.active
    ws_info.title = "اطلاعات بکاپ"
    ws_info.sheet_view.rightToLeft = True
    info_rows = [
        ("عنوان", "سیستم فرماندهی شطرنج — گزارش پشتیبان"),
        ("تاریخ تهیه", data["generated_at"]),
        ("بازه زمانی", {
            "today": "امروز", "week": "این هفته",
            "month": "این ماه", "all": "از ابتدا"
        }.get(period, period)),
        ("تعداد بازیکنان", data["total_players"]),
        ("تعداد مسابقات", data["total_matches"]),
        ("تعداد تورنمنت‌ها", len(data["tournaments"])),
        ("تعداد کلاس‌ها", len(data["classes"])),
    ]
    for i, (k, v) in enumerate(info_rows, 1):
        ws_info.cell(row=i, column=1, value=k).font = Font(bold=True, name="Arial")
        ws_info.cell(row=i, column=2, value=str(v)).font = Font(name="Arial")
    ws_info.column_dimensions["A"].width = 22
    ws_info.column_dimensions["B"].width = 38

    # ─── شیت بازیکنان ────────────────────────────────────────
    ws_p = wb.create_sheet("بازیکنان")
    ws_p.sheet_view.rightToLeft = True
    headers_p = ["ردیف", "نام کامل", "کلاس", "وضعیت", "برد", "مساوی", "باخت",
                 "اخطار", "برتر", "ویژه", "تاریخ ثبت"]
    _write_header(ws_p, headers_p)
    status_map = {
        "active": "فعال", "suspended": "تعلیق",
        "kicked": "اخراج", "eliminated": "حذف"
    }
    for i, p in enumerate(data["players"], 1):
        _write_row(ws_p, i + 1, [
            i,
            p["full_name"] or "",
            p["class_name"] or "" if p["class_name"] else "",
            status_map.get(p["status"], p["status"]),
            p["wins"], p["draws"], p["losses"],
            p["warnings"],
            "بله" if p["is_elite"] else "خیر",
            "بله" if p["is_special"] else "خیر",
            str(p["created_at"] or "")[:10],
        ])
    _auto_width(ws_p)

    # ─── شیت مسابقات ─────────────────────────────────────────
    ws_m = wb.create_sheet("مسابقات")
    ws_m.sheet_view.rightToLeft = True
    headers_m = ["ردیف", "بازیکن سفید", "بازیکن سیاه", "نتیجه",
                 "علت تساوی", "تاریخ مسابقه", "ثبت‌کننده", "زمان ثبت"]
    _write_header(ws_m, headers_m)
    result_fa = {"white": "برد سفید", "black": "برد سیاه",
                 "draw": "تساوی", None: "بدون نتیجه", "": "بدون نتیجه"}
    for i, m in enumerate(data["matches"], 1):
        _write_row(ws_m, i + 1, [
            i,
            m["white_name"] or "",
            m["black_name"] or "",
            result_fa.get(m["result"], str(m["result"] or "")),
            m["draw_reason"] or "",
            str(m["match_date"] or ""),
            str(m["created_by"] or ""),
            str(m["created_at"] or "")[:16],
        ])
    _auto_width(ws_m)

    # ─── شیت تورنمنت‌ها ──────────────────────────────────────
    ws_t = wb.create_sheet("تورنمنت‌ها")
    ws_t.sheet_view.rightToLeft = True
    headers_t = ["ردیف", "نام", "وضعیت", "پیش‌فرض", "تاریخ ایجاد"]
    _write_header(ws_t, headers_t)
    for i, t in enumerate(data["tournaments"], 1):
        _write_row(ws_t, i + 1, [
            i, t["name"], t["status"],
            "بله" if t["is_default"] else "خیر",
            str(t["created_at"] or "")[:10],
        ])
    _auto_width(ws_t)

    # ─── شیت کلاس‌ها ─────────────────────────────────────────
    ws_c = wb.create_sheet("کلاس‌ها")
    ws_c.sheet_view.rightToLeft = True
    headers_c = ["ردیف", "نام کلاس", "تاریخ ثبت"]
    _write_header(ws_c, headers_c)
    for i, c in enumerate(data["classes"], 1):
        _write_row(ws_c, i + 1, [i, c["name"], str(c["created_at"] or "")[:10]])
    _auto_width(ws_c)

    # ─── شیت «شیت‌ها»: اطلاعات و وضعیتِ اتصالِ گوگل‌شیت‌ها داخلِ بکاپ ─────────
    try:
        import sheets_sync
        rows_s = await sheets_sync.backup_rows()
        ws_s = wb.create_sheet("شیت‌ها")
        ws_s.sheet_view.rightToLeft = True
        _write_header(ws_s, rows_s[0])
        for i, r in enumerate(rows_s[1:], 1):
            _write_row(ws_s, i + 1, r)
        _auto_width(ws_s)
    except Exception as _e:
        logger.warning(f"backup: sheets info skipped: {_e}")

    # ─── عکسِ کاملِ دیتابیس: یک تب برای هر جدول (همیشه «از ابتدا»، مستقل از بازه‌ی بالا) ───
    try:
        ws_idx = wb.create_sheet("فهرست عکس دیتابیس")
        ws_idx.sheet_view.rightToLeft = True
        index = await _add_snapshot_sheets(wb, ws_idx)
        n = ws_info.max_row + 1
        ws_info.cell(row=n, column=1, value="عکس کامل دیتابیس").font = Font(bold=True, name="Arial")
        ws_info.cell(row=n, column=2, value=f"{len(index)} جدول · {sum(r[3] for r in index)} ردیف (فهرست در تب «فهرست عکس دیتابیس»)").font = Font(name="Arial")
        ws_info.cell(row=n + 1, column=1, value="توجه").font = Font(bold=True, name="Arial")
        ws_info.cell(row=n + 1, column=2, value="تب‌های «بازیکنان / مسابقات / تورنمنت‌ها / کلاس‌ها» بازه‌ی انتخابی را دنبال می‌کنند؛ عکس کامل همیشه از ابتدا است.").font = Font(name="Arial")
    except Exception as _e:
        logger.warning(f"backup: full snapshot skipped: {_e}", exc_info=True)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ──────────────────────────────────────────────────────────────
# تولید فایل Word
# ──────────────────────────────────────────────────────────────
async def generate_word_backup(period: str = "all") -> io.BytesIO:
    data = await gather_data(period)
    doc = Document()

    # تنظیم فونت پیش‌فرض
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    # عنوان
    title = doc.add_heading("سیستم فرماندهی شطرنج — گزارش پشتیبان", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"📅 تاریخ تهیه: {data['generated_at']}")
    doc.add_paragraph(f"📊 بازه: {period} | بازیکنان: {data['total_players']} | مسابقات: {data['total_matches']}")

    p = doc.add_paragraph()
    p.add_run("─" * 50)

    # ─── بازیکنان ────────────────────────────────────────────
    doc.add_heading("👤 بازیکنان", level=1)
    if data["players"]:
        table = doc.add_table(rows=1, cols=8)
        table.style = "Table Grid"
        hdr = table.rows[0].cells
        for i, h in enumerate(["نام", "کلاس", "وضعیت", "برد", "مساوی", "باخت", "اخطار", "تاریخ"]):
            hdr[i].text = h
            try:
                hdr[i].paragraphs[0].runs[0].font.bold = True
            except IndexError:
                run = hdr[i].paragraphs[0].add_run(h)
                run.font.bold = True
                hdr[i].text = ""

        status_map = {"active": "فعال", "suspended": "تعلیق",
                      "kicked": "اخراج", "eliminated": "حذف"}
        for p_row in data["players"]:
            row = table.add_row().cells
            row[0].text = p_row["full_name"] or ""
            row[1].text = (p_row["class_name"] or "") if p_row["class_name"] else ""
            row[2].text = status_map.get(p_row["status"], p_row["status"])
            row[3].text = str(p_row["wins"])
            row[4].text = str(p_row["draws"])
            row[5].text = str(p_row["losses"])
            row[6].text = str(p_row["warnings"])
            row[7].text = str(p_row["created_at"] or "")[:10]
    else:
        doc.add_paragraph("هیچ بازیکنی ثبت نشده.")

    doc.add_paragraph()

    # ─── مسابقات ─────────────────────────────────────────────
    doc.add_heading("♟️ مسابقات", level=1)
    if data["matches"]:
        table2 = doc.add_table(rows=1, cols=5)
        table2.style = "Table Grid"
        hdr2 = table2.rows[0].cells
        for i, h in enumerate(["سفید", "سیاه", "نتیجه", "علت تساوی", "تاریخ"]):
            hdr2[i].text = h
            try:
                hdr2[i].paragraphs[0].runs[0].font.bold = True
            except IndexError:
                pass

        result_fa = {"white": "برد سفید", "black": "برد سیاه",
                     "draw": "تساوی", None: "—"}
        for m in data["matches"]:
            row = table2.add_row().cells
            row[0].text = m["white_name"] or ""
            row[1].text = m["black_name"] or ""
            row[2].text = result_fa.get(m["result"], str(m["result"] or ""))
            row[3].text = m["draw_reason"] or ""
            row[4].text = str(m["match_date"] or "")
    else:
        doc.add_paragraph("هیچ مسابقه‌ای ثبت نشده.")

    doc.add_paragraph()

    # ─── تورنمنت‌ها ───────────────────────────────────────────
    doc.add_heading("🏅 تورنمنت‌ها", level=1)
    for t in data["tournaments"]:
        doc.add_paragraph(
            f"• {t['name']} | {t['status']} | {'پیش‌فرض ✅' if t['is_default'] else ''}"
        )

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


# ──────────────────────────────────────────────────────────────
# نام فایل استاندارد
# ──────────────────────────────────────────────────────────────
def backup_filename(fmt: str, period: str) -> str:
    ts = today_shamsi().replace("/", "-")
    period_fa = {
        "today": "امروز", "week": "هفته", "month": "ماه", "all": "کامل"
    }.get(period, period)
    ext = "xlsx" if fmt == "excel" else "docx"
    return f"chess_backup_{period_fa}_{ts}.{ext}"


# ──────────────────────────────────────────────────────────────
# ارسال بکاپ
# ──────────────────────────────────────────────────────────────
async def send_backup(bot, chat_id: int, period: str, fmt: str):
    """تهیه و ارسال فایل بکاپ"""
    try:
        if fmt == "excel":
            buf = await generate_excel_backup(period)
        else:
            buf = await generate_word_backup(period)

        filename = backup_filename(fmt, period)
        ts = now_shamsi()
        period_fa = {
            "today": "امروز", "week": "این هفته",
            "month": "این ماه", "all": "از ابتدا"
        }.get(period, period)

        caption = (
            f"💾 *بکاپ سیستم شطرنج*\n\n"
            f"📊 بازه: {period_fa}\n"
            f"📁 فرمت: {'Excel' if fmt == 'excel' else 'Word'}\n"
            f"⏱️ تهیه‌شده در: `{ts}`"
        )
        await bot.send_document(
            chat_id=chat_id,
            document=buf,
            filename=filename,
            caption=caption,
            parse_mode="Markdown"
        )
        logger.info(f"Backup sent to {chat_id}: {filename}")
        return True
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        raise e


# ──────────────────────────────────────────────────────────────
# چندسرور: بکاپِ یک سرور (مجموعه) — داده‌ی همان سرور، در زمینه‌ی خودش
# ──────────────────────────────────────────────────────────────
async def send_server_backup(bot, chat_id: int, sid: int, period: str = "all", fmt: str = "excel"):
    """بکاپِ کاملِ «سرورِ sid» (فقط داده‌ی همان سرور) را به chat_id می‌فرستد."""
    import servers
    import srvctx
    s = servers.get(sid) or {}
    with srvctx.use(sid):
        buf = await (generate_excel_backup(period) if fmt == "excel" else generate_word_backup(period))
    ts = today_shamsi().replace("/", "-")
    ext = "xlsx" if fmt == "excel" else "docx"
    period_fa = {"today": "امروز", "week": "این هفته", "month": "این ماه", "all": "از ابتدا"}.get(period, period)
    await bot.send_document(
        chat_id=chat_id, document=buf, filename=f"server{sid}_backup_{period}_{ts}.{ext}",
        caption=(f"💾 *بکاپ سرور {sid}*\n🏫 {s.get('name', '')}\n📊 بازه: {period_fa}\n"
                 f"📁 فرمت: {'Excel' if fmt == 'excel' else 'Word'}\n⏱️ `{now_shamsi()}`"),
        parse_mode="Markdown")
    return True


async def send_all_servers_backup(bot, chat_id: int, period: str = "all", fmt: str = "excel", include_main: bool = True):
    """بکاپِ همه‌ی سرورها (اصلی + مجموعه‌های فعال) — هر سرور یک فایلِ جدا. (موفق، ناموفق) را برمی‌گرداند."""
    import servers
    ok = fail = 0
    for sid in servers.active_ids():
        if sid == 1 and not include_main:
            continue
        try:
            if sid == 1:
                await send_backup(bot, chat_id, period, fmt)
            else:
                await send_server_backup(bot, chat_id, sid, period, fmt)
            ok += 1
        except Exception:
            logger.exception("backup server %s failed", sid)
            fail += 1
    return ok, fail


# ──────────────────────────────────────────────────────────────
# بکاپ خودکار (Job Queue)
# ──────────────────────────────────────────────────────────────
# باگ قبلی: هر بار که این job ثبت می‌شد (چه با تغییرِ تایمر توسطِ کاربر،
# چه با هر ری‌استارتِ خودِ ربات روی Railway که restartPolicyType آن
# ON_FAILURE است) با first=timedelta(seconds=30) یک بکاپِ فوری، ۳۰ ثانیه
# بعد از همان لحظه، زمان‌بندی می‌شد — کاملاً مستقل از این‌که آخرین بکاپِ
# واقعی کِی فرستاده شده. اگر ربات مکرراً ری‌استارت شود (کرش، دیپلویِ
# مجدد، Conflict بینِ چند instance و…)، همین رفتار دقیقاً همان حسِ
# «هرچی تایمر رو عوض می‌کنم بازم زود/بی‌نظم میاد» را ایجاد می‌کند.
# رفعِ باگ: زمانِ آخرین بکاپِ واقعی در دیتابیس ذخیره می‌شود؛ first از
# رویِ فاصله‌ی واقعیِ باقی‌مانده تا سررسیدِ بعدی محاسبه می‌شود، نه یک
# عددِ ثابتِ ۳۰ ثانیه‌ای. اگر بکاپِ قبلی خیلی وقت پیش بوده (یا اصلاً
# نبوده)، همان لحظه (۳۰ ثانیه) اجرا می‌شود؛ وگرنه صبر می‌کند تا دقیقاً
# سرِ موعدِ interval انتخابیِ کاربر برسد.
async def auto_backup_job(context):
    """این تابع توسط job queue صدا زده میشه"""
    from config import PISHVA_ID
    fmt = await db.get_setting("auto_backup_format", "excel")
    period = await db.get_setting("auto_backup_period", "all")
    logger.info(f"Auto backup running: {fmt} / {period}")
    try:
        await send_backup(context.bot, PISHVA_ID, period, fmt)
        await db.log_action(PISHVA_ID, "auto_backup", f"بکاپ خودکار: {fmt}/{period}")
        # ثبتِ لحظه‌ی دقیقِ این بکاپِ موفق، تا اگر ربات بینِ این و بکاپِ
        # بعدی ری‌استارت شد، schedule_auto_backup بداند چقدر از موعدِ
        # واقعی باقی مانده و یک بکاپِ زودهنگامِ اضافه نفرستد.
        await db.set_setting("auto_backup_last_run", datetime.now().isoformat())
        # چندسرور: با همان زمان‌بندی/فرمت/بازه، بکاپِ هر مجموعه‌ی فعال هم (فایلِ جدا) برای مدیر ارشد
        try:
            ok, fail = await send_all_servers_backup(context.bot, PISHVA_ID, period, fmt, include_main=False)
            if ok or fail:
                logger.info(f"Auto backup of sub-servers: {ok} ok, {fail} failed")
        except Exception:
            logger.exception("auto backup of sub-servers failed")
    except Exception as e:
        logger.error(f"Auto backup job failed: {e}")
        try:
            await context.bot.send_message(
                chat_id=PISHVA_ID,
                text=f"❌ خطا در بکاپ خودکار:\n`{str(e)}`",
                parse_mode="Markdown"
            )
        except Exception:
            pass


def schedule_auto_backup(app, interval_hours: int = 24, _first_override: timedelta = None):
    """ثبت job بکاپ خودکار"""
    if not hasattr(app, 'job_queue') or app.job_queue is None:
        logger.warning("Job queue not available for auto backup")
        return False
    # حذف job قبلی اگر وجود داشت
    current = app.job_queue.get_jobs_by_name("auto_backup")
    for job in current:
        job.schedule_removal()

    if _first_override is not None:
        first = _first_override
    else:
        first = timedelta(seconds=30)

    # ثبت job جدید
    app.job_queue.run_repeating(
        auto_backup_job,
        interval=timedelta(hours=interval_hours),
        first=first,
        name="auto_backup"
    )
    logger.info(
        f"Auto backup scheduled every {interval_hours}h (first run in {first})"
    )
    return True


async def schedule_auto_backup_smart(app, interval_hours: int = 24):
    """
    نسخه‌ی «آگاه از تاریخچه»ی schedule_auto_backup: قبل از ثبتِ job، آخرین
    زمانِ بکاپِ موفق را از دیتابیس می‌خواند و first را طوری تنظیم می‌کند
    که سررسیدِ بعدی دقیقاً interval_hours ساعت پس از همان بکاپِ قبلی
    باشد — نه ۳۰ ثانیه پس از این ری‌استارت. برای فعال‌سازیِ اولیه یا
    زمانی که هنوز هیچ بکاپی ثبت نشده، همان رفتارِ قبلی (اجرای فوری بعد
    از ۳۰ ثانیه) حفظ می‌شود.
    این تابع را باید در startup ربات (bot.py) به‌جای فراخوانیِ مستقیمِ
    schedule_auto_backup صدا زد. توجه: خودِ schedule_auto_backup (sync)
    دست‌نخورده می‌ماند تا جاهایی که کاربر همین لحظه تایمر را عوض می‌کند
    (و طبیعتاً باید همین الان یک چرخه‌ی جدید شروع شود) رفتارِ قبلی‌شان
    را حفظ کنند.
    """
    last_run_raw = await db.get_setting("auto_backup_last_run", "")
    first = timedelta(seconds=30)
    if last_run_raw:
        try:
            last_run = datetime.fromisoformat(last_run_raw)
            elapsed = datetime.now() - last_run
            remaining = timedelta(hours=interval_hours) - elapsed
            # اگر موعدِ بعدی هنوز نرسیده، دقیقاً به‌اندازه‌ی باقی‌مانده صبر کن؛
            # حداقلِ ۳۰ ثانیه برای جلوگیری از first منفی/صفر.
            if remaining.total_seconds() > 30:
                first = remaining
        except (ValueError, TypeError):
            pass
    return schedule_auto_backup(app, interval_hours, _first_override=first)
