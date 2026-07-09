import sqlite3
import os
import json
import logging
import subprocess
import threading
from logging.handlers import RotatingFileHandler
from datetime import datetime
from flask import Flask, redirect, request, jsonify, render_template_string, send_from_directory
from dotenv import load_dotenv

load_dotenv()

LOG_DIR     = os.path.join(os.path.dirname(__file__), 'logs')
DB_PATH     = os.path.join(os.path.dirname(__file__), 'agency.db')
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON      = os.path.join(PROJECT_DIR, 'ghostenv', 'bin', 'python3')

os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, 'ghost_worker.log')

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [dashboard] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)
app    = Flask(__name__, static_folder='static')
app.secret_key = os.getenv("FLASK_SECRET_KEY", "ghost-worker-2024")
SERVER_PORT    = int(os.getenv("SERVER_PORT", 5000))

DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "ghost2024")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def load_config():
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return {
            "scrape_interval_hours": 12,
            "pipeline_interval_hours": 1,
            "lifecycle_check_hours": 6,
            "scheduled_queries": [],
            "last_updated": ""
        }


def save_config(cfg):
    cfg['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)

def now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')



def time_ago(dt_string):
    if not dt_string:
        return "Never"
    try:
        dt   = datetime.strptime(dt_string, '%Y-%m-%d %H:%M:%S')
        diff = datetime.now() - dt
        s    = int(diff.total_seconds())
        if s < 60:    return f"{s}s ago"
        if s < 3600:  return f"{s//60}m ago"
        if s < 86400: return f"{s//3600}h ago"
        return f"{s//86400}d ago"
    except:
        return "Never"

def check_auth():
    auth = request.authorization
    if not auth:
        return False
    return auth.username == DASHBOARD_USER and auth.password == DASHBOARD_PASS


def auth_required(f):
    from functools import wraps
    from flask import Response
    @wraps(f)
    def decorated(*args, **kwargs):
        if not check_auth():
            return Response(
                'Authentication required.',
                401,
                {'WWW-Authenticate': 'Basic realm="Ghost Worker"'}
            )
        return f(*args, **kwargs)
    return decorated


def run_pipeline_bg(query):
    def _run():
        logger.info(f"PIPELINE START: {query}")
        steps = [
            ([PYTHON, 'scraper.py', query], 300),
            ([PYTHON, 'ai_brain.py'],        300),
            ([PYTHON, 'builder.py'],          120),
            ([PYTHON, 's3_deployer.py'],      120),
        ]
        for cmd, timeout in steps:

            try:
                script = cmd[1]
                logger.info(f"Running: {script}")
                result = subprocess.run(
                    [cmd[0], os.path.join(PROJECT_DIR, cmd[1])] + cmd[2:],
                    cwd=PROJECT_DIR, timeout=timeout,
                    capture_output=False
                )
                if result.returncode != 0:
                    logger.error(f"{script} exited with code {result.returncode}")
            except subprocess.TimeoutExpired:
                logger.error(f"{cmd[1]} timed out")
            except Exception as e:
                logger.error(f"{cmd[1]} error: {e}")
        logger.info(f"PIPELINE COMPLETE: {query}")
    threading.Thread(target=_run, daemon=True).start()


# ── Static files ─────────────────────────────────────────

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)


# ── welcome page ─────────────────────────────────────────

GATEWAY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{{BUSINESS_NAME}} — Website Preview</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{
  height:100%;
  font-family:'Inter',system-ui,sans-serif;
  background:#08090c;
}
body{
  min-height:100vh;
  display:flex;
  align-items:center;
  justify-content:center;
  padding:40px 24px;
}
.wrap{
  max-width:420px;
  width:100%;
}
.badge{
  display:inline-flex;
  align-items:center;
  gap:7px;
  background:#0f1218;
  border:1px solid #1e2433;
  border-radius:100px;
  padding:6px 14px 6px 10px;
  margin-bottom:40px;
}
.badge-dot{
  width:6px;height:6px;
  border-radius:50%;
  background:#4ade80;
  animation:blink 2.5s ease-in-out infinite;
}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0.2}}
.badge-text{
  font-size:11px;
  letter-spacing:0.5px;
  color:#6b7280;
  font-weight:400;
}
.label{
  font-size:11px;
  letter-spacing:3px;
  text-transform:uppercase;
  color:#3b82f6;
  font-weight:500;
  margin-bottom:18px;
}
.heading{
  font-size:36px;
  font-weight:300;
  color:#e5e7eb;
  line-height:1.2;
  margin-bottom:14px;
  letter-spacing:-0.5px;
}
.heading strong{
  font-weight:500;
  color:#f9fafb;
}
.tagline{
  font-size:12px;
  color:#6b7280;
  font-weight:400;
  letter-spacing:1.5px;
  text-transform:uppercase;
  margin-bottom:14px;
}
.sub{
  font-size:14px;
  color:#9ca3af;
  line-height:1.85;
  margin-bottom:44px;
}
.cta{
  display:flex;
  align-items:center;
  justify-content:space-between;
  background:#e8eaf0;
  color:#08090c;
  border:none;
  border-radius:16px;
  padding:20px 24px;
  font-size:15px;
  font-weight:500;
  font-family:'Inter',sans-serif;
  cursor:pointer;
  text-decoration:none;
  width:100%;
  transition:transform 0.18s ease,background 0.15s;
  letter-spacing:-0.1px;
}
.cta:hover{
  background:#d1d5e0;
  transform:translateY(-2px);
}
.cta-arrow{
  width:36px;height:36px;
  background:#08090c;
  border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  flex-shrink:0;
  transition:transform 0.18s ease;
}
.cta:hover .cta-arrow{transform:translateX(3px)}
.cta-arrow svg{
  width:14px;height:14px;
  stroke:#e8eaf0;fill:none;
  stroke-width:2;
  stroke-linecap:round;stroke-linejoin:round;
}
.rule{
  width:100%;
  height:1px;
  background:#161b24;
  margin:32px 0;
}
.meta{
  display:flex;
  align-items:center;
  justify-content:space-between;
}
.meta-left{
  font-size:12px;
  color:#6b7280;
}
.meta-left span{
  color:#9ca3af;
  font-weight:500;
}
.meta-right{
  font-size:11px;
  color:#6b7280;
  letter-spacing:0.5px;
}
@media(max-width:480px){
  .heading{font-size:28px}
  body{padding:32px 20px}
}
</style>
</head>
<body>
<div class="wrap">
  <div class="badge">
    <div class="badge-dot"></div>
    <span class="badge-text">Live preview ready</span>
  </div>

  <div class="label">Only for {{BUSINESS_NAME}}</div>

  <h1 class="heading">Results first.<br><strong>Talk later.</strong></h1>

  <div class="tagline">We built. You judge. No pressure.</div>

  <p class="sub">Your website is live — built free, just for you.<br>No cost. No commitment. See it first.</p>

  <a href="/click/{{LEAD_ID}}" class="cta">
    <span>Open my website</span>
    <div class="cta-arrow">
      <svg viewBox="0 0 24 24"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
    </div>
  </a>

  <div class="rule"></div>

  <div class="meta">
    <div class="meta-left">Built by <span>{{AGENCY_NAME}}</span></div>
    <div class="meta-right">Free &nbsp;·&nbsp; No strings</div>
  </div>
