"""Iran food-supplier directory (حبوبات، ادویه، عطاری، خشکبار، پخش، عمده‌فروشی ...) -> Excel.  v2

Sources: Balad + Neshan (via the balad-mcp / neshan-mcp libraries). Resumable: progress lives in iran_food.db.
v2 is gentle on the servers: each source has its own pace that slows down after errors, speeds up again on
success and pauses (2, 4, 8 ... 30 min) when it looks blocked. Failed jobs are retried later, never in a loop.

Run:  uv run --with balad-mcp --with neshan-mcp --with openpyxl python iran_food_scraper.py --hours
Options:
  --cities تهران,مشهد   only these cities (default: all)
  --hours               after scraping, also fetch opening hours (slow: ~3 requests per place)
  --export              only rebuild the Excel from the saved data
  --retry               give failed jobs / places another chance
  --max-minutes N       stop cleanly after N minutes (used by the GitHub workflow)
  --smoke               one test call per source, exit code 3 if both fail
  --status              print how much work is left (add --hours to include opening hours)
Ctrl+C once = stop and save the Excel. Never press it twice or close the window while it says "saving".
Optional proxies (used by the libraries): set BALAD_MCP_PROXY / NESHAN_MCP_PROXY to an HTTP proxy URL.
"""
import argparse, asyncio, math, os, sqlite3, sys, time

from balad_mcp.maps import bl_nearby, bl_place
from neshan_mcp.maps import ns_place, ns_search

# city: (lat, lng, half_extent_km)  -- grid covers a square of +-extent around the centre
CITIES = {
    "تهران": (35.70, 51.38, 16), "کرج": (35.83, 50.98, 8), "مشهد": (36.30, 59.58, 10),
    "اصفهان": (32.65, 51.67, 8), "شیراز": (29.61, 52.54, 8), "تبریز": (38.08, 46.30, 8),
    "قم": (34.64, 50.88, 6), "اهواز": (31.32, 48.68, 8), "کرمانشاه": (34.31, 47.07, 5),
    "ارومیه": (37.55, 45.07, 5), "رشت": (37.28, 49.58, 5), "زاهدان": (29.50, 60.86, 5),
    "کرمان": (30.28, 57.08, 5), "همدان": (34.80, 48.51, 5), "یزد": (31.90, 54.37, 5),
    "اراک": (34.09, 49.69, 5), "اردبیل": (38.25, 48.30, 4), "بندرعباس": (27.19, 56.27, 5),
    "اسلامشهر": (35.55, 51.23, 4), "قزوین": (36.27, 50.00, 5), "زنجان": (36.68, 48.49, 4),
    "سنندج": (35.31, 47.00, 4), "خرم‌آباد": (33.49, 48.35, 4), "گرگان": (36.84, 54.43, 4),
    "ساری": (36.56, 53.06, 4), "بوشهر": (28.92, 50.84, 4), "ورامین": (35.32, 51.65, 3),
    "قدس و شهریار": (35.72, 51.10, 5), "بجنورد": (37.47, 57.33, 3), "بیرجند": (32.87, 59.22, 3),
    "ایلام": (33.64, 46.42, 3), "یاسوج": (30.67, 51.59, 3), "شهرکرد": (32.33, 50.86, 3),
    "سمنان": (35.58, 53.39, 3), "بابل": (36.55, 52.68, 3), "آمل": (36.47, 52.35, 3),
    "دزفول": (32.38, 48.40, 3), "نجف‌آباد": (32.63, 51.37, 3), "بروجرد": (33.90, 48.76, 3),
    "ساوه": (35.02, 50.36, 3), "خمینی‌شهر": (32.69, 51.52, 3), "سیرجان": (29.45, 55.68, 3),
    "رفسنجان": (30.41, 55.99, 3), "ملایر": (34.30, 48.82, 3), "مراغه": (37.39, 46.24, 3),
    "خوی": (38.55, 44.95, 3), "بم": (29.11, 58.36, 2), "قائمشهر": (36.46, 52.86, 3),
    "کاشان": (33.98, 51.41, 4), "آبادان": (30.34, 48.30, 4), "ماهشهر": (30.56, 49.20, 3),
    "بهبهان": (30.59, 50.24, 3), "ایرانشهر": (27.20, 60.69, 2), "چابهار": (25.29, 60.64, 2),
    "کیش": (26.54, 53.97, 3), "نیشابور": (36.21, 58.80, 3), "سبزوار": (36.21, 57.68, 3),
    "تربت حیدریه": (35.27, 59.22, 2), "قوچان": (37.11, 58.51, 2), "شاهرود": (36.42, 54.97, 3),
}
QUERIES = ["حبوبات فروشی", "ادویه فروشی", "medicinal-herbs-store", "nuts-store",
           "پخش مواد غذایی", "عمده فروشی مواد غذایی", "برنج فروشی"]
