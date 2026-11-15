"""Merge collected Instagram pages into the places DB as another source (اینستاگرام).

Usage: python instagram_merge.py instagram-pages.json [iran_food.db]

Each page becomes a places row with id "ig:<handle>". At export time the shared
cross-source merge (common.merged_rows) attaches the page to a matching map shop
(same normalized name + same city) or keeps it as a standalone Instagram row.
Re-running is idempotent: existing ig: rows are updated, never duplicated.
"""
import json
import sqlite3
import sys

from common import classify
from ostan import fill_ostan


def load_pages(paths):
    by_handle = {}
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for r in json.load(f):
                h = (r.get("handle") or "").strip().lower()
                if not h:
                    continue
                prev = by_handle.get(h, {})
                phones = list(dict.fromkeys((prev.get("phones") or []) + (r.get("phones") or [])))
                cities = list(dict.fromkeys((prev.get("cities") or []) + (r.get("cities") or [])))
                cats = list(dict.fromkeys((prev.get("categories") or []) + (r.get("categories") or [])))
                by_handle[h] = {
                    "handle": h,
                    "name": (r.get("name") or prev.get("name") or h).strip(),
                    "url": r.get("url") or f"https://www.instagram.com/{h}/",
                    "phones": phones,
                    "cities": cities,
                    "categories": cats,
                }
    return list(by_handle.values())


def merge(db_path, pages):
    db = sqlite3.connect(db_path)
    try:
        db.execute("ALTER TABLE places ADD COLUMN instagram TEXT")
    except sqlite3.OperationalError:
        pass
    added = updated = 0
    for r in pages:
        pid = "ig:" + r["handle"]
        cat = " / ".join(r["categories"]) or "ادویه"
        grp = classify(r["name"], cat)
        city = r["cities"][0] if r["cities"] else None
        phone = "، ".join(r["phones"]) or None
        exists = db.execute("SELECT 1 FROM places WHERE id=?", (pid,)).fetchone()
        if exists:
            db.execute(
                "UPDATE places SET name=?, grp=?, cat=?, city=?, phone=?, instagram=? WHERE id=?",
                (r["name"], grp, cat, city, phone, r["url"], pid),
            )
            updated += 1
        else:
            db.execute(
                "INSERT INTO places (id,name,cat,grp,city,address,phone,rating,nratings,lat,lng,hours,hours_done,ostan,website,instagram) "
                "VALUES (?,?,?,?,?,NULL,?,NULL,NULL,NULL,NULL,NULL,0,NULL,NULL,?)",
                (pid, r["name"], cat, grp, city, phone, r["url"]),
            )
            added += 1
    fill_ostan(db)
    db.commit()
    total = db.execute("SELECT COUNT(*) FROM places WHERE id LIKE 'ig:%'").fetchone()[0]
    print(f"instagram merge: +{added} new, {updated} updated, {total} ig rows total")
    return added, updated, total


if __name__ == "__main__":
    args = sys.argv[1:]
    db = "iran_food.db"
    if args and args[-1].endswith(".db"):
        db = args.pop()
    if not args:
        print("usage: python instagram_merge.py <pages.json> [more.json ...] [iran_food.db]")
        sys.exit(1)
    merge(db, load_pages(args))