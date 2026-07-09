import sqlite3
import os
import json
import logging
import time
from logging.handlers import RotatingFileHandler
from datetime import datetime
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
DB_PATH = os.path.join(os.path.dirname(__file__), 'agency.db')
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [ai_brain] %(message)s',
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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")



def check_env():
    missing = []
    if not GEMINI_API_KEY: missing.append("GEMINI_API_KEY")
    if not GROQ_API_KEY:   missing.append("GROQ_API_KEY")
    if missing:
        logger.error(f"Missing: {', '.join(missing)}")
        return False
    logger.info("Environment check — OK")
    return True


def get_pending_leads():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM leads WHERE status='Scraped' ORDER BY reviews_count DESC"
            )
            leads = [dict(row) for row in cursor.fetchall()]
            logger.info(f"Leads waiting for AI: {len(leads)}")
            return leads
    except Exception as e:
        logger.error(f"Could not fetch leads: {e}")
        return []

def get_available_niches():
    """Read actual template files to build niche options dynamically."""
    template_dir = os.path.join(os.path.dirname(__file__), 'templates')
    niches = []
    if os.path.exists(template_dir):
        for f in os.listdir(template_dir):
            if f.endswith('.html') and f != 'default.html':
                niches.append(f.replace('.html', ''))
    return niches if niches else ['default']


def build_dual_prompt(lead):
    name    = lead['business_name']
    city    = lead['city'] or 'India'
    rating  = lead['rating'] or 4.5
    reviews = lead['reviews_count'] or 0
    niche   = lead['niche'] or 'business'
    address = lead['address'] or city

    # Extract neighbourhood/area for hyper-local copy
    area = address.split(',')[0].strip() if ',' in address else city
    available_niches = '/'.join(get_available_niches())

    return f"""You are an elite brand strategist and copywriter. You are writing EXCLUSIVELY for this ONE business. Never recycle phrases from other businesses.

BUSINESS FACTS — use these specific details, not generic ones:
- Exact name: {name}
- Located in: {area}, {city}
- Type: {niche}
- Stars: {rating} on Google
- Reviews: {reviews} real customer reviews
- Has zero online presence right now

YOUR ANTI-LAZY RULES — these will be checked:
1. The word "Transform" is BANNED from hero headlines
2. The word "Smile" standalone is BANNED unless the business name contains it
3. Hero headlines must NOT start with a verb — no "Get", "Book", "Transform", "Discover"
4. Alpha and Beta heroes must share ZERO words with each other
5. Every sentence in "about" must contain either the business name, the city/area name, or the review count — no floating generic sentences
6. Services must match what THIS specific niche in THIS city would realistically offer — not copy-paste from a template

TWO BRAND PERSONALITIES — make them feel like rival agencies pitched the same client:

ALPHA — THE LEGACY INSTITUTION:
The reader should feel they are choosing something their grandparents would have trusted and their grandchildren will inherit. Weight. Permanence. Quiet pride.
- Hero: 6-9 words. No verb. Evokes a specific feeling tied to the city or the business name. Like a monument inscription.
- About: 3 sentences. Sentence 1 plants the business in {city}'s story specifically — a founding detail, a neighbourhood anchor, something real. Sentence 2 uses {reviews} reviews as proof of generational trust, not a marketing metric. Sentence 3 makes a promise that sounds like it was written in stone, not in an ad.
- Services: Premium language. "Advanced Implantology" not "Implants". "Restorative Artistry" not "Fillings".
- Pitch WA: Formal Hinglish. Opens by referencing something specific about {name} — their rating, their area, their specialty. Ends with a respectful question. 75 words max. Include [LINK].

BETA — THE CITY'S NEW FAVOURITE:
The reader should feel like they just got a hot tip from their coolest friend. Energy. Immediacy. The feeling that everyone is already going and they're missing out.
- Hero: 4-6 words. Outcome or identity statement. Present tense. What the customer BECOMES, not what the business DOES. Zero overlap with Alpha hero.
- About: 3 sentences. Sentence 1 starts with "You" or the customer's situation — their problem, their hesitation, their goal. Sentence 2 drops {reviews} as a social proof bomb — make it feel like insider knowledge. Sentence 3 is the invitation — specific, exciting, no generic CTAs.
- Services: Accessible punchy language. "Same-Day Appointments" not "Scheduling". "Painless Procedures" not "Treatment".
- Pitch WA: Casual Hinglish like texting a friend. Opens mid-thought, no greeting. References their specific location or specialty. Creates FOMO through excitement. 75 words max. Include [LINK].

RETURN ONLY THIS JSON — no explanation, no markdown, no code blocks:

{{


  "detected_niche": "one word from ONLY these available options: {available_niches}. Pick the closest match. If none match well, use the first option.",
  "alpha": {{
    "hero": "your alpha hero here",
    "about": "your alpha about paragraph here",
    "services": "Service One, Service Two, Service Three, Service Four",
    "pitch": "your alpha whatsapp pitch here with [LINK]"
  }},
  "beta": {{
    "hero": "your beta hero here",
    "about": "your beta about paragraph here",
    "services": "Service One, Service Two, Service Three, Service Four",
    "pitch": "your beta whatsapp pitch here with [LINK]"
  }}
}}"""


def clean_and_parse(raw):
    if not raw:
        return None
    try:
        text = raw.strip()
        if '```' in text:
            text = '\n'.join(
                l for l in text.split('\n')
                if not l.strip().startswith('```')
            ).strip()
        start = text.find('{')
        end   = text.rfind('}')
        if start == -1 or end == -1:
            return None
        return json.loads(text[start:end+1])
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return None


