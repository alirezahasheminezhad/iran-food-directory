# iran-food-directory

Directory of Iranian food wholesalers and shops (pulses, spices, herbal shops, nuts, rice, distributors) built from
the **Balad** and **Neshan** maps, optionally enriched with **Google Maps** and **Instagram**. GitHub Actions collects
the data; the results are an Excel file in the **data** release and a searchable web dashboard.

## What you get

- **Excel:** Releases → `data` → `iran_food_directory.xlsx` — one row per shop, gaps filled across sources,
  with province (استان), city, address, phones, opening hours, rating, website, **Instagram page (اینستاگرام)** and source (بلد / نشان / گوگل / اینستاگرام).
- **Dashboard:** `index.html` — search, filter by group/province/city/rating/phone, sort, charts,
  CSV/Excel export, per-shop panel with call/copy/website/map links. Deployed by the `pages` workflow
  (Settings → Pages → Source: **GitHub Actions**), fed by the `sitedata` branch.
- **JSON:** `export_json.py` builds the compact `places.json` the dashboard reads — the *same* cross-source
  merge as the Excel, so the two always agree.

## Instagram as a source (اینستاگرام)

Instagram pages are discovered **without login and without scraping Instagram itself** (no fake accounts),
via free public directories that list businesses' Instagram handles:

- **behtarino.com** — business pages expose an `instagramUrl` field plus phones (high fill-rate).
- **top118.ir** — business detail pages list the Instagram handle, phones and city.

Collectors live in `instagram/scripts/` (Node ≥ 20, zero dependencies). `instagram_merge.py` inserts each page
as a `places` row with id `ig:<handle>`. The shared cross-source merge then either **attaches the page to a
matching map shop** (same normalized name + same city → source `بلد+اینستاگرام`, phones combined) or keeps it
as a **standalone Instagram row** (source `اینستاگرام`). The Excel and dashboard both show the new
`اینستاگرام` column with a clickable link.

```bash
node instagram/scripts/collect-behtarino.mjs        # crawl behtarino (IG_PERSIST=1 for multi-pass)
node instagram/scripts/collect-top118.mjs           # crawl top118
node instagram/scripts/build.mjs                    # merge IG sources -> instagram/data/instagram-pages.json
python instagram_merge.py instagram/data/instagram-pages.json iran_food.db
python export_xlsx.py iran_food.db iran_food_directory.xlsx   # rebuild Excel (no Balad/Neshan needed)
python export_json.py iran_food.db places.json                # rebuild dashboard JSON
```

The **instagram** workflow (`.github/workflows/instagram.yml`) runs every 6 hours: it restores the DB from the
`data` release, collects Instagram pages (bounded), merges them, rebuilds the Excel + dashboard, uploads the DB
and Excel back to the release, and publishes the dashboard. Re-running is idempotent (`ig:` rows are updated,
never duplicated), and the map scrape preserves Instagram rows.

> Honest limits: a few thousand real pages, not millions; phones only where businesses publish them; directory
> search occasionally returns loosely-related shops. Datacenter IPs (CI) may be rate-limited by the directories —
> the merge/export/publish steps still run, and locally-collected data pushed to the release is preserved.

## How collection works

- **Start / resume:** Actions → *scrape* → Run workflow (opening hours are off by default; tick *hours* to fetch them).
  Each run works up to 5 h, saves the state in the release `data` (`iran_food.db`) and starts the next run by itself
  until nothing is left (chains itself run after run; a skipped smoke test or a cancelled run is the
  only thing that pauses it, and the daily run then picks it back up).
- **Automatic:** the daily run picks up everything left (new towns, rings around busy towns, retries of failed jobs)
  and chains itself; nothing to start by hand.
- **Province (استان):** every place gets its province automatically; sort/filter by it. Coverage per run:
  `coverage.txt` in the same release (and on the run summary page). Failed requests: `errors.log` there too.
- **Gentle by design:** each source has its own pace that slows down after errors and pauses (2, 4, 8 … 30 min)
  when it looks blocked. Failed jobs are retried later, never in a loop.

## Google Maps enrichment (optional, local only)

`google_enrich.py` adds Google Maps as a third source through
[google-maps-scraper-kit](https://github.com/Mahanaicoach/google-maps-scraper-kit) — a local engine with headless
Chrome, no API key. It is built to keep ban risk as close to zero as scraping allows:

- **No Google account is involved** (anonymous browsing), so no account can be banned. The only remaining risk is a
  *temporary* IP rate-limit, and the design minimizes it: one small job at a time, ~2 minutes apart (jittered),
  depth 2 (~40 results), a daily cap (default 8 jobs), every finished (city, query) remembered and **never repeated**,
  and an immediate stop for the day at the first rate-limit signal.
- **Run it on your own residential connection, never in CI** (datacenter IPs are what Google blocks hardest).
- The only 100% ToS-safe route is the official Google Places API; this is the gentle free alternative.

```bash
# one-time setup: clone the kit next to this repo and start its engine
git clone https://github.com/Mahanaicoach/google-maps-scraper-kit.git ../google-maps-scraper-kit
cd ../google-maps-scraper-kit && python scripts/engine.py start && cd ../iran-food-directory

# safest: import a result you (or an agent) already scraped with the kit - zero extra Google traffic
python google_enrich.py --import-csv results-xxxx.csv --city تهران

# or let it scrape gently by itself (plan first, then run)
python google_enrich.py --scrape --dry-run
python google_enrich.py --scrape --cities تهران,مشهد
python google_enrich.py --status

# rebuild the outputs afterwards
python iran_food_scraper.py --export && python export_json.py
```

Google rows are classified with the same groups (Persian + English category keys), deduplicated against
Balad/Neshan by normalized name + distance (≤150 m), and shown with source `گوگل` (or `بلد+گوگل` when merged).

## Local use

```bash
pip install -r requirements.txt
python iran_food_scraper.py --hours        # see the script header for all options
python iran_food_scraper.py --export       # just rebuild the Excel from the saved DB
python export_json.py                      # rebuild places.json for the dashboard
python test_hours_fix.py                   # offline regression test (no network)
```

Open `index.html` next to a `places.json` (or let it fetch the published one) to browse locally.