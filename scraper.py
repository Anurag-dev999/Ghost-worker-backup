import json
import os
import sys
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from dotenv import load_dotenv
from apify_client import ApifyClient

load_dotenv()

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [scraper] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        RotatingFileHandler(
            os.path.join(LOG_DIR, 'ghost_worker.log'),
            maxBytes=5 * 1024 * 1024,
            backupCount=3
        ),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

APIFY_TOKEN = os.getenv("APIFY_API_TOKEN")
DB_PATH     = os.path.join(os.path.dirname(__file__), 'agency.db')
CACHE_FILE  = "raw_leads.json"

# ── CONFIG ──────────────────────────────────────
# Change search_query here or pass from command line:
# python3 scraper.py "Gyms in Lucknow"
# ────────────────────────────────────────────────
DEFAULT_QUERY = "clinics in Jalandhar"
MIN_REVIEWS   = 30


def get_existing_phones():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.execute(
                "SELECT phone FROM leads WHERE phone IS NOT NULL"
            )
            return set(row[0] for row in cursor.fetchall())
    except Exception as e:
        logger.error(f"Could not read DB: {e}")
        return set()


def save_to_db(leads, query_string):
    saved = 0
    existing_phones = get_existing_phones()

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            for lead in leads:
                phone = (lead.get('phone') or '').strip()

                if not phone:
                    logger.debug(f"Skip {lead.get('title')} — no phone")
                    continue
                if phone in existing_phones:
                    logger.debug(f"Skip {lead.get('title')} — duplicate")
                    continue

                try:
                    conn.execute('''
                        INSERT INTO leads (
                            business_name, phone, email,
                            address, city,
                            rating, reviews_count,
                            website_url, tier,
                            query_string,
                            status, lifecycle_status,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Scraped', 'NEW', ?)
                    ''', (
                        (lead.get('title')        or '').strip(),
                        phone,
                        (lead.get('email')        or '').strip(),
                        (lead.get('address')      or '').strip(),
                        (lead.get('city')         or '').strip(),
                        float(lead.get('totalScore')   or 0),
                        int(lead.get('reviewsCount')   or 0),
                        None,
                        1,
                        query_string,
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    ))
                    saved += 1
                    existing_phones.add(phone)
                    logger.info(
                        f"Saved: {lead.get('title')} | "
                        f"{lead.get('totalScore')}* | "
                        f"{lead.get('reviewsCount')} reviews"
                    )
                except sqlite3.IntegrityError as e:
                    logger.warning(f"DB skip {lead.get('title')}: {e}")

            conn.commit()

    except Exception as e:
        logger.error(f"DB error: {e}")

    return saved


# ── STEP 1: Dynamic query from command line ──────
if len(sys.argv) > 1:
    search_query = sys.argv[1]
    logger.info(f"Dynamic query: {search_query}")
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
        logger.info("Old cache cleared for fresh search")
else:
    search_query = DEFAULT_QUERY
    logger.info(f"Default query: {search_query}")

# ── STEP 2: Cache check — saves Apify compute ───
if os.path.exists(CACHE_FILE):
    logger.info("Cache found — loading raw_leads.json")
    with open(CACHE_FILE, "r") as f:
        raw_leads = json.load(f)
    logger.info(f"Loaded {len(raw_leads)} leads from cache")
else:
    logger.info(f"Calling Apify for: {search_query}")
    client = ApifyClient(APIFY_TOKEN)
    run_input = {
        "searchStringsArray":        [search_query],
        "maxCrawledPlacesPerSearch": 50,
        "language":                  "en",
        "hasWebsite":                False,
    }
    run       = client.actor("compass/crawler-google-places").call(
                    run_input=run_input
                )
    raw_leads = list(
                    client.dataset(run["defaultDatasetId"]).iterate_items()
                )
    with open(CACHE_FILE, "w") as f:
        json.dump(raw_leads, f, indent=4)
    logger.info(f"Apify returned {len(raw_leads)} raw results — cached")

# ── STEP 3: Whale filter ─────────────────────────
logger.info("Running Whale Filter...")

qualified = [
    lead for lead in raw_leads
    if (lead.get('reviewsCount') or 0) >= MIN_REVIEWS
]
qualified.sort(
    key=lambda x: (x.get('reviewsCount') or 0),
    reverse=True
)
top_whales = qualified[:5]

logger.info(f"Total raw: {len(raw_leads)} | Qualified: {len(qualified)} | Top whales: {len(top_whales)}")

if not top_whales:
    logger.warning("No whales found — try lower MIN_REVIEWS or different query")
else:
    print(f"\nTop {len(top_whales)} Whales:")
    for i, w in enumerate(top_whales, 1):
        print(
            f"  {i}. {w.get('title')} | "
            f"Reviews: {w.get('reviewsCount')} | "
            f"Phone: {w.get('phone')}"
        )

    # ── STEP 4: Save to DB ───────────────────────
    saved = save_to_db(top_whales, search_query)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        total   = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='Scraped'"
        ).fetchone()[0]

    logger.info(f"\n{'='*50}")
    logger.info(f"Saved to DB      : {saved}")
    logger.info(f"Total in DB      : {total}")
    logger.info(f"Waiting for AI   : {pending}")
    logger.info(f"{'='*50}")
    logger.info("Next step: python3 ai_brain.py")
