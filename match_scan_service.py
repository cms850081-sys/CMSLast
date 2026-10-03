"""
match_scan_service.py — منطقِ مشترکِ «ثبت نتیجه با عکسِ برگه» برای هاب (وب) و ربات (تلگرام).

  • scan_sheet():   عکس ← Gemini ← فهرستِ ردیف‌های بازبینی (چیزی ثبت نمی‌شود).
  • commit_items(): ثبتِ واقعیِ مسابقه‌ها (+ نتیجه و Elo). فقط مدیر ارشد مستقیم صدایش می‌زند؛
                    برای مدیر مسابقات فقط بعد از تأییدِ مدیر ارشد (approve_scan_request) اجرا می‌شود.
  • submit_for_approval(): ذخیره‌ی درخواست برای مدیر ارشد (بدونِ ساختنِ هیچ مسابقه‌ای).
"""

import json
import logging
from datetime import date

import database as db
import elo
from helpers import today_gregorian

logger = logging.getLogger(__name__)

COMMIT_CHUNK = 20
MAX_ITEMS = 80
_busy = set()          # هر اسکن هزینه دارد؛ جلوگیری از دوبار-زدن


class ScanError(Exception):
    def __init__(self, code, message, status=400, detail=""):
        super().__init__(message)
        self.code, self.message, self.status, self.detail = code, message, status, detail


def valid_date(s) -> str:
    s = str(s or "").strip()[:10]
    try:
        date.fromisoformat(s)
        return s
    except ValueError:
        raise ValueError("تاریخ نامعتبر است.")


async def resolve_tournament(tid):
    """tid خالی ← تورنمنتِ پیش‌فرض. اگر آی‌دی داده شده ولی وجود ندارد ValueError."""
    if tid:
        t = await db.get_tournament(int(tid))
        if not t:
            raise ValueError("مسابقه/تورنمنت پیدا نشد.")
        return t["id"]
    dt = await db.get_default_tournament()
    return dt["id"] if dt else None


# ───────────────────────── خواندنِ عکس ─────────────────────────
async def scan_sheet(raw: bytes, mime: str, uid: int) -> dict:
    """عکسِ خام (bytes) ← review: {items, date_text, sheet_note, today}. خطا: ScanError."""
    import match_vision as mv
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in mv.ALLOWED_MIME:
        raise ScanError("bad_image", "فرمتِ عکس پشتیبانی نمی‌شود (JPG، PNG یا WebP).")
    if len(raw) < 2000:
        raise ScanError("bad_image", "عکس خالی یا بسیار کوچک است.")
    if len(raw) > 6 * 1024 * 1024:
        raise ScanError("bad_image", "حجمِ عکس زیاد است (حداکثر ۶ مگابایت).")
    if uid in _busy:
        raise ScanError("busy", "عکسِ قبلی هنوز در حال خوانده‌شدن است.", 429)
    _busy.add(uid)          # قبل از هر await، تا دو درخواستِ هم‌زمان رد نشوند
    try:
        players = [p for p in await db.get_all_players() if (p["status"] or "active") == "active"]
        roster = mv.make_roster(players)
        try:
            data = await mv.extract_matches(raw, mime, [r["name"] for r in roster])
        except mv.VisionError as e:
            raise ScanError("ai_" + e.code, e.message, 503 if e.code in ("no_key", "auth", "model") else 502,
                            detail=getattr(e, "detail", ""))
    finally:
        _busy.discard(uid)
    review = mv.build_review(data, roster)
    if not review["items"]:
        raise ScanError("nothing_found", "در عکس مسابقه‌ای پیدا نشد. عکسِ واضح‌تر و کامل‌تری بفرستید.", 422)
    today = today_gregorian()
    exist = {(r["white_player_id"], r["black_player_id"]) for r in await db.get_matches_on_date(today)}
    for it in review["items"]:
        it["dup"] = bool(it["w"]["id"] and it["b"]["id"] and (it["w"]["id"], it["b"]["id"]) in exist)
    review["today"] = today
    await db.log_action(uid, "scan_matches", f"خواندنِ عکسِ برگه: {len(review['items'])} ردیف")
    return review


# ───────────────────────── اعتبارسنجی و ثبت ─────────────────────────
def clean_items(items) -> list:
    """فقط فیلدهای لازم را نگه می‌دارد و شکلِ ورودی را چک می‌کند (ورودیِ کاربر قابلِ‌اعتماد نیست)."""
    if not isinstance(items, list) or not items:
        raise ValueError("موردی برای ثبت نیست.")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"حداکثر {MAX_ITEMS} مسابقه در هر عکس.")
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise ValueError("ساختارِ ردیف‌ها نامعتبر است.")
        try:
            wid, bid = int(it.get("white_id")), int(it.get("black_id"))
        except (TypeError, ValueError):
            raise ValueError(f"ردیف {i + 1}: سفید/سیاه مشخص نیست.")
        res = it.get("result") or None
        if res not in (None, "white", "black", "draw"):
            raise ValueError(f"ردیف {i + 1}: نتیجه‌ی نامعتبر.")
        if wid == bid:
            raise ValueError(f"ردیف {i + 1}: سفید و سیاه یک نفر است.")
        idx = it.get("i")
        out.append({"i": idx if isinstance(idx, int) else i, "white_id": wid, "black_id": bid, "result": res})
    return out


