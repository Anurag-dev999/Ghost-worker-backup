"""
Ghost Worker — Analytics Sync
Syncs lead data to Google Sheets daily for business analysis.
Tracks: conversion rates, message performance, niche patterns, city data.
"""

import sqlite3
import os
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

LOG_DIR      = os.path.join(os.path.dirname(__file__), 'logs')
DB_PATH      = os.path.join(os.path.dirname(__file__), 'agency.db')
CREDS_FILE   = os.path.join(os.path.dirname(__file__), 'google_credentials.json')
SHEET_ID     = os.getenv('GOOGLE_SHEET_ID', '')

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [analytics] %(message)s',
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


def get_sheet_client():
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    creds  = Credentials.from_service_account_file(CREDS_FILE, scopes=scopes)
    return gspread.authorize(creds)


def get_or_create_worksheet(spreadsheet, title, headers):
    """Get worksheet by title or create it with headers."""
    try:
        ws = spreadsheet.worksheet(title)
        # Check if headers exist
        existing = ws.row_values(1)
        if not existing:
            ws.append_row(headers)
        return ws
    except Exception:
        ws = spreadsheet.add_worksheet(title=title, rows=5000, cols=len(headers))
        ws.append_row(headers)
        return ws


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def hours_between(dt1_str, dt2_str):
    """Calculate hours between two datetime strings."""
    if not dt1_str or not dt2_str:
        return None
    try:
        fmt = '%Y-%m-%d %H:%M:%S'
        d1  = datetime.strptime(dt1_str[:19], fmt)
        d2  = datetime.strptime(dt2_str[:19], fmt)
        return round(abs((d2 - d1).total_seconds() / 3600), 1)
    except:
        return None


def click_hour(dt_str):
    """Extract hour of day from datetime string."""
    if not dt_str:
        return None
    try:
        return datetime.strptime(dt_str[:19], '%Y-%m-%d %H:%M:%S').hour
    except:
        return None


def conversion_status(lead):
    """Determine if lead converted and at what stage."""
    lifecycle = lead['lifecycle_status'] or 'NEW'
    stage     = lead['outreach_stage'] or 0
    clicks    = lead['click_count'] or 0

    if lifecycle in ('WARM', 'Contacted') and clicks > 0:
        return 'CONVERTED'       # Clicked + replied
    elif lifecycle == 'HOT' and clicks > 0:
        return 'HOT_INTERESTED'  # Clicked, no reply yet
    elif clicks > 0:
        return 'CLICKED_NO_REPLY'
    elif lifecycle == 'DEAD':
        return 'DEAD'
    else:
        return 'NO_ENGAGEMENT'


# ══════════════════════════════════════════════════════════
# SHEET 1 — Master Lead Analytics
# Every lead with full conversion data
# ══════════════════════════════════════════════════════════