NS_QUERIES = ["حبوبات فروشی", "ادویه فروشی", "عطاری", "خشکبار", "پخش مواد غذایی", "عمده فروشی مواد غذایی", "برنج فروشی"]
STEP_KM, RADIUS_KM, LIMIT, MIN_RADIUS = 4.0, 2.4, 40, 0.3
BL_DELAY, NS_DELAY = 2.0, 1.5  # seconds between calls, per source (one Balad call = 2-3 HTTP requests)
WORKERS, MAX_TRIES, PAUSE_S, PAUSE_MAX = 2, 6, 120, 1800  # pause after 4 failures in a row: 2, 4, 8 ... 30 min
GROUPS = [  # first match wins
    ("حبوبات", ("حبوبات", "بقولات")), ("ادویه", ("ادویه", "ادویه‌جات")),
    ("عطاری/گیاهان دارویی", ("عطاری", "گیاهان دارویی", "گیاه")),
    ("خشکبار/آجیل", ("خشکبار", "آجیل")), ("برنج", ("برنج",)),
    ("پخش/عمده/بنکداری", ("پخش", "عمده", "بنکدار", "انبار", "بازرگانی", "بورس")),
]  # plain supermarkets / grocery shops fall into "سایر" and are left out of the export


def classify(name, cat):
    t = f"{name} {cat}"
    for g, keys in GROUPS:
        if any(k in t for k in keys):
            return g
    return "سایر"


assert classify("ادویه فروشی محمدی", "ادویه فروشی") == "ادویه" and classify("x", "y") == "سایر"

db = sqlite3.connect("iran_food.db")
db.executescript("""
CREATE TABLE IF NOT EXISTS jobs(city TEXT, lat REAL, lng REAL, r REAL, q TEXT, done INTEGER DEFAULT 0, PRIMARY KEY(city,lat,lng,r,q));
CREATE TABLE IF NOT EXISTS places(id TEXT PRIMARY KEY, name TEXT, cat TEXT, grp TEXT, city TEXT, address TEXT, phone TEXT,
  rating REAL, nratings INT, lat REAL, lng REAL, hours TEXT, hours_done INTEGER DEFAULT 0);
""")
try:  # first start of v2 on a v1 database
    db.execute("ALTER TABLE jobs ADD COLUMN tries INTEGER DEFAULT 0")
    # v1 split Neshan boxes into 4 sub-boxes forever; Neshan ranks by distance, not by box, so those ~9000 jobs were redundant
    db.execute("DELETE FROM jobs WHERE done=0 AND q LIKE 'ns:%' AND ROUND(r,2) IN (1.2,0.6,0.3)")
    db.commit()
except sqlite3.OperationalError:
    pass


def grid(city):
    la, ln, e = CITIES[city]
    n = max(1, round(2 * e / STEP_KM))
    for i in range(n):
        for j in range(n):
            dy = -e + STEP_KM / 2 + i * STEP_KM if n > 1 else 0
            dx = -e + STEP_KM / 2 + j * STEP_KM if n > 1 else 0
            yield round(la + dy / 111.0, 4), round(ln + dx / (111.0 * math.cos(math.radians(la))), 4)


