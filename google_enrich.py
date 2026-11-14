"""Google Maps enrichment for the Iran food directory, via google-maps-scraper-kit.

Kit: https://github.com/Mahanaicoach/google-maps-scraper-kit (local engine + headless Chrome,
no API key). This script drives the kit's own scripts/scrape.py - it never talks to Google itself.

Design goal: as close to zero ban risk as scraping can get.
  * No Google account is involved (the kit browses anonymously), so no account can be banned.
  * One small job at a time, ~2 minutes apart (jittered), depth 2 (~40 results), daily cap (default 8).
  * Every finished (city, query) is remembered in iran_food.db and never scraped again.
  * At the first sign of Google rate limiting the run stops for the day.
  * Run it on your own home/office (residential) connection - never in CI / on a datacenter IP.
The only 100% ToS-safe route is the official Google Places API; this is the gentle free alternative.
The safest mode of all is --import-csv: you (or an agent) run the kit yourself once, and this script
only reads the result file - zero extra Google traffic.

Usage:
  python google_enrich.py --import-csv results.csv --city تهران    # offline import of a kit result
  python google_enrich.py --scrape --dry-run                       # show the plan, touch nothing
  python google_enrich.py --scrape --cities تهران,مشهد             # gentle scrape + import
  python google_enrich.py --status                                 # Google enrichment progress
Options:
  --kit PATH      kit folder (default: ../google-maps-scraper-kit)
  --depth N       result batches per job, ~20 places each (default 2, capped at 3)
  --delay S       seconds between jobs (default 120, jittered ±30%)
  --max-jobs N    daily cap (default 8)
After importing, rebuild the outputs:  python iran_food_scraper.py --export  &&  python export_json.py
"""
import argparse, csv, hashlib, json, os, random, sqlite3, subprocess, sys, tempfile, time
import urllib.request

from common import CITIES, classify

GQUERIES = ["حبوبات فروشی", "ادویه فروشی", "عطاری", "خشکبار", "پخش مواد غذایی", "عمده فروشی مواد غذایی", "برنج فروشی"]
MAX_TRIES = 3
DAY_NAMES = {"Saturday": "شنبه", "Sunday": "یکشنبه", "Monday": "دوشنبه", "Tuesday": "سه‌شنبه",
             "Wednesday": "چهارشنبه", "Thursday": "پنجشنبه", "Friday": "جمعه"}
WEEK_ORDER = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

HERE = os.path.dirname(os.path.abspath(__file__))
db = sqlite3.connect(os.path.join(HERE, "iran_food.db"))
db.executescript("""
CREATE TABLE IF NOT EXISTS places(id TEXT PRIMARY KEY, name TEXT, cat TEXT, grp TEXT, city TEXT, address TEXT, phone TEXT,
  rating REAL, nratings INT, lat REAL, lng REAL, hours TEXT, hours_done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS gjobs(city TEXT, q TEXT, done INTEGER DEFAULT 0, tries INTEGER DEFAULT 0, day TEXT, PRIMARY KEY(city,q));
CREATE TABLE IF NOT EXISTS gday(day TEXT PRIMARY KEY, n INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0);
""")
try:
    db.execute("ALTER TABLE places ADD COLUMN website TEXT")
    db.commit()
except sqlite3.OperationalError:
    pass


def fa_hours(raw):
    """The kit's open_hours JSON ({"Monday": ["7 AM–9 PM"], ...}) -> Persian week text, شنبه first."""
    try:
        week = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return raw or ""
    if not isinstance(week, dict):
        return ""
    parts = []
    for d in WEEK_ORDER + [k for k in week if k not in WEEK_ORDER]:
        v = week.get(d)
        if v:
            spans = "، ".join(v) if isinstance(v, list) else str(v)
            parts.append(f"{DAY_NAMES.get(d, d)}: {spans}")
    return "؛ ".join(parts)


