# -*- coding: utf-8 -*-
"""
rankings.py — رتبه‌بندیِ بازیکنان برای ربات و هاب (یک منبعِ مشترک).

ترتیبِ مرتب‌سازی (دقیقاً همین ترتیب):
  1) امتیاز مسابقات (برد=۱، مساوی=۰٫۵، باخت=۰) — بیشتر بالاتر.
     یعنی عادیِ امتیازِ بالاتر، بالاتر از برتر/ویژه‌ی امتیازِ کمتر است.
  2) فقط در صورت برابریِ امتیاز: مقام  ویژه > برتر > عادی.
  3) فقط در صورت برابریِ امتیاز و مقام:
     a) اخطار — کمتر بهتر. ویژه‌ها معاف‌اند (اخطارشان روی رتبه اثر ندارد؛ امتیازشان کامل محاسبه می‌شود).
     b) تعداد مسابقات — بیشتر بهتر.
     c) سختیِ حریف — میانگین امتیازِ حریف‌ها — بیشتر بهتر.
     d) نام، سپس شناسه (فقط برای ثبات).

فقط بازیکنانِ فعال (status='active') رتبه می‌گیرند.
"""
import asyncio
import logging

import database as db

logger = logging.getLogger(__name__)

TOP_OVERALL_N = 5   # تعداد نفرات برتر کل
TOP_CLASS_N = 3     # تعداد دانش‌آموزان برتر هر کلاس

TIER_RANK = {"special": 0, "elite": 1, "normal": 2}


def tier_of(is_elite, is_special):
    if is_special:
        return "special"
    if is_elite:
        return "elite"
    return "normal"


def _sort_key(r):
    return (
        -r["score"],                 # ۱) امتیاز
        TIER_RANK[r["tier"]],        # ۲) ویژه > برتر > عادی (فقط با امتیاز برابر)
        r["warn_eff"],               # ۳a) اخطار؛ ویژه‌ها صفر
        -r["games"],                 # ۳b) تعداد مسابقات
        -r["opp"],                   # ۳c) سختیِ حریف
        r["full_name"] or "",        # ۳d) ثبات
        r["id"],
    )


def _get(row, key, default=None):
    try:
        v = row[key]
    except (KeyError, IndexError):
        return default
    return default if v is None else v


async def _build_rows():
    players, matches = await asyncio.gather(db.get_all_players(), db.get_matches_by_filter("all"))
    players, matches = players or [], matches or []

    # مرحله‌ی ۱: امتیاز و تعداد بازی هرکس
    stats = {}
    results = []
    for m in matches:
        if not m or m["result"] not in ("white", "black", "draw"):
            continue
        w, b = m["white_player_id"], m["black_player_id"]
        results.append((w, b, m["result"]))
        for pid in (w, b):
            stats.setdefault(pid, {"games": 0, "wins": 0, "draws": 0, "losses": 0, "score": 0.0, "opp_sum": 0.0})
        stats[w]["games"] += 1
        stats[b]["games"] += 1
        if m["result"] == "white":
            stats[w]["wins"] += 1; stats[w]["score"] += 1; stats[b]["losses"] += 1
        elif m["result"] == "black":
            stats[b]["wins"] += 1; stats[b]["score"] += 1; stats[w]["losses"] += 1
        else:
            stats[w]["draws"] += 1; stats[w]["score"] += 0.5
            stats[b]["draws"] += 1; stats[b]["score"] += 0.5

    # مرحله‌ی ۲: سختیِ حریف = مجموعِ امتیازِ حریف‌ها (برای هر بازی)
    for w, b, _ in results:
        stats[w]["opp_sum"] += stats[b]["score"]
        stats[b]["opp_sum"] += stats[w]["score"]

    rows = []
    for p in players:
        if (_get(p, "status", "active") or "active") != "active":
            continue
        pid = p["id"]
        s = stats.get(pid, {"games": 0, "wins": 0, "draws": 0, "losses": 0, "score": 0.0, "opp_sum": 0.0})
        is_elite = bool(_get(p, "is_elite", 0))
        is_special = bool(_get(p, "is_special", 0))
        tier = tier_of(is_elite, is_special)
        warnings = int(_get(p, "warnings", 0) or 0)
        rows.append({
            "id": pid,
            "full_name": _get(p, "full_name", ""),
            "class_id": _get(p, "class_id"),
            "class_name": _get(p, "class_name", None) or "بدون کلاس",
            "is_elite": is_elite,
            "is_special": is_special,
            "tier": tier,
            "warnings": warnings,
            "warn_eff": 0 if tier == "special" else warnings,
            "games": s["games"],
            "wins": s["wins"],
            "draws": s["draws"],
            "losses": s["losses"],
            "score": s["score"],
            "opp": (s["opp_sum"] / s["games"]) if s["games"] else 0.0,
        })
    rows.sort(key=_sort_key)
    return rows


async def build_standings():
    """
    overall: همه‌ی بازیکنان فعال به ترتیب رتبه (هر ردیف با pos)
    by_class: {class_id: ردیف‌های همان کلاس با cpos}
    """
    rows = await _build_rows()
    overall = []
    for i, r in enumerate(rows, start=1):
        overall.append(dict(r, pos=i))
    by_class, class_names = {}, {}
    for r in overall:
        cid = r["class_id"]
        class_names[cid] = r["class_name"]
        lst = by_class.setdefault(cid, [])
        lst.append(dict(r, cpos=len(lst) + 1))
    return {"overall": overall, "by_class": by_class, "class_names": class_names, "total": len(overall)}


async def top_overall(n: int = TOP_OVERALL_N):
    st = await build_standings()
    return st["overall"][:n], st["total"]


async def top_by_class(class_id, n: int = TOP_CLASS_N):
    st = await build_standings()
    lst = st["by_class"].get(class_id, [])
    return lst[:n], len(lst)


async def position_of(player_id: int):
    st = await build_standings()
    me = next((r for r in st["overall"] if r["id"] == player_id), None)
    if not me:
        return None
    cls_list = st["by_class"].get(me["class_id"], [])
    cpos = next((r["cpos"] for r in cls_list if r["id"] == player_id), None)
    return {
        "class_name": me["class_name"],
        "class_pos": cpos, "class_total": len(cls_list),
        "overall_pos": me["pos"], "overall_total": st["total"],
    }
