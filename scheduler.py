import schedule
import time
import sqlite3
import os
import json
import logging
import subprocess
import boto3
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

LOG_DIR     = os.path.join(os.path.dirname(__file__), 'logs')
DB_PATH     = os.path.join(os.path.dirname(__file__), 'agency.db')
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON      = os.path.join(PROJECT_DIR, 'ghostenv', 'bin', 'python3')

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [scheduler] %(message)s',
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

# ── Query rotation state ──────────────────────────────────
# Tracks which query runs next in the rotation
_query_index = 0


def load_config():
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return {
            "scrape_interval_hours":   12,
            "pipeline_interval_hours": 1,
            "lifecycle_check_hours":   6,
            "scheduled_queries":       [],
        }


def get_next_query():
    """
    Returns the next query to run.
    - If only 1 query exists → always return that same query
    - If multiple queries → rotate through them in order
    """
    global _query_index
    cfg     = load_config()
    queries = cfg.get('scheduled_queries', [])

    if not queries:
        logger.warning("No queries in config — nothing to scrape")
        return None

    if len(queries) == 1:
        logger.info(f"Single query mode — always running: {queries[0]}")
        return queries[0]

    # Rotation mode — pick next in queue
    _query_index = _query_index % len(queries)
    query = queries[_query_index]
    logger.info(
        f"Queue rotation — running query {_query_index + 1}/{len(queries)}: {query}"
    )
    _query_index += 1
    return query


def run_script(script_name, args=None):
    script_path = os.path.join(PROJECT_DIR, script_name)
    cmd         = [PYTHON, script_path]
    if args:
        cmd.extend(args)
    try:
        logger.info(f"Running: {script_name} {args or ''}")
        result = subprocess.run(
            cmd,
            capture_output = True,
            text           = True,
            cwd            = PROJECT_DIR,
            timeout        = 600
        )
        if result.returncode == 0:
            logger.info(f"{script_name} completed OK")
        else:
            logger.error(f"{script_name} failed:\n{result.stderr[-400:]}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        logger.error(f"{script_name} timed out after 600s")
        return False
    except Exception as e:
        logger.error(f"{script_name} error: {e}")
        return False


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


# ── JOB 1: Scrape next query in rotation ─────────────────
def job_scrape():
    logger.info("JOB: Scrape — getting next query")
    try:
        query = get_next_query()
        if not query:
            return
        run_script('scraper.py', [query])
        logger.info(f"JOB: Scrape complete for: {query}")
    except Exception as e:
        logger.error(f"Scrape job error: {e}")


# ── JOB 2: AI Brain ──────────────────────────────────────
def job_ai_brain():
    try:
        with get_db() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE status='Scraped'"
            ).fetchone()[0]
        if pending > 0:
            logger.info(f"JOB: AI Brain — {pending} leads waiting")
            run_script('ai_brain.py')
        else:
            logger.info("JOB: AI Brain — nothing to process")
    except Exception as e:
        logger.error(f"AI Brain job error: {e}")


# ── JOB 3: Builder ───────────────────────────────────────
def job_builder():
    try:
        with get_db() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE status='AI_Complete'"
            ).fetchone()[0]
        if pending > 0:
            logger.info(f"JOB: Builder — {pending} leads waiting")
            run_script('builder.py')
        else:
            logger.info("JOB: Builder — nothing to build")
    except Exception as e:
        logger.error(f"Builder job error: {e}")


# ── JOB 4: S3 Deployer ───────────────────────────────────
def job_deployer():
    try:
        with get_db() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE status='Built'"
            ).fetchone()[0]
        if pending > 0:
            logger.info(f"JOB: Deployer — {pending} sites waiting")
            run_script('s3_deployer.py')
        else:
            logger.info("JOB: Deployer — nothing to deploy")
    except Exception as e:
        logger.error(f"Deployer job error: {e}")


# ── JOB 5: Outreach Writer ───────────────────────────────
def job_outreach_writer():
    try:
        with get_db() as conn:
            pending = conn.execute('''
                SELECT COUNT(*) FROM leads
                WHERE status='Deployed'
                AND (wa_draft_1 IS NULL OR wa_draft_1='')
                AND (outreach_paused IS NULL OR outreach_paused=0)
            ''').fetchone()[0]
        if pending > 0:
            logger.info(f"JOB: Outreach Writer — {pending} leads need messages")
            run_script('outreach_writer.py')
        else:
            logger.info("JOB: Outreach Writer — all messages written")
    except Exception as e:
        logger.error(f"Outreach writer job error: {e}")


# ── JOB 6: Outreach Engine (every 3 min for HOT strikes) ─
def job_outreach_engine():
    try:
        from outreach_engine import run_outreach_engine
        run_outreach_engine()
    except Exception as e:
        logger.error(f"Outreach engine error: {e}")