def sync_master_leads(spreadsheet):
    headers = [
        'Lead ID', 'Business Name', 'City', 'Niche', 'Rating',
        'Reviews', 'Rating Band', 'Review Band',
        'Status', 'Lifecycle', 'Outreach Stage',
        'Conversion Status', 'Click Count', 'Click Hour (IST)',
        'Hours to First Click', 'Days in System',
        'Followups Sent', 'Template Used', 'Query Used',
        'Added Date', 'Last Clicked', 'Last Contacted',
        'Is Paused', 'Has Email', 'Phone'
    ]

    ws = get_or_create_worksheet(spreadsheet, '📊 Master Leads', headers)
    ws.clear()
    ws.append_row(headers)

    with get_db() as conn:
        leads = conn.execute('''
            SELECT * FROM leads ORDER BY created_at ASC
        ''').fetchall()

    rows = []
    for l in leads:
        rating  = l['rating'] or 0
        reviews = l['reviews_count'] or 0

        # Rating band for pattern analysis
        if rating >= 4.8:   rating_band = '4.8-5.0 ⭐⭐⭐'
        elif rating >= 4.5: rating_band = '4.5-4.8 ⭐⭐'
        elif rating >= 4.0: rating_band = '4.0-4.5 ⭐'
        else:               rating_band = 'Below 4.0'

        # Review band
        if reviews >= 500:   review_band = '500+'
        elif reviews >= 200: review_band = '200-500'
        elif reviews >= 100: review_band = '100-200'
        elif reviews >= 50:  review_band = '50-100'
        else:                review_band = '30-50'

        # Time to first click
        hrs_to_click = hours_between(l['created_at'], l['last_clicked'])

        # Days in system
        days_in = None
        if l['created_at']:
            try:
                d = datetime.strptime(l['created_at'][:19], '%Y-%m-%d %H:%M:%S')
                days_in = (datetime.now() - d).days
            except: pass

        rows.append([
            l['id'],
            l['business_name'],
            l['city'] or '',
            l['niche'] or '',
            rating,
            reviews,
            rating_band,
            review_band,
            l['status'] or '',
            l['lifecycle_status'] or '',
            l['outreach_stage'] or 0,
            conversion_status(dict(l)),
            l['click_count'] or 0,
            click_hour(l['last_clicked']) or '',
            hrs_to_click or '',
            days_in or '',
            l['followup_count'] or 0,
            l['template_used'] or '',
            l['query_string'] or '',
            (l['created_at'] or '')[:10],
            (l['last_clicked'] or '')[:16],
            (l['last_contacted'] or '')[:16],
            'Yes' if l['outreach_paused'] else 'No',
            'Yes' if l['email'] else 'No',
            l['phone'] or ''
        ])

    if rows:
        ws.append_rows(rows, value_input_option='RAW')

    logger.info(f"Master Leads: {len(rows)} rows synced")
    return len(rows)


# ══════════════════════════════════════════════════════════
# SHEET 2 — Niche Performance
# Which niches convert best
# ══════════════════════════════════════════════════════════

def sync_niche_performance(spreadsheet):
    headers = [
        'Niche', 'Total Leads', 'Deployed', 'Total Clicks',
        'HOT Leads', 'Converted (WARM/Contacted)', 'DEAD',
        'Avg Click Count', 'Avg Rating', 'Avg Reviews',
        'Click Rate %', 'Conversion Rate %', 'Best City'
    ]

    ws = get_or_create_worksheet(spreadsheet, '🎯 Niche Performance', headers)
    ws.clear()
    ws.append_row(headers)

    with get_db() as conn:
        niches = conn.execute(
            "SELECT DISTINCT niche FROM leads WHERE niche IS NOT NULL"
        ).fetchall()

        rows = []
        for n in niches:
            niche = n['niche']
            if not niche:
                continue

            stats = conn.execute('''
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status='Deployed' THEN 1 ELSE 0 END) as deployed,
                    SUM(click_count) as total_clicks,
                    SUM(CASE WHEN lifecycle_status='HOT' THEN 1 ELSE 0 END) as hot,
                    SUM(CASE WHEN lifecycle_status IN ('WARM','Contacted') THEN 1 ELSE 0 END) as converted,
                    SUM(CASE WHEN lifecycle_status='DEAD' THEN 1 ELSE 0 END) as dead,
                    AVG(click_count) as avg_clicks,
                    AVG(rating) as avg_rating,
                    AVG(reviews_count) as avg_reviews
                FROM leads WHERE niche=?
            ''', (niche,)).fetchone()

            # Best city for this niche (most clicks)
            best_city_row = conn.execute('''
                SELECT city, SUM(click_count) as total_clicks
                FROM leads WHERE niche=? AND city IS NOT NULL
                GROUP BY city ORDER BY total_clicks DESC LIMIT 1
            ''', (niche,)).fetchone()

            total    = stats['total'] or 1
            deployed = stats['deployed'] or 1
            clicks   = stats['total_clicks'] or 0
            click_rt = round((clicks / deployed * 100), 1) if deployed > 0 else 0
            conv_rt  = round((stats['converted'] or 0) / total * 100, 1)

            rows.append([
                niche,
                stats['total'],
                stats['deployed'],
                clicks,
                stats['hot'],
                stats['converted'],
                stats['dead'],
                round(stats['avg_clicks'] or 0, 2),
                round(stats['avg_rating'] or 0, 1),
                round(stats['avg_reviews'] or 0, 0),
                click_rt,
                conv_rt,
                best_city_row['city'] if best_city_row else ''
            ])

        rows.sort(key=lambda x: x[10], reverse=True)  # Sort by click rate
        if rows:
            ws.append_rows(rows)

    logger.info(f"Niche Performance: {len(rows)} niches synced")


