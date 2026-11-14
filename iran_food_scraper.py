"""Iran food-supplier directory (حبوبات، ادویه، عطاری، خشکبار، پخش، عمده‌فروشی ...) -> Excel.  v2

Sources: Balad + Neshan (via the balad-mcp / neshan-mcp libraries), optionally Google Maps via
google_enrich.py (local, gentle, resumable). Resumable: progress lives in iran_food.db.
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
  --merge FILE          add the places of another scraper database and mark its finished jobs as done here
  --status              print how much work is left (add --hours to include opening hours)
  --coverage            per province: towns looked up, towns not found on the map, places found
Every place gets its province (استان) automatically; towns that are still busy at the edge of their search circle
get rings of extra circles (grow), and towns Balad did not find are looked up again with looser matching.
Ctrl+C once = stop and save the Excel. Never press it twice or close the window while it says "saving".
Optional proxies (used by the libraries): set BALAD_MCP_PROXY / NESHAN_MCP_PROXY to an HTTP proxy URL.
"""
import argparse, asyncio, json, math, os, sqlite3, sys, time

from balad_mcp.maps import bl_nearby, bl_place, bl_search
from neshan_mcp.maps import ns_place, ns_search
from common import CITIES, classify, key as _key, norm as _norm, merged_rows
from ostan import CITY_OSTAN, fill_ostan, km

assert set(CITIES) == set(CITY_OSTAN), "every city in CITIES needs its province in ostan.py"
QUERIES = ["حبوبات فروشی", "ادویه فروشی", "medicinal-herbs-store", "nuts-store",
           "پخش مواد غذایی", "عمده فروشی مواد غذایی", "برنج فروشی"]
NS_QUERIES = ["حبوبات فروشی", "ادویه فروشی", "عطاری", "خشکبار", "پخش مواد غذایی", "عمده فروشی مواد غذایی", "برنج فروشی"]
STEP_KM, RADIUS_KM, LIMIT, MIN_RADIUS = 4.0, 2.4, 40, 0.3
BL_DELAY, NS_DELAY = 2.0, 6.0  # seconds between calls, per source (one Balad call = 2-3 HTTP requests)
WORKERS, MAX_TRIES, PAUSE_S, PAUSE_MAX = 2, 6, 120, 1800  # pause after 4 failures in a row: 2, 4, 8 ... 30 min

assert classify("ادویه فروشی محمدی", "ادویه فروشی") == "ادویه" and classify("x", "y") == "سایر"
assert classify("Herbal medicine store", "Herbal medicine store") == "عطاری/گیاهان دارویی"

HERE = os.path.dirname(os.path.abspath(__file__))
db = sqlite3.connect(os.path.join(HERE, "iran_food.db"))
db.executescript("""
CREATE TABLE IF NOT EXISTS jobs(city TEXT, lat REAL, lng REAL, r REAL, q TEXT, done INTEGER DEFAULT 0, PRIMARY KEY(city,lat,lng,r,q));
CREATE TABLE IF NOT EXISTS places(id TEXT PRIMARY KEY, name TEXT, cat TEXT, grp TEXT, city TEXT, address TEXT, phone TEXT,
  rating REAL, nratings INT, lat REAL, lng REAL, hours TEXT, hours_done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS towns(name TEXT, ostan TEXT, lat REAL, lng REAL, county TEXT, PRIMARY KEY(name,ostan));
CREATE INDEX IF NOT EXISTS places_city ON places(city);
""")
try:  # first start of v2 on a v1 database
    db.execute("ALTER TABLE jobs ADD COLUMN tries INTEGER DEFAULT 0")
    # v1 split Neshan boxes into 4 sub-boxes forever; Neshan ranks by distance, not by box, so those ~9000 jobs were redundant
    db.execute("DELETE FROM jobs WHERE done=0 AND q LIKE 'ns:%' AND ROUND(r,2) IN (1.2,0.6,0.3)")
    db.commit()
except sqlite3.OperationalError:
    pass
try:  # website column (filled by --hours and by the Google import)
    db.execute("ALTER TABLE places ADD COLUMN website TEXT")
    db.commit()
except sqlite3.OperationalError:
    pass
if db.execute("PRAGMA user_version").fetchone()[0] < 1:
    # towns Balad "did not find" were mostly a spelling mismatch ("سیستان وبلوچستان" vs "سیستان و بلوچستان"): ask again, once
    db.execute("DELETE FROM towns WHERE lat IS NULL")
    db.commit()
    db.execute("PRAGMA user_version=1")


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


TOWN_Q = QUERIES + ["ns:" + x for x in ("عطاری", "حبوبات فروشی")]  # small towns: Neshan gets 2 queries, it rate-limits hardest