# ── JOB 7: Lifecycle manager ─────────────────────────────
def job_lifecycle():
    logger.info("JOB: Lifecycle manager")
    try:
        cutoff_cold = (
            datetime.now() - timedelta(hours=48)
        ).strftime('%Y-%m-%d %H:%M:%S')

        cutoff_dead = (
            datetime.now() - timedelta(days=7)
        ).strftime('%Y-%m-%d %H:%M:%S')

        with get_db() as conn:
            cold = conn.execute('''
                UPDATE leads SET lifecycle_status='COLD'
                WHERE status='Deployed'
                AND click_count=0
                AND lifecycle_status='NEW'
                AND created_at < ?
                AND (outreach_paused IS NULL OR outreach_paused=0)
            ''', (cutoff_cold,)).rowcount
            conn.commit()

            dead = conn.execute('''
                UPDATE leads SET lifecycle_status='DEAD'
                WHERE lifecycle_status IN ('COLD','NEW')
                AND followup_count >= 3
                AND created_at < ?
                AND (outreach_paused IS NULL OR outreach_paused=0)
            ''', (cutoff_dead,)).rowcount
            conn.commit()

        logger.info(f"Lifecycle: {cold} → COLD | {dead} → DEAD")

    except Exception as e:
        logger.error(f"Lifecycle job error: {e}")


# ── JOB 8: DB Backup ─────────────────────────────────────
def job_backup():
    logger.info("JOB: DB Backup")
    try:
        if not all([AWS_ACCESS_KEY, AWS_SECRET_KEY, S3_BUCKET]):
            logger.warning("Backup skipped — AWS credentials missing")
            return
        date_str    = datetime.now().strftime('%Y-%m-%d')
        backup_name = f"backups/agency_backup_{date_str}.db"
        s3 = boto3.client(
            's3',
            aws_access_key_id     = AWS_ACCESS_KEY,
            aws_secret_access_key = AWS_SECRET_KEY,
            region_name           = AWS_REGION
        )
        s3.upload_file(DB_PATH, S3_BUCKET, backup_name)
        logger.info(f"DB backed up: s3://{S3_BUCKET}/{backup_name}")
    except Exception as e:
        logger.error(f"Backup error: {e}")


# ── DYNAMIC SCHEDULE BUILDER ──────────────────────────────

def build_schedule():
    """
    Reads intervals from config.json and builds the schedule.
    Called on startup and can be re-called to rebuild after config changes.
    """
    schedule.clear()
    cfg = load_config()

    scrape_h   = cfg.get('scrape_interval_hours', 12)
    pipeline_h = cfg.get('pipeline_interval_hours', 1)
    lifecycle_h= cfg.get('lifecycle_check_hours', 6)

    # Core pipeline
    schedule.every(scrape_h).hours.do(job_scrape)
    schedule.every(pipeline_h).hours.do(job_ai_brain)
    schedule.every(pipeline_h).hours.do(job_builder)
    schedule.every(pipeline_h).hours.do(job_deployer)
    schedule.every(pipeline_h).hours.do(job_outreach_writer)

    # Outreach engine — every 3 minutes for HOT strike detection
    schedule.every(3).minutes.do(job_outreach_engine)

    # Lifecycle and backup
    schedule.every(lifecycle_h).hours.do(job_lifecycle)
    schedule.every(24).hours.do(job_backup)

    # Reload config every 30 minutes to pick up dashboard changes
    schedule.every(30).minutes.do(rebuild_schedule)

    logger.info(
        f"Schedule built — "
        f"Scrape: every {scrape_h}h | "
        f"Pipeline: every {pipeline_h}h | "
        f"Lifecycle: every {lifecycle_h}h | "
        f"HOT check: every 3min"
    )

    queries = cfg.get('scheduled_queries', [])
    if queries:
        logger.info(f"Query queue ({len(queries)}): {queries}")
    else:
        logger.warning("No queries configured — add queries from dashboard")


def rebuild_schedule():
    """Rebuilds schedule from latest config — picks up dashboard changes."""
    logger.info("Rebuilding schedule from config...")
    build_schedule()


def run_startup_pipeline():
    """Run all pipeline jobs once immediately on startup."""
    logger.info("Running startup pipeline check...")
    job_ai_brain()
    job_builder()
    job_deployer()
    job_outreach_writer()
    job_outreach_engine()
    job_lifecycle()


def run_scheduler():
    logger.info("Ghost Worker Scheduler — Starting")
    build_schedule()
    run_startup_pipeline()

    logger.info("Scheduler live — entering main loop")
    logger.info(f"Next scrape: {schedule.next_run()}")

    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user")
            break
        except Exception as e:
            logger.error(f"Scheduler loop error: {e} — continuing")
            time.sleep(60)


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  Ghost Worker — Scheduler")
    print("="*50 + "\n")
    run_scheduler()
