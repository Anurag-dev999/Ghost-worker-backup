import sqlite3
import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [init] %(message)s',
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

DB_PATH = os.path.join(os.path.dirname(__file__), 'agency.db')

def init_database():
    logger.info("Starting database initialization...")

    db_existed = os.path.exists(DB_PATH)

    try:
        with sqlite3.connect(DB_PATH) as conn:

            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

            conn.execute('''
                CREATE TABLE IF NOT EXISTS leads (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_name       TEXT NOT NULL,
                    phone               TEXT,
                    email               TEXT,
                    niche               TEXT,
                    address             TEXT,
                    city                TEXT,
                    rating              REAL,
                    reviews_count       INTEGER DEFAULT 0,
                    website_url         TEXT,
                    tier                INTEGER DEFAULT 1,
                    website_score       INTEGER,
                    query_string        TEXT,
                    ai_headline         TEXT,
                    ai_about            TEXT,
                    ai_pitch_whatsapp   TEXT,
                    ai_pitch_email      TEXT,
                    ai_footer           TEXT,
                    template_used       TEXT,
                    s3_url              TEXT,
                    tracker_url         TEXT,
                    status              TEXT DEFAULT 'Scraped',
                    lifecycle_status    TEXT DEFAULT 'NEW',
                    click_count         INTEGER DEFAULT 0,
                    last_clicked        TEXT,
                    followup_count      INTEGER DEFAULT 0,
                    last_contacted      TEXT,
                    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_status
                ON leads(status)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_lifecycle
                ON leads(lifecycle_status)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_phone
                ON leads(phone)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_tier
                ON leads(tier)
            ''')

            conn.commit()

            cursor = conn.execute("SELECT COUNT(*) FROM leads")
            lead_count = cursor.fetchone()[0]

        if db_existed:
            logger.info(f"Database already existed — verified and updated.")
        else:
            logger.info(f"Fresh database created at: {DB_PATH}")

        logger.info(f"Table: leads — OK")
        logger.info(f"Indexes: status, lifecycle, phone, tier — OK")
        logger.info(f"WAL mode: ON")
        logger.info(f"Current lead count: {lead_count}")
        logger.info("Database initialization complete.")

        return True

    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return False


def verify_database():
    logger.info("Running database verification...")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA journal_mode=WAL")

            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='leads'"
            )
            if not cursor.fetchone():
                logger.error("leads table NOT FOUND — run init again")
                return False

            cursor = conn.execute("PRAGMA table_info(leads)")
            columns = [row[1] for row in cursor.fetchall()]

            required = [
                'id', 'business_name', 'phone', 'email', 'niche',
                'address', 'city', 'rating', 'reviews_count', 'website_url',
                'tier', 'website_score', 'query_string', 'ai_headline',
                'ai_about', 'ai_pitch_whatsapp', 'ai_pitch_email',
                'ai_footer', 'template_used', 's3_url', 'tracker_url',
                'status', 'lifecycle_status', 'click_count', 'last_clicked',
                'followup_count', 'last_contacted', 'created_at'
            ]

            missing = [col for col in required if col not in columns]

            if missing:
                logger.error(f"Missing columns: {missing}")
                return False

            logger.info(f"All {len(required)} columns verified — OK")

            conn.execute('''
                INSERT INTO leads (
                    business_name, phone, city, niche,
                    rating, reviews_count, tier, query_string,
                    status, lifecycle_status
                ) VALUES (
                    'TEST LEAD - DELETE ME', '0000000000', 'TestCity',
                    'test', 4.5, 100, 1, 'test query',
                    'Scraped', 'NEW'
                )
            ''')
            conn.commit()

            cursor = conn.execute(
                "SELECT id FROM leads WHERE phone='0000000000'"
            )
            test_id = cursor.fetchone()[0]

            conn.execute("DELETE FROM leads WHERE id=?", (test_id,))
            conn.commit()

            logger.info("Write + delete test — OK")
            logger.info("Database verification PASSED.")
            return True

    except Exception as e:
        logger.error(f"Verification failed: {e}")
        return False


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  Ghost Worker — Database Initializer")
    print("="*50 + "\n")

    success = init_database()

    if success:
        verified = verify_database()
        if verified:
            print("\n" + "="*50)
            print("  SUCCESS — Database is ready.")
            print(f"  Location: {DB_PATH}")
            print("  Next step: run scraper.py")
            print("="*50 + "\n")
        else:
            print("\n  ERROR — Verification failed. Check logs/ghost_worker.log")
    else:
        print("\n  ERROR — Init failed. Check logs/ghost_worker.log")