def _shahr():
    """Every city of data/shahr.json (1324) as (name, province)."""
    here = os.path.dirname(os.path.abspath(__file__))
    ostan = {o["id"]: o["name"] for o in json.load(open(os.path.join(here, "data/ostan.json"), encoding="utf-8"))}
    return [(x["name"], ostan[x["ostan"]]) for x in json.load(open(os.path.join(here, "data/shahr.json"), encoding="utf-8"))]


def todo_towns():
    done = {(a, b) for a, b in db.execute("SELECT name,ostan FROM towns")}
    skip = {_norm(c) for c in CITIES}
    return [t for t in _shahr() if t not in done and _norm(t[0]) not in skip]


async def discover():
    """Every city of data/shahr.json that the 60 big-city grids do not already cover -> coordinates via Balad search."""
    for name, prov in todo_towns():
        if DEADLINE and time.time() > DEADLINE:
            return
        hit = None
        try:
            for q, kinds in ((f"شهر {name} استان {prov}", ("city",)), (f"{name} {prov}", ("city", "region"))):
                rows = (await call("bl", bl_search, query=q, limit=8))["results"]
                hit = next((r for r in rows if r.get("type") in kinds and _key(name) in _key(r.get("name"))
                            and _key(prov) in _key(r.get("address"))), None)
                if hit:
                    break
        except Stop:
            return
        except Exception:
            continue  # tried again on the next run
        if hit:
            county = (hit.get("address") or "").split("شهرستان")[-1].strip()
            db.execute("INSERT OR IGNORE INTO towns VALUES(?,?,?,?,?)", (name, prov, hit["lat"], hit["lng"], county))
        else:  # not found: remember with no coordinates so we do not ask again
            db.execute("INSERT OR IGNORE INTO towns VALUES(?,?,NULL,NULL,NULL)", (name, prov))
        db.commit()


def seed_towns():
    """One job set per town found, unless it lies inside an already-gridded big city."""
    big = [(la, ln, e + 1.5) for la, ln, e in CITIES.values()]
    for name, prov, la, ln in db.execute("SELECT name,ostan,lat,lng FROM towns WHERE lat IS NOT NULL").fetchall():
        if any(abs(la - a) * 111 < e and abs(ln - b) * 111 * math.cos(math.radians(a)) < e for a, b, e in big):
            continue
        for q in TOWN_Q:
            db.execute("INSERT OR IGNORE INTO jobs(city,lat,lng,r,q) VALUES(?,?,?,?,?)", (name, round(la, 4), round(ln, 4), 3.0, q))
    db.commit()


RING_KM, MAX_RING, EDGE_MIN = 5.0, 2, 5  # extra circles 5 km and 10 km out; a ring is added when >=5 shops sit in the outer 1.5 km


def grow():
    """A town whose outer 1.5 km band still holds shops is bigger than the 3 km circle searched: add a ring of circles
    around it (8 at 5 km, then 16 at 10 km; bigger cities are in CITIES). Returns True when jobs were added."""
    added = 0
    for name, la, ln in db.execute("SELECT name,lat,lng FROM towns WHERE lat IS NOT NULL").fetchall():
        # searched radius so far = farthest job edge; jobs of a same-named town in another province lie > 15 km away
        R = max((d + r for d, r in ((km(la, ln, a, b), r) for a, b, r in db.execute("SELECT lat,lng,r FROM jobs WHERE city=?", (name,))) if d < 15), default=0)
        k = int((R - 3.0) / RING_KM + 0.5)  # rings already added: 0, 1 or 2
        if R == 0 or k >= MAX_RING:
            continue
        band = sum(1 for a, b in db.execute("SELECT lat,lng FROM places WHERE city=? AND lat IS NOT NULL", (name,)) if R - 1.5 <= km(la, ln, a, b) <= R)
        if band < EDGE_MIN:
            continue
        n, d = 8 * (k + 1), RING_KM * (k + 1)
        for i in range(n):
            t = 2 * math.pi * i / n
            for q in TOWN_Q:
                added += db.execute("INSERT OR IGNORE INTO jobs(city,lat,lng,r,q) VALUES(?,?,?,?,?)",
                                    (name, round(la + d * math.cos(t) / 111.0, 4), round(ln + d * math.sin(t) / (111.0 * math.cos(math.radians(la))), 4), 3.0, q)).rowcount
    db.commit()
    return added > 0