async def commit_items(items, match_date: str, tournament_id, uid: int):
    """ثبتِ واقعیِ مسابقه‌ها به ترتیبِ برگه (Elo به ترتیبِ بازی‌ها حساب می‌شود).
    تک‌به‌تک؛ خطای یک ردیف بقیه را نمی‌خوابانَد. خروجی: (created, failed[{i,message}])."""
    pmap = {p["id"]: p for p in await db.get_all_players()}
    created, failed = 0, []
    try:
        await elo.ensure_elo_table()
    except Exception:
        logger.warning("scan: ensure_elo_table failed", exc_info=True)
    for it in items:
        idx = it.get("i")
        try:
            wid, bid, res = it["white_id"], it["black_id"], it.get("result") or None
            if res not in (None, "white", "black", "draw"):
                raise ValueError("نتیجه‌ی نامعتبر.")
            if wid == bid:
                raise ValueError("سفید و سیاه یک نفر است.")
            for pid_ in (wid, bid):
                p = pmap.get(pid_)
                if not p:
                    raise ValueError("بازیکن پیدا نشد.")
                if (p["status"] or "active") != "active":
                    raise ValueError(f"«{p['full_name']}» فعال نیست.")
            mid = await db.create_match(wid, bid, match_date, tournament_id, uid)
            if res:
                reason = "توافقی" if res == "draw" else ""
                fresh = await db.get_match(mid)
                await db.record_match_result(mid, res, reason, uid, match=fresh)
                try:
                    await elo.update_elo_after_match(wid, bid, res, mid)
                except Exception:
                    logger.warning("scan: Elo update failed", exc_info=True)
            created += 1
        except Exception as e:
            msg = getattr(e, "message", None) or str(e) or "خطا"
            logger.warning("scan: commit item %s failed: %s", idx, msg)
            failed.append({"i": idx, "message": str(msg)[:120]})
    return created, failed


# ───────────────────────── جریانِ تأیید ─────────────────────────
async def submit_for_approval(admin_id: int, items, match_date: str, tournament_id) -> int:
    """درخواست را ذخیره می‌کند. هیچ مسابقه‌ای ساخته نمی‌شود."""
    items = clean_items(items)
    match_date = valid_date(match_date)
    tournament_id = await resolve_tournament(tournament_id)
    payload = json.dumps({"items": items}, ensure_ascii=False)
    return await db.create_scan_request(admin_id, payload, len(items), match_date, tournament_id)


async def request_rows(req) -> list:
    """برای نمایش: [{w, b, res}] با نامِ بازیکن‌ها (یک کوئری برای همه)."""
    try:
        items = json.loads(req["payload"] or "{}").get("items", [])
    except Exception:
        items = []
    names = await db.get_players_names([x for it in items for x in (it.get("white_id"), it.get("black_id"))])
    return [{"w": names.get(it.get("white_id"), "؟"), "b": names.get(it.get("black_id"), "؟"),
             "res": it.get("result")} for it in items]


async def approve_scan_request(req_id: int, approver_uid: int):
    """(ok, created, failed, req). ok=False یعنی درخواست قبلاً بررسی شده/وجود ندارد."""
    req = await db.get_scan_request(req_id)
    if not req or req["status"] != "pending":
        return False, 0, [], req
    if not await db.claim_scan_request(req_id, "approved", approver_uid):
        return False, 0, [], req
    try:
        items = clean_items(json.loads(req["payload"] or "{}").get("items"))
    except Exception:
        await db.set_scan_request_result(req_id, "payload نامعتبر")
        return True, 0, [{"i": None, "message": "اطلاعاتِ درخواست خراب است."}], req
    created, failed = await commit_items(items, req["match_date"] or today_gregorian(),
                                         req["tournament_id"], req["admin_id"])
    await db.set_scan_request_result(req_id, f"created={created} failed={len(failed)}")
    await db.log_action(approver_uid, "scan_approve",
                        f"تأییدِ ثبت با عکس (درخواستِ {req['admin_id']}): {created} مسابقه", req["admin_id"])
    return True, created, failed, req


async def reject_scan_request(req_id: int, approver_uid: int):
    """(ok, req). هیچ مسابقه‌ای ساخته نمی‌شود."""
    req = await db.get_scan_request(req_id)
    if not req or req["status"] != "pending":
        return False, req
    if not await db.claim_scan_request(req_id, "rejected", approver_uid):
        return False, req
    await db.log_action(approver_uid, "scan_reject",
                        f"ردِ ثبت با عکس (درخواستِ {req['admin_id']})", req["admin_id"])
    return True, req
