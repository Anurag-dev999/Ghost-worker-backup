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
_query_index = 0


def load_config():
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return {
            "scrape_interval_hours": 8,
            "lifecycle_check_hours": 6,
            "scheduled_queries":     [],
        }


def get_next_query():
    """
    Sniper mode rotation:
    - 1 query  → always run same query
    - N queries → rotate one at a time, each run gets next in queue
    """
    global _query_index
    cfg     = load_config()
    queries = cfg.get('scheduled_queries', [])

    if not queries:
        logger.warning("No queries configured — add queries from dashboard")
        return None

    if len(queries) == 1:
        logger.info(f"Single query mode: {queries[0]}")
        return queries[0]

    _query_index = _query_index % len(queries)
    query        = queries[_query_index]
    logger.info(
        f"Query {_query_index + 1}/{len(queries)}: {query}"
    )
    _query_index += 1
    return query


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def run_script(script_name, args=None, timeout=600):
    """Run a Python script synchronously. Returns True on success."""
    script_path = os.path.join(PROJECT_DIR, script_name)
    cmd         = [PYTHON, script_path]
    if args:
        cmd.extend(args)
    try:
        logger.info(f"▶ Running: {script_name} {args or ''}")
        result = subprocess.run(
            cmd,
            cwd     = PROJECT_DIR,
            timeout = timeout,
        )
        if result.returncode == 0:
            logger.info(f"✓ {script_name} completed")
            return True
        else:
            logger.error(f"✗ {script_name} failed (exit code {result.returncode})")
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"✗ {script_name} timed out after {timeout}s")
        return False
    except Exception as e:
        logger.error(f"✗ {script_name} error: {e}")
        return False


# ═══════════════════════════════════════════════════════════
# SNIPER MISSION — runs one complete pipeline per interval
# ═══════════════════════════════════════════════════════════

def job_run_mission():
    """
    The complete Ghost Worker mission for ONE query:
    Scrape → AI → Build → Deploy → Write Messages → Send Outreach

    Runs sequentially. Each step waits for the previous to finish.
    One failure logs the error but tries to continue where possible.
    """
    query = get_next_query()
    if not query:
        logger.warning("Mission aborted — no query configured")
        return

    logger.info(f"\n{'='*60}")
    logger.info(f"MISSION START: {query}")
    logger.info(f"Time: {datetime.now().strftime('%d %b %Y %H:%M:%S')}")
    logger.info(f"{'='*60}")

    mission_start = datetime.now()

    # ── Step 1: Scrape ───────────────────────────────────
    logger.info("STEP 1/6 — Scraping leads...")
    scrape_ok = run_script('scraper.py', [query], timeout=300)
    if not scrape_ok:
        logger.error("Scrape failed — checking if existing leads need processing")

    # Check if there's anything to process
    with get_db() as conn:
        pending_ai = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='Scraped'"
        ).fetchone()[0]
        pending_build = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='AI_Complete'"
        ).fetchone()[0]
        pending_deploy = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='Built'"
        ).fetchone()[0]

    logger.info(
        f"Pipeline status — "
        f"Scraped:{pending_ai} | "
        f"AI_Complete:{pending_build} | "
        f"Built:{pending_deploy}"
    )

    if pending_ai == 0 and pending_build == 0 and pending_deploy == 0:
        logger.info("Nothing new to process — mission complete (no new leads)")
        return

    # ── Step 2: AI Brain ─────────────────────────────────
    if pending_ai > 0:
        logger.info(f"STEP 2/6 — AI Brain ({pending_ai} leads)...")
        run_script('ai_brain.py', timeout=300)
    else:
        logger.info("STEP 2/6 — AI Brain skipped (no scraped leads)")

    # ── Step 3: Builder ──────────────────────────────────
    with get_db() as conn:
        pending_build = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='AI_Complete'"
        ).fetchone()[0]

    if pending_build > 0:
        logger.info(f"STEP 3/6 — Builder ({pending_build} leads)...")
        run_script('builder.py', timeout=120)
    else:
        logger.info("STEP 3/6 — Builder skipped (no AI_Complete leads)")

    # ── Step 4: S3 Deployer ──────────────────────────────
    with get_db() as conn:
        pending_deploy = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='Built'"
        ).fetchone()[0]

    if pending_deploy > 0:
        logger.info(f"STEP 4/6 — S3 Deployer ({pending_deploy} leads)...")
        run_script('s3_deployer.py', timeout=120)
    else:
        logger.info("STEP 4/6 — S3 Deployer skipped (no Built leads)")

    # ── Step 5: Outreach Writer ──────────────────────────
    with get_db() as conn:
        needs_msgs = conn.execute('''
            SELECT COUNT(*) FROM leads
            WHERE status='Deployed'
            AND (wa_draft_1 IS NULL OR wa_draft_1='')
            AND (outreach_paused IS NULL OR outreach_paused=0)
        ''').fetchone()[0]

    if needs_msgs > 0:
        logger.info(f"STEP 5/6 — Writing outreach messages ({needs_msgs} leads)...")
        run_script('outreach_writer.py', timeout=300)
    else:
        logger.info("STEP 5/6 — Outreach Writer skipped (all messages written)")

    # ── Step 6: Send outreach ────────────────────────────
    logger.info("STEP 6/6 — Sending outreach messages...")
    try:
        from outreach_engine import run_outreach_engine
        run_outreach_engine()
    except Exception as e:
        logger.error(f"Outreach engine error: {e}")

    # ── Mission summary ──────────────────────────────────
    duration = (datetime.now() - mission_start).seconds
    with get_db() as conn:
        total    = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        deployed = conn.execute("SELECT COUNT(*) FROM leads WHERE status='Deployed'").fetchone()[0]
        hot      = conn.execute("SELECT COUNT(*) FROM leads WHERE lifecycle_status='HOT'").fetchone()[0]

    logger.info(f"\n{'='*60}")
    logger.info(f"MISSION COMPLETE: {query}")
    logger.info(f"Duration : {duration}s")
    logger.info(f"DB total : {total} leads | {deployed} deployed | {hot} HOT")
    cfg = load_config()
    queries = cfg.get('scheduled_queries', [])
    interval = cfg.get('scrape_interval_hours', 8)
    logger.info(f"Next mission in {interval}h")
    logger.info(f"{'='*60}\n")


