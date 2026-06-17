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
    format='[%(asctime)s] [%(levelname)s] [writer] %(message)s',
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
GROQ_API_KEY   = os.getenv("GROQ_OUTREACH_KEY") or os.getenv("GROQ_API_KEY")
AGENCY_NAME    = os.getenv("AGENCY_NAME", "LaunchPad Web")
AGENCY_EMAIL   = os.getenv("SMTP_EMAIL", "hello@fixingmyself.sbs")


# ── API CALLERS ───────────────────────────────────────────

@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=3, max=8))
def call_gemini(prompt):
    from google import genai
    client   = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model    = "gemini-2.0-flash",
        contents = prompt
    )
    return response.text.strip()


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=3, max=8))
def call_groq(prompt):
    from groq import Groq
    client   = Groq(api_key=GROQ_API_KEY)
    response = client.chat.completions.create(
        model      = "llama-3.3-70b-versatile",
        messages   = [{"role": "user", "content": prompt}],
        max_tokens = 500,
    )
    return response.choices[0].message.content.strip()


def call_ai(prompt, label):
    """
    Outreach writer uses Groq as primary — faster, higher rate limits.
    Gemini as fallback only.
    ai_brain.py keeps Gemini primary for content generation.
    """
    try:
        result = call_groq(prompt)
        logger.info(f"  Groq OK: {label}")
        return result
    except Exception as e:
        logger.warning(f"  Groq failed ({label}): {e} — trying Gemini")
#    try:
 #       result = call_gemini(prompt)
  #      logger.info(f"  Gemini OK: {label}")
   #     return result
    #except Exception as e:
     #   logger.error(f"  Both APIs failed ({label}): {e}")
      #  return None


# ── PROMPTS ───────────────────────────────────────────────

def build_prompt(lead, msg_type, channel):
    name    = lead['business_name']
    city    = lead['city'] or 'your city'
    reviews = lead['reviews_count'] or 0
    rating  = lead['rating'] or 4.5
    tracker = lead['tracker_url'] or ''
    niche   = lead['niche'] or 'business'

    context = f"""Business: {name}
City: {city}
Niche: {niche}
Google Rating: {rating} stars
Reviews: {reviews}
Demo site link: {tracker}
Agency: {AGENCY_NAME} ({AGENCY_EMAIL})"""

    if channel == 'whatsapp':
        rules = """Rules:
- Hinglish (Hindi + English mix) like a real person texting
- Warm and human — NOT corporate or spammy
- Include business name naturally
- Include demo link at the end
- Max 100 words
- Max 2 emojis
- End with ONE question to get a reply
- Return ONLY the message text, nothing else"""

        messages = {
            'cold': f"""You are writing a WhatsApp cold intro message for {AGENCY_NAME}.
{context}

{rules}

Message goal: First ever contact. Tell them we built them a FREE demo website.
They have {reviews} Google reviews but NO website — they are losing customers online.
Sound helpful, not salesy. Create curiosity about their demo site link.""",

            'hot': f"""You are writing a WhatsApp HOT STRIKE message for {AGENCY_NAME}.
{context}

{rules}

IMPORTANT: This lead JUST opened their demo website RIGHT NOW (within last 3 minutes).
Message goal: Strike while iron is hot — loha garam hai energy.
Start with something referencing they just saw their site.
Sound excited and personal — this is the most powerful message.
Make it feel like you knew they were looking at it.""",

            'followup1': f"""You are writing WhatsApp Followup 1 for {AGENCY_NAME}.
{context}

{rules}

Context: We sent them a cold intro 48 hours ago. No reply yet.
Message goal: Gentle reminder. Add social proof — {reviews} people already trust them.
Tone: Casual, not pushy. "Bas ek baar dekh lo" energy.
Do NOT sound desperate.""",

            'followup2': f"""You are writing WhatsApp Followup 2 for {AGENCY_NAME}.
{context}

{rules}

Context: Two followups sent. Still no reply. This is last regular followup.
Message goal: Create urgency. Limited time framing.
Tone: Friendly urgency — "sirf is hafte ke liye" type energy.
Mention we are offering this to only one business per niche per city.""",

            'final': f"""You are writing a final WhatsApp goodbye message for {AGENCY_NAME}.
{context}

{rules}

Context: 30 days passed. No response at all. This is the last message ever.
Message goal: Polite withdrawal of the offer. Leave door open.
Tone: Respectful goodbye. No pressure. Mention we will give this slot to another business.
End: If they ever want, they can reach back."""
        }
        return messages.get(msg_type, '')

    elif channel == 'email':
        rules = """Rules:
- Professional but warm tone
- Subject line must mention their business name specifically
- Body max 120 words
- Include the demo link
- End with clear call to action
- Return ONLY valid JSON: {"subject": "...", "body": "..."}
- No markdown, no code blocks, raw JSON only"""

        messages = {
            'cold': f"""You are writing a cold intro email for {AGENCY_NAME}.
{context}

{rules}

Email goal: First contact. Tell them we built a FREE demo website for their business.
Subject must create curiosity — reference their business name.""",

            'hot': f"""You are writing a HOT STRIKE email for {AGENCY_NAME}.
{context}

{rules}

Email goal: They just viewed their demo site. Follow up on their interest immediately.
Subject must reference that they checked it out.""",

            'followup1': f"""You are writing Followup 1 email for {AGENCY_NAME}.
{context}

{rules}

Email goal: 48h after intro. Gentle reminder.
Mention their {reviews} Google reviews as social proof.""",

            'followup2': f"""You are writing Followup 2 email for {AGENCY_NAME}.
{context}

{rules}

Email goal: Last regular followup. Urgency framing.
Limited time offer — mention only one business per niche gets this.""",

            'final': f"""You are writing a final goodbye email for {AGENCY_NAME}.
{context}

{rules}

Email goal: 30 days passed, withdrawing offer politely.
Leave door open for future."""
        }
        return messages.get(msg_type, '')


