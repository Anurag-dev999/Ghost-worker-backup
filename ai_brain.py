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
DB_PATH        = os.path.join(os.path.dirname(__file__), 'agency.db')


def check_env():
    missing = []
    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")
    if not GROQ_API_KEY:
        missing.append("GROQ_API_KEY")
    if missing:
        logger.error(f"Missing from .env: {', '.join(missing)}")
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


def build_prompt(lead):
    name    = lead['business_name']
    city    = lead['city'] or 'India'
    rating  = lead['rating'] or 4.5
    reviews = lead['reviews_count'] or 0
    niche   = lead['niche'] or 'business'

    return f"""You are an expert digital marketing copywriter for Indian small businesses.

Generate personalised website and sales copy for this business. Return ONLY a valid JSON object — no explanation, no markdown, no code blocks, just raw JSON.

Business details:
- Name: {name}
- City: {city}
- Category: {niche}
- Rating: {rating} stars on Google
- Reviews: {reviews} customer reviews
- They currently have NO website

Return this exact JSON structure:
{{
  "detected_niche": "one word from: gym / clinic / restaurant / salon / hotel / coaching / retail / default",
  "hero_headline": "5 to 7 powerful words, no punctuation, makes visitor want to stay",
  "about_content": "Exactly 2 sentences. First sentence introduces the business with their city. Second sentence mentions their exact review count as social proof. Professional and warm tone.",
  "whatsapp_pitch": "Hook-Pain-Solution format. Under 120 words. Casual and direct. Written in mix of Hindi-English (Hinglish) like a real person. Mention their business name. End with a question to get a reply.",
  "email_pitch": "Professional format. Include subject line as first line starting with 'Subject:'. Then body under 150 words. Mention free demo website. End with clear call to action.",
  "footer_text": "One sentence only. Example: Helping {name} reach its true potential online."
}}

Rules:
- hero_headline must be in English
- about_content must be in English  
- whatsapp_pitch should feel human, not corporate
- email_pitch subject line must be compelling and specific to their business
- All content must reference their actual business name, not a placeholder
- JSON must be valid — no trailing commas, no extra text outside the JSON"""


def clean_json_response(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.split('\n')
        lines = [l for l in lines if not l.startswith("```")]
        text  = '\n'.join(lines).strip()
    start = text.find('{')
    end   = text.rfind('}')
    if start != -1 and end != -1:
        text = text[start:end+1]
    return text


def validate_json(data):
    required = [
        'detected_niche', 'hero_headline', 'about_content',
        'whatsapp_pitch', 'email_pitch', 'footer_text'
    ]
    missing = [k for k in required if not data.get(k)]
    if missing:
        return False, f"Missing keys: {missing}"

    words = len(data['hero_headline'].split())
    if words < 3 or words > 12:
        return False, f"Hero headline word count {words} out of range"

    if len(data['whatsapp_pitch']) < 20:
        return False, "WhatsApp pitch too short"

    return True, "OK"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=10))
def call_gemini(prompt):
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    model    = genai.GenerativeModel('gemini-2.0-flash')
    response = model.generate_content(prompt)
    return response.text


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=4, max=10))
def call_groq(prompt):
    from groq import Groq
    client   = Groq(api_key=GROQ_API_KEY)
    response = client.chat.completions.create(
        model    = "llama-3.3-70b-versatile",
        messages = [{"role": "user", "content": prompt}],
        max_tokens = 1000,
    )
    return response.choices[0].message.content


def generate_content(lead):
    prompt = build_prompt(lead)
    raw    = None

    # Try Gemini first
    try:
        logger.info(f"  Trying Gemini...")
        raw = call_gemini(prompt)
        logger.info(f"  Gemini responded")
    except Exception as e:
        logger.warning(f"  Gemini failed: {e}")

    # Fallback to Groq
    if not raw:
        try:
            logger.info(f"  Trying Groq fallback...")
            raw = call_groq(prompt)
            logger.info(f"  Groq responded")
        except Exception as e:
            logger.error(f"  Groq also failed: {e}")
            return None

    # Parse and validate
    try:
        cleaned = clean_json_response(raw)
        data    = json.loads(cleaned)
        valid, reason = validate_json(data)
        if not valid:
            logger.error(f"  Validation failed: {reason}")
            logger.debug(f"  Raw response: {raw[:300]}")
            return None
        return data
    except json.JSONDecodeError as e:
        logger.error(f"  JSON parse error: {e}")
        logger.debug(f"  Raw response: {raw[:300]}")
        return None


def save_ai_content(lead_id, data):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute('''
                UPDATE leads SET
                    niche             = :niche,
                    ai_headline       = :headline,
                    ai_about          = :about,
                    ai_pitch_whatsapp = :whatsapp,
                    ai_pitch_email    = :email,
                    ai_footer         = :footer,
                    status            = 'AI_Complete'
                WHERE id = :id
            ''', {
                'niche':     data['detected_niche'],
                'headline':  data['hero_headline'],
                'about':     data['about_content'],
                'whatsapp':  data['whatsapp_pitch'],
                'email':     data['email_pitch'],
                'footer':    data['footer_text'],
                'id':        lead_id
            })
            conn.commit()
        logger.info(f"  Saved AI content to DB")
        return True
    except Exception as e:
        logger.error(f"  DB save error: {e}")
        return False


def mark_failed(lead_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "UPDATE leads SET status='AI_Failed' WHERE id=?",
                (lead_id,)
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Could not mark lead as failed: {e}")


def run_ai_brain():
    logger.info("Ghost Worker AI Brain — Starting")

    if not check_env():
        return

    leads = get_pending_leads()
    if not leads:
        logger.info("No leads waiting for AI — run scraper.py first")
        return

    success = 0
    failed  = 0

    for lead in leads:
        lead_id = lead['id']
        name    = lead['business_name']

        logger.info(f"\n── Processing: {name} (ID: {lead_id})")

        content = generate_content(lead)

        if content:
            saved = save_ai_content(lead_id, content)
            if saved:
                success += 1
                logger.info(f"  Niche    : {content['detected_niche']}")
                logger.info(f"  Headline : {content['hero_headline']}")
                logger.info(f"  WA pitch : {content['whatsapp_pitch'][:80]}...")
            else:
                mark_failed(lead_id)
                failed += 1
        else:
            mark_failed(lead_id)
            failed += 1

        # Small delay between API calls — avoids rate limiting
        time.sleep(2)

    # Final summary
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        ready = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='AI_Complete'"
        ).fetchone()[0]

    logger.info(f"\n{'='*50}")
    logger.info(f"AI BRAIN COMPLETE")
    logger.info(f"Success  : {success}")
    logger.info(f"Failed   : {failed}")
    logger.info(f"Ready for builder: {ready}")
    logger.info(f"{'='*50}")
    logger.info("Next step: python3 builder.py")


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  Ghost Worker — AI Brain")
    print("="*50 + "\n")
    run_ai_brain()
