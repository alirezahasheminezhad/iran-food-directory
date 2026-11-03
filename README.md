# iran-food-directory

Directory of Iranian food wholesalers and shops (pulses, spices, herbal shops, nuts, rice, distributors) built from
the Balad and Neshan maps. GitHub Actions collects the data; the result is an Excel file in the **data** release.

- **Start / resume:** Actions → *scrape* → Run workflow (opening hours are off by default; tick *hours* to fetch them). Each run works up to 5 h, saves the state in the release
  `data` (`iran_food.db`) and starts the next run by itself until nothing is left (at most 12 runs in a chain).
- **Result:** Releases → `data` → `iran_food_directory.xlsx` (refreshed at the end of every run).
- **Log of failed requests:** `errors.log` in the same release.
- Locally: `pip install -r requirements.txt && python iran_food_scraper.py --hours` (see the script header for options).
