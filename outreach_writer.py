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
FOUNDER_NAME   = "Anurag"
FOUNDER_TITLE  = "Founder"
FOUNDER_PHONE  = "+91 78148 71810"
FOUNDER_EMAIL  = "dev.anurag999@gmail.com"
AGENCY_NAME = os.getenv("AGENCY_NAME", "LaunchPad Web")
WA_SIGNATURE   = f"\n\n— {FOUNDER_NAME}\n{FOUNDER_TITLE}, {AGENCY_NAME}\n{FOUNDER_PHONE}"
EMAIL_SIGNATURE = f"\n\nWarm regards,\n{FOUNDER_NAME}\n{FOUNDER_TITLE} — {AGENCY_NAME}\n📞 {FOUNDER_PHONE}\n✉ {FOUNDER_EMAIL}"


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
    try:
        result = call_gemini(prompt)
        logger.info(f"  Gemini OK: {label}")
        return result
    except Exception as e:
        logger.error(f"  Both APIs failed ({label}): {e}")
        return None


# ── PROMPTS ───────────────────────────────────────────────


def build_prompt(lead, msg_type, channel):
    name    = lead['business_name']
    city    = lead['city'] or 'your city'
    reviews = lead['reviews_count'] or 0
    rating  = lead['rating'] or 4.5
    tracker = lead['tracker_url'] or ''
    niche   = lead['niche'] or 'business'

    # Clean business name
    short_name = name.split('-')[0].split('|')[0].strip()

    context = f"""Business Details:
- Name: {short_name}
- City: {city}
- Niche: {niche}
- Rating: {rating} stars
- Reviews: {reviews}
- Demo Link: {tracker}
- Agency: {AGENCY_NAME}"""

    # ── WHATSAPP PROMPTS (Hinglish, Casual, Conversational) ──
    if channel == 'whatsapp':
        
        base_wa_rules = """RULES:
- Language: Natural Hinglish (Roman Hindi mixed with English).
- Tone: Friendly, respectful, professional, energetic. Like a real person texting on WhatsApp.
- NO formal greetings (No "Dear Sir", "Greetings").
- Emojis: Max 1 or 2, placed naturally.
- Output: Return ONLY the message text. No quotes, no intro, no extra text."""

        if msg_type == 'cold':
            return f"""Write a cold WhatsApp message to {short_name}.
{context}

TONE REFERENCE (Mimic this exact style):
"Hey {short_name} team,

Just saw {short_name} on Google—{rating} stars, {reviews} reviews. Solid! ⭐

Most local {niche} with your rep actually lose 40% of calls because prospects can't find key info (hours, services, booking).

Built a quick site for a {niche} in {city} last month. They got 8 inquiries in week 1 from people who couldn't reach them before.

Worth a 2-min look? {tracker} "


Now, write the message for {short_name} using the exact structure and tone above.
- Do NOT use "free" anywhere.
- Do NOT use generic praise like "amazing" or "great job" — go straight to the pain point.
- Lead with THEIR problem (lost calls/inquiries), not your solution.
- Include one specific proof number (inquiries/calls) like the example.
- Mention their {reviews} reviews and {rating} stars naturally in the opener.
- Naturally include the demo link: {tracker}
{base_wa_rules}"""



        elif msg_type == 'hot':
            return f"""Write a 'Hot Strike' WhatsApp message to {short_name}.
{context}

SITUATION: They literally JUST opened the demo link we sent them. 
GOAL: Act excited that they are checking it out.

Draft a short message (3-4 lines) in the same natural Hinglish tone.
Example vibe: "Arre {short_name} team! Maine dekha aapne abhi website open ki..."
- Ask them how the design looks on their phone.
- Do NOT sound creepy. Make it sound like perfectly timed excitement.
- Include the demo link naturally: {tracker}
{base_wa_rules}"""

        elif msg_type == 'followup1':
            return f"""Write the first follow-up WhatsApp message to {short_name}.
{context}

SITUATION: We sent the link 2 days ago, no reply.
GOAL: Friendly nudge.

Draft a short message (3-4 lines) in natural Hinglish.
- Acknowledge they are probably busy with their business.
- Suggest them to just quickly open it on their mobile device because the mobile view is very smooth.
- Include the demo link naturally: {tracker}
{base_wa_rules}"""

        elif msg_type == 'followup2':
            return f"""Write the second follow-up WhatsApp message to {short_name}.
{context}

SITUATION: 3rd message, no reply. We need an answer so we can move on.
GOAL: Honest scarcity.

Draft a short message (3-4 lines) in natural Hinglish.
- Be direct: Tell them we only make 1 free site per niche in {city}.
- If they don't need it, ask them to just reply "No" so we can offer this exact design to another {niche} in the area.
- Keep it polite, no attitude. 
- Include the demo link: {tracker}
{base_wa_rules}"""

        elif msg_type == 'final':
            return f"""Write the final goodbye WhatsApp message to {short_name}.
{context}

SITUATION: 30 days passed, no reply. Withdrawing the offer.
GOAL: Close the door gracefully.

Draft a short message (3-4 lines) in natural Hinglish.
- Tell them we are taking down the demo site today.
- Say: "Since {short_name} has such great {rating}-star reviews, you truly deserve a great online presence."
- Leave the door open: "Future mein kabhi website chahiye ho toh yaad karna."
- Include the demo link one last time: {tracker}
{base_wa_rules}"""


    # ── EMAIL PROMPTS (Professional but Warm, JSON Output) ──
    elif channel == 'email':

        base_email_rules = f"""RULES:
- Tone: Professional, warm, human (NOT corporate robot).
- Keep it concise (under 100 words).
- MUST include the tracker link: {tracker}
- Sign-off as {AGENCY_NAME}.
- CRITICAL: Return ONLY valid JSON in this exact format: {{"subject": "your subject", "body": "your body text"}}
- Do not use markdown, do not wrap in ```json, just raw JSON text."""

        if msg_type == 'cold':
            return f"""Write a cold outreach email to {short_name}.
{context}
SITUATION: Reaching out for the first time with a pre-built free website.
Subject: Catchy, mentions {short_name}.
Body: 
- Compliment their {reviews} reviews.
- State we proactively built a custom demo website for them.
- Provide the link.
- End with a low-pressure question.
{base_email_rules}"""

        elif msg_type == 'hot':
            return f"""Write a hot strike email to {short_name}.
{context}
SITUATION: They just visited the demo site right now.
Subject: "Quick question about the site" or similar.
Body: Notice they checked it out, ask if it loaded smoothly or if they have any initial thoughts on the design. Keep it super short.
{base_email_rules}"""

        elif msg_type == 'followup1':
            return f"""Write Followup 1 email to {short_name}.
{context}
SITUATION: 2 days later, no reply.
Subject: Checking in.
Body: Acknowledge they are busy. Mention that local {niche} clients in {city} are searching online, and this site will help them capture that traffic. Remind them it's free to look.
{base_email_rules}"""

        elif msg_type == 'followup2':
            return f"""Write Followup 2 email to {short_name}.
{context}
SITUATION: Final regular follow-up. Genuine scarcity.
Subject: Moving on / One slot per city.
Body: Explain we only partner with one {niche} in {city}. Ask for a simple yes/no if they want to keep the design, otherwise we will offer the framework to another local business.
{base_email_rules}"""

        elif msg_type == 'final':
            return f"""Write the final closure email to {short_name}.
{context}
SITUATION: 30 days, no reply. Taking down the site.
Subject: Taking down the demo for {short_name}.
Body: Polite closure. We are removing the server link today. Compliment their business one last time. Tell them to reach out if they ever need help in the future.
{base_email_rules}"""

    return ""


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
                wa_draft_4      = :wa_draft_4,
                email_draft_1   = :email_draft_1,
                email_draft_hot = :email_draft_hot,
                email_draft_2   = :email_draft_2,
                email_draft_3   = :email_draft_3,
                email_draft_4   = :email_draft_4,
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
