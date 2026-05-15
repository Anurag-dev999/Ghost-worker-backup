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
            maxBytes=5 * 1024 * 1024,
            backupCount=3
        ),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON      = os.path.join(PROJECT_DIR, 'ghostenv', 'bin', 'python3')

REQUIRED_ENV_VARS = [
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "APIFY_API_TOKEN",
    "AWS_ACCESS_KEY",
    "AWS_SECRET_KEY",
    "S3_BUCKET_NAME",
]

REQUIRED_DIRS = [
    os.path.join(PROJECT_DIR, 'templates'),
    os.path.join(PROJECT_DIR, 'built_sites'),
    os.path.join(PROJECT_DIR, 'logs'),
]


def check_env_vars():
    logger.info("Checking environment variables...")
    missing = []
    for var in REQUIRED_ENV_VARS:
        if not os.getenv(var):
            missing.append(var)
            print(f"  ERROR: {var} not found. Please add it to your .env file.")

    if missing:
        logger.error(f"Missing env vars: {', '.join(missing)}")
        return False

    logger.info(f"All {len(REQUIRED_ENV_VARS)} environment variables — OK")
    return True


def check_dirs():
    logger.info("Checking required directories...")
    for d in REQUIRED_DIRS:
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
            logger.info(f"  Created: {d}")
        else:
            logger.info(f"  OK: {d}")
    return True


def check_database():
    logger.info("Checking database...")
    if not os.path.exists(DB_PATH):
        logger.warning("agency.db not found — running init_agency.py...")
        result = subprocess.run(
            [PYTHON, os.path.join(PROJECT_DIR, 'init_agency.py')],
            cwd=PROJECT_DIR
        )
        if result.returncode != 0:
            logger.error("Database initialization failed")
            return False
        logger.info("Database initialized successfully")
    else:
        logger.info("Database exists — OK")
    return True


def check_templates():
    template_dir  = os.path.join(PROJECT_DIR, 'templates')
    default_tmpl  = os.path.join(template_dir, 'default.html')
    if not os.path.exists(default_tmpl):
        logger.warning("default.html template missing — dashboard will work but builder needs it")
    else:
        templates = [f for f in os.listdir(template_dir) if f.endswith('.html')]
        logger.info(f"Templates found: {', '.join(templates)}")
    return True


def get_public_ip():
    try:
        return requests.get('https://api.ipify.org', timeout=5).text.strip()
    except:
        return "YOUR_EC2_IP"


def get_db_stats():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            total = conn.execute(
                "SELECT COUNT(*) FROM leads"
            ).fetchone()[0]
            hot = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE lifecycle_status='HOT'"
            ).fetchone()[0]
            deployed = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE status='Deployed'"
            ).fetchone()[0]
            pending_ai = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE status='Scraped'"
            ).fetchone()[0]
            return total, hot, deployed, pending_ai
    except Exception as e:
        logger.error(f"Could not read DB stats: {e}")
        return 0, 0, 0, 0


def start_dashboard():
    logger.info("Starting dashboard on port 5000...")
    try:
        subprocess.run(
            [
                os.path.join(PROJECT_DIR, 'ghostenv', 'bin', 'gunicorn'),
                '-w', '2',
                '-b', '0.0.0.0:5000',
                '--timeout', '120',
                'dashboard_tracker:app'
            ],
            cwd=PROJECT_DIR
        )
    except Exception as e:
        logger.error(f"Dashboard crashed: {e}")


def start_scheduler():
    logger.info("Starting scheduler...")
    try:
        subprocess.run(
            [PYTHON, os.path.join(PROJECT_DIR, 'scheduler.py')],
            cwd=PROJECT_DIR
        )
    except Exception as e:
        logger.error(f"Scheduler crashed: {e}")


def print_banner(public_ip, total, hot, deployed, pending_ai):
    print("\n")
    print("╔══════════════════════════════════════════════════════╗")
    print("║          GHOST WORKER — SYSTEM IS LIVE               ║")
    print("╠══════════════════════════════════════════════════════╣")
    print(f"║  Dashboard  : http://{public_ip}:5000")
    print(f"║  Health     : http://{public_ip}:5000/health")
    print("╠══════════════════════════════════════════════════════╣")
    print(f"║  Total leads   : {total}")
    print(f"║  HOT leads     : {hot}")
    print(f"║  Deployed sites: {deployed}")
    print(f"║  Pending AI    : {pending_ai}")
    print("╠══════════════════════════════════════════════════════╣")
    print(f"║  Started at : {datetime.now().strftime('%d %b %Y %H:%M:%S')}")
    print("║  Press Ctrl+C to stop")
    print("╚══════════════════════════════════════════════════════╝")
    print()


def run_agency():
    print("\n" + "="*54)
    print("  Ghost Worker — Starting up...")
    print("="*54 + "\n")

    # ── Pre-flight checks ────────────────────────────────
    if not check_env_vars():
        print("\nFix missing .env variables then run again.")
        sys.exit(1)

    check_dirs()

    if not check_database():
        print("\nDatabase setup failed. Check logs/ghost_worker.log")
        sys.exit(1)

    check_templates()

    public_ip = get_public_ip()
    logger.info(f"Server IP: {public_ip}")

    # ── Launch dashboard in background thread ────────────
    dashboard_thread = threading.Thread(
        target=start_dashboard,
        daemon=True,
        name="dashboard"
    )
    dashboard_thread.start()
    logger.info("Dashboard thread started")
    time.sleep(3)

    # ── Launch scheduler in background thread ────────────
    scheduler_thread = threading.Thread(
        target=start_scheduler,
        daemon=True,
        name="scheduler"
    )
    scheduler_thread.start()
    logger.info("Scheduler thread started")
    time.sleep(2)

    # ── Print startup banner ─────────────────────────────
    total, hot, deployed, pending_ai = get_db_stats()
    print_banner(public_ip, total, hot, deployed, pending_ai)

    # ── Keep alive — monitor threads ─────────────────────
    while True:
        try:
            time.sleep(60)

            if not dashboard_thread.is_alive():
                logger.warning("Dashboard thread died — restarting...")
                dashboard_thread = threading.Thread(
                    target=start_dashboard,
                    daemon=True,
                    name="dashboard"
                )
                dashboard_thread.start()

            if not scheduler_thread.is_alive():
                logger.warning("Scheduler thread died — restarting...")
                scheduler_thread = threading.Thread(
                    target=start_scheduler,
                    daemon=True,
                    name="scheduler"
                )
                scheduler_thread.start()

        except KeyboardInterrupt:
            logger.info("Ghost Worker stopped by user")
            print("\n  Ghost Worker stopped. Goodbye.")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Orchestrator error: {e}")
            time.sleep(30)


if __name__ == "__main__":
    run_agency()