def seed(cities):
    for c in cities:
        for la, ln in grid(c):
            for q in QUERIES + ["ns:" + x for x in NS_QUERIES]:
                db.execute("INSERT OR IGNORE INTO jobs(city,lat,lng,r,q) VALUES(?,?,?,?,?)", (c, la, ln, RADIUS_KM, q))
    # the Tehran bazaar / Molavi / Khayyam wholesale belt is far denser than the rest: extra fine grid
    if "تهران" in cities:
        for i in range(-3, 4):
            for j in range(-3, 4):
                for q in QUERIES + ["ns:" + x for x in NS_QUERIES]:
                    db.execute("INSERT OR IGNORE INTO jobs(city,lat,lng,r,q) VALUES(?,?,?,?,?)",
                               ("تهران", round(35.668 + i * 0.0072, 4), round(51.42 + j * 0.0088, 4), 0.5, q))
    db.commit()


def save(city, places):
    for p in places:
        g = classify(p.get("name") or "", p.get("category") or "")
        # no supermarkets, and drop unrelated hits from free-text search
        if not p.get("id") or g == "سایر" or ("مارکت" in (p.get("category") or "") and g == "خواربار/مواد غذایی"):
            continue
        db.execute("INSERT OR IGNORE INTO places(id,name,cat,grp,city,address,phone,rating,nratings,lat,lng) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (p["id"], p.get("name"), p.get("category"), g, city,
                    p.get("address"), p.get("phone"), p.get("rating"), p.get("ratings", p.get("votes")), p.get("lat"), p.get("lng")))


class Gate:
    """Pacing for one source: slows down after errors, speeds up on success, pauses when it looks blocked."""

    def __init__(self, name, delay):
        self.name, self.base, self.delay = name, delay, delay
        self.next = self.pause_until = 0.0
        self.streak = self.errors = 0

    async def wait(self):
        now = time.monotonic()
        t = max(self.next, self.pause_until, now)
        self.next = t + self.delay  # slot reserved before any await, so workers never share one
        await asyncio.sleep(t - now)

    def ok(self):
        self.streak = 0
        self.delay = max(self.base, self.delay * 0.9)

    def bad(self, err):
        self.errors += 1
        self.streak += 1
        self.delay = min(30.0, self.delay * 1.3)  # stays put at ~30% errors, slows beyond that
        with open("errors.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {self.name} {err}\n")
        if self.streak >= 4:
            wait = min(PAUSE_MAX, PAUSE_S * 2 ** (self.streak - 4))
            self.pause_until = time.monotonic() + wait
            print(f"{time.strftime('%H:%M:%S')} {self.name}: {self.streak} errors in a row ({err[:80]}) - pausing {wait / 60:.1f} min", flush=True)


GATES = {"bl": Gate("Balad", BL_DELAY), "ns": Gate("Neshan", NS_DELAY)}


async def call(src, fn, **kw):
    g = GATES[src]
    await g.wait()
    try:
        r = await fn(**kw)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        if "404" not in msg:  # a missing place says nothing about being blocked
            g.bad(msg[:200])
        raise
    g.ok()
    return r


async def run_job(row):
    rid, city, la, ln, r, q = row
    src = "ns" if q.startswith("ns:") else "bl"
    try:
        if src == "ns":  # Neshan: no radius, the 30 nearest to the point
            res = (await call("ns", ns_search, query=q[3:], near=f"{la},{ln}", limit=30))["results"]
            full = len(res) >= 30 and max(p.get("distance_m") or 0 for p in res) < r * 1000  # all 30 inside the box: maybe more
        else:
            d = await call("bl", bl_nearby, what=q, lat=la, lng=ln, radius_km=r, limit=LIMIT)
            res, full = d["places"], len(d["places"]) >= LIMIT or d.get("found", 0) > LIMIT
    except Exception:
        db.execute("UPDATE jobs SET tries=tries+1 WHERE rowid=?", (rid,))
        return
    save(city, res)
    if full and r / 2 >= MIN_RADIUS:  # dense spot: 4 sub-boxes so nothing is cut off by the cap
        h = r / 2
        dlat, dlng = h / 111.0, h / (111.0 * math.cos(math.radians(la)))
        for sy in (-1, 1):
            for sx in (-1, 1):
                db.execute("INSERT OR IGNORE INTO jobs(city,lat,lng,r,q) VALUES(?,?,?,?,?)",
                           (city, round(la + sy * dlat, 5), round(ln + sx * dlng, 5), round(h, 3), q))
    db.execute("UPDATE jobs SET done=1 WHERE rowid=?", (rid,))