def parse_email_json(raw):
    if not raw:
        return None, None
    try:
        raw = raw.strip()
        if '```' in raw:
            raw = '\n'.join(
                l for l in raw.split('\n')
                if not l.startswith('```')
            ).strip()
        start = raw.find('{')
        end   = raw.rfind('}')
        if start != -1 and end != -1:
            data = json.loads(raw[start:end+1])
            return data.get('subject', ''), data.get('body', '')
    except Exception as e:
        logger.error(f"Email JSON parse error: {e} | raw: {raw[:100]}")
    return None, None


# ── MAIN WRITER ───────────────────────────────────────────

def write_all_messages(lead):
    """
    Writes all 5 WA + 5 Email messages for a lead.
    Gemini primary → Groq fallback for every message.
    All saved to DB in one transaction.
    """
    lead_id = lead['id']
    name    = lead['business_name']
    logger.info(f"\n── Writing messages: {name[:45]} (ID:{lead_id})")

    # ── WhatsApp messages ─────────────────────────────────
    wa_types = ['cold', 'hot', 'followup1', 'followup2', 'final']
    wa_cols  = ['wa_draft_1', 'wa_draft_hot', 'wa_draft_2', 'wa_draft_3', 'wa_draft_4']
    wa_results = {}

    for msg_type, col in zip(wa_types, wa_cols):
        prompt = build_prompt(lead, msg_type, 'whatsapp')
        result = call_ai(prompt, f"WA-{msg_type}")
        wa_results[col] = result
        time.sleep(1)  # small delay between calls

    # ── Email messages ────────────────────────────────────
    em_types = ['cold', 'hot', 'followup1', 'followup2', 'final']
    em_cols  = [
        'email_draft_1', 'email_draft_hot',
        'email_draft_2', 'email_draft_3', 'email_draft_4'
    ]
    em_results = {}

    for msg_type, col in zip(em_types, em_cols):
        prompt  = build_prompt(lead, msg_type, 'email')
        raw     = call_ai(prompt, f"EMAIL-{msg_type}")
        subject, body = parse_email_json(raw)
        if subject and body:
            em_results[col] = json.dumps(
                {'subject': subject, 'body': body},
                ensure_ascii=False
            )
        else:
            em_results[col] = None
        time.sleep(1)

    # ── Save everything to DB ─────────────────────────────
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute('''
                UPDATE leads SET
                    wa_draft_1      = :wa_draft_1,
                    wa_draft_hot    = :wa_draft_hot,
                    wa_draft_2      = :wa_draft_2,
                    wa_draft_3      = :wa_draft_3,
                    wa_draft_hot    = :wa_draft_hot,
                    email_draft_1   = :email_draft_1,
                    email_draft_hot = :email_draft_hot,
                    email_draft_2   = :email_draft_2,
                    email_draft_3   = :email_draft_3,
                    stage_updated_at = :now
                WHERE id = :id
            ''', {
                **wa_results,
                **em_results,
                'now': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'id':  lead_id
            })
            conn.commit()
        logger.info(f"  All messages saved — ID:{lead_id}")
        return True
    except Exception as e:
        logger.error(f"  DB save error: {e}")
        return False


def process_new_leads():
    """Find Deployed leads with no messages written and write them all."""
    logger.info("Outreach Writer — scanning for leads needing messages...")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            leads = conn.execute('''
                SELECT * FROM leads
                WHERE status = 'Deployed'
                AND (wa_draft_1 IS NULL OR wa_draft_1 = '')
                AND (outreach_paused IS NULL OR outreach_paused = 0)
                ORDER BY created_at ASC
            ''').fetchall()
            leads = [dict(l) for l in leads]

        logger.info(f"Leads needing messages: {len(leads)}")

        success = 0
        for lead in leads:
            ok = write_all_messages(lead)
            if ok:
                success += 1
            time.sleep(2)

        logger.info(f"Writer complete — {success}/{len(leads)} done")
        return success

    except Exception as e:
        logger.error(f"Writer scan error: {e}")
        return 0


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  Ghost Worker — Outreach Writer")
    print("="*50 + "\n")
    count = process_new_leads()
    print(f"\nMessages written for {count} leads.")
