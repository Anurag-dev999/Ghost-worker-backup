import os
import sys
import time
import sqlite3
import logging
import requests
import threading
import subprocess
from logging.handlers import RotatingFileHandler
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
DB_PATH = os.path.join(os.path.dirname(__file__), 'agency.db')
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [orchestrator] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        RotatingFileHandler(
            os.path.join(LOG_DIR, 'ghost_worker.log'),
            maxBytes=5*1024*1024, backupCount=3
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON      = os.path.join(PROJECT_DIR, 'ghostenv', 'bin', 'python3')

REQUIRED_ENV = [
    'GROQ_API_KEY', 'APIFY_API_TOKEN',
    'AWS_ACCESS_KEY', 'AWS_SECRET_KEY', 'S3_BUCKET_NAME',
    'EVOLUTION_URL', 'EVOLUTION_GLOBAL_KEY', 'EVOLUTION_INSTANCE',
    'SMTP_HOST', 'SMTP_EMAIL', 'SMTP_PASSWORD',
    'AGENCY_NAME', 'SERVER_DOMAIN',
]

REQUIRED_DIRS = [
    os.path.join(PROJECT_DIR, 'templates'),
    os.path.join(PROJECT_DIR, 'built_sites'),
    os.path.join(PROJECT_DIR, 'logs'),
    os.path.join(PROJECT_DIR, 'static'),
]


def check_env():
    missing = []
    for var in REQUIRED_ENV:
        if not os.getenv(var):
            missing.append(var)
            print(f"  MISSING: {var}")
    if missing:
        logger.error(f"Missing env vars: {', '.join(missing)}")
        return False
    logger.info(f"All env vars OK")
    return True


def check_dirs():
    for d in REQUIRED_DIRS:
        os.makedirs(d, exist_ok=True)
    logger.info("All directories OK")
    return True


def check_database():
    if not os.path.exists(DB_PATH):
        logger.warning("DB missing — running init...")
        subprocess.run([PYTHON, os.path.join(PROJECT_DIR, 'init_agency.py')], cwd=PROJECT_DIR)
    logger.info("Database OK")
    return True


def check_templates():
    template_dir  = os.path.join(PROJECT_DIR, 'templates')
    default_tmpl  = os.path.join(template_dir, 'default.html')
    templates     = [f for f in os.listdir(template_dir) if f.endswith('.html')]
    if not templates:
        logger.warning("No templates found in /templates/")
    else:
        logger.info(f"Templates: {', '.join(templates)}")
    return True


def check_whatsapp():
    try:
        from whatsapp_sender import check_connection
        ok = check_connection()
        if ok:
            logger.info("WhatsApp: connected")
        else:
            logger.warning("WhatsApp: NOT connected — outreach will be skipped")
        return ok
    except Exception as e:
        logger.error(f"WhatsApp check error: {e}")
        return False


def check_evolution_api():
    try:
        url = os.getenv('EVOLUTION_URL', 'http://localhost:8080')
        r   = requests.get(f"{url}", timeout=5)
        if r.status_code == 200:
            logger.info("Evolution API: running")
            return True
        return False
    except Exception as e:
        logger.error(f"Evolution API not reachable: {e}")
        return False


def get_db_stats():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            total    = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            hot      = conn.execute("SELECT COUNT(*) FROM leads WHERE lifecycle_status='HOT'").fetchone()[0]
            deployed = conn.execute("SELECT COUNT(*) FROM leads WHERE status='Deployed'").fetchone()[0]
            pending  = conn.execute("SELECT COUNT(*) FROM leads WHERE status='Scraped'").fetchone()[0]
            stage0   = conn.execute("SELECT COUNT(*) FROM leads WHERE outreach_stage=0 AND status='Deployed'").fetchone()[0]
            return total, hot, deployed, pending, stage0
    except Exception as e:
        logger.error(f"DB stats error: {e}")
        return 0, 0, 0, 0, 0


def get_public_ip():
    try:
        return requests.get('https://api.ipify.org', timeout=5).text.strip()
    except:
        return "unknown"


def print_banner(public_ip, total, hot, deployed, pending, stage0, wa_ok):
    domain = os.getenv('SERVER_DOMAIN', f'http://{public_ip}:5000')
    print("\n")
    print("╔══════════════════════════════════════════════════════╗")
    print("║         GHOST WORKER — SYSTEM IS LIVE               ║")
    print("╠══════════════════════════════════════════════════════╣")
    print(f"║  Dashboard : {domain}/admin")
    print(f"║  Health    : {domain}/health")
    print("╠══════════════════════════════════════════════════════╣")
    print(f"║  Total leads    : {total}")
    print(f"║  HOT leads      : {hot}")
    print(f"║  Deployed sites : {deployed}")
    print(f"║  Pending AI     : {pending}")
    print(f"║  Needs outreach : {stage0}")
    print(f"║  WhatsApp       : {'✅ Connected' if wa_ok else '❌ Disconnected'}")
    print("╠══════════════════════════════════════════════════════╣")
    print(f"║  Started : {datetime.now().strftime('%d %b %Y %H:%M:%S')}")
    print("║  PM2 manages dashboard + scheduler automatically")
    print("╚══════════════════════════════════════════════════════╝")
    print()


def run_agency():
    print("\n" + "="*54)
    print("  Ghost Worker — Pre-flight check")
    print("="*54 + "\n")

    if not check_env():
        print("\nFix missing .env variables then run again.")
        sys.exit(1)

    check_dirs()
    check_database()
    check_templates()
    wa_ok = check_whatsapp()
    check_evolution_api()

    public_ip = get_public_ip()
    total, hot, deployed, pending, stage0 = get_db_stats()
    print_banner(public_ip, total, hot, deployed, pending, stage0, wa_ok)

    if not wa_ok:
        print("WARNING: WhatsApp disconnected.")
        print(f"Fix: open http://{public_ip}:8080/manager and reconnect")
        print()

    if stage0 > 0:
        print(f"ACTION NEEDED: {stage0} leads waiting for cold outreach")
        print("Run: python3 outreach_engine.py")
        print()

    print("Ghost Worker is managed by PM2.")
    print("Commands:")
    print("  pm2 status          — check processes")
    print("  pm2 logs            — view live logs")
    print("  pm2 restart all     — restart everything")
    print("  python3 scraper.py 'query'  — manual scrape")
    print("  python3 outreach_engine.py  — manual outreach")


if __name__ == "__main__":
    run_agency()
