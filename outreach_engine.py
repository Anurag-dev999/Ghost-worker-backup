import sqlite3
import os
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
DB_PATH = os.path.join(os.path.dirname(__file__), 'agency.db')
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [outreach] %(message)s',
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

SMTP_HOST    = os.getenv("SMTP_HOST", "smtp.hostinger.com")
SMTP_PORT    = int(os.getenv("SMTP_PORT", 465))
SMTP_EMAIL   = os.getenv("SMTP_EMAIL", "hello@fixingmyself.sbs")
SMTP_PASS    = os.getenv("SMTP_PASSWORD", "")
AGENCY_NAME  = os.getenv("AGENCY_NAME", "LaunchPad Web")

# Timing config
HOURS_TO_FOLLOWUP1   = 48
HOURS_TO_FOLLOWUP2   = 96
DAYS_TO_FINAL        = 30
DAYS_TO_DELETE       = 5
MINUTES_HOT_STRIKE   = 3


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def send_email(to_email, subject, body):
    """Send email via Hostinger SMTP."""
    if not to_email or not SMTP_PASS:
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['From']    = f"{AGENCY_NAME} <{SMTP_EMAIL}>"
        msg['To']      = to_email
        msg['Subject'] = subject

        # Plain text version
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        # HTML version — wraps body in simple styling
        html_body = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#333">
        <p>{body.replace(chr(10), '<br>')}</p>
        <hr style="border:none;border-top:1px solid #eee;margin:20px 0">
        <p style="font-size:12px;color:#999">{AGENCY_NAME} · {SMTP_EMAIL}</p>
        </body></html>"""
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_EMAIL, SMTP_PASS)
            server.sendmail(SMTP_EMAIL, to_email, msg.as_string())

        logger.info(f"  Email sent to {to_email} | Subject: {subject}")
        return True

    except Exception as e:
        logger.error(f"  Email failed to {to_email}: {e}")
        return False


def update_stage(conn, lead_id, new_stage, extra_updates=None):
    """Update outreach stage and timestamp."""
    updates = {
        'stage':      new_stage,
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'id':         lead_id
    }
    if extra_updates:
        updates.update(extra_updates)

    sql = "UPDATE leads SET outreach_stage=:stage, stage_updated_at=:updated_at"

    if 'followup_count' in (extra_updates or {}):
        sql += ", followup_count=:followup_count"
    if 'last_contacted' in (extra_updates or {}):
        sql += ", last_contacted=:last_contacted"
    if 'lifecycle_status' in (extra_updates or {}):
        sql += ", lifecycle_status=:lifecycle_status"
    if 'scheduled_delete_at' in (extra_updates or {}):
        sql += ", scheduled_delete_at=:scheduled_delete_at"
    if 'email_sent_count' in (extra_updates or {}):
        sql += ", email_sent_count=:email_sent_count"

    sql += " WHERE id=:id"
    conn.execute(sql, updates)
    conn.commit()


def process_stage_0(conn, lead):
    """
    Stage 0 → 1: New deployed lead.
    Write messages (if not written) then mark ready for outreach.
    Dashboard shows wa_draft_1 for manual send.
    Email sent automatically if email exists.
    """
    lead_id = lead['id']
    name    = lead['business_name']

    # Messages not written yet — trigger writer
    if not lead['wa_draft_1']:
        logger.info(f"  Stage 0: Messages not written yet for ID:{lead_id} — triggering writer")
        from outreach_writer import write_all_messages
        write_all_messages(dict(lead))
        # Reload lead with fresh messages
        lead = dict(conn.execute(
            "SELECT * FROM leads WHERE id=?", (lead_id,)
        ).fetchone())

    # Send email if available
    if lead['email'] and lead['email_draft_1']:
        try:
            em = json.loads(lead['email_draft_1'])
            sent = send_email(lead['email'], em['subject'], em['body'])
            if sent:
                update_stage(conn, lead_id, 1, {
                    'email_sent_count': 1,
                    'last_contacted':   datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'followup_count':   1
                })
                logger.info(f"  Stage 0→1: Email sent to {name[:30]}")
                return
        except Exception as e:
            logger.error(f"  Email parse error: {e}")

    # No email — just advance stage, WA shown on dashboard
    update_stage(conn, lead_id, 1, {
        'last_contacted': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })
    logger.info(f"  Stage 0→1: {name[:30]} — WA draft ready on dashboard")


def process_hot_strike(conn, lead):
    """
    Special: Lead clicked tracker URL (HOT).
    If outreach_stage = 1 and click happened within last 3 minutes
    and hot strike not sent yet (stage != 2) → send HOT STRIKE.
    """
    lead_id      = lead['id']
    name         = lead['business_name']
    last_clicked = lead['last_clicked']

    if not last_clicked:
        return

    try:
        clicked_at = datetime.strptime(last_clicked, '%Y-%m-%d %H:%M:%S')
        minutes_since = (datetime.now() - clicked_at).total_seconds() / 60
    except:
        return

    # Only fire within 3 minutes of click
    if minutes_since > MINUTES_HOT_STRIKE:
        return

    logger.info(f"  HOT STRIKE: {name[:30]} clicked {minutes_since:.1f}min ago!")

    # Send hot email if available
    if lead['email'] and lead['email_draft_hot']:
        try:
            em   = json.loads(lead['email_draft_hot'])
            sent = send_email(lead['email'], em['subject'], em['body'])
            if sent:
                logger.info(f"  HOT email sent")
        except Exception as e:
            logger.error(f"  HOT email error: {e}")

    # Advance to stage 2 — WA hot draft shown on dashboard
    update_stage(conn, lead_id, 2, {
        'followup_count': (lead['followup_count'] or 0) + 1,
        'last_contacted': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })
    logger.info(f"  Stage 1→2 (HOT): WA hot draft ready on dashboard")


def process_followup1(conn, lead):
    """Stage 2 → 3: 48h after cold intro, no reply."""
    lead_id      = lead['id']
    last_contact = lead['last_contacted']
    if not last_contact:
        return

    hours_since = (
        datetime.now() -
        datetime.strptime(last_contact, '%Y-%m-%d %H:%M:%S')
    ).total_seconds() / 3600

    if hours_since < HOURS_TO_FOLLOWUP1:
        return

    name = lead['business_name']
    logger.info(f"  Followup 1: {name[:30]} ({hours_since:.0f}h since contact)")

    if lead['email'] and lead['email_draft_2']:
        try:
            em   = json.loads(lead['email_draft_2'])
            sent = send_email(lead['email'], em['subject'], em['body'])
        except Exception as e:
            logger.error(f"  F1 email error: {e}")

    update_stage(conn, lead_id, 3, {
        'followup_count': (lead['followup_count'] or 0) + 1,
        'last_contacted': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })
    logger.info(f"  Stage 2→3: F1 done — WA draft 2 on dashboard")


def process_followup2(conn, lead):
    """Stage 3 → 4: 48h after followup 1."""
    lead_id      = lead['id']
    last_contact = lead['last_contacted']
    if not last_contact:
        return

    hours_since = (
        datetime.now() -
        datetime.strptime(last_contact, '%Y-%m-%d %H:%M:%S')
    ).total_seconds() / 3600

    if hours_since < HOURS_TO_FOLLOWUP2:
        return

    name = lead['business_name']
    logger.info(f"  Followup 2: {name[:30]}")

    if lead['email'] and lead['email_draft_3']:
        try:
            em   = json.loads(lead['email_draft_3'])
            sent = send_email(lead['email'], em['subject'], em['body'])
        except Exception as e:
            logger.error(f"  F2 email error: {e}")

    update_stage(conn, lead_id, 4, {
        'followup_count': (lead['followup_count'] or 0) + 1,
        'last_contacted': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })
    logger.info(f"  Stage 3→4: F2 done — WA draft 3 on dashboard")


def process_final(conn, lead):
    """Stage 4 → 5: 30 days total, send final message."""
    lead_id    = lead['id']
    created_at = lead['created_at']
    if not created_at:
        return

    days_since = (
        datetime.now() -
        datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
    ).days

    if days_since < DAYS_TO_FINAL:
        return

    name = lead['business_name']
    logger.info(f"  Final message: {name[:30]} ({days_since} days old)")

    if lead['email'] and lead['email_draft_hot']:
        try:
            # email_draft_4 not in schema — use email_draft_hot as final
            # In future add email_draft_4 column
            em   = json.loads(lead['email_draft_hot'])
            sent = send_email(lead['email'], f"Last message — {em['subject']}", em['body'])
        except Exception as e:
            logger.error(f"  Final email error: {e}")

    delete_at = (datetime.now() + timedelta(days=DAYS_TO_DELETE)).strftime('%Y-%m-%d %H:%M:%S')

    update_stage(conn, lead_id, 5, {
        'lifecycle_status':   'DEAD',
        'scheduled_delete_at': delete_at,
        'last_contacted':      datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })
    logger.info(f"  Stage 4→5: Final sent. Delete scheduled: {delete_at}")


def process_deletions(conn):
    """Delete leads past their scheduled_delete_at date."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    leads = conn.execute('''
        SELECT id, business_name FROM leads
        WHERE scheduled_delete_at IS NOT NULL
        AND scheduled_delete_at <= ?
        AND lifecycle_status = 'DEAD'
        AND (outreach_paused IS NULL OR outreach_paused = 0)
    ''', (now,)).fetchall()

    for lead in leads:
        conn.execute("DELETE FROM leads WHERE id=?", (lead['id'],))
        logger.info(f"  DELETED: {lead['business_name'][:40]} (ID:{lead['id']})")

    if leads:
        conn.commit()
        logger.info(f"  Total deleted: {len(leads)}")


def run_outreach_engine():
    logger.info("Outreach Engine — Starting cycle")

    try:
        with get_db() as conn:

            # ── Process deletions first ───────────────────
            process_deletions(conn)

            # ── Load all active non-paused leads ─────────
            leads = conn.execute('''
                SELECT * FROM leads
                WHERE status = 'Deployed'
                AND (outreach_paused IS NULL OR outreach_paused = 0)
                ORDER BY outreach_stage ASC, created_at ASC
            ''').fetchall()
            leads = [dict(l) for l in leads]

            logger.info(f"Active leads to process: {len(leads)}")

            for lead in leads:
                stage     = lead['outreach_stage'] or 0
                lifecycle = lead['lifecycle_status']

                # Skip HOT leads that are being managed manually
                # unless they need a hot strike
                if lifecycle == 'HOT' and stage == 1:
                    process_hot_strike(conn, lead)
                    continue

                if lifecycle == 'HOT' and stage > 1:
                    # Already had hot strike — skip automated followups
                    # You handle HOT leads manually
                    continue

                if stage == 0:
                    process_stage_0(conn, lead)

                elif stage == 1:
                    # Check if they clicked — hot strike
                    if lead['click_count'] and lead['click_count'] > 0:
                        process_hot_strike(conn, lead)
                    # If no click yet — check if 48h passed for followup
                    else:
                        process_followup1(conn, lead)

                elif stage == 2:
                    process_followup1(conn, lead)

                elif stage == 3:
                    process_followup2(conn, lead)

                elif stage == 4:
                    process_final(conn, lead)

                # Stage 5 = DEAD, waiting for deletion — handled above

        logger.info("Outreach Engine — Cycle complete")

    except Exception as e:
        logger.error(f"Outreach engine error: {e}")


if __name__ == "__main__":
    print("\n" + "="*50)
    print("  Ghost Worker — Outreach Engine")
    print("="*50 + "\n")
    run_outreach_engine()