# ══════════════════════════════════════════════════════════
# SHEET 3 — City Performance
# ══════════════════════════════════════════════════════════

def sync_city_performance(spreadsheet):
    headers = [
        'City', 'Total Leads', 'Total Clicks', 'HOT Leads',
        'Converted', 'Click Rate %', 'Conversion Rate %',
        'Avg Rating', 'Top Niche', 'Avg Hours to Click'
    ]

    ws = get_or_create_worksheet(spreadsheet, '🏙️ City Performance', headers)
    ws.clear()
    ws.append_row(headers)

    with get_db() as conn:
        cities = conn.execute(
            "SELECT DISTINCT city FROM leads WHERE city IS NOT NULL AND city != ''"
        ).fetchall()

        rows = []
        for c in cities:
            city = c['city']
            if not city:
                continue

            stats = conn.execute('''
                SELECT
                    COUNT(*) as total,
                    SUM(click_count) as total_clicks,
                    SUM(CASE WHEN lifecycle_status='HOT' THEN 1 ELSE 0 END) as hot,
                    SUM(CASE WHEN lifecycle_status IN ('WARM','Contacted') THEN 1 ELSE 0 END) as converted,
                    AVG(rating) as avg_rating
                FROM leads WHERE city=?
            ''', (city,)).fetchone()

            top_niche = conn.execute('''
                SELECT niche, COUNT(*) as cnt
                FROM leads WHERE city=? AND niche IS NOT NULL
                GROUP BY niche ORDER BY cnt DESC LIMIT 1
            ''', (city,)).fetchone()

            # Avg hours to first click
            click_leads = conn.execute('''
                SELECT created_at, last_clicked FROM leads
                WHERE city=? AND last_clicked IS NOT NULL AND created_at IS NOT NULL
            ''', (city,)).fetchall()

            avg_hrs = None
            if click_leads:
                hrs_list = [
                    hours_between(r['created_at'], r['last_clicked'])
                    for r in click_leads
                ]
                valid = [h for h in hrs_list if h is not None]
                if valid:
                    avg_hrs = round(sum(valid) / len(valid), 1)

            total    = stats['total'] or 1
            clicks   = stats['total_clicks'] or 0
            click_rt = round(clicks / total * 100, 1)
            conv_rt  = round((stats['converted'] or 0) / total * 100, 1)

            rows.append([
                city,
                total,
                clicks,
                stats['hot'],
                stats['converted'],
                click_rt,
                conv_rt,
                round(stats['avg_rating'] or 0, 1),
                top_niche['niche'] if top_niche else '',
                avg_hrs or ''
            ])

        rows.sort(key=lambda x: x[5], reverse=True)
        if rows:
            ws.append_rows(rows)

    logger.info(f"City Performance: {len(rows)} cities synced")


# ══════════════════════════════════════════════════════════
# SHEET 4 — Message Performance
# Which message type drives most clicks
# ══════════════════════════════════════════════════════════