def validate_dual(data):
    required_top  = ['detected_niche', 'alpha', 'beta']
    required_vibe = ['hero', 'about', 'services', 'pitch']

    for key in required_top:
        if key not in data:
            return False, f"Missing top-level key: {key}"

    for vibe in ['alpha', 'beta']:
        for key in required_vibe:
            if not data[vibe].get(key):
                return False, f"Missing or empty: {vibe}.{key}"
        if len(data[vibe]['hero'].split()) > 12:
            return False, f"{vibe} hero too long"
        if len(data[vibe]['about']) < 60:
            return False, f"{vibe} about too short — not personalised enough"

    return True, "OK"


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=4, max=10))
def call_gemini(prompt):
    from google import genai
    client   = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model    = "gemini-2.0-flash",
        contents = prompt
    )
    return response.text


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=4, max=10))
def call_groq(prompt):
    from groq import Groq
    client   = Groq(api_key=GROQ_API_KEY)
    response = client.chat.completions.create(
        model      = "llama-3.3-70b-versatile",
        messages   = [{"role": "user", "content": prompt}],
        max_tokens = 1200,
    )
    return response.choices[0].message.content


def generate_dual_content(lead):
    prompt = build_dual_prompt(lead)
    raw    = None

    # Gemini primary
#    try:
 #       logger.info("  Trying Gemini...")
  #      raw = call_gemini(prompt)
   #     logger.info("  Gemini responded")
    #except Exception as e:
     #   logger.warning(f"  Gemini failed: {e}")

    # Groq fallback
    if not raw:
        try:
            logger.info("  Trying Groq fallback...")
            raw = call_groq(prompt)
            logger.info("  Groq responded")
        except Exception as e:
            logger.error(f"  Groq also failed: {e}")
            return None

    data = clean_and_parse(raw)
    if not data:
        logger.error("  Could not parse JSON response")
        logger.debug(f"  Raw: {raw[:300] if raw else 'empty'}")
        return None

    valid, reason = validate_dual(data)
    if not valid:
        logger.error(f"  Validation failed: {reason}")
        # Try fallback API with same prompt if primary gave bad data
        if raw:
            try:
                logger.info("  Retrying with Groq for better output...")
                raw2 = call_groq(prompt)
                data2 = clean_and_parse(raw2)
                if data2:
                    valid2, reason2 = validate_dual(data2)
                    if valid2:
                        logger.info("  Groq retry succeeded")
                        return data2
            except Exception:
                pass
        return None

    return data


def save_dual_content(lead_id, data):
    try:
        alpha = data['alpha']
        beta  = data['beta']

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute('''
                UPDATE leads SET
                    niche             = :niche,
                    hero_a            = :hero_a,
                    about_a           = :about_a,
                    services_a        = :services_a,
                    pitch_a           = :pitch_a,
                    hero_b            = :hero_b,
                    about_b           = :about_b,
                    services_b        = :services_b,
                    pitch_b           = :pitch_b,
                    ai_headline       = :hero_a,
                    ai_about          = :about_a,
                    ai_pitch_whatsapp = :pitch_a,
                    status            = 'AI_Complete'
                WHERE id = :id
            ''', {
                'niche':     data['detected_niche'],
                'hero_a':    alpha['hero'],
                'about_a':   alpha['about'],
                'services_a':alpha['services'],
                'pitch_a':   alpha['pitch'],
                'hero_b':    beta['hero'],
                'about_b':   beta['about'],
                'services_b':beta['services'],
                'pitch_b':   beta['pitch'],
                'id':        lead_id
            })
            conn.commit()
        logger.info(f"  Dual content saved — ID:{lead_id}")
        return True
    except Exception as e:
        logger.error(f"  DB save error: {e}")
        return False


def mark_failed(lead_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "UPDATE leads SET status='AI_Failed' WHERE id=?", (lead_id,)
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Could not mark failed: {e}")


def run_ai_brain():
    logger.info("Ghost Worker AI Brain (Dual-Vibe) — Starting")

    if not check_env():
        return

    leads = get_pending_leads()
    if not leads:
        logger.info("No leads waiting — run scraper.py first")
        return

    success = 0
    failed  = 0

    for lead in leads:
        lead_id = lead['id']
        name    = lead['business_name']

        logger.info(f"\n── Processing: {name[:50]} (ID:{lead_id})")

        data = generate_dual_content(lead)

        if data:
            saved = save_dual_content(lead_id, data)
            if saved:
                success += 1
                logger.info(f"  Alpha hero : {data['alpha']['hero']}")
                logger.info(f"  Beta hero  : {data['beta']['hero']}")
                logger.info(f"  Niche      : {data['detected_niche']}")
            else:
                mark_failed(lead_id)
                failed += 1
        else:
            mark_failed(lead_id)
            failed += 1

        time.sleep(2)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        ready = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='AI_Complete'"
        ).fetchone()[0]

    logger.info(f"\n{'='*50}")
    logger.info(f"AI BRAIN COMPLETE")
    logger.info(f"Success : {success}")
    logger.info(f"Failed  : {failed}")
    logger.info(f"Ready for builder: {ready}")
    logger.info(f"{'='*50}")
    logger.info("Next step: python3 builder.py")


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  Ghost Worker — AI Brain (Dual-Vibe)")
    print("="*50 + "\n")
    run_ai_brain()