def _num(v, cast):
    try:
        return cast(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def import_rows(rows, city):
    """Add kit rows to the places table (id prefix g:). Same classification as Balad/Neshan;
    unrelated hits (supermarkets, restaurants, ...) are dropped. Existing rows keep their data,
    gaps are filled. Cross-source duplicates are merged later by the shared export."""
    n_new = n_upd = n_skip = 0
    for r in rows:
        name = (r.get("title") or r.get("name") or "").strip()
        if not name:
            continue
        cat = r.get("category") or r.get("cat") or ""
        g = classify(name, cat)
        if g == "سایر":
            n_skip += 1
            continue
        pid = (r.get("place_id") or "").strip()
        rid = "g:" + (pid or hashlib.sha1((name + (r.get("address") or "")).encode("utf-8")).hexdigest()[:16])
        vals = (rid, name, cat, g, city,
                r.get("address") or r.get("complete_address") or None,
                r.get("phone") or None,
                _num(r.get("review_rating"), float), _num(r.get("review_count"), int),
                _num(r.get("latitude"), float), _num(r.get("longitude"), float),
                fa_hours(r.get("open_hours")), r.get("website") or None)
        existed = db.execute("SELECT 1 FROM places WHERE id=?", (rid,)).fetchone()
        db.execute("""INSERT INTO places(id,name,cat,grp,city,address,phone,rating,nratings,lat,lng,hours,hours_done,website)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?)
                      ON CONFLICT(id) DO UPDATE SET
                        name=COALESCE(NULLIF(excluded.name,''), places.name),
                        cat=COALESCE(NULLIF(excluded.cat,''), places.cat),
                        address=COALESCE(NULLIF(excluded.address,''), places.address),
                        phone=COALESCE(NULLIF(excluded.phone,''), places.phone),
                        rating=COALESCE(places.rating, excluded.rating),
                        nratings=COALESCE(places.nratings, excluded.nratings),
                        lat=COALESCE(places.lat, excluded.lat),
                        lng=COALESCE(places.lng, excluded.lng),
                        hours=COALESCE(NULLIF(excluded.hours,''), places.hours),
                        website=COALESCE(NULLIF(excluded.website,''), places.website),
                        hours_done=1""", vals)
        if existed:
            n_upd += 1
        else:
            n_new += 1
    db.commit()
    return n_new, n_upd, n_skip


def read_kit_file(path):
    if path.lower().endswith(".json"):
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("rows", [])
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def kit_env(kit):
    """Resolve the engine URL the way the kit does: kit/.env (last line wins), then process env."""
    env = dict(os.environ)
    p = os.path.join(kit, ".env")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    base = (env.get("SCRAPER_BASE_URL") or f"http://localhost:{env.get('SCRAPER_PORT') or '8080'}").rstrip("/")
    return base, env


def engine_up(base, env):
    req = urllib.request.Request(base + "/api/v1/jobs")
    if env.get("SCRAPER_API_KEY"):
        req.add_header("X-API-Key", env["SCRAPER_API_KEY"])
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def seed_gjobs(cities):
    for c in cities:
        for q in GQUERIES:
            db.execute("INSERT OR IGNORE INTO gjobs(city,q) VALUES(?,?)", (c, q))
    db.commit()


def status():
    total, done = db.execute("SELECT COUNT(*), SUM(done) FROM gjobs").fetchone()
    today = time.strftime("%Y-%m-%d")
    n, blocked = db.execute("SELECT n, blocked FROM gday WHERE day=?", (today,)).fetchone() or (0, 0)
    g_places = db.execute("SELECT COUNT(*) FROM places WHERE id LIKE 'g:%'").fetchone()[0]
    print(f"Google jobs: {done or 0}/{total} done | today: {n} run(s){' - BLOCKED, rest for today' if blocked else ''} | Google places in DB: {g_places}")


def scrape(a):
    kit = os.path.abspath(a.kit)
    script = os.path.join(kit, "scripts", "scrape.py")
    if not os.path.exists(script):
        sys.exit(f"kit not found at {kit} - clone https://github.com/Mahanaicoach/google-maps-scraper-kit there, or pass --kit PATH")
    cities = [c.strip() for c in a.cities.split(",") if c.strip()] or list(CITIES)
    bad = [c for c in cities if c not in CITIES]
    if bad:
        sys.exit(f"unknown city: {bad}")
    seed_gjobs(cities)
    today = time.strftime("%Y-%m-%d")
    n_today, blocked = db.execute("SELECT n, blocked FROM gday WHERE day=?", (today,)).fetchone() or (0, 0)
    pending = db.execute("SELECT city,q FROM gjobs WHERE done=0 AND tries<? ORDER BY tries,rowid", (MAX_TRIES,)).fetchall()
    budget = max(0, a.max_jobs - n_today)
    print(f"plan: {len(pending)} pending jobs, {budget} allowed today (cap {a.max_jobs}), depth {a.depth}, ~{a.delay}s apart")
    if a.dry_run:
        for c, q in pending[:budget]:
            print(f"  would run: {q} در {c}")
        return
    if blocked:
        print("Google rate-limited this IP earlier today - resting. Try again tomorrow.")
        return
    if not pending:
        print("nothing left to scrape.")
        return
    if budget <= 0:
        print("daily cap reached - try again tomorrow.")
        return
    base, env = kit_env(kit)
    if not engine_up(base, env):
        sys.exit(f"engine not reachable at {base}. Start it in the kit folder:\n  python scripts/engine.py start\nthen run this again.")
    tmpdir = tempfile.mkdtemp(prefix="ifd-google-")
    out = os.path.join(tmpdir, "g.csv")
    depth = max(1, min(3, a.depth))
    try:
        for i, (city, q) in enumerate(pending):
            if budget <= 0:
                print("daily cap reached - stopping. The rest stays pending for tomorrow.")
                break
            la, ln, _ = CITIES[city]
            if os.path.exists(out):
                os.remove(out)
            cmd = [sys.executable, script, f"{q} در {city}", str(la), str(ln),
                   "--depth", str(depth), "--lang", "fa", "--country", "ir",
                   "--no-email", "--no-socials", "--full", "--max-time", "240", "--out", out]
            print(f"[{i + 1}] {city} / {q} ...", flush=True)
            p = subprocess.run(cmd, cwd=kit, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            db.execute("INSERT INTO gday(day,n) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET n=n+1", (today,))
            budget -= 1
            tail = ((p.stdout or "") + "\n" + (p.stderr or "")).strip()
            if os.path.exists(out):  # even a blocked run saves what it found first - keep it
                try:
                    n_new, n_upd, n_skip = import_rows(read_kit_file(out), city)
                    print(f"  imported: +{n_new} new, {n_upd} updated, {n_skip} unrelated")
                except Exception as e:
                    print(f"  import failed: {e}")
            if p.returncode == 0:
                db.execute("UPDATE gjobs SET done=1, day=? WHERE city=? AND q=?", (today, city, q))
            else:
                db.execute("UPDATE gjobs SET tries=tries+1 WHERE city=? AND q=?", (city, q))
                low = tail.lower()
                if "rate-limiting" in low or "unusual traffic" in low or "unusual-traffic" in low or "blocked" in low:
                    db.execute("UPDATE gday SET blocked=1 WHERE day=?", (today,))
                    db.commit()
                    print("Google is rate-limiting this IP - stopping for today. Wait until tomorrow; "
                          "finished jobs are never repeated, so nothing is lost.")
                    return
                print(f"  job failed (exit {p.returncode}): {tail.splitlines()[-1] if tail else 'no output'}")
            db.commit()
            if budget > 0 and i + 1 < len(pending):
                delay = a.delay * random.uniform(0.7, 1.3)
                print(f"  waiting {delay:.0f}s before the next job ...", flush=True)
                time.sleep(delay)
    except KeyboardInterrupt:
        db.commit()
        print("\nstopped - progress is saved, finished jobs are never repeated.")
        return
    print("done for now. Rebuild the outputs with:\n  python iran_food_scraper.py --export\n  python export_json.py")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--import-csv", default="", help="kit result file (.csv or .json) to import - no Google traffic")
    ap.add_argument("--city", default="", help="city of the imported file (required with --import-csv)")
    ap.add_argument("--scrape", action="store_true", help="gentle scrape via the local kit engine, then import")
    ap.add_argument("--cities", default="", help="comma-separated subset of cities (default: all)")
    ap.add_argument("--kit", default=os.path.join(HERE, "..", "google-maps-scraper-kit"), help="kit folder")
    ap.add_argument("--depth", type=int, default=2, help="result batches per job (~20 places each), capped at 3")
    ap.add_argument("--delay", type=float, default=120, help="seconds between jobs (jittered ±30%%)")
    ap.add_argument("--max-jobs", type=int, default=8, help="daily cap on jobs")
    ap.add_argument("--dry-run", action="store_true", help="with --scrape: print the plan and exit")
    ap.add_argument("--status", action="store_true", help="show Google enrichment progress")
    a = ap.parse_args()
    if a.status:
        status()
    elif a.import_csv:
        if not a.city:
            sys.exit("--import-csv needs --city (e.g. --city تهران)")
        if not os.path.exists(a.import_csv):
            sys.exit(f"file not found: {a.import_csv}")
        n_new, n_upd, n_skip = import_rows(read_kit_file(a.import_csv), a.city)
        print(f"imported {a.import_csv}: +{n_new} new, {n_upd} updated, {n_skip} unrelated (dropped)")
        print("rebuild the outputs with:\n  python iran_food_scraper.py --export\n  python export_json.py")
    elif a.scrape:
        scrape(a)
    else:
        ap.print_help()