def sync_message_performance(spreadsheet):
    headers = [
        'Stage', 'Message Type', 'Total Sent',
        'Clicked After This Stage', 'Click Rate %',
        'Replied After This Stage', 'Reply Rate %',
        'Avg Hours to Click After Send'
    ]

    ws = get_or_create_worksheet(spreadsheet, '💬 Message Performance', headers)
    ws.clear()
    ws.append_row(headers)

    stage_labels = {
        1: 'Stage 1 — Cold Intro',
        2: 'Stage 2 — HOT Strike',
        3: 'Stage 3 — Followup 1',
        4: 'Stage 4 — Followup 2',
        5: 'Stage 5 — Final',
    }

    with get_db() as conn:
        rows = []
        for stage, label in stage_labels.items():
            total_sent = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE outreach_stage >= ?",
                (stage,)
            ).fetchone()[0]

            clicked = conn.execute('''
                SELECT COUNT(*) FROM leads
                WHERE outreach_stage >= ? AND click_count > 0
            ''', (stage,)).fetchone()[0]

            replied = conn.execute('''
                SELECT COUNT(*) FROM leads
                WHERE outreach_stage >= ?
                AND lifecycle_status IN ('WARM', 'Contacted')
            ''', (stage,)).fetchone()[0]

            click_rt = round(clicked / total_sent * 100, 1) if total_sent > 0 else 0
            reply_rt = round(replied / total_sent * 100, 1) if total_sent > 0 else 0

            rows.append([
                stage,
                label,
                total_sent,
                clicked,
                click_rt,
                replied,
                reply_rt,
                ''   # avg hrs to click after stage — complex, skip for now
            ])

        if rows:
            ws.append_rows(rows)

    logger.info(f"Message Performance: {len(rows)} stages synced")


# ══════════════════════════════════════════════════════════
# SHEET 5 — Click Time Analysis
# What hour of day gets most clicks
# ══════════════════════════════════════════════════════════

def sync_click_time_analysis(spreadsheet):
    headers = ['Hour (IST)', 'Time Label', 'Click Count', 'Percentage %']

    ws = get_or_create_worksheet(spreadsheet, '⏰ Click Time Analysis', headers)
    ws.clear()
    ws.append_row(headers)

    with get_db() as conn:
        leads = conn.execute(
            "SELECT last_clicked FROM leads WHERE last_clicked IS NOT NULL"
        ).fetchall()

    hour_counts = {i: 0 for i in range(24)}
    for l in leads:
        h = click_hour(l['last_clicked'])
        if h is not None:
            hour_counts[h] += 1

    total_clicks = sum(hour_counts.values()) or 1

    time_labels = {
        0: '12 AM', 1: '1 AM', 2: '2 AM', 3: '3 AM',
        4: '4 AM', 5: '5 AM', 6: '6 AM', 7: '7 AM',
        8: '8 AM', 9: '9 AM', 10: '10 AM', 11: '11 AM',
        12: '12 PM', 13: '1 PM', 14: '2 PM', 15: '3 PM',
        16: '4 PM', 17: '5 PM', 18: '6 PM', 19: '7 PM',
        20: '8 PM', 21: '9 PM', 22: '10 PM', 23: '11 PM'
    }

    rows = []
    for hour in range(24):
        count = hour_counts[hour]
        rows.append([
            hour,
            time_labels[hour],
            count,
            round(count / total_clicks * 100, 1)
        ])

    ws.append_rows(rows)
    logger.info(f"Click Time Analysis: 24 hours synced")


# ══════════════════════════════════════════════════════════
# SHEET 6 — Rating & Review Conversion Analysis
# Does higher rating = more clicks?
# ══════════════════════════════════════════════════════════