</div>
</body>
</html>"""


# ---- Radar routes ─────────────────────────────────────────

@app.route('/view/<int:lead_id>')
def track_view(lead_id):
    """
    Gateway page — shown to EVERYONE including WhatsApp preview bots.
    Does NOT update database. Just shows a landing page with a button.
    """
    try:
        with get_db() as conn:
            lead = conn.execute(
                "SELECT business_name FROM leads WHERE id=?",
                (lead_id,)
            ).fetchone()

        if not lead:
            return "Not found", 404

        agency_name = os.getenv('AGENCY_NAME', 'LaunchPad Web')
        html = GATEWAY_HTML\
            .replace('{{BUSINESS_NAME}}', lead['business_name'])\
            .replace('{{LEAD_ID}}',       str(lead_id))\
            .replace('{{AGENCY_NAME}}',   agency_name)

        return html, 200

    except Exception as e:
        logger.error(f"Gateway error: {e}")
        return "Error", 500


@app.route('/click/<int:lead_id>')
def track_click(lead_id):
    """
    Real click handler — only triggered when human clicks the button.
    Updates DB to HOT and redirects to S3 site.
    """
    try:
        ip        = request.headers.get('X-Forwarded-For', request.remote_addr)
        ua        = request.headers.get('User-Agent', '').lower()
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        device    = 'Mobile' if any(
            x in ua for x in ['mobile', 'android', 'iphone']
        ) else 'Desktop'

        with get_db() as conn:
            lead = conn.execute(
                "SELECT s3_url, business_name, click_count FROM leads WHERE id=?",
                (lead_id,)
            ).fetchone()

            if not lead:
                return "Not found", 404

            new_count = (lead['click_count'] or 0) + 1

            conn.execute('''
                UPDATE leads SET
                    click_count      = ?,
                    last_clicked     = ?,
                    lifecycle_status = 'HOT'
                WHERE id = ?
            ''', (new_count, timestamp, lead_id))
            conn.commit()

            logger.info(
                f"REAL CLICK — {lead['business_name'][:40]} | "
                f"Click #{new_count} | {device} | {ip}"
            )
            s3_url = lead['s3_url']
            if not s3_url:
                return "Site not ready", 404
            return redirect(s3_url, code=302)

    except Exception as e:
        logger.error(f"Click tracker error: {e}")
        return "Error", 500


@app.route('/view/<int:lead_id>/pixel')
def tracker_pixel(lead_id):
    """Silent 1x1 pixel — does NOT update click count."""
    from flask import Response
    gif = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    return Response(gif, mimetype='image/gif')


# ── API routes ────────────────────────────────────────────

@app.route('/api/leads')
@auth_required
def api_leads():
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM leads ORDER BY click_count DESC"
            ).fetchall()
            return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/update_status', methods=['POST'])
@auth_required
def api_update_status():
    try:
        data    = request.json
        lead_id = data.get('lead_id')
        status  = data.get('new_status')
        valid   = ['NEW','HOT','WARM','COLD','DEAD','Contacted']
        if status not in valid:
            return jsonify({"error": "Invalid status"}), 400
        with get_db() as conn:
            conn.execute(
                "UPDATE leads SET lifecycle_status=? WHERE id=?",
                (status, lead_id)
            )
            if status == 'Contacted':
                conn.execute(
                    "UPDATE leads SET last_contacted=?, followup_count=followup_count+1 WHERE id=?",
                    (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), lead_id)
                )
            conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/delete_lead', methods=['POST'])
@auth_required
def api_delete_lead():
    try:
        lead_id = request.json.get('lead_id')
        with get_db() as conn:
            # Get S3 URL before deleting
            lead = conn.execute(
                "SELECT s3_url, business_name, phone FROM leads WHERE id=?",
                (lead_id,)
            ).fetchone()

            if lead and lead['s3_url']:
                # Delete from S3
                try:
                    import boto3
                    s3 = boto3.client('s3',
                        aws_access_key_id     = os.getenv('AWS_ACCESS_KEY'),
                        aws_secret_access_key = os.getenv('AWS_SECRET_KEY'),
                        region_name           = os.getenv('AWS_REGION', 'ap-south-1')
                    )
                    bucket   = os.getenv('S3_BUCKET_NAME')
                    filename = lead['s3_url'].split('/')[-1]
                    s3.delete_object(Bucket=bucket, Key=filename)
                    logger.info(f"S3 deleted: {filename} for {lead['business_name']}")
                except Exception as e:
                    logger.error(f"S3 delete failed: {e}")

            # Auto-blacklist before deleting
            if lead:
                try:
                    conn.execute('''
                        INSERT OR IGNORE INTO blacklist (phone, business_name, reason)
                        VALUES (?, ?, ?)
                    ''', (lead['phone'], lead['business_name'], 'Deleted from dashboard'))
                    logger.info(f"Auto-blacklisted: {lead['business_name']} ({lead['phone']})")
                except Exception as bl_err:
                    logger.warning(f"Blacklist insert failed: {bl_err}")

            # Delete from DB
            conn.execute("DELETE FROM leads WHERE id=?", (lead_id,))
            conn.commit()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/pause_lead', methods=['POST'])
@auth_required
def api_pause_lead():
    try:
        data      = request.json
        lead_id   = data.get('lead_id')
        paused    = int(data.get('paused', 1))
        with get_db() as conn:
            conn.execute(
                "UPDATE leads SET outreach_paused=? WHERE id=?",
                (paused, lead_id)
            )
            conn.commit()
        action = "PAUSED" if paused else "RESUMED"
        logger.info(f"Lead ID:{lead_id} outreach {action}")
        return jsonify({"success": True, "paused": paused})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/run_query', methods=['POST'])
@auth_required
def api_run_query():
    try:
        query = (request.json.get('query') or '').strip()
        if not query:
            return jsonify({"error": "Empty query"}), 400

        cfg = load_config()
        if query not in cfg['scheduled_queries']:
            cfg['scheduled_queries'].append(query)
            save_config(cfg)

        run_pipeline_bg(query)
        logger.info(f"Manual pipeline triggered: {query}")
        return jsonify({"success": True, "message": f"Pipeline started: {query}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/update_config', methods=['POST'])
@auth_required
def api_update_config():
    try:
        data = request.json
        cfg  = load_config()
        for key in ['scrape_interval_hours','pipeline_interval_hours','lifecycle_check_hours']:
            if key in data:
                cfg[key] = int(data[key])
        if 'scheduled_queries' in data:
            cfg['scheduled_queries'] = data['scheduled_queries']
        save_config(cfg)
        return jsonify({"success": True, "config": cfg})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/remove_query', methods=['POST'])
@auth_required
def api_remove_query():
    try:
        query = (request.json.get('query') or '').strip()
        cfg   = load_config()
        if query in cfg['scheduled_queries']:
            cfg['scheduled_queries'].remove(query)
            save_config(cfg)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/add_query', methods=['POST'])
@auth_required
def api_add_query():
    try:
        query = (request.json.get('query') or '').strip()
        if not query:
            return jsonify({"error": "Empty query"}), 400
        cfg = load_config()
        if query not in cfg['scheduled_queries']:
            cfg['scheduled_queries'].append(query)
            save_config(cfg)
            logger.info(f"Query added to scheduler: {query}")
            return jsonify({"success": True, "message": f"Added: {query}"})
        return jsonify({"success": True, "message": "Already exists"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_config')
@auth_required
def api_get_config():
    return jsonify(load_config())


@app.route('/api/logs')
@auth_required
def api_logs():
    try:
        with open(LOG_FILE, 'r') as f:
            lines = f.readlines()
        return jsonify({"logs": ''.join(lines[-60:])})
    except Exception as e:
        return jsonify({"logs": f"No logs yet: {e}"})


@app.route('/health')
def health():
    try:
        with get_db() as conn:
            total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            hot   = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE lifecycle_status='HOT'"
            ).fetchone()[0]
        return jsonify({"status":"alive","total_leads":total,"hot_leads":hot})
    except Exception as e:
        return jsonify({"status":"error","error":str(e)}), 500



@app.route('/webhook/whatsapp', methods=['POST'])
def whatsapp_webhook():
    """Receive incoming WhatsApp messages from Evolution API."""
    try:
        data  = request.json
        if not data:
            return jsonify({"status": "no_data"}), 200

        event = data.get('event', '')

        # Only process incoming messages
        if event != 'messages.upsert':
            return jsonify({"status": "ignored"}), 200

        msg_data = data.get('data', {})
        key      = msg_data.get('key', {})

        # CRITICAL: Ignore ALL outgoing messages (sent by us)
        if key.get('fromMe', True):
            return jsonify({"status": "own_message_ignored"}), 200

        # Ignore if no actual message content
        msg_content = msg_data.get('message', {})
        if not msg_content:
            return jsonify({"status": "no_content"}), 200

        # Ignore status updates and broadcasts
        remote_jid = key.get('remoteJid', '')
        if 'status' in remote_jid or 'broadcast' in remote_jid:
            return jsonify({"status": "status_ignored"}), 200

        # Extract sender phone
        phone = remote_jid.replace('@s.whatsapp.net', '').replace('@g.us', '')
        if not phone:
            return jsonify({"status": "no_phone"}), 200

        # Ignore messages from your own number
        own_number = os.getenv('EVOLUTION_INSTANCE_PHONE', '917814871810')
        if phone.endswith(own_number[-10:]):
            return jsonify({"status": "own_number_ignored"}), 200

        # Extract message text
        text = (
            msg_content.get('conversation') or
            msg_content.get('extendedTextMessage', {}).get('text') or
            ''
        )

        if not text:
            return jsonify({"status": "no_text"}), 200

        logger.info(f"WA REPLY from {phone}: {text[:80]}")

        # Find lead by phone
        with get_db() as conn:
            lead = conn.execute('''
                SELECT id, business_name, lifecycle_status
                FROM leads
                WHERE replace(replace(replace(phone,'+',''),' ',''),'-','')
                LIKE ?
                LIMIT 1
            ''', (f'%{phone[-10:]}%',)).fetchone()

            if lead:
                conn.execute('''
                    UPDATE leads SET
                        lifecycle_status = CASE
                            WHEN lifecycle_status = 'HOT' THEN 'HOT'
                            ELSE 'WARM'
                        END,
                        outreach_paused  = 1,
                        last_clicked     = ?
                    WHERE id = ?
                ''', (now_str(), lead['id']))
                conn.commit()
                logger.info(
                    f"Reply matched: {lead['business_name'][:40]} "
                    f"(ID:{lead['id']}) — WARM + PAUSED"
                )

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"status": "error"}), 500

# ── Template Dashboard ─────────────────────────────────────────────


TEMPLATES_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Ghost Worker — Template Manager</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',Arial,sans-serif;background:#0a0e1a;color:#e0e6f0;min-height:100vh;padding:20px}
.topbar{background:#0d1117;border-bottom:1px solid #1e2d45;padding:14px 24px;display:flex;align-items:center;justify-content:space-between;margin:-20px -20px 24px;flex-wrap:wrap;gap:10px}
.logo{font-size:18px;font-weight:700;color:#58a6ff}.logo span{color:#3fb950}
.nav-links{display:flex;gap:16px}
.nav-links a{color:#8b949e;text-decoration:none;font-size:13px;padding:6px 12px;border-radius:6px;transition:all .15s}
.nav-links a:hover{background:#161b27;color:#e0e6f0}
.nav-links a.active{background:#1e2d45;color:#58a6ff}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;max-width:1200px;margin:0 auto}
@media(max-width:768px){.grid{grid-template-columns:1fr}}
.panel{background:#161b27;border:1px solid #1e2d45;border-radius:12px;padding:20px}
.panel-title{font-size:14px;font-weight:600;color:#e0e6f0;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.form-group{margin-bottom:14px}
.form-label{font-size:12px;color:#8b949e;margin-bottom:6px;display:block;text-transform:uppercase;letter-spacing:1px}
.inp{background:#0d1117;border:1px solid #1e2d45;color:#e0e6f0;padding:10px 14px;border-radius:8px;font-size:13px;width:100%}
.inp:focus{outline:none;border-color:#58a6ff}
.file-drop{background:#0d1117;border:2px dashed #1e2d45;border-radius:8px;padding:24px;text-align:center;cursor:pointer;transition:border-color .15s;position:relative}
.file-drop:hover{border-color:#58a6ff}
.file-drop input[type=file]{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%}
.file-drop-icon{font-size:28px;margin-bottom:8px}
.file-drop-text{font-size:13px;color:#8b949e}
.file-drop-text span{color:#58a6ff}
.file-name{font-size:12px;color:#3fb950;margin-top:8px;display:none}
.btn{padding:10px 20px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;border:none;transition:opacity .15s;width:100%;margin-top:8px}
.btn:hover{opacity:.8}
.btn-blue{background:#1f6feb;color:#fff}
.btn-green{background:#238636;color:#fff}
.btn-red{background:#da3633;color:#fff;width:auto;padding:5px 12px;font-size:11px}
.toast{position:fixed;bottom:20px;right:20px;background:#1e2d45;color:#58a6ff;padding:12px 18px;border-radius:8px;font-size:13px;border:1px solid #2a4060;display:none;z-index:999;max-width:320px}
.template-list{display:flex;flex-direction:column;gap:8px}
.template-item{background:#0d1117;border:1px solid #1e2d45;border-radius:8px;padding:12px 16px;display:flex;align-items:center;justify-content:space-between}
.template-name{font-size:13px;font-weight:500;color:#e0e6f0}
.template-badge{font-size:11px;background:#1e2d45;color:#58a6ff;padding:2px 8px;border-radius:20px;margin-left:8px}
.template-size{font-size:11px;color:#8b949e}
.asset-list{display:flex;flex-direction:column;gap:6px;max-height:300px;overflow-y:auto}
.asset-item{background:#0d1117;border:1px solid #1e2d45;border-radius:6px;padding:8px 12px;display:flex;align-items:center;justify-content:space-between;gap:8px}
.asset-key{font-size:11px;color:#8b949e;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.asset-url-btn{font-size:11px;color:#58a6ff;cursor:pointer;background:none;border:none;padding:0;white-space:nowrap}
.tip{font-size:11px;color:#8b949e;margin-top:10px;padding:8px 12px;background:#0d1117;border-radius:6px;border-left:3px solid #1e2d45;line-height:1.6}
.tip code{color:#58a6ff;font-family:monospace}
.empty{text-align:center;color:#8b949e;font-size:13px;padding:20px}
</style>
</head>
<body>
<div class="topbar">
  <div class="logo">Ghost<span>Worker</span></div>
  <div class="nav-links">
    <a href="/admin">Dashboard</a>
    <a href="/admin/templates" class="active">Templates</a>
  </div>
</div>

<div class="grid">

  <!-- Upload Template -->
  <div class="panel">
    <div class="panel-title">📄 Upload HTML Template</div>
    <div class="form-group">
      <label class="form-label">Niche Name (becomes filename)</label>
      <input class="inp" id="tmpl-niche" placeholder="e.g. gym, salon, hotel" />
    </div>
    <div class="form-group">
      <label class="form-label">HTML File</label>
      <div class="file-drop" id="tmpl-drop">
        <input type="file" accept=".html" id="tmpl-file" onchange="showFileName('tmpl-file','tmpl-fname')">
        <div class="file-drop-icon">📁</div>
        <div class="file-drop-text">Drop your <span>.html</span> file here or click to browse</div>
        <div class="file-name" id="tmpl-fname"></div>
      </div>
    </div>
    <div class="tip">
      Template will be saved as <code>{niche}.html</code> in <code>/templates/</code> folder.<br>
      Use standard placeholders: <code>{{BUSINESS_NAME}}</code> <code>{{HERO}}</code> <code>{{ABOUT}}</code> <code>{{PHONE_CLEAN}}</code> etc.
    </div>
    <button class="btn btn-blue" onclick="uploadTemplate()">Upload Template</button>
  </div>

  <!-- Upload S3 Asset -->
  <div class="panel">
    <div class="panel-title">☁️ Upload Asset to S3</div>
    <div class="form-group">
      <label class="form-label">Niche Folder</label>
      <input class="inp" id="asset-niche" placeholder="e.g. gym, salon, cafe" />
    </div>
    <div class="form-group">
      <label class="form-label">File (CSS / JS / Image)</label>
      <div class="file-drop">
        <input type="file" accept=".css,.js,.png,.jpg,.jpeg,.webp,.svg" id="asset-file" onchange="showFileName('asset-file','asset-fname')">
        <div class="file-drop-icon">⬆️</div>
        <div class="file-drop-text">Drop <span>CSS / JS / Image</span> here or click to browse</div>
        <div class="file-name" id="asset-fname"></div>
      </div>
    </div>
    <div class="tip">
      File will be uploaded to S3 at:<br>
      <code>{{bucket_url}}/assets/{niche}/filename</code><br>
      Link this URL in your HTML template.
    </div>
    <button class="btn btn-green" onclick="uploadAsset()">Upload to S3</button>
  </div>

  <!-- Existing Templates -->
  <div class="panel">
    <div class="panel-title">🗂 Installed Templates ({{ templates|length }})</div>
    {% if templates %}
    <div class="template-list">
      {% for t in templates %}
      <div class="template-item">
        <div>
          <span class="template-name">{{ t.niche }}</span>
          <span class="template-badge">{{ t.name }}</span>
        </div>
        <div style="display:flex;align-items:center;gap:10px">
          <span class="template-size">{{ t.size_kb }}KB</span>
          {% if t.niche != 'default' %}
          <button class="btn btn-red" onclick="deleteTemplate('{{ t.niche }}')">Delete</button>
          {% endif %}
        </div>
      </div>
      {% endfor %}
    </div>
    {% else %}
    <div class="empty">No templates installed yet</div>
    {% endif %}
  </div>

  <!-- S3 Assets -->
  <div class="panel">
    <div class="panel-title">📦 S3 Assets ({{ s3_assets|length }})</div>
    {% if s3_assets %}
    <div class="asset-list">
      {% for a in s3_assets %}
      <div class="asset-item">
        <span class="asset-key">{{ a.key }}</span>
        <button class="asset-url-btn" onclick="copyURL('{{ a.url }}')">Copy URL</button>
      </div>
      {% endfor %}
    </div>
    {% else %}
    <div class="empty">No assets uploaded yet</div>
    {% endif %}
  </div>

</div>

<div class="toast" id="toast"></div>

<script>
function showToast(msg, err) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.background = err ? '#3d0f0f' : '#1e2d45';
  t.style.color      = err ? '#f85149' : '#58a6ff';
  t.style.display    = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}

function showFileName(inputId, labelId) {
  const f = document.getElementById(inputId).files[0];
  const l = document.getElementById(labelId);
  if (f) { l.textContent = '✓ ' + f.name; l.style.display = 'block'; }
}

function uploadTemplate() {
  const niche = document.getElementById('tmpl-niche').value.trim().toLowerCase();
  const file  = document.getElementById('tmpl-file').files[0];
  if (!niche) { showToast('Enter a niche name', true); return; }
  if (!file)  { showToast('Select an HTML file', true); return; }

  const fd = new FormData();
  fd.append('niche', niche);
  fd.append('file',  file);

  fetch('/api/upload_template', { method: 'POST', body: fd })
  .then(r => r.json())
  .then(d => {
    if (d.success) { showToast('Template saved: ' + d.filename); setTimeout(() => location.reload(), 1000); }
    else showToast(d.error, true);
  });
}

function uploadAsset() {
  const niche = document.getElementById('asset-niche').value.trim().toLowerCase();
  const file  = document.getElementById('asset-file').files[0];
  if (!niche) { showToast('Enter a niche folder name', true); return; }
  if (!file)  { showToast('Select a file', true); return; }

  const fd = new FormData();
  fd.append('niche', niche);
  fd.append('file',  file);

  showToast('Uploading to S3...');
  fetch('/api/upload_asset', { method: 'POST', body: fd })
  .then(r => r.json())
  .then(d => {
    if (d.success) {
      showToast('Uploaded! URL copied to clipboard');
      navigator.clipboard.writeText(d.url).catch(() => {});
      setTimeout(() => location.reload(), 1000);
    } else showToast(d.error, true);
  });
}

function copyURL(url) {
  navigator.clipboard.writeText(url)
  .then(() => showToast('URL copied!'))
  .catch(() => { prompt('Copy this URL:', url); });
}

function deleteTemplate(niche) {
  if (!confirm('Delete ' + niche + '.html? This cannot be undone.')) return;
  fetch('/api/delete_template', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({niche})
  }).then(r => r.json()).then(d => {
    if (d.success) { showToast('Template deleted'); setTimeout(() => location.reload(), 600); }
    else showToast(d.error, true);
  });
}
</script>
</body>
</html>"""

