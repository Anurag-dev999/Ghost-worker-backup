import json
import os
import sys
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from dotenv import load_dotenv
from apify_client import ApifyClient

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'raw_leads.json')

load_dotenv()

LOG_DIR    = os.path.join(os.path.dirname(__file__), 'logs')
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

APIFY_TOKEN   = os.getenv("APIFY_API_TOKEN")
APIFY_TOKEN_2 = os.getenv("APIFY_API_TOKEN_2")
DB_PATH     = os.path.join(os.path.dirname(__file__), 'agency.db')


DEFAULT_QUERY = "dental clinics in Ludhiana Punjab India"
MIN_REVIEWS   = 30
MIN_RATING    = 4.0
TOP_WHALES    = 5

# Social media URLs that don't count as a real website
SOCIAL_DOMAINS = [
    'facebook.com', 'fb.com', 'instagram.com', 'twitter.com',
    'youtube.com', 'linkedin.com', 't.me', 'wa.me',
    'justdial.com', 'indiamart.com', 'sulekha.com'
]


def has_real_website(url):
    """Returns True if the URL is a genuine business website."""
    if not url or not url.strip():
        return False
    url_lower = url.lower().strip()
    # Strip to bare domain check
    for social in SOCIAL_DOMAINS:
        if social in url_lower:
            return False
    # Must look like a real URL
    if url_lower in ('', 'http://', 'https://'):
        return False
    return True


def get_existing_phones():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            # Include both active leads AND blacklisted numbers
            leads_phones = set(
                row[0] for row in conn.execute(
                    "SELECT phone FROM leads WHERE phone IS NOT NULL"
                ).fetchall()
            )
            blacklist_phones = set(
                row[0] for row in conn.execute(
                    "SELECT phone FROM blacklist WHERE phone IS NOT NULL"
                ).fetchall()
            )
            combined = leads_phones | blacklist_phones
            logger.info(f"Existing phones: {len(leads_phones)} leads + {len(blacklist_phones)} blacklisted")
            return combined
    except Exception as e:
        logger.error(f"Could not read DB: {e}")
        return set()


def whale_filter(raw_leads):
    """
    Level-2 filter — runs on local RAM after Apify returns data.
    Apify's hasWebsite:False is not 100% reliable — this catches slippage.
    """
    existing_phones = get_existing_phones()
    passed   = []
    rejected = {}

    for lead in raw_leads:
        name    = (lead.get('title')        or '').strip()
        phone   = (lead.get('phone')        or '').strip()
        website = (lead.get('website')      or '').strip()
        try:
            reviews = int(lead.get('reviewsCount') or 0)
        except (ValueError, TypeError):
            reviews = 0
        try:
            rating = float(lead.get('totalScore') or 0)
        except (ValueError, TypeError):
            rating = 0.0
        closed  = lead.get('permanentlyClosed', False)
        temp_cl = lead.get('temporarilyClosed', False)

        if not name:
            rejected['no_name'] = rejected.get('no_name', 0) + 1
            continue

        if not phone:
            rejected['no_phone'] = rejected.get('no_phone', 0) + 1
            continue

        # KEY FIX: Check actual website field from Apify data
        if has_real_website(website):
            rejected['has_website'] = rejected.get('has_website', 0) + 1
            logger.debug(f"Skip [{name}] — has real website: {website[:50]}")
            continue

        if reviews < MIN_REVIEWS:
            rejected['low_reviews'] = rejected.get('low_reviews', 0) + 1
            continue

        if rating < MIN_RATING:
            rejected['low_rating'] = rejected.get('low_rating', 0) + 1
            continue

        if closed or temp_cl:
            rejected['closed'] = rejected.get('closed', 0) + 1
            continue

        if phone in existing_phones:
            rejected['duplicate'] = rejected.get('duplicate', 0) + 1
            continue

        passed.append({
            'title':        name,
            'phone':        phone,
            'email':        (lead.get('email')   or '').strip(),
            'address':      (lead.get('address') or '').strip(),
            'city':         (lead.get('city')    or '').strip(),
            'totalScore':   rating,
            'reviewsCount': reviews,
            'website':      website,  # store actual value for logging
        })
        existing_phones.add(phone)

    logger.info(f"Filter results: {len(passed)} passed | rejected: {rejected}")
    return passed


