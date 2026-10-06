# -*- coding: utf-8 -*-
"""
rankings.py — رتبه‌بندیِ بازیکنان برای ربات و هاب (یک منبعِ مشترک).

قانون رتبه‌بندی (برای همه‌ی فهرست‌ها یکسان است):
  1) بازیکنی که مسابقه داشته، قبل از بازیکنی که هنوز بازی نکرده قرار می‌گیرد.
  2) بر اساس امتیاز مسابقات: برد = ۱، مساوی = ۰٫۵، باخت = ۰.
  3) تساوی امتیاز: تعداد بردها، سپس ستاره‌ی برتر (is_elite)، سپس نیروی ویژه (is_special)، سپس نام.
  4) بازیکنی که هنوز بازی نکرده: فقط بر اساس برتر/ویژه بودن (برتر بالاتر از ویژه).

فقط بازیکنانِ فعال (status='active') در رتبه‌بندی شمرده می‌شوند.
"""
import asyncio
import logging

import database as db

logger = logging.getLogger(__name__)

TOP_OVERALL_N = 5   # تعداد نفرات برتر کل
TOP_CLASS_N = 3     # تعداد دانش‌آموزان برتر هر کلاس


def _sort_key(row):
    return (
        0 if row["games"] > 0 else 1,      # بازی‌کرده‌ها اول
        -row["score"],
        -row["wins"],
        -(1 if row["is_elite"] else 0),
        -(1 if row["is_special"] else 0),
        row["full_name"] or "",
        row["id"],
    )


async def _build_rows():
    players, matches = await asyncio.gather(db.get_all_players(), db.get_matches_by_filter("all"))
    players, matches = players or [], matches or []
    stats = {}
    for m in matches:
        if not m or m["result"] not in ("white", "black", "draw"):
            continue
        w, b = m["white_player_id"], m["black_player_id"]
        for pid in (w, b):
            stats.setdefault(pid, {"games": 0, "wins": 0, "draws": 0, "losses": 0, "score": 0.0})
        stats[w]["games"] += 1
        stats[b]["games"] += 1
        if m["result"] == "white":
            stats[w]["wins"] += 1; stats[w]["score"] += 1; stats[b]["losses"] += 1
        elif m["result"] == "black":
            stats[b]["wins"] += 1; stats[b]["score"] += 1; stats[w]["losses"] += 1
        else:
            stats[w]["draws"] += 1; stats[w]["score"] += 0.5
            stats[b]["draws"] += 1; stats[b]["score"] += 0.5

    rows = []
    for p in players:
        if (p["status"] or "active") != "active":
            continue
        s = stats.get(p["id"], {"games": 0, "wins": 0, "draws": 0, "losses": 0, "score": 0.0})
        rows.append({
            "id": p["id"],
            "full_name": p["full_name"],
            "class_id": p["class_id"],
            "class_name": p["class_name"] or "بدون کلاس",
            "is_elite": bool(p["is_elite"]),
            "is_special": bool(p["is_special"]),
            **s,
        })
    rows.sort(key=_sort_key)
    return rows


async def build_standings():
    """
    خروجیِ کامل:
      overall: فهرستِ همه‌ی بازیکنان فعال به ترتیبِ رتبه (هر ردیف یک dict با pos)
      by_class: {class_id: [ردیف‌های همان کلاس، به ترتیب، هرکدام با cpos]}
      class_names: {class_id: نام}
    """
    rows = await _build_rows()
    overall = []
    for i, r in enumerate(rows, start=1):
        r = dict(r, pos=i)
        overall.append(r)
    by_class = {}
    class_names = {}
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
    """
    جایگاهِ یک بازیکن: در کلاسِ خودش و در کل.
    خروجی dict یا None (اگه بازیکن فعال نیست یا وجود ندارد).
    """
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