def save(city, places):
    for p in places:
        g = classify(p.get("name") or "", p.get("category") or "")
        # no supermarkets, and drop unrelated hits from free-text search
        if not p.get("id") or g == "سایر":
            continue
        # keep the first row, but fill fields it lacks from later sightings (another source, another grid cell)
        db.execute("""INSERT INTO places(id,name,cat,grp,city,address,phone,rating,nratings,lat,lng) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(id) DO UPDATE SET
                        name=COALESCE(NULLIF(excluded.name,''), places.name),
                        cat=COALESCE(NULLIF(excluded.cat,''), places.cat),
                        address=COALESCE(NULLIF(excluded.address,''), places.address),
                        phone=COALESCE(NULLIF(excluded.phone,''), places.phone),
                        rating=COALESCE(places.rating, excluded.rating),
                        nratings=COALESCE(places.nratings, excluded.nratings),
                        lat=COALESCE(places.lat, excluded.lat),
                        lng=COALESCE(places.lng, excluded.lng)""",
                   (p["id"], p.get("name"), p.get("category"), g, city,
                    p.get("address"), p.get("phone"), p.get("rating"), p.get("ratings", p.get("votes")), p.get("lat"), p.get("lng")))


class Stop(Exception):
    """The time budget (--max-minutes) ends before the next call could start."""


class Gate:
    """Pacing for one source: slows down after errors, speeds up on success, pauses when it looks blocked."""

    def __init__(self, name, delay):
        self.name, self.base, self.delay = name, delay, delay
        self.next = self.pause_until = 0.0
        self.streak = self.errors = 0

    async def wait(self):
        now = time.monotonic()
        t = max(self.next, self.pause_until, now)
        if DEADLINE and time.time() + (t - now) > DEADLINE:  # t is on the monotonic clock, DEADLINE on the wall clock
            raise Stop()
        self.next = t + self.delay  # slot reserved before any await, so workers never share one
        await asyncio.sleep(t - now)

    def ok(self):
        self.streak = 0
        self.delay = max(self.base, self.delay * 0.9)

    def bad(self, err):
        self.errors += 1
        self.streak += 1
        self.delay = min(30.0, self.delay * 1.3)  # stays put at ~30% errors, slows beyond that
        with open(os.path.join(HERE, "errors.log"), "a", encoding="utf-8") as f:
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
    except Stop:
        return
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
            web = d.get("website") or ""
        else:
            d = await call("bl", bl_place, id=pid, reviews=0, photos=0)
            txt = "؛ ".join(f"{k}: {v}" for k, v in (d.get("hours") or {}).items()) or (d.get("hours_status") or "")
            web = (d.get("links") or {}).get("web") or ""
    except Stop:
        return
    except Exception:
        db.execute("UPDATE places SET hours_done=hours_done-1 WHERE id=?", (pid,))  # 0 = todo, 1 = done, -1..-3 = failed
        return
    ph = "، ".join(d.get("phones") or [])
    db.execute("UPDATE places SET hours=?, hours_done=1, phone=COALESCE(NULLIF(?,''), phone), website=COALESCE(NULLIF(?,''), website) WHERE id=?",
               (txt, ph, web, pid))


async def fetch_hours():
    d0 = db.execute("SELECT SUM(hours_done=1) FROM places").fetchone()[0] or 0
    prog = asyncio.create_task(progress("hours", time.time(), d0))
    # Google places (g:...) arrive with their hours already filled by google_enrich.py
    base = "SELECT id FROM places WHERE hours_done<=0 AND hours_done>-3 AND grp!='سایر' AND id NOT LIKE 'g:%' AND %s ORDER BY hours_done DESC, rowid LIMIT 8"
    try:
        await asyncio.gather(*(pool(base % c, hours_one) for c in ("length(id)!=32", "length(id)=32") for _ in range(WORKERS)))
    finally:
        prog.cancel()
        db.commit()


def merge(path):
    """Take over the work of another database (e.g. a run from another IP): its places, and its finished jobs."""
    seed(list(CITIES))  # make sure every grid job exists here, so it can be marked done
    db.commit()
    db.execute("ATTACH DATABASE ? AS o", (path,))
    if db.execute("SELECT name FROM o.sqlite_master WHERE type='table' AND name='towns'").fetchone():
        db.execute("INSERT OR IGNORE INTO towns SELECT name,ostan,lat,lng,county FROM o.towns")
        seed_towns()  # town jobs of the other run exist here too, so they can be marked done
    before = db.execute("SELECT COUNT(*) FROM places").fetchone()[0]
    db.execute("INSERT OR IGNORE INTO places(id,name,cat,grp,city,address,phone,rating,nratings,lat,lng,hours,hours_done) "
               "SELECT id,name,cat,grp,city,address,phone,rating,nratings,lat,lng,hours,hours_done FROM o.places")
    db.execute("UPDATE jobs SET done=1 WHERE done=0 AND EXISTS (SELECT 1 FROM o.jobs j WHERE j.done=1 AND "
               "j.city=jobs.city AND j.lat=jobs.lat AND j.lng=jobs.lng AND j.r=jobs.r AND j.q=jobs.q)")
    db.commit()
    after, left = db.execute("SELECT (SELECT COUNT(*) FROM places), (SELECT COUNT(*) FROM jobs WHERE done=0)").fetchone()
    print(f"merged {path}: places {before} -> {after}, jobs still open: {left}")


