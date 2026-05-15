import sqlite3
import os
import re
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

LOG_DIR      = os.path.join(os.path.dirname(__file__), 'logs')
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), 'templates')
OUTPUT_DIR   = os.path.join(os.path.dirname(__file__), 'built_sites')
DB_PATH      = os.path.join(os.path.dirname(__file__), 'agency.db')

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [builder] %(message)s',
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


def get_ai_complete_leads():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM leads WHERE status='AI_Complete' ORDER BY reviews_count DESC"
            )
            leads = [dict(row) for row in cursor.fetchall()]
            logger.info(f"Leads ready to build: {len(leads)}")
            return leads
    except Exception as e:
        logger.error(f"Could not fetch leads: {e}")
        return []


def load_template(niche):
    # Try niche-specific template first, fall back to default
    niche_file   = os.path.join(TEMPLATE_DIR, f"{niche}.html")
    default_file = os.path.join(TEMPLATE_DIR, "default.html")

    if os.path.exists(niche_file):
        with open(niche_file, 'r', encoding='utf-8') as f:
            logger.info(f"  Template: {niche}.html")
            return f.read()

    if os.path.exists(default_file):
        with open(default_file, 'r', encoding='utf-8') as f:
            logger.info(f"  Template: default.html (fallback)")
            return f.read()

    logger.error("No template found — not even default.html")
    return None


def clean_phone(phone):
    # Remove everything except digits and leading +
    if not phone:
        return ''
    digits = re.sub(r'[^\d]', '', phone)
    # Indian numbers: ensure 91 country code
    if digits.startswith('91') and len(digits) >= 12:
        return digits
    if len(digits) == 10:
        return '91' + digits
    return digits


def inject_content(template, lead, tracker_url):
    phone_clean = clean_phone(lead.get('phone', ''))

    replacements = {
        '{{BUSINESS_NAME}}': lead.get('business_name', ''),
        '{{HERO_HEADLINE}}': lead.get('ai_headline', ''),
        '{{ABOUT_CONTENT}}': lead.get('ai_about', ''),
        '{{FOOTER_TEXT}}':   lead.get('ai_footer', ''),
        '{{CITY}}':          lead.get('city', ''),
        '{{RATING}}':        str(lead.get('rating', '')),
        '{{REVIEWS_COUNT}}': str(lead.get('reviews_count', '')),
        '{{PHONE_CLEAN}}':   phone_clean,
        '{{ADDRESS}}':       lead.get('address', ''),
        '{{TRACKER_URL}}':   tracker_url,
    }

    html = template
    for placeholder, value in replacements.items():
        html = html.replace(placeholder, str(value) if value else '')

    return html


def save_html(html, phone):
    # File name is the phone number — unique per lead
    phone_clean = re.sub(r'[^\d]', '', phone)
    filename    = f"{phone_clean}.html"
    filepath    = os.path.join(OUTPUT_DIR, filename)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)

    return filepath, filename


def update_db_built(lead_id, filename, template_used):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute('''
                UPDATE leads SET
                    status        = 'Built',
                    template_used = ?
                WHERE id = ?
            ''', (template_used, lead_id))
            conn.commit()
    except Exception as e:
        logger.error(f"DB update error: {e}")


def run_builder():
    logger.info("Ghost Worker Builder — Starting")

    leads = get_ai_complete_leads()
    if not leads:
        logger.info("No AI_Complete leads — run ai_brain.py first")
        return

    built   = 0
    failed  = 0

    for lead in leads:
        lead_id = lead['id']
        name    = lead['business_name']
        niche   = lead.get('niche') or 'default'
        phone   = lead.get('phone', '')

        logger.info(f"\n── Building: {name[:50]} (ID: {lead_id})")

        # Placeholder tracker URL — will be replaced by real URL after S3 deploy
        tracker_url = f"http://YOUR_SERVER_IP:5000/view/{lead_id}"

        template = load_template(niche)
        if not template:
            logger.error(f"  No template found for niche '{niche}' — skipping")
            failed += 1
            continue

        try:
            html     = inject_content(template, lead, tracker_url)
            filepath, filename = save_html(html, phone)

            # Detect which template was actually used
            niche_file = os.path.join(TEMPLATE_DIR, f"{niche}.html")
            template_used = f"{niche}.html" if os.path.exists(niche_file) else "default.html"

            update_db_built(lead_id, template_used, template_used)

            built += 1
            logger.info(f"  Built   : {filepath}")
            logger.info(f"  Template: {template_used}")

        except Exception as e:
            logger.error(f"  Build failed: {e}")
            failed += 1

    # Summary
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        ready = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='Built'"
        ).fetchone()[0]

    logger.info(f"\n{'='*50}")
    logger.info(f"BUILDER COMPLETE")
    logger.info(f"Built   : {built}")
    logger.info(f"Failed  : {failed}")
    logger.info(f"Ready for S3: {ready}")
    logger.info(f"{'='*50}")
    logger.info("Next step: python3 s3_deployer.py")


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  Ghost Worker — Builder")
    print("="*50 + "\n")
    run_builder()
