"""Province (استان) of every place, filled in automatically: the 60 big cities from a table, towns from the towns table
(the nearest one when a town name exists in several provinces). Used by iran_food_scraper.py and export_json.py."""
import math, sqlite3

CITY_OSTAN = {  # spelled exactly as in data/ostan.json
    "تهران": "تهران", "کرج": "البرز", "مشهد": "خراسان رضوی", "اصفهان": "اصفهان", "شیراز": "فارس",
    "تبریز": "آذربایجان شرقی", "قم": "قم", "اهواز": "خوزستان", "کرمانشاه": "کرمانشاه", "ارومیه": "آذربایجان غربی",
    "رشت": "گیلان", "زاهدان": "سیستان وبلوچستان", "کرمان": "کرمان", "همدان": "همدان", "یزد": "یزد",
    "اراک": "مرکزی", "اردبیل": "اردبیل", "بندرعباس": "هرمزگان", "اسلامشهر": "تهران", "قزوین": "قزوین",
    "زنجان": "زنجان", "سنندج": "کردستان", "خرم‌آباد": "لرستان", "گرگان": "گلستان", "ساری": "مازندران",
    "بوشهر": "بوشهر", "ورامین": "تهران", "قدس و شهریار": "تهران", "بجنورد": "خراسان شمالی",
    "بیرجند": "خراسان جنوبی", "ایلام": "ایلام", "یاسوج": "کهگیلویه وبویراحمد", "شهرکرد": "چهارمحال وبختیاری",
    "سمنان": "سمنان", "بابل": "مازندران", "آمل": "مازندران", "دزفول": "خوزستان", "نجف‌آباد": "اصفهان",
    "بروجرد": "لرستان", "ساوه": "مرکزی", "خمینی‌شهر": "اصفهان", "سیرجان": "کرمان", "رفسنجان": "کرمان",
    "ملایر": "همدان", "مراغه": "آذربایجان شرقی", "خوی": "آذربایجان غربی", "بم": "کرمان", "قائمشهر": "مازندران",
    "کاشان": "اصفهان", "آبادان": "خوزستان", "ماهشهر": "خوزستان", "بهبهان": "خوزستان", "ایرانشهر": "سیستان وبلوچستان",
    "چابهار": "سیستان وبلوچستان", "کیش": "هرمزگان", "نیشابور": "خراسان رضوی", "سبزوار": "خراسان رضوی",
    "تربت حیدریه": "خراسان رضوی", "قوچان": "خراسان رضوی", "شاهرود": "سمنان",
}


def km(la1, ln1, la2, ln2):
    return math.hypot((la1 - la2) * 111, (ln1 - ln2) * 111 * math.cos(math.radians(la1)))


def fill_ostan(db):
    """Add the places.ostan column if missing and fill it for every place that has none yet. Cheap to call again."""
    try:
        db.execute("ALTER TABLE places ADD COLUMN ostan TEXT")
    except sqlite3.OperationalError:
        pass  # already there
    for city, o in CITY_OSTAN.items():
        db.execute("UPDATE places SET ostan=? WHERE ostan IS NULL AND city=?", (o, city))
    towns = {}
    try:
        town_rows = db.execute("SELECT name,ostan,lat,lng FROM towns WHERE lat IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        town_rows = []  # a v1 database has no towns table yet
    for name, o, la, ln in town_rows:
        towns.setdefault(name, []).append((o, la, ln))
    for pid, city, la, ln in db.execute("SELECT id,city,lat,lng FROM places WHERE ostan IS NULL").fetchall():
        cand = towns.get(city, [])
        if len(cand) > 1 and la is not None:  # same town name in several provinces: the one the place lies next to
            cand = [min(cand, key=lambda t: km(la, ln, t[1], t[2]))]
        db.execute("UPDATE places SET ostan=? WHERE id=?", (cand[0][0] if cand else "", pid))
    db.commit()