def sync_rating_analysis(spreadsheet):
    headers = [
        'Rating Band', 'Review Band', 'Lead Count',
        'Total Clicks', 'Click Rate %', 'Converted Count',
        'Conversion Rate %', 'Avg Click Count'
    ]

    ws = get_or_create_worksheet(spreadsheet, '⭐ Rating Analysis', headers)
    ws.clear()
    ws.append_row(headers)

    rating_bands  = [
        ('4.0-4.5', 4.0, 4.5),
        ('4.5-4.8', 4.5, 4.8),
        ('4.8-5.0', 4.8, 5.1),
    ]
    review_bands = [
        ('30-50',   30,  50),
        ('50-100',  50,  100),
        ('100-200', 100, 200),
        ('200-500', 200, 500),
        ('500+',    500, 999999),
    ]

    with get_db() as conn:
        rows = []
        for rb_label, rb_min, rb_max in rating_bands:
            for rv_label, rv_min, rv_max in review_bands:
                stats = conn.execute('''
                    SELECT
                        COUNT(*) as total,
                        SUM(click_count) as clicks,
                        SUM(CASE WHEN lifecycle_status IN ('WARM','Contacted','HOT') THEN 1 ELSE 0 END) as engaged,
                        AVG(click_count) as avg_clicks
                    FROM leads
                    WHERE rating >= ? AND rating < ?
                    AND reviews_count >= ? AND reviews_count < ?
                    AND status = 'Deployed'
                ''', (rb_min, rb_max, rv_min, rv_max)).fetchone()

                total = stats['total'] or 0
                if total == 0:
                    continue

                clicks   = stats['clicks'] or 0
                click_rt = round(clicks / total * 100, 1)
                conv_rt  = round((stats['engaged'] or 0) / total * 100, 1)

                rows.append([
                    rb_label, rv_label, total,
                    clicks, click_rt,
                    stats['engaged'] or 0, conv_rt,
                    round(stats['avg_clicks'] or 0, 2)
                ])

        rows.sort(key=lambda x: x[4], reverse=True)
        if rows:
            ws.append_rows(rows)

    logger.info(f"Rating Analysis: {len(rows)} combinations synced")


# ══════════════════════════════════════════════════════════
# SHEET 7 — Daily Summary
# One row per day showing system health
# ══════════════════════════════════════════════════════════

def sync_daily_summary(spreadsheet):
    headers = [
        'Date', 'New Leads', 'Sites Deployed',
        'Messages Sent', 'Clicks Today', 'HOT Leads',
        'Conversions', 'Total in DB'
    ]

    ws = get_or_create_worksheet(spreadsheet, '📅 Daily Summary', headers)

    today = datetime.now().strftime('%Y-%m-%d')

    # Check if today already exists
    existing_dates = ws.col_values(1)
    if today in existing_dates:
        logger.info("Daily Summary: today already logged")
        return

    with get_db() as conn:
        new_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE created_at LIKE ?",
            (f'{today}%',)
        ).fetchone()[0]

        deployed_today = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='Deployed' AND created_at LIKE ?",
            (f'{today}%',)
        ).fetchone()[0]

        clicks_today = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE last_clicked LIKE ?",
            (f'{today}%',)
        ).fetchone()[0]

        hot = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE lifecycle_status='HOT'"
        ).fetchone()[0]

        converted = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE lifecycle_status IN ('WARM','Contacted')"
        ).fetchone()[0]

        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]

    ws.append_row([
        today, new_leads, deployed_today,
        0,  # messages sent — would need separate tracking
        clicks_today, hot, converted, total
    ])
    logger.info("Daily Summary: today's row added")


# ══════════════════════════════════════════════════════════
# MAIN SYNC
# ══════════════════════════════════════════════════════════

def run_sync():
    logger.info("Analytics Sync — Starting")

    if not SHEET_ID:
        logger.error("GOOGLE_SHEET_ID not set in .env")
        return False

    if not os.path.exists(CREDS_FILE):
        logger.error(f"google_credentials.json not found at {CREDS_FILE}")
        return False

    try:
        client      = get_sheet_client()
        spreadsheet = client.open_by_key(SHEET_ID)
        logger.info(f"Connected to Google Sheet: {spreadsheet.title}")
    except Exception as e:
        logger.error(f"Could not connect to Google Sheets: {e}")
        return False

    try:
        count = sync_master_leads(spreadsheet)
        sync_niche_performance(spreadsheet)
        sync_city_performance(spreadsheet)
        sync_message_performance(spreadsheet)
        sync_click_time_analysis(spreadsheet)
        sync_rating_analysis(spreadsheet)
        sync_daily_summary(spreadsheet)

        logger.info(f"Analytics Sync Complete — {count} leads synced to 7 sheets")
        return True

    except Exception as e:
        logger.error(f"Sync error: {e}")
        return False


if __name__ == "__main__":
    print("\n" + "="*55)
    print("  Ghost Worker — Analytics Sync")
    print("="*55 + "\n")
    success = run_sync()
    print("\nSync complete!" if success else "\nSync failed — check logs")
