"""Shared helpers for the Iran food directory: the city grid table, classification, text
normalization and the cross-source merge used by BOTH the Excel and the JSON export, so the
dashboard and the spreadsheet always show the same shops (one shop = one row, gaps filled)."""
import re
import sqlite3

from ostan import km, fill_ostan

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

GROUPS = [  # first match wins; Persian keys for Balad/Neshan, English word keys for Google Maps
    ("حبوبات", ("حبوبات", "بقولات"), r"\blegumes?\b|\bpulses?\b"),
    ("ادویه", ("ادویه", "ادویه‌جات"), r"\bspices?\b"),
    ("عطاری/گیاهان دارویی", ("عطاری", "گیاهان دارویی", "گیاه"), r"\bherbal\b|\bherbs?\b|\bmedicinal\b"),
    ("خشکبار/آجیل", ("خشکبار", "آجیل"), r"\bnuts?\b|\bdried fruits?\b"),
    ("برنج", ("برنج",), r"\brice\b"),
    ("پخش/عمده/بنکداری", ("پخش", "عمده", "بنکدار", "انبار", "بازرگانی", "بورس"),
     r"\bwholesal\w*\b|\bdistribut\w*\b|\bwarehouse\b"),
]  # plain supermarkets / grocery shops fall into "سایر" and are left out of the export


def classify(name, cat):
    t = f"{name} {cat}"
    for g, fa, en in GROUPS:
        if any(k in t for k in fa) or re.search(en, t, re.IGNORECASE):
            return g
    return "سایر"


def norm(t):
    return (t or "").replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").strip()


def key(t):  # spacing differs between sources ("آران وبیدگل" / "آران و بیدگل"), so names are compared without spaces
    return norm(t).replace(" ", "")


def source_of(pid):
    pid = pid or ""
    if pid.startswith("ig:"):
        return "اینستاگرام"
    if pid.startswith("g:"):
        return "گوگل"
    return "نشان" if len(pid) == 32 else "بلد"


MERGE_KM = 0.15  # same normalized name within 150 m = the same shop (branches farther apart stay separate)

# raw row layout: 0 name, 1 grp, 2 cat, 3 ostan, 4 city, 5 address, 6 phone, 7 hours,
#                 8 rating, 9 nratings, 10 lat, 11 lng, 12 id, 13 website, 14 instagram
# merged row layout: the first 12, then website, then instagram, then source(s)


def _same_spot(a, b):
    if a[10] is None or a[11] is None or b[10] is None or b[11] is None:
        return a[4] == b[4]  # no coordinates: only merge within the same city
    return km(a[10], a[11], b[10], b[11]) <= MERGE_KM


def _merge_cluster(c):
    out = [next((r[i] for r in c if r[i] not in (None, "")), None) for i in range(12)]
    phones = list(dict.fromkeys(p.strip() for r in c for p in (r[6] or "").replace(";", "،").split("،") if p.strip()))
    out[6] = "، ".join(phones[:3]) or None
    out.append(next((r[13] for r in c if r[13]), None))  # website
    igs = list(dict.fromkeys(r[14] for r in c if r[14]))
    out.append("، ".join(igs) or None)  # instagram page(s)
    out.append("+".join(dict.fromkeys(source_of(r[12]) for r in c)))
    return out


def merged_rows(db):
    """One row per shop across Balad / Neshan / Google / Instagram: same normalized name close by = one shop,
    empty fields filled from the other sources, phones combined, sources listed as بلد+نشان+گوگل+اینستاگرام.
    Used by both exports so the Excel and the dashboard can never disagree."""
    for col in ("website", "instagram"):
        try:
            db.execute(f"ALTER TABLE places ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass  # already there
    fill_ostan(db)
    raw = db.execute("SELECT name,grp,cat,ostan,city,address,phone,hours,rating,nratings,lat,lng,id,website,instagram "
                     "FROM places WHERE grp!='سایر'").fetchall()
    by_key = {}
    for r in raw:
        by_key.setdefault(key(r[0]), []).append(r)
    rows = []
    for group in by_key.values():
        clusters = []
        for r in group:
            for c in clusters:
                if any(_same_spot(r, o) for o in c):
                    c.append(r)
                    break
            else:
                clusters.append([r])
        rows.extend(_merge_cluster(c) for c in clusters)
    rows.sort(key=lambda r: (r[3] or "", r[4] or "", r[1] or "", r[0] or ""))
    return rows