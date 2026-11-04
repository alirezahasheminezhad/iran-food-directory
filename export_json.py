"""Export the places DB to a compact JSON for the dashboard: python export_json.py [db] [out]."""
import json, sqlite3, sys, time

COLS = ["id", "name", "grp", "cat", "ostan", "city", "address", "phone", "rating", "nratings", "lat", "lng"]


def export(db, out):
    ostan = {o["id"]: o["name"] for o in json.load(open("data/ostan.json", encoding="utf-8"))}
    prov = {s["name"]: ostan.get(s["ostan"], "") for s in json.load(open("data/shahr.json", encoding="utf-8"))}
    c = sqlite3.connect(db)
    rows = [[i, n, g, ct, prov.get(city, ""), city, a, p, r, nr, la, ln]
            for i, n, ct, g, city, a, p, r, nr, la, ln in c.execute(
                "select id,name,cat,grp,city,address,phone,rating,nratings,lat,lng from places order by grp,city,name")]
    json.dump({"updated": int(time.time()), "cols": COLS, "rows": rows}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    return len(rows)


if __name__ == "__main__":
    print(export(*(sys.argv[1:3] or ["iran_food.db", "places.json"])), "places")