def save_to_db(leads, query_string):
    saved = 0

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            for lead in leads:
                phone = lead.get('phone', '').strip()
                try:
                    # Store actual website value (empty string or social URL)
                    # so dashboard can show it — NULL means we never checked
                    website_val = lead.get('website') or None

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
                        lead.get('title', '').strip(),
                        phone,
                        lead.get('email', '').strip(),
                        lead.get('address', '').strip(),
                        lead.get('city', '').strip(),
                        float(lead.get('totalScore') or 0),
                        int(lead.get('reviewsCount') or 0),
                        website_val,
                        1,
                        query_string,
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    ))
                    saved += 1
                    logger.info(
                        f"Saved: {lead.get('title', '')[:40]} | "
                        f"{lead.get('totalScore')}* | "
                        f"{lead.get('reviewsCount')} reviews"
                    )
                except sqlite3.IntegrityError as e:
                    logger.warning(f"DB skip [{lead.get('title')}]: {e}")

            conn.commit()

    except Exception as e:
        logger.error(f"DB save error: {e}")

    return saved


# ── MAIN ─────────────────────────────────────────

def run_scraper(query=None):
    global search_query

    # ── STEP 1: Dynamic query ──────────────────
    if query:
        search_query = query
        logger.info(f"Dynamic query: {search_query}")
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
            logger.info("Old cache cleared for fresh search")
    elif len(sys.argv) > 1:
        search_query = sys.argv[1]
        logger.info(f"Dynamic query: {search_query}")
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
            logger.info("Old cache cleared for fresh search")
    else:
        search_query = DEFAULT_QUERY
        logger.info(f"Default query: {search_query}")

    # ── STEP 2: Cache check ─────────────────────
    if os.path.exists(CACHE_FILE):
        logger.info("Cache found — loading raw_leads.json")
        with open(CACHE_FILE, "r") as f:
            raw_leads = json.load(f)
        logger.info(f"Loaded {len(raw_leads)} from cache")
    else:
        run_input = {
            "searchStringsArray":        [search_query],
            "maxCrawledPlacesPerSearch": 50,
            "language":                  "en",
            "hasWebsite":                False,
        }
        raw_leads = None
        try:
            logger.info(f"Calling Apify account 1 for: {search_query}")
            client    = ApifyClient(APIFY_TOKEN)
            run       = client.actor("compass/crawler-google-places").call(run_input=run_input)
            raw_leads = list(client.dataset(run["defaultDatasetId"]).iterate_items())
            logger.info(f"Apify account 1 OK — {len(raw_leads)} results")
        except Exception as e:
            logger.warning(f"Apify account 1 failed: {e}")
            if APIFY_TOKEN_2:
                try:
                    logger.info("Trying Apify account 2 fallback...")
                    client    = ApifyClient(APIFY_TOKEN_2)
                    run       = client.actor("compass/crawler-google-places").call(run_input=run_input)
                    raw_leads = list(client.dataset(run["defaultDatasetId"]).iterate_items())
                    logger.info(f"Apify account 2 OK — {len(raw_leads)} results")
                except Exception as e2:
                    logger.error(f"Apify account 2 also failed: {e2}")
                    raw_leads = []
            else:
                logger.error("APIFY_API_TOKEN_2 not set — no fallback available")
                raw_leads = []
        with open(CACHE_FILE, "w") as f:
            json.dump(raw_leads, f, indent=4)
        logger.info(f"Apify returned {len(raw_leads)} raw results — cached")

    # ── STEP 3: Whale filter ────────────────────
    logger.info("Running Whale Filter...")
    qualified = whale_filter(raw_leads)
    qualified.sort(key=lambda x: x.get('reviewsCount', 0), reverse=True)
    top_whales = qualified[:TOP_WHALES]

    logger.info(
        f"Total raw: {len(raw_leads)} | "
        f"Passed filter: {len(qualified)} | "
        f"Top whales: {len(top_whales)}"
    )

    if not top_whales:
        logger.warning("No whales found — try lower MIN_REVIEWS or different query")
        return 0

    print(f"\nTop {len(top_whales)} Whales:")
    for i, w in enumerate(top_whales, 1):
        print(
            f"  {i}. {w.get('title')} | "
            f"Reviews: {w.get('reviewsCount')} | "
            f"Phone: {w.get('phone')}"
        )

    # ── STEP 4: Save to DB ──────────────────────
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
    return saved


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  Ghost Worker — Scraper")
    print("="*50 + "\n")
    run_scraper()
