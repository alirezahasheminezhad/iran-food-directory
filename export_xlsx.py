"""Rebuild the Excel from the places DB: python export_xlsx.py [db] [out].
Lightweight (only openpyxl + common); used by the Instagram workflow which does
not need the Balad/Neshan libraries. Same cross-source merge as the dashboard."""
import os
import sqlite3
import sys
import time

from common import merged_rows

HEAD = ["ردیف", "نام", "گروه", "دسته", "استان", "شهر", "نشانی", "تلفن", "ساعت کار",
        "امتیاز", "تعداد امتیاز", "lat", "lng", "وب‌سایت", "اینستاگرام", "منبع"]
WIDTHS = (6, 38, 20, 18, 18, 14, 55, 22, 40, 8, 10, 10, 10, 30, 30, 14)


def export_xlsx(db, path="iran_food_directory.xlsx"):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "همه"
    ws.sheet_view.rightToLeft = True
    rows = merged_rows(db)
    ws.append(HEAD)
    for i, r in enumerate(rows, 1):
        ws.append([i, *r])
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F4E78")
    for col, w in zip("ABCDEFGHIJKLMNOP", WIDTHS):
        ws.column_dimensions[col].width = w
    ws.freeze_panes, ws.auto_filter.ref = "A2", ws.dimensions
    tmp = path + ".tmp"
    wb.save(tmp)
    try:
        os.replace(tmp, path)
    except PermissionError:
        path = path.replace(".xlsx", time.strftime("_%H%M%S.xlsx"))
        os.replace(tmp, path)
    n_phone = sum(1 for r in rows if r[6])
    n_ig = sum(1 for r in rows if r[13])
    print(f"saved {path}: {len(rows)} places, {n_phone} with phone, {n_ig} with instagram")
    return path


if __name__ == "__main__":
    db_path, out = (sys.argv[1:3] or ["iran_food.db", "iran_food_directory.xlsx"])
    export_xlsx(sqlite3.connect(db_path), out)