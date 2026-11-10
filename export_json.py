"""Export the places DB to a compact JSON for the dashboard: python export_json.py [db] [out]."""
import json, sqlite3, sys, time

from ostan import fill_ostan

COLS = ["id", "name", "grp", "cat", "ostan", "city", "address", "phone", "rating", "nratings", "lat", "lng"]


def export(db, out):
    c = sqlite3.connect(db)
    fill_ostan(c)  # province of every place (big-city table + towns table), same logic as the Excel
    rows = [[i, n, g, ct, o or "", city, a, p, r, nr, la, ln]
            for i, n, ct, g, o, city, a, p, r, nr, la, ln in c.execute(
                "select id,name,cat,grp,ostan,city,address,phone,rating,nratings,lat,lng from places order by ostan,grp,city,name")]
    json.dump({"updated": int(time.time()), "cols": COLS, "rows": rows}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    return len(rows)


if __name__ == "__main__":
    print(export(*(sys.argv[1:3] or ["iran_food.db", "places.json"])), "places")
