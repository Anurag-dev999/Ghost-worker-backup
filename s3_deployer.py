import sqlite3
import os
import logging
import requests
import boto3
from logging.handlers import RotatingFileHandler
from datetime import datetime
from dotenv import load_dotenv
from botocore.exceptions import ClientError

load_dotenv()

LOG_DIR    = os.path.join(os.path.dirname(__file__), 'logs')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'built_sites')
DB_PATH    = os.path.join(os.path.dirname(__file__), 'agency.db')

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [deployer] %(message)s',
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

AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_KEY")
S3_BUCKET      = os.getenv("S3_BUCKET_NAME")
AWS_REGION     = os.getenv("AWS_REGION", "ap-south-1")
SERVER_PORT    = os.getenv("SERVER_PORT", "5000")


def check_env():
    missing = []
    for var in ["AWS_ACCESS_KEY", "AWS_SECRET_KEY", "S3_BUCKET_NAME"]:
        if not os.getenv(var):
            missing.append(var)
    if missing:
        logger.error(f"Missing from .env: {', '.join(missing)}")
        return False
    logger.info("Environment check — OK")
    return True


def get_public_ip():
    try:
        ip = requests.get('https://api.ipify.org', timeout=5).text.strip()
        logger.info(f"Server public IP: {ip}")
        return ip
    except Exception as e:
        logger.error(f"Could not get public IP: {e}")
        return "YOUR_SERVER_IP"


def get_built_leads():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM leads WHERE status='Built' ORDER BY reviews_count DESC"
            )
            leads = [dict(row) for row in cursor.fetchall()]
            logger.info(f"Leads ready to deploy: {len(leads)}")
            return leads
    except Exception as e:
        logger.error(f"Could not fetch leads: {e}")
        return []


def get_s3_client():
    return boto3.client(
        's3',
        aws_access_key_id     = AWS_ACCESS_KEY,
        aws_secret_access_key = AWS_SECRET_KEY,
        region_name           = AWS_REGION
    )


def upload_to_s3(s3_client, filepath, filename):
    try:
        s3_client.upload_file(
            filepath,
            S3_BUCKET,
            filename,
            ExtraArgs={
                'ContentType': 'text/html',
                'CacheControl': 'max-age=3600',
            }
        )
        s3_url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{filename}"
        logger.info(f"  Uploaded to S3: {s3_url}")
        return s3_url

    except ClientError as e:
        logger.error(f"  S3 upload failed: {e}")
        return None
    except Exception as e:
        logger.error(f"  Upload error: {e}")
        return None


def update_html_tracker_url(filepath, lead_id, public_ip):
    # Replace the placeholder tracker URL with the real one
    tracker_url = f"http://{public_ip}:{SERVER_PORT}/view/{lead_id}"
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            html = f.read()

        html = html.replace(
            f"http://YOUR_SERVER_IP:{SERVER_PORT}/view/{lead_id}",
            tracker_url
        )
        # Also catch generic placeholder in case it was set differently
        html = html.replace("http://YOUR_SERVER_IP:5000", f"http://{public_ip}:{SERVER_PORT}")

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)

        return tracker_url
    except Exception as e:
        logger.error(f"  Could not update tracker URL: {e}")
        return tracker_url


def update_db_deployed(lead_id, s3_url, tracker_url):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute('''
                UPDATE leads SET
                    s3_url       = ?,
                    tracker_url  = ?,
                    status       = 'Deployed'
                WHERE id = ?
            ''', (s3_url, tracker_url, lead_id))
            conn.commit()
        logger.info(f"  DB updated — status: Deployed")
    except Exception as e:
        logger.error(f"  DB update error: {e}")


def run_deployer():
    logger.info("Ghost Worker S3 Deployer — Starting")

    if not check_env():
        return

    public_ip = get_public_ip()
    s3_client = get_s3_client()
    leads     = get_built_leads()

    if not leads:
        logger.info("No Built leads to deploy — run builder.py first")
        return

    deployed = 0
    failed   = 0

    for lead in leads:
        lead_id  = lead['id']
        name     = lead['business_name']
        phone    = lead.get('phone', '')

        import re
        phone_clean = re.sub(r'[^\d]', '', phone)
        filename    = f"{phone_clean}.html"
        filepath    = os.path.join(OUTPUT_DIR, filename)

        logger.info(f"\n── Deploying: {name[:50]} (ID: {lead_id})")

        if not os.path.exists(filepath):
            logger.error(f"  HTML file not found: {filepath}")
            failed += 1
            continue

        # Step 1 — inject real tracker URL into HTML before uploading
        tracker_url = update_html_tracker_url(filepath, lead_id, public_ip)
        logger.info(f"  Tracker URL: {tracker_url}")

        # Step 2 — upload to S3
        s3_url = upload_to_s3(s3_client, filepath, filename)

        if s3_url:
            # Step 3 — save both URLs to DB
            update_db_deployed(lead_id, s3_url, tracker_url)
            deployed += 1
            logger.info(f"  Live at: {s3_url}")
        else:
            failed += 1

    # Final summary
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        total_deployed = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='Deployed'"
        ).fetchone()[0]

    logger.info(f"\n{'='*50}")
    logger.info(f"DEPLOYER COMPLETE")
    logger.info(f"Deployed : {deployed}")
    logger.info(f"Failed   : {failed}")
    logger.info(f"Live sites: {total_deployed}")
    logger.info(f"{'='*50}")
    logger.info("Next step: python3 dashboard_tracker.py")


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  Ghost Worker — S3 Deployer")
    print("="*50 + "\n")
    run_deployer()
