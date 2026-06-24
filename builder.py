import sqlite3
import os
import re
import logging
import requests
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


def get_server_domain():
    """Get server domain from env or auto-detect public IP."""
    domain = os.getenv('SERVER_DOMAIN', '').strip()
    if domain:
        return domain
    try:
        ip = requests.get('https://api.ipify.org', timeout=5).text.strip()
        return f"http://{ip}:5000"
    except Exception:
        return "http://localhost:5000"


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
    niche_clean  = (niche or 'default').lower().strip()
    
    # Normalize compound niches to their primary template
    niche_map = {
        'cafe/restaurant': 'cafe',
        'restaurant/cafe': 'cafe',
        'dental clinic':   'clinic',
        'medical clinic':  'clinic',
        'beauty salon':    'salon',
        'hair salon':      'salon',
        'fitness center':  'gym',
        'fitness centre':  'gym',
    }
    niche_clean = niche_map.get(niche_clean, niche_clean)
    # Also handle slash — take first word before /
    if '/' in niche_clean:
        niche_clean = niche_clean.split('/')[0].strip()

    niche_file   = os.path.join(TEMPLATE_DIR, f"{niche_clean}.html")
    default_file = os.path.join(TEMPLATE_DIR, "default.html")

    if os.path.exists(niche_file):
        with open(niche_file, 'r', encoding='utf-8') as f:
            logger.info(f"  Template: {niche_clean}.html")
            return f.read(), f"{niche_clean}.html"

    if os.path.exists(default_file):
        with open(default_file, 'r', encoding='utf-8') as f:
            logger.info(f"  Template: default.html (fallback for '{niche_clean}')")
            return f.read(), "default.html"

    logger.error(f"No template found for niche '{niche_clean}' and no default.html")
    return None, None


def clean_phone(phone):
    """Convert any phone format to digits-only Indian number."""
    if not phone:
        return ''
    digits = re.sub(r'[^\d]', '', phone)
    if digits.startswith('91') and len(digits) >= 12:
        return digits
    if len(digits) == 10:
        return '91' + digits
    return digits


def build_replacements(lead, tracker_url):
    """
    Universal placeholder map.
    Covers ALL placeholder tags any template might use.
    Adding a new template never requires editing this file —
    just use these standard tags in your HTML.
    """
    phone_clean = clean_phone(lead.get('phone', ''))

    # Core fields — available for every lead
    hero    = lead.get('hero_a') or lead.get('ai_headline') or ''
    about   = lead.get('about_a') or lead.get('ai_about') or ''
    footer  = lead.get('ai_footer') or ''
    svc_a   = lead.get('services_a') or ''
    hero_b  = lead.get('hero_b') or hero
    about_b = lead.get('about_b') or about
    svc_b   = lead.get('services_b') or svc_a

    return {
        # ── Identity ────────────────────────────────────
        '{{BUSINESS_NAME}}':  lead.get('business_name', ''),
        '{{CITY}}':           lead.get('city', ''),
        '{{RATING}}':         str(lead.get('rating', '') or ''),
        '{{REVIEWS_COUNT}}':  str(lead.get('reviews_count', '') or ''),
        '{{PHONE_CLEAN}}':    phone_clean,
        '{{ADDRESS}}':        lead.get('address', ''),
        '{{TRACKER_URL}}':    tracker_url,

        # ── Single-vibe placeholders ─────────────────────
        # Use these in single-design templates (cafe, gym, salon etc.)
        '{{HERO}}':           hero,
        '{{ABOUT}}':          about,
        '{{FOOTER_TEXT}}':    footer,
        '{{SERVICES}}':       svc_a,

        # ── Alpha vibe placeholders ──────────────────────
        # Use these in dual-vibe templates
        '{{HERO_A}}':         hero,
        '{{ABOUT_A}}':        about,
        '{{FOOTER_A}}':       footer,
        '{{SERVICES_A}}':     svc_a,

        # ── Beta vibe placeholders ───────────────────────
        '{{HERO_B}}':         hero_b,
        '{{ABOUT_B}}':        about_b,
        '{{FOOTER_B}}':       footer,
        '{{SERVICES_B}}':     svc_b,

        # ── Legacy support (old clinic template) ─────────
        '{{HERO_HEADLINE}}':  hero,
        '{{ABOUT_CONTENT}}':  about,
    }


def inject_content(template, lead, tracker_url):
    replacements = build_replacements(lead, tracker_url)
    html = template
    for placeholder, value in replacements.items():
        html = html.replace(placeholder, str(value) if value else '')
    return html


def save_html(html, phone):
    phone_clean = re.sub(r'[^\d]', '', phone)
    filename    = f"{phone_clean}.html"
    filepath    = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)
    return filepath, filename


def update_db_built(lead_id, template_used):
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

    server_domain = get_server_domain()
    logger.info(f"Server domain: {server_domain}")

    leads = get_ai_complete_leads()
    if not leads:
        logger.info("No AI_Complete leads — run ai_brain.py first")
        return

    built  = 0
    failed = 0

    for lead in leads:
        lead_id = lead['id']
        name    = lead['business_name']
        niche   = lead.get('niche') or 'default'
        phone   = lead.get('phone', '')

        logger.info(f"\n── Building: {name[:50]} (ID:{lead_id})")

        tracker_url   = f"{server_domain}/view/{lead_id}"
        template, template_used = load_template(niche)

        if not template:
            logger.error(f"  Skipping — no template available")
            failed += 1
            continue

        try:
            html               = inject_content(template, lead, tracker_url)
            filepath, filename = save_html(html, phone)
            update_db_built(lead_id, template_used)

            built += 1
            size_kb = os.path.getsize(filepath) / 1024
            logger.info(f"  Built    : {filepath}")
            logger.info(f"  Template : {template_used}")
            logger.info(f"  Size     : {size_kb:.1f} KB")

        except Exception as e:
            logger.error(f"  Build failed: {e}")
            failed += 1

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        ready = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='Built'"
        ).fetchone()[0]

    logger.info(f"\n{'='*50}")
    logger.info(f"BUILDER COMPLETE")
    logger.info(f"Built      : {built}")
    logger.info(f"Failed     : {failed}")
    logger.info(f"Ready for S3: {ready}")
    logger.info(f"{'='*50}")
    logger.info("Next step: python3 s3_deployer.py")


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  Ghost Worker — Builder")
    print("="*50 + "\n")
    run_builder()
