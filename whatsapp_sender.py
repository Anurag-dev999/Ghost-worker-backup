import os
import re
import json
import logging
import requests
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

load_dotenv()

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [wa_sender] %(message)s',
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

EVOLUTION_URL      = os.getenv("EVOLUTION_URL",      "http://localhost:8080")
EVOLUTION_API_KEY  = os.getenv("EVOLUTION_API_KEY",  "changeme")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "ghost-worker")

FOUNDER_NAME  = "Anurag"
FOUNDER_TITLE = "Founder"
AGENCY_NAME   = os.getenv("AGENCY_NAME", "LaunchPad Web")
FOUNDER_PHONE = "+91 78148 71810"
FOUNDER_EMAIL = "dev.anurag999@gmail.com"

SIGNATURE = (
    f"\n\n— {FOUNDER_NAME}\n"
    f"{FOUNDER_TITLE}, {AGENCY_NAME}\n"
    f"{FOUNDER_PHONE}"
)


def clean_phone(phone):
    """Convert any phone format to WhatsApp-compatible number."""
    digits = re.sub(r'[^\d]', '', phone)
    # Ensure Indian country code
    if digits.startswith('0'):
        digits = '91' + digits[1:]
    if len(digits) == 10:
        digits = '91' + digits
    return digits


def inject_tracker(message, tracker_url):
    """Replace [LINK] placeholder with actual tracker URL."""
    if not message:
        return message
    return message.replace('[LINK]', tracker_url or '')


def send_whatsapp(phone, message, tracker_url=None):
    """
    Send a WhatsApp message via Evolution API.
    Returns True on success, False on failure.
    """
    if not phone or not message:
        logger.error("send_whatsapp: missing phone or message")
        return False

    # Clean phone number
    number = clean_phone(phone)
    if len(number) < 10:
        logger.error(f"Invalid phone number: {phone}")
        return False

    # Inject tracker URL and append signature
    final_message = inject_tracker(message, tracker_url)
    final_message = final_message.strip() + SIGNATURE

    url     = f"{EVOLUTION_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    headers = {
        "Content-Type": "application/json",
        "apikey":        EVOLUTION_API_KEY
    }

    payload = {
        "number":      f"{number}@s.whatsapp.net",
        "textMessage": {"text": final_message},
        "delay":       1200
    }

    try:
        response = requests.post(
            url,
            headers = headers,
            json    = payload,
            timeout = 30
        )

        if response.status_code in (200, 201):
            data = response.json()
            msg_id = data.get('key', {}).get('id', 'unknown')
            logger.info(
                f"WA sent → {number} | "
                f"MsgID: {msg_id} | "
                f"Preview: {final_message[:60]}..."
            )
            return True
        else:
            resp_text = response.text
            # Check if number doesn't exist on WhatsApp
            if 'exists":false' in resp_text or '"exists": false' in resp_text:
                logger.warning(
                    f"WA not available → {number} | "
                    f"Number not on WhatsApp"
                )
                return "NO_WHATSAPP"
            logger.error(
                f"WA send failed → {number} | "
                f"Status: {response.status_code} | "
                f"Response: {resp_text[:200]}"
            )
            return False


    except requests.exceptions.Timeout:
        logger.error(f"WA send timeout → {number}")
        return False
    except Exception as e:
        logger.error(f"WA send error → {number}: {e}")
        return False


def check_connection():
    """Verify Evolution API and WhatsApp session are alive."""
    try:
        url = f"{EVOLUTION_URL}/instance/fetchInstances"
        r   = requests.get(
            url,
            headers = {"apikey": EVOLUTION_API_KEY},
            timeout = 10
        )
        if r.status_code == 200:
            instances = r.json()
            for inst in instances:
                if inst.get('instance', {}).get('instanceName') == EVOLUTION_INSTANCE:
                    status = inst.get('instance', {}).get('status')
                    if status == 'open':
                        logger.info(f"WhatsApp connection: OK (status: {status})")
                        return True
                    else:
                        logger.warning(f"WhatsApp status: {status} — not connected")
                        return False
            logger.error(f"Instance '{EVOLUTION_INSTANCE}' not found")
            return False
        return False
    except Exception as e:
        logger.error(f"Connection check failed: {e}")
        return False


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  Ghost Worker — WhatsApp Sender Test")
    print("="*50 + "\n")

    # Check connection first
    connected = check_connection()
    if not connected:
        print("ERROR: WhatsApp not connected. Check Evolution API.")
        exit(1)

    print("WhatsApp connection: OK")
    print("\nSending test message to yourself...")

    # Send test message to your own number
    test_msg = (
        f"Ghost Worker test message.\n"
        f"Evolution API is connected and working.\n"
        f"System is ready to send automated outreach."
    )

    success = send_whatsapp(
        phone       = "917814871810",
        message     = test_msg,
        tracker_url = None
    )

    if success:
        print("Test message sent! Check your WhatsApp.")
    else:
        print("Test failed. Check logs/ghost_worker.log")