def export(path="iran_food_directory.xlsx"):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "همه"
    ws.sheet_view.rightToLeft = True
    head = ["ردیف", "نام", "گروه", "دسته", "استان", "شهر", "نشانی", "تلفن", "ساعت کار", "امتیاز", "تعداد امتیاز", "lat", "lng", "وب‌سایت", "منبع"]
    rows = merged_rows(db)  # one row per shop, gaps filled across sources (same merge as the dashboard)
    ws.append(head)
    for i, r in enumerate(rows, 1):
        ws.append([i, *r])
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F4E78")
    for col, w in zip("ABCDEFGHIJKLMNO", (6, 38, 20, 18, 18, 14, 55, 22, 40, 8, 10, 10, 10, 30, 14)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes, ws.auto_filter.ref = "A2", ws.dimensions
    tmp = path + ".tmp"
    wb.save(tmp)  # write aside, then swap: an interrupted save can never destroy the old file
    try:
        os.replace(tmp, path)
    except PermissionError:  # the old file is open in Excel
        path = path.replace(".xlsx", time.strftime("_%H%M%S.xlsx"))
        os.replace(tmp, path)
    n_phone = sum(1 for r in rows if r[6])
    print(f"saved {path}: {len(rows)} places, {n_phone} with phone")


def coverage():
    """Per province: towns in data/shahr.json, towns Balad could not locate, jobs still open, places found."""
    fill_ostan(db)
    found = {(a, b): c for a, b, c in db.execute("SELECT name,ostan,lat IS NOT NULL FROM towns")}
    cities = {_norm(c) for c in CITIES}
    per = {}
    for name, prov in _shahr():
        s = per.setdefault(prov, [0, 0, 0, 0, 0])
        s[0] += 1
        if _norm(name) in cities:
            continue  # covered by its own grid
        if (name, prov) not in found:
            s[2] += 1  # never looked up
        elif not found[(name, prov)]:
            s[1] += 1  # looked up, not on the map
    for prov, n in db.execute("SELECT ostan,COUNT(*) FROM places GROUP BY ostan"):
        per.setdefault(prov, [0, 0, 0, 0, 0])[4] = n
    open_jobs = db.execute("SELECT COUNT(*) FROM jobs WHERE done=0 AND tries<?", (MAX_TRIES,)).fetchone()[0]
    print(f"{'استان':<22}{'شهرها':>7}{'پیدانشده':>10}{'جست‌وجونشده':>13}{'مکان':>8}")
    for prov, s in sorted(per.items(), key=lambda x: -x[1][4]):
        print(f"{prov or '(نامشخص)':<22}{s[0]:>7}{s[1]:>10}{s[2]:>13}{s[4]:>8}")
    t = [sum(s[i] for s in per.values()) for i in range(5)]
    print(f"{'جمع':<22}{t[0]:>7}{t[1]:>10}{t[2]:>13}{t[4]:>8} | jobs still open: {open_jobs}")


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
    ap.add_argument("--merge", default="")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--coverage", action="store_true")
    a = ap.parse_args()
    if a.coverage:
        coverage()
        sys.exit(0)
    if a.merge:
        merge(a.merge)
        sys.exit(0)
    if a.smoke:
        sys.exit(0 if asyncio.run(smoke()) else 3)
    if a.status:
        n = db.execute("SELECT COUNT(*) FROM jobs WHERE done=0 AND tries<?", (MAX_TRIES,)).fetchone()[0]
        if a.hours:
            n += db.execute("SELECT COUNT(*) FROM places WHERE hours_done<=0 AND hours_done>-3 AND grp!='سایر'").fetchone()[0]
        print(n + len(todo_towns()))
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
            async def phase1():  # one event loop for everything: the libs keep a session bound to the first loop they see
                if not a.cities:
                    await discover()
                    seed_towns()
                await scrape()
                while not a.cities and not (DEADLINE and time.time() > DEADLINE) and grow():
                    await scrape()  # busy towns got rings of extra circles; rings can grow once more, then stop
                print("scraping finished - saving Excel...", flush=True)
                export()  # a usable file exists before the slow hours phase starts
                if a.hours and not (DEADLINE and time.time() > DEADLINE):
                    await fetch_hours()
            asyncio.run(phase1())
        except KeyboardInterrupt:
            print("stopped - progress is saved.")
        db.commit()
    print("saving Excel - please wait, do not close the window...", flush=True)
    export()