busy = set()
DEADLINE = None  # unix time after which workers take no new work


async def pool(sql, one):
    """Worker: take the next free row (fewest failures first), run it, repeat until none are left."""
    while True:
        if DEADLINE and time.time() > DEADLINE:
            return
        rows = db.execute(sql).fetchall()
        row = next((r for r in rows if r[0] not in busy), None)
        if row is None:
            if rows:  # the rest is in the hands of other workers
                await asyncio.sleep(1)
                continue
            return
        busy.add(row[0])
        try:
            await one(row)
        finally:
            busy.discard(row[0])


async def progress(kind, t0, d0):
    while True:
        await asyncio.sleep(30)
        db.commit()
        if kind == "jobs":
            total, done, gave_up = db.execute("SELECT COUNT(*), SUM(done), SUM(done=0 AND tries>=?) FROM jobs", (MAX_TRIES,)).fetchone()
        else:
            total, done = db.execute("SELECT COUNT(*), SUM(hours_done=1) FROM places WHERE grp!='سایر'").fetchone()
            gave_up = db.execute("SELECT COUNT(*) FROM places WHERE hours_done<=-3").fetchone()[0]
        done, todo = done or 0, total - (gave_up or 0)
        rate = (done - d0) / max(1.0, time.time() - t0)
        eta = f"{(todo - done) / rate / 3600:.1f}h" if rate > 0 else "?"
        n = db.execute("SELECT COUNT(*) FROM places").fetchone()[0]
        g = GATES
        print(f"[{100 * done / max(1, todo):5.1f}%] {kind} {done}/{todo} | places {n} | gave up {gave_up} | "
              f"Balad {g['bl'].delay:.1f}s ({g['bl'].errors} err) Neshan {g['ns'].delay:.1f}s ({g['ns'].errors} err) | ETA {eta}", flush=True)


async def scrape():
    d0 = db.execute("SELECT SUM(done) FROM jobs").fetchone()[0] or 0
    prog = asyncio.create_task(progress("jobs", time.time(), d0))
    base = "SELECT rowid,city,lat,lng,r,q FROM jobs WHERE done=0 AND tries<%d AND %%s ORDER BY tries, rowid LIMIT 8" % MAX_TRIES
    try:
        await asyncio.gather(*(pool(base % c, run_job) for c in ("q NOT LIKE 'ns:%'", "q LIKE 'ns:%'") for _ in range(WORKERS)))
    finally:
        prog.cancel()
        db.commit()


async def hours_one(row):
    pid = row[0]
    try:
        if len(pid) == 32:  # Neshan id
            d = await call("ns", ns_place, place_id=pid)
            txt = "؛ ".join(d.get("hours") or []) or (d.get("open") or "")
        else:
            d = await call("bl", bl_place, id=pid, reviews=0, photos=0)
            txt = "؛ ".join(f"{k}: {v}" for k, v in (d.get("hours") or {}).items()) or (d.get("hours_status") or "")
    except Exception:
        db.execute("UPDATE places SET hours_done=hours_done-1 WHERE id=?", (pid,))  # 0 = todo, 1 = done, -1..-3 = failed
        return
    ph = "، ".join(d.get("phones") or [])
    db.execute("UPDATE places SET hours=?, hours_done=1, phone=COALESCE(NULLIF(?,''), phone) WHERE id=?", (txt, ph, pid))


async def fetch_hours():
    d0 = db.execute("SELECT SUM(hours_done=1) FROM places").fetchone()[0] or 0
    prog = asyncio.create_task(progress("hours", time.time(), d0))
    base = "SELECT id FROM places WHERE hours_done<=0 AND hours_done>-3 AND grp!='سایر' AND %s ORDER BY hours_done DESC, rowid LIMIT 8"
    try:
        await asyncio.gather(*(pool(base % c, hours_one) for c in ("length(id)!=32", "length(id)=32") for _ in range(WORKERS)))
    finally:
        prog.cancel()
        db.commit()


