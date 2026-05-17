"""
NHS Drug Tariff Scraper — Deploy to Railway or Render
------------------------------------------------------
1. Push this file to a GitHub repo
2. Connect repo to Railway (railway.app) or Render (render.com)
3. Set START_COMMAND to: python nhs_tariff_scraper.py
4. Add a CRON job: 0 9 1 * *  (runs 9am on 1st of every month)
5. Set env var: PORT=8080
6. Your frontend fetches: https://your-app.railway.app/prices

Requirements (requirements.txt):
    flask
    requests
    beautifulsoup4
    flask-cors
    apscheduler
"""

import os, json, re, time, logging
from datetime import datetime
from flask import Flask, jsonify
from flask_cors import CORS
from bs4 import BeautifulSoup
import requests
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# ── Drugs to monitor ─────────────────────────────────────────
DRUGS = [
    {"id": 1,  "name": "Trimethoprim",   "strength": "200mg", "form": "tablets",  "pack": 28},
    {"id": 2,  "name": "Nitrofurantoin", "strength": "100mg", "form": "capsules", "pack": 30},
    {"id": 3,  "name": "Pregabalin",     "strength": "75mg",  "form": "capsules", "pack": 56},
    {"id": 4,  "name": "Gabapentin",     "strength": "300mg", "form": "capsules", "pack": 100},
    {"id": 5,  "name": "Ramipril",       "strength": "5mg",   "form": "capsules", "pack": 28},
    {"id": 6,  "name": "Lansoprazole",   "strength": "30mg",  "form": "capsules", "pack": 28},
    {"id": 7,  "name": "Sertraline",     "strength": "100mg", "form": "tablets",  "pack": 28},
    {"id": 8,  "name": "Lymecycline",    "strength": "408mg", "form": "capsules", "pack": 56},
    {"id": 9,  "name": "Flucloxacillin", "strength": "500mg", "form": "capsules", "pack": 28},
    {"id": 10, "name": "Omeprazole",     "strength": "20mg",  "form": "capsules", "pack": 28},
    {"id": 11, "name": "Metformin",      "strength": "500mg", "form": "tablets",  "pack": 84},
]

BASE_URL = "https://www.drugtariff.nhsbsa.nhs.uk"
CACHE = {"prices": {}, "last_updated": None, "month": None}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PharmaIntel/1.0; NHS Tariff Monitor)"
}

def pence_to_pounds(text):
    """Convert tariff price string like '135p' or '£1.35' to float pounds."""
    if not text:
        return None
    text = text.strip().replace(",", "")
    if "p" in text.lower():
        try:
            return round(float(re.sub(r"[^\d.]", "", text)) / 100, 4)
        except:
            return None
    try:
        return round(float(re.sub(r"[^\d.]", "", text)), 4)
    except:
        return None

def fetch_tariff_price(drug):
    """
    Scrape NHS BSA Drug Tariff for a specific drug.
    Returns dict with price, concession, shortage fields.
    """
    search_term = f"{drug['name']} {drug['strength']}"
    try:
        # Step 1: Search for drug
        search_url = f"{BASE_URL}/#/0/A"
        params = {"q": search_term}
        r = requests.get(
    f"{BASE_URL}/api/drugTariff/v1/drugs",
            params={"searchText": search_term, "tariffType": "A"},
            headers=HEADERS,
            timeout=15
        )

        if r.status_code != 200:
            logging.warning(f"Search failed for {drug['name']}: HTTP {r.status_code}")
            return fallback_entry(drug)

        data = r.json()
        results = data.get("results", data.get("items", []))

        if not results:
            logging.warning(f"No results for {drug['name']}")
            return fallback_entry(drug)

        # Find best matching result
        best = None
        for item in results:
            name_match = drug["name"].lower() in str(item.get("name", "")).lower()
            strength_match = drug["strength"].lower() in str(item.get("strength", "")).lower()
            pack_match = str(drug["pack"]) in str(item.get("packSize", ""))
            if name_match and (strength_match or pack_match):
                best = item
                break

        if not best and results:
            best = results[0]

        if not best:
            return fallback_entry(drug)

        # Extract price
        price_raw = best.get("basicPrice", best.get("price", best.get("basicNhsPrice", "")))
        price = pence_to_pounds(str(price_raw))

        # Check concession
        conc_raw = best.get("concessionPrice", best.get("concession", None))
        concession = pence_to_pounds(str(conc_raw)) if conc_raw else None

        # Check shortage (look for shortage flag or concession presence as proxy)
        shortage = bool(
            best.get("shortageMeasure", False) or
            best.get("shortage", False) or
            best.get("specialMeasure", False)
        )

        logging.info(f"✓ {drug['name']}: £{price} | concession: {concession} | shortage: {shortage}")

        return {
            "id": drug["id"],
            "name": drug["name"],
            "strength": drug["strength"],
            "form": drug["form"],
            "pack": drug["pack"],
            "price": price,
            "concession": concession,
            "shortage": shortage,
            "raw": best.get("name", ""),
        }

    except Exception as e:
        logging.error(f"Error fetching {drug['name']}: {e}")
        return fallback_entry(drug)

def fallback_entry(drug):
    return {
        "id": drug["id"],
        "name": drug["name"],
        "strength": drug["strength"],
        "form": drug["form"],
        "pack": drug["pack"],
        "price": None,
        "concession": None,
        "shortage": False,
        "raw": "FETCH_FAILED",
    }

def run_scrape():
    """Scrape all drugs and update cache."""
    logging.info(f"🔄 Starting Drug Tariff scrape — {datetime.now().isoformat()}")
    results = {}
    for drug in DRUGS:
        entry = fetch_tariff_price(drug)
        results[drug["id"]] = entry
        time.sleep(1.5)  # Polite delay between requests

    CACHE["prices"] = results
    CACHE["last_updated"] = datetime.now().isoformat()
    CACHE["month"] = datetime.now().strftime("%b %Y")
    logging.info(f"✅ Scrape complete. {len(results)} drugs updated.")

# ── API Routes ─────────────────────────────────────────
@app.route("/prices", methods=["GET"])
def get_prices():
    if not CACHE["prices"]:
        return jsonify({"error": "No data yet. Trigger /refresh first."}), 503
    return jsonify({
        "prices": CACHE["prices"],
        "last_updated": CACHE["last_updated"],
        "month": CACHE["month"],
    })

@app.route("/refresh", methods=["GET", "POST"])
def refresh():
    """Manually trigger a scrape."""
    run_scrape()
    return jsonify({"status": "ok", "updated": CACHE["last_updated"]})

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "last_updated": CACHE["last_updated"]})

# ── Scheduler (runs 1st of each month at 09:00) ────────
scheduler = BackgroundScheduler()
scheduler.add_job(run_scrape, "cron", day=1, hour=9, minute=0)
scheduler.start()

if __name__ == "__main__":
    # Run immediately on startup so cache is populated
    run_scrape()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