# ── Dashboard ─────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Ghost Worker — Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#f5f4f0;
  --surface:#ffffff;
  --surface2:#fafaf8;
  --border:#e8e6e0;
  --border2:#d4d1c8;
  --text:#1a1a18;
  --text2:#5a5850;
  --text3:#9a9890;
  --accent:#1a1a18;
  --blue:#2563eb;
  --green:#16a34a;
  --red:#dc2626;
  --amber:#d97706;
  --hot:#dc2626;
  --warm:#d97706;
  --radius:10px;
  --shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
  --shadow-md:0 4px 12px rgba(0,0,0,.08);
}
body{font-family:-apple-system,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;font-size:14px}

/* ── Topbar ── */
.topbar{background:var(--surface);border-bottom:1px solid var(--border);padding:0 28px;display:flex;align-items:center;height:56px;gap:20px;position:sticky;top:0;z-index:100;box-shadow:var(--shadow)}
.logo{font-size:16px;font-weight:700;color:var(--text);letter-spacing:-.3px}
.logo em{font-style:normal;color:var(--text3);font-weight:400}
.live-dot{width:7px;height:7px;border-radius:50%;background:#16a34a;display:inline-block;animation:pulse 2s infinite;margin-right:5px}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.live-time{font-size:12px;color:var(--text3)}
.topbar-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.tbtn{padding:7px 14px;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;border:1px solid var(--border);background:var(--surface);color:var(--text2);text-decoration:none;display:inline-flex;align-items:center;gap:5px;transition:all .15s;white-space:nowrap}
.tbtn:hover{background:var(--surface2);border-color:var(--border2);color:var(--text)}
.tbtn-dark{background:var(--text);color:#fff;border-color:var(--text)}
.tbtn-dark:hover{background:#333;color:#fff}

/* ── Stats ── */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;padding:22px 28px 0}
@media(max-width:768px){.stats{grid-template-columns:repeat(2,1fr)}}
.stat{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:18px 20px;box-shadow:var(--shadow)}
.slbl{font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px;font-weight:500}
.sval{font-size:30px;font-weight:700;line-height:1;color:var(--text)}
.sval.blue{color:var(--blue)}.sval.red{color:var(--red)}.sval.green{color:var(--green)}.sval.amber{color:var(--amber)}

/* ── Panels ── */
.panels{padding:18px 28px 0;display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:900px){.panels{grid-template-columns:1fr}}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:18px 20px;box-shadow:var(--shadow)}
.ptitle{font-size:13px;font-weight:600;color:var(--text);margin-bottom:14px;display:flex;align-items:center;gap:8px}
.ptitle-sub{font-size:11px;color:var(--text3);font-weight:400;margin-left:auto}

/* ── Inputs ── */
.inp{background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:9px 13px;border-radius:7px;font-size:13px;width:100%;outline:none;transition:border .15s;font-family:inherit}
.inp:focus{border-color:var(--blue)}
.qrow{display:flex;gap:8px}
.qrow .inp{flex:1}

/* ── Buttons ── */
.btn{padding:8px 16px;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;border:1px solid transparent;transition:all .15s;white-space:nowrap;display:inline-flex;align-items:center;gap:4px;font-family:inherit}
.btn:hover{opacity:.85}
.btn-primary{background:var(--text);color:#fff;border-color:var(--text)}
.btn-green{background:var(--green);color:#fff}
.btn-red{background:var(--red);color:#fff}
.btn-amber{background:var(--amber);color:#fff}
.btn-ghost{background:transparent;border-color:var(--border);color:var(--text2)}
.btn-ghost:hover{background:var(--surface2);color:var(--text)}
.btn-sm{padding:5px 10px;font-size:11px;border-radius:6px}
.btn-xs{padding:3px 8px;font-size:11px;border-radius:5px}

/* ── Query tags ── */
.qtags{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.qtag{background:var(--surface2);border:1px solid var(--border);color:var(--text2);padding:4px 10px;border-radius:20px;font-size:11px;display:flex;align-items:center;gap:5px}
.qx{cursor:pointer;color:var(--text3);font-size:12px;line-height:1}.qx:hover{color:var(--red)}

/* ── Irow ── */
.irow{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.iitem{display:flex;align-items:center;gap:7px;font-size:13px;color:var(--text2)}
.iitem input{width:58px;background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:6px;font-size:13px;text-align:center;outline:none;font-family:inherit}
.iitem input:focus{border-color:var(--blue)}

/* ── Running badge ── */
.rbadge{background:#fef3c7;color:#92400e;border:1px solid #fcd34d;padding:3px 10px;border-radius:20px;font-size:11px;display:none;font-weight:600;animation:pulse 1.5s infinite}

/* ── Toolbar ── */
.lead-toolbar{padding:18px 28px 10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.lead-toolbar-title{font-size:15px;font-weight:700;color:var(--text)}
.sort-sel{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:7px 12px;border-radius:7px;font-size:12px;font-weight:500;cursor:pointer;outline:none;font-family:inherit}
.sort-sel:focus{border-color:var(--blue)}
.lead-count{font-size:12px;color:var(--text3);margin-left:auto}

/* ── Lead batches ── */
.batch-wrap{padding:0 28px 30px}
.batch{margin-bottom:28px}
.batch-header{display:flex;align-items:center;gap:10px;margin-bottom:12px;padding-bottom:10px;border-bottom:2px solid var(--border)}
.batch-label{font-size:12px;font-weight:700;color:var(--text);letter-spacing:-.1px}
.batch-meta{font-size:11px;color:var(--text3)}
.batch-count{background:var(--surface2);border:1px solid var(--border);color:var(--text2);font-size:11px;font-weight:600;padding:2px 8px;border-radius:10px}
.batch-niche{display:inline-block;padding:2px 9px;border-radius:4px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-right:4px}

/* ── Lead cards ── */
.lead-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;margin-bottom:8px;display:grid;grid-template-columns:32px 200px 100px 110px 100px 80px 80px 1fr;gap:12px;align-items:start;box-shadow:var(--shadow);transition:box-shadow .15s}
.lead-card:hover{box-shadow:var(--shadow-md)}
.lead-card.is-hot{border-left:3px solid var(--hot);background:linear-gradient(to right,#fff5f5,var(--surface))}
.lead-card.is-warm{border-left:3px solid var(--warm)}
@media(max-width:1200px){.lead-card{grid-template-columns:1fr 1fr;gap:8px}}

.lc-id{font-size:11px;font-weight:700;color:var(--text3);padding-top:3px}
.lc-name{font-size:13px;font-weight:600;color:var(--text);line-height:1.3}
.lc-city{font-size:11px;color:var(--text3);margin-top:2px}
.lc-phone{font-size:12px;color:var(--blue);font-weight:500;margin-top:4px;letter-spacing:.2px}
.lc-niche .bx{font-size:10px}
.lc-status .bx{font-size:11px}
.lc-lifecycle .bx{font-size:11px;font-weight:700}
.lc-clicks{font-size:13px;font-weight:700;color:var(--hot)}
.lc-clicks.zero{color:var(--text3);font-weight:400}
.lc-lastseen{font-size:11px;color:var(--text3)}
.lc-actions{display:flex;gap:5px;flex-wrap:wrap;align-items:flex-start}

/* ── Badges ── */
.bx{display:inline-block;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:600}
.xhot{background:#fee2e2;color:#991b1b;border:1px solid #fca5a5}
.xwarm{background:#fef3c7;color:#92400e;border:1px solid #fcd34d}
.xnew{background:#dbeafe;color:#1d4ed8;border:1px solid #93c5fd}
.xcold{background:#e0f2fe;color:#0369a1;border:1px solid #7dd3fc}
.xdead{background:#f3f4f6;color:#6b7280;border:1px solid #d1d5db}
.xcont{background:#dcfce7;color:#15803d;border:1px solid #86efac}
.xdep{background:#dcfce7;color:#15803d}
.xai{background:#fef3c7;color:#92400e}
.xblt{background:#dbeafe;color:#1d4ed8}
.xscr{background:#f3f4f6;color:#6b7280}

/* ── Msg drawer ── */
.msg-drawer{display:none;margin-top:10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:14px;font-size:12px;grid-column:1/-1}
.msg-section{margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid var(--border)}
.msg-section:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.msg-label{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.msg-body{color:var(--text2);line-height:1.6;white-space:pre-wrap;font-size:12px}
.msg-meta{font-size:11px;color:var(--text3);margin-top:8px;padding-top:8px;border-top:1px solid var(--border)}

/* ── Logs ── */
.log-panel{margin:0 28px 28px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:18px 20px;box-shadow:var(--shadow)}
.lbox{background:var(--surface2);border:1px solid var(--border);border-radius:7px;padding:12px;height:200px;overflow-y:auto;font-family:'SF Mono','Fira Code',monospace;font-size:11px;color:var(--text2);white-space:pre-wrap;word-break:break-all;margin-top:10px}

/* ── Toast ── */
.toast{position:fixed;bottom:24px;right:24px;background:var(--text);color:#fff;padding:10px 18px;border-radius:8px;font-size:13px;display:none;z-index:9999;box-shadow:var(--shadow-md)}
.toast.err{background:var(--red)}
</style>
</head>
<body>

<div class="topbar">
  <div class="logo">Ghost<em>Worker</em></div>
  <div class="live-time"><span class="live-dot"></span>{{ now }}</div>
  <div class="topbar-right">
    <a href="/api/export_leads" class="tbtn">📥 Export CSV</a>
    <a href="/admin/templates" class="tbtn">📄 Templates</a>
  </div>
</div>

<!-- Stats -->
<div class="stats">
  <div class="stat"><div class="slbl">Total Leads</div><div class="sval blue">{{ total }}</div></div>
  <div class="stat"><div class="slbl">HOT Leads</div><div class="sval red">{{ hot }}</div></div>
  <div class="stat"><div class="slbl">Deployed</div><div class="sval green">{{ deployed }}</div></div>
  <div class="stat"><div class="slbl">Pending AI</div><div class="sval amber">{{ pending }}</div></div>
</div>

<!-- Control Panels -->
<div class="panels">
  <!-- Run Mission -->
  <div class="panel">
    <div class="ptitle">
      🚀 Run Mission
      <span class="rbadge" id="running-badge">Running...</span>
    </div>
    <div class="qrow">
      <input class="inp" id="query-inp" placeholder='e.g. Gyms in Lucknow India'>
      <button class="btn btn-primary" id="run-btn">Run</button>
    </div>
    <div style="font-size:11px;color:var(--text3);margin-top:6px">Scrape → AI → Build → Deploy → Outreach. Auto-added to scheduler.</div>
    <div class="qtags" id="query-tags">
      {% for q in scheduled_queries %}
      <div class="qtag" data-query="{{ q }}">{{ q }}<span class="qx" data-query="{{ q }}">×</span></div>
      {% endfor %}
    </div>
  </div>

  <!-- Scheduler -->
  <div class="panel">
    <div class="ptitle">⏱ Scheduler <span class="ptitle-sub">every {{ scrape_interval }}h</span></div>
    <div class="irow">
      <div class="iitem"><span>Scrape every</span><input type="number" id="scrape-interval" value="{{ scrape_interval }}" min="1" max="48"><span>hrs</span></div>
      <div class="iitem"><span>Lifecycle every</span><input type="number" id="lifecycle-interval" value="{{ lifecycle_interval }}" min="1" max="24"><span>hrs</span></div>
      <button class="btn btn-primary btn-sm" id="save-config-btn">Save</button>
    </div>
    <div style="font-size:11px;color:var(--text3);margin-top:10px">One query per interval, rotating through the queue above.</div>
  </div>
</div>

<!-- Lead Pipeline Toolbar -->
<div class="lead-toolbar">
  <span class="lead-toolbar-title">Lead Pipeline</span>
  <select class="sort-sel" id="sort-sel" onchange="applySort()">
    <option value="batch">Newest Batch First</option>
    <option value="id_asc">ID Ascending</option>
    <option value="name_az">Name A–Z</option>
    <option value="scraped_new">Last Scraped (Newest)</option>
  </select>
  <span class="lead-count">{{ total }} leads · HOT on top within each batch</span>
</div>

<!-- Lead Batches -->
<div class="batch-wrap" id="batch-wrap">
{% set ns = namespace(prev_query='', prev_date='', batch_num=0) %}
{% for lead in leads %}
  {% set curr_query = lead.query_string or 'Unknown Query' %}
  {% set curr_date  = (lead.created_at or '')[:10] %}
  {% set batch_key  = curr_query + '|' + curr_date %}

  {% if loop.first or batch_key != (leads[loop.index0-1].query_string or '') + '|' + ((leads[loop.index0-1].created_at or '')[:10]) %}
    {% if not loop.first %}</div>{% endif %}
    {% set ns.batch_num = ns.batch_num + 1 %}
    <div class="batch" data-batch="{{ ns.batch_num }}" data-query="{{ curr_query }}" data-date="{{ curr_date }}">
    <div class="batch-header">
      <span class="batch-label">{{ curr_query }}</span>
      <span class="batch-meta">{{ curr_date }}</span>
      <span class="batch-count">{{ leads | selectattr('query_string','equalto', curr_query) | list | length }} leads</span>
    </div>
  {% endif %}

  <!-- Lead Card -->
  <div class="lead-card {% if lead.lifecycle_status=='HOT' %}is-hot{% elif lead.lifecycle_status=='WARM' %}is-warm{% endif %}"
       data-id="{{ lead.id }}"
       data-name="{{ lead.business_name }}"
       data-created="{{ lead.created_at or '' }}">

    <div class="lc-id">#{{ lead.id }}</div>

    <div>
      <div class="lc-name">{{ lead.business_name[:28] }}{% if lead.business_name|length > 28 %}…{% endif %}</div>
      <div class="lc-city">{{ lead.city or '—' }}</div>
      <div class="lc-phone">{% if lead.phone %}📞 {{ lead.phone }}{% endif %}</div>
    </div>

    <div class="lc-niche">
      <span class="bx xnew">{{ (lead.niche or '—')[:14] }}</span>
    </div>

    <div class="lc-status">
      {% if lead.status=='Deployed' %}<span class="bx xdep">Deployed</span>
      {% elif lead.status=='AI_Complete' %}<span class="bx xai">AI Done</span>
      {% elif lead.status=='Built' %}<span class="bx xblt">Built</span>
      {% else %}<span class="bx xscr">{{ lead.status }}</span>{% endif %}
    </div>

    <div class="lc-lifecycle">
      {% if lead.lifecycle_status=='HOT' %}<span class="bx xhot">🔥 HOT</span>
      {% elif lead.lifecycle_status=='WARM' %}<span class="bx xwarm">WARM</span>
      {% elif lead.lifecycle_status=='COLD' %}<span class="bx xcold">COLD</span>
      {% elif lead.lifecycle_status=='DEAD' %}<span class="bx xdead">DEAD</span>
      {% elif lead.lifecycle_status=='Contacted' %}<span class="bx xcont">Contacted</span>
      {% else %}<span class="bx xnew">NEW</span>{% endif %}
    </div>

    <div class="lc-clicks {% if not lead.click_count or lead.click_count == 0 %}zero{% endif %}">
      {% if lead.click_count and lead.click_count > 0 %}{{ lead.click_count }} clicks{% else %}—{% endif %}
    </div>

    <div class="lc-lastseen">{{ time_ago(lead.last_clicked) }}</div>

    <div class="lc-actions">
      {% if lead.tracker_url %}<button class="btn btn-ghost btn-sm copy-btn" data-url="{{ lead.tracker_url }}">🔗 Link</button>{% endif %}

      {% if lead.phone %}
      {% set wa_msg = '' %}
      {% if lead.outreach_stage == 1 %}{% set wa_msg = lead.wa_draft_1 or '' %}
      {% elif lead.outreach_stage == 2 %}{% set wa_msg = lead.wa_draft_hot or lead.wa_draft_1 or '' %}
      {% elif lead.outreach_stage == 3 %}{% set wa_msg = lead.wa_draft_2 or '' %}
      {% elif lead.outreach_stage == 4 %}{% set wa_msg = lead.wa_draft_3 or '' %}
      {% elif lead.outreach_stage == 5 %}{% set wa_msg = lead.wa_draft_4 or '' %}
      {% endif %}
      <a class="btn btn-green btn-sm"
         href="https://wa.me/{{ lead.phone|replace('+','')|replace(' ','')|replace('-','') }}{% if wa_msg %}?text={{ wa_msg|replace('[LINK]', lead.tracker_url or '')|urlencode }}{% endif %}"
         target="_blank">WA S{{ lead.outreach_stage or 0 }}</a>
      {% endif %}

      {% if lead.outreach_paused %}
      <button class="btn btn-amber btn-sm pause-btn" data-id="{{ lead.id }}" data-paused="1">▶ Resume</button>
      {% else %}
      <button class="btn btn-ghost btn-sm pause-btn" data-id="{{ lead.id }}" data-paused="0">⏸ Pause</button>
      {% endif %}

      <button class="btn btn-ghost btn-sm msg-btn" data-id="{{ lead.id }}">📋 Msgs</button>
      <button class="btn btn-red btn-sm delete-btn" data-id="{{ lead.id }}">Del</button>
    </div>

    <!-- Message Drawer (spans full row) -->
    <div class="msg-drawer" id="msgs-{{ lead.id }}">
      <div style="font-size:11px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px">
        Messages · {{ lead.business_name[:30] }} · Stage {{ lead.outreach_stage or 0 }} · Followups: {{ lead.followup_count or 0 }}
      </div>

      {% if lead.wa_draft_1 %}
      <div class="msg-section">
        <div class="msg-label" style="color:var(--green)">📱 Cold WA (Stage 1)</div>
        <div class="msg-body">{{ lead.wa_draft_1 }}</div>
        <div style="margin-top:6px"><button class="btn btn-ghost btn-xs copy-btn" data-url="{{ lead.wa_draft_1 }}">Copy</button></div>
      </div>{% endif %}

      {% if lead.wa_draft_hot %}
      <div class="msg-section">
        <div class="msg-label" style="color:var(--red)">🔥 HOT Strike WA</div>
        <div class="msg-body">{{ lead.wa_draft_hot }}</div>
        <div style="margin-top:6px"><button class="btn btn-ghost btn-xs copy-btn" data-url="{{ lead.wa_draft_hot }}">Copy</button></div>
      </div>{% endif %}

      {% if lead.wa_draft_2 %}
      <div class="msg-section">
        <div class="msg-label" style="color:var(--amber)">📱 Followup 1 WA</div>
        <div class="msg-body">{{ lead.wa_draft_2 }}</div>
        <div style="margin-top:6px"><button class="btn btn-ghost btn-xs copy-btn" data-url="{{ lead.wa_draft_2 }}">Copy</button></div>
      </div>{% endif %}

      {% if lead.wa_draft_3 %}
      <div class="msg-section">
        <div class="msg-label" style="color:var(--blue)">📱 Followup 2 WA</div>
        <div class="msg-body">{{ lead.wa_draft_3 }}</div>
        <div style="margin-top:6px"><button class="btn btn-ghost btn-xs copy-btn" data-url="{{ lead.wa_draft_3 }}">Copy</button></div>
      </div>{% endif %}

      {% if lead.wa_draft_4 %}
      <div class="msg-section">
        <div class="msg-label" style="color:var(--text3)">📱 Final WA</div>
        <div class="msg-body">{{ lead.wa_draft_4 }}</div>
        <div style="margin-top:6px"><button class="btn btn-ghost btn-xs copy-btn" data-url="{{ lead.wa_draft_4 }}">Copy</button></div>
      </div>{% endif %}

      <div class="msg-meta">Paused: {{ 'Yes' if lead.outreach_paused else 'No' }} · Last contacted: {{ time_ago(lead.last_contacted) }} · Added: {{ time_ago(lead.created_at) }}</div>
    </div>

  </div>
{% endfor %}
{% if leads %}</div>{% endif %}
</div>

<!-- Live Logs -->
<div class="log-panel">
  <div class="ptitle">📋 Live Logs <button class="btn btn-ghost btn-sm" id="refresh-logs-btn" style="margin-left:auto">Refresh</button></div>
  <div class="lbox" id="log-box">Loading...</div>
</div>

<div class="toast" id="toast"></div>
<script src="/static/dashboard.js"></script>
<script>
document.addEventListener('DOMContentLoaded', function() {
  document.getElementById('run-btn').addEventListener('click', runQuery);
  document.getElementById('save-config-btn').addEventListener('click', saveConfig);
  document.getElementById('refresh-logs-btn').addEventListener('click', refreshLogs);
  document.querySelectorAll('.copy-btn').forEach(function(btn){
    btn.addEventListener('click', function(){ copyText(this.dataset.url, this); });
  });
  document.querySelectorAll('.delete-btn').forEach(function(btn){
    btn.addEventListener('click', function(){
      if(!confirm('Delete this lead permanently?')) return;
      fetch('/api/delete_lead',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lead_id:parseInt(this.dataset.id)})})
      .then(r=>r.json()).then(d=>{ if(d.success){showToast('Deleted');setTimeout(()=>location.reload(),600);}else showToast(d.error,true); });
    });
  });
  document.querySelectorAll('.qx').forEach(function(el){
    el.addEventListener('click', function(){ removeQuery(this.dataset.query); });
  });
  document.getElementById('query-inp').addEventListener('keydown', function(e){ if(e.key==='Enter') runQuery(); });
  refreshLogs();
  setInterval(refreshLogs, 15000);
  setInterval(()=>location.reload(), 60000);
});
</script>
</body>
</html>"""



@app.route('/admin/templates')
@auth_required
def templates_page():
    """Template management page."""
    import os
    template_dir = os.path.join(os.path.dirname(__file__), 'templates')
    templates    = []
    if os.path.exists(template_dir):
        for f in os.listdir(template_dir):
            if f.endswith('.html'):
                size = os.path.getsize(os.path.join(template_dir, f))
                templates.append({
                    'name':    f,
                    'niche':   f.replace('.html', ''),
                    'size_kb': round(size / 1024, 1)
                })
    templates.sort(key=lambda x: x['name'])

    # Get S3 assets
    s3_assets = []
    try:
        import boto3
        s3 = boto3.client('s3',
            aws_access_key_id     = os.getenv('AWS_ACCESS_KEY'),
            aws_secret_access_key = os.getenv('AWS_SECRET_KEY'),
            region_name           = os.getenv('AWS_REGION', 'ap-south-1')
        )
        bucket   = os.getenv('S3_BUCKET_NAME')
        response = s3.list_objects_v2(Bucket=bucket, Prefix='assets/')
        for obj in response.get('Contents', []):
            key = obj['Key']
            if not key.endswith('/'):
                s3_assets.append({
                    'key':  key,
                    'url':  f"https://{bucket}.s3.ap-south-1.amazonaws.com/{key}",
                    'size': round(obj['Size'] / 1024, 1)
                })
    except Exception as e:
        logger.error(f"S3 list error: {e}")

    bucket_url = f"https://{os.getenv('S3_BUCKET_NAME')}.s3.ap-south-1.amazonaws.com"

    return render_template_string(
        TEMPLATES_HTML,
        templates  = templates,
        s3_assets  = s3_assets,
        bucket_url = bucket_url
    )


@app.route('/api/upload_template', methods=['POST'])
@auth_required
def api_upload_template():
    """Upload HTML template file."""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file  = request.files['file']
        niche = request.form.get('niche', '').strip().lower()

        if not niche:
            return jsonify({"error": "Niche name required"}), 400
        if not file.filename.endswith('.html'):
            return jsonify({"error": "Only .html files allowed"}), 400

        # Sanitize niche name
        import re
        niche = re.sub(r'[^a-z0-9_-]', '', niche)
        if not niche:
            return jsonify({"error": "Invalid niche name"}), 400

        template_dir = os.path.join(os.path.dirname(__file__), 'templates')
        os.makedirs(template_dir, exist_ok=True)
        save_path = os.path.join(template_dir, f"{niche}.html")

        file.save(save_path)
        size_kb = round(os.path.getsize(save_path) / 1024, 1)
        logger.info(f"Template uploaded: {niche}.html ({size_kb}KB)")

        return jsonify({
            "success": True,
            "filename": f"{niche}.html",
            "size_kb":  size_kb,
            "message":  f"Template saved as {niche}.html"
        })
    except Exception as e:
        logger.error(f"Template upload error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/upload_asset', methods=['POST'])
@auth_required
def api_upload_asset():
    """Upload CSS/JS/image asset to S3."""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file      = request.files['file']
        niche     = request.form.get('niche', '').strip().lower()
        file_type = request.form.get('type', 'css')

        if not niche or not file.filename:
            return jsonify({"error": "Niche and file required"}), 400

        # Determine S3 key and content type
        ext = file.filename.rsplit('.', 1)[-1].lower()
        content_types = {
            'css':  'text/css',
            'js':   'application/javascript',
            'png':  'image/png',
            'jpg':  'image/jpeg',
            'jpeg': 'image/jpeg',
            'webp': 'image/webp',
            'svg':  'image/svg+xml',
        }
        ct  = content_types.get(ext, 'application/octet-stream')
        key = f"assets/{niche}/{file.filename}"

        import boto3
        s3 = boto3.client('s3',
            aws_access_key_id     = os.getenv('AWS_ACCESS_KEY'),
            aws_secret_access_key = os.getenv('AWS_SECRET_KEY'),
            region_name           = os.getenv('AWS_REGION', 'ap-south-1')
        )
        bucket = os.getenv('S3_BUCKET_NAME')

        s3.upload_fileobj(
            file,
            bucket,
            key,
            ExtraArgs={
                'ContentType':  ct,
                'CacheControl': 'max-age=86400'
            }
        )

        url = f"https://{bucket}.s3.ap-south-1.amazonaws.com/{key}"
        logger.info(f"Asset uploaded: {key}")

        return jsonify({
            "success": True,
            "key":     key,
            "url":     url,
            "message": f"Uploaded to S3: {url}"
        })
    except Exception as e:
        logger.error(f"Asset upload error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/delete_template', methods=['POST'])
@auth_required
def api_delete_template():
    """Delete a template file."""
    try:
        niche = request.json.get('niche', '').strip()
        if not niche or niche == 'default':
            return jsonify({"error": "Cannot delete default template"}), 400

        template_path = os.path.join(
            os.path.dirname(__file__), 'templates', f"{niche}.html"
        )
        if os.path.exists(template_path):
            os.remove(template_path)
            logger.info(f"Template deleted: {niche}.html")
            return jsonify({"success": True})
        return jsonify({"error": "Template not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/admin')
@app.route('/admin/')
@app.route('/')
@auth_required
def dashboard():
    try:
        cfg = load_config()
        with get_db() as conn:
            leads_raw = conn.execute('''
                SELECT * FROM leads
                ORDER BY
                  date(created_at) DESC,
                  query_string,
                  CASE lifecycle_status
                    WHEN 'HOT'  THEN 1 WHEN 'WARM' THEN 2 WHEN 'NEW' THEN 3
                    WHEN 'Contacted' THEN 4 WHEN 'COLD' THEN 5
                    WHEN 'DEAD' THEN 6 ELSE 7 END,
                  click_count DESC, reviews_count DESC
            ''').fetchall()
            leads    = [dict(r) for r in leads_raw]
            total    = len(leads)
            hot      = sum(1 for l in leads if l['lifecycle_status']=='HOT')
            deployed = sum(1 for l in leads if l['status']=='Deployed')
            pending  = sum(1 for l in leads if l['status']=='Scraped')

        return render_template_string(
            DASHBOARD_HTML,
            leads=leads, total=total, hot=hot,
            deployed=deployed, pending=pending,
            now=datetime.now().strftime('%d %b %Y %H:%M'),
            time_ago=time_ago,
            scheduled_queries=cfg.get('scheduled_queries',[]),
            scrape_interval=cfg.get('scrape_interval_hours',12),
            pipeline_interval=cfg.get('pipeline_interval_hours',1),
            lifecycle_interval=cfg.get('lifecycle_check_hours',6),
        )
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return f"<h2 style='color:red'>Error</h2><pre>{e}</pre>", 500


if __name__ == "__main__":
    logger.info(f"Starting on port {SERVER_PORT}")
    app.run(host='0.0.0.0', port=SERVER_PORT, debug=False, threaded=True)