def export(path="iran_food_directory.xlsx"):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "همه"
    ws.sheet_view.rightToLeft = True
    head = ["ردیف", "نام", "گروه", "دسته", "شهر", "نشانی", "تلفن", "ساعت کار", "امتیاز", "تعداد امتیاز", "lat", "lng", "منبع"]
    raw = db.execute("SELECT name,grp,cat,city,address,phone,hours,rating,nratings,lat,lng,id FROM places WHERE grp!='سایر' ORDER BY city,grp,name").fetchall()
    seen, rows = {}, []
    for r in raw:  # same shop in Balad and Neshan (same name, ~100 m): keep one, fill gaps from the other
        k = (" ".join((r[0] or "").split()), round(r[9] or 0, 3), round(r[10] or 0, 3))
        src = "نشان" if len(r[11]) == 32 else "بلد"
        if k in seen:
            o = rows[seen[k]]
            rows[seen[k]] = [o[i] or r[i] for i in range(11)] + ["+".join(dict.fromkeys(f"{o[11]}+{src}".split("+")))]
        else:
            seen[k] = len(rows)
            rows.append(list(r[:11]) + [src])
    ws.append(head)
    for i, r in enumerate(rows, 1):
        ws.append([i, *r])
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F4E78")
    for col, w in zip("ABCDEFGHIJKLM", (6, 38, 20, 18, 14, 55, 18, 40, 8, 10, 10, 10, 10)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes, ws.auto_filter.ref = "A2", ws.dimensions
    tmp = path + ".tmp"
    wb.save(tmp)  # write aside, then swap: an interrupted save can never destroy the old file
    try:
        os.replace(tmp, path)
    except PermissionError:  # the old file is open in Excel
        path = path.replace(".xlsx", time.strftime("_%H%M%S.xlsx"))
        os.replace(tmp, path)
    n_phone = sum(1 for r in rows if r[5])
    print(f"saved {path}: {len(rows)} places, {n_phone} with phone")


async def smoke():
    ok = []
    for name, fn, kw in (("Balad", bl_nearby, dict(what="ادویه فروشی", lat=35.67, lng=51.42, radius_km=1, limit=3)),
                         ("Neshan", ns_search, dict(query="ادویه فروشی", near="35.67,51.42", limit=3))):
        try:
            r = await fn(**kw)
            n = len(r.get("places") or r.get("results") or [])
            print(f"{name}: OK ({n} results)", flush=True)
            ok.append(name)
        except Exception as e:
            print(f"{name}: FAILED {type(e).__name__}: {str(e)[:200]}", flush=True)
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cities", default="")
    ap.add_argument("--hours", action="store_true")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--retry", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=0)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.smoke:
        sys.exit(0 if asyncio.run(smoke()) else 3)
    if a.status:
        n = db.execute("SELECT COUNT(*) FROM jobs WHERE done=0 AND tries<?", (MAX_TRIES,)).fetchone()[0]
        if a.hours:
            n += db.execute("SELECT COUNT(*) FROM places WHERE hours_done<=0 AND hours_done>-3 AND grp!='سایر'").fetchone()[0]
        print(n)
        sys.exit(0)
    if a.max_minutes:
        DEADLINE = time.time() + a.max_minutes * 60
    if a.retry:
        db.execute("UPDATE jobs SET tries=0 WHERE done=0")
        db.execute("UPDATE places SET hours_done=0 WHERE hours_done<0")
        db.commit()
    if not a.export:
        sel = [c.strip() for c in a.cities.split(",") if c.strip()] or list(CITIES)
        bad = [c for c in sel if c not in CITIES]
        sys.exit(f"unknown city: {bad}") if bad else None
        seed(sel)
        try:
            asyncio.run(scrape())
            print("scraping finished - saving Excel...", flush=True)
            export()  # a usable file exists before the slow hours phase starts
            if a.hours and not (DEADLINE and time.time() > DEADLINE):
                asyncio.run(fetch_hours())
        except KeyboardInterrupt:
            print("stopped - progress is saved.")
        db.commit()
    print("saving Excel - please wait, do not close the window...", flush=True)
    export()