# ═══════════════════════════════════════════════════════════
# BACKGROUND JOBS — run independently of missions
# ═══════════════════════════════════════════════════════════

def job_outreach_engine():
    """Every 3 minutes — HOT strike detection only."""
    try:
        from outreach_engine import run_outreach_engine
        run_outreach_engine()
    except Exception as e:
        logger.error(f"Outreach engine error: {e}")


def job_lifecycle():
    """Every 6 hours — mark COLD and DEAD leads."""
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
        logger.error(f"Lifecycle error: {e}")


def job_backup():
    """Every 24 hours — backup DB to S3."""
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


_last_config = {}

def rebuild_schedule():
    """Only rebuild if config actually changed — prevents timer drift."""
    global _last_config
    cfg = load_config()
    check_keys = ['scrape_interval_hours', 'lifecycle_check_hours', 'scheduled_queries']
    changed = any(cfg.get(k) != _last_config.get(k) for k in check_keys)
    if changed:
        logger.info("Config changed — rebuilding schedule")
        _last_config = {k: cfg.get(k) for k in check_keys}
        build_schedule()
    else:
        logger.debug("Config unchanged — schedule kept")


# ═══════════════════════════════════════════════════════════
# SCHEDULE BUILDER
# ═══════════════════════════════════════════════════════════

def build_schedule():
    schedule.clear()
    cfg      = load_config()
    interval = cfg.get('scrape_interval_hours', 8)
    life_h   = cfg.get('lifecycle_check_hours', 6)
    queries  = cfg.get('scheduled_queries', [])

    # ── Master mission timer ──────────────────────────────
    schedule.every(interval).hours.do(job_run_mission)

    # ── Background tasks ──────────────────────────────────
    schedule.every(3).minutes.do(job_outreach_engine)
    schedule.every(life_h).hours.do(job_lifecycle)
    schedule.every(24).hours.do(job_backup)

    # ── Config reload every 30 min ────────────────────────
    schedule.every(30).minutes.do(rebuild_schedule)

    logger.info(f"Schedule built:")
    logger.info(f"  Sniper mission  : every {interval}h")
    logger.info(f"  HOT check       : every 3min")
    logger.info(f"  Lifecycle       : every {life_h}h")
    logger.info(f"  DB backup       : every 24h")
    logger.info(f"  Config reload   : every 30min")
    if queries:
        logger.info(f"  Query queue ({len(queries)}): {queries}")
    else:
        logger.warning("  No queries configured — add from dashboard")


def run_startup_pipeline():
    """Fire one complete mission immediately on boot."""
    logger.info("Startup mission — running immediately...")
    try:
        job_run_mission()
    except Exception as e:
        logger.error(f"Startup mission error: {e}")


def run_scheduler():
    logger.info("Ghost Worker Scheduler (Sniper Mode) — Starting")
    build_schedule()
    run_startup_pipeline()

    logger.info("Scheduler live — entering main loop")
    cfg = load_config()
    logger.info(f"Next mission in {cfg.get('scrape_interval_hours', 8)}h")

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
    print("\n" + "="*60)
    print("  Ghost Worker — Scheduler (Sniper Mode)")
    print("="*60 + "\n")
    run_scheduler()
