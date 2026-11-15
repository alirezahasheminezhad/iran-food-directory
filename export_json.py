"""Export the places DB to a compact JSON for the dashboard: python export_json.py [db] [out].
Uses the same cross-source merge as the Excel export (one shop = one row, gaps filled)."""
import json, os, sqlite3, sys, time

from common import merged_rows

COLS = ["name", "grp", "cat", "ostan", "city", "address", "phone", "hours", "rating", "nratings", "lat", "lng", "website", "instagram", "src"]


def export(db, out):
    c = sqlite3.connect(db)
    rows = merged_rows(c)  # provinces filled, duplicates across Balad/Neshan/Google merged
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"updated": int(time.time()), "cols": COLS, "rows": rows}, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out)  # an interrupted write can never destroy the old file
    return len(rows)


if __name__ == "__main__":
    print(export(*(sys.argv[1:3] or ["iran_food.db", "places.json"])), "places")