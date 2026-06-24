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


# ── Radar routes ─────────────────────────────────────────

@app.route('/view/<int:lead_id>')
def track_view(lead_id):
    try:
        ip        = request.headers.get('X-Forwarded-For', request.remote_addr)
        ua        = request.headers.get('User-Agent', '').lower()
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        device    = 'Mobile' if any(x in ua for x in ['mobile','android','iphone']) else 'Desktop'

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
                    click_count=?, last_clicked=?, lifecycle_status='HOT'
                WHERE id=?
            ''', (new_count, timestamp, lead_id))
            conn.commit()

            logger.info(f"RADAR — {lead['business_name']} | Click #{new_count} | {device} | {ip}")
            s3_url = lead['s3_url']

        if not s3_url:
            return "Site not ready", 404

        return redirect(s3_url, code=302)

    except Exception as e:
        logger.error(f"Tracker error: {e}")
        return "Error", 500


@app.route('/view/<int:lead_id>/pixel')
def tracker_pixel(lead_id):
    try:
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with get_db() as conn:
            lead = conn.execute(
                "SELECT click_count FROM leads WHERE id=?", (lead_id,)
            ).fetchone()
            if lead:
                conn.execute('''
                    UPDATE leads SET click_count=?, last_clicked=?,
                    lifecycle_status='HOT' WHERE id=?
                ''', ((lead['click_count'] or 0)+1, ts, lead_id))
                conn.commit()
    except Exception as e:
        logger.error(f"Pixel error: {e}")

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
        event = data.get('event', '')

        if event != 'messages.upsert':
            return jsonify({"status": "ignored"}), 200

        msg_data = data.get('data', {})
        key      = msg_data.get('key', {})

        # Only process incoming messages (not our own sent messages)
        if key.get('fromMe', True):
            return jsonify({"status": "own_message"}), 200

        # Extract sender phone
        remote_jid = key.get('remoteJid', '')
        phone      = remote_jid.replace('@s.whatsapp.net', '').replace('@g.us', '')

        # Extract message text
        msg_content = msg_data.get('message', {})
        text = (
            msg_content.get('conversation') or
            msg_content.get('extendedTextMessage', {}).get('text') or
            ''
        )

        if not text or not phone:
            return jsonify({"status": "no_text"}), 200

        logger.info(f"WA REPLY received from {phone}: {text[:80]}")

        # Find lead by phone number
        with get_db() as conn:
            # Try matching with various phone formats
            lead = conn.execute('''
                SELECT id, business_name, lifecycle_status
                FROM leads
                WHERE replace(replace(replace(phone, '+', ''), ' ', ''), '-', '')
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
                        outreach_paused = 1,
                        last_clicked = ?
                    WHERE id = ?
                ''', (now_str(), lead['id']))
                conn.commit()
                logger.info(
                    f"Reply from: {lead['business_name'][:40]} "
                    f"(ID:{lead['id']}) — marked WARM + PAUSED"
                )
                # Save reply and mark as WARM (they replie

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"status": "error"}), 500



# ── Dashboard ─────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Ghost Worker</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',Arial,sans-serif;background:#0a0e1a;color:#e0e6f0;min-height:100vh}
.topbar{background:#0d1117;border-bottom:1px solid #1e2d45;padding:14px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}
.logo{font-size:18px;font-weight:700;color:#58a6ff}.logo span{color:#3fb950}
.live{font-size:12px;color:#8b949e}
.dot{width:7px;height:7px;border-radius:50%;background:#3fb950;display:inline-block;margin-right:5px;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;padding:18px 24px}
.stat{background:#161b27;border:1px solid #1e2d45;border-radius:10px;padding:18px}
.slbl{font-size:11px;color:#8b949e;margin-bottom:4px;text-transform:uppercase;letter-spacing:1px}
.sval{font-size:28px;font-weight:700}
.cb{color:#58a6ff}.cr{color:#f85149}.cg{color:#3fb950}.ca{color:#d29922}
.panel{background:#161b27;border:1px solid #1e2d45;border-radius:10px;margin:0 24px 16px;padding:18px}
.ptitle{font-size:13px;font-weight:600;color:#e0e6f0;margin-bottom:12px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.qrow{display:flex;gap:8px;flex-wrap:wrap}
.inp{background:#0d1117;border:1px solid #1e2d45;color:#e0e6f0;padding:9px 14px;border-radius:7px;font-size:13px;flex:1;min-width:200px}
.inp:focus{outline:none;border-color:#58a6ff}
.btn{padding:9px 16px;border-radius:7px;font-size:13px;font-weight:600;cursor:pointer;border:none;transition:opacity .15s}
.btn:hover{opacity:.8}
.bb{background:#1f6feb;color:#fff}.bg{background:#238636;color:#fff}
.br{background:#da3633;color:#fff}.bk{background:#21262d;color:#e0e6f0}
.bs{padding:5px 10px;font-size:11px;border-radius:6px}
.irow{display:flex;gap:16px;flex-wrap:wrap;align-items:center}
.iitem{display:flex;align-items:center;gap:7px;font-size:13px;color:#8b949e}
.iitem input{width:58px;background:#0d1117;border:1px solid #1e2d45;color:#e0e6f0;padding:6px 10px;border-radius:6px;font-size:13px;text-align:center}
.qtags{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}
.qtag{background:#1e2d45;color:#58a6ff;padding:4px 11px;border-radius:20px;font-size:12px;display:flex;align-items:center;gap:5px}
.qx{cursor:pointer;color:#8b949e;font-size:13px}.qx:hover{color:#f85149}
.sbar{padding:4px 24px 10px;display:flex;align-items:center;justify-content:space-between}
.stitle{font-size:13px;font-weight:600;color:#e0e6f0}
.ssub{font-size:12px;color:#8b949e}
.tw{overflow-x:auto;padding:0 24px 30px}
table{width:100%;border-collapse:collapse;font-size:13px}
thead th{background:#161b27;color:#8b949e;font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:1px;padding:10px 12px;text-align:left;border-bottom:1px solid #1e2d45}
tbody tr{border-bottom:1px solid #1a2030;transition:background .15s}
tbody tr:hover{background:#161b27}
tbody tr.hot{border-left:3px solid #f85149;background:#1a0d0d;animation:hg 3s ease-in-out infinite}
@keyframes hg{0%,100%{background:#1a0d0d}50%{background:#200f0f}}
td{padding:11px 12px;vertical-align:middle}
.bx{display:inline-block;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:600}
.xhot{background:#3d0f0f;color:#f85149;border:1px solid #5c1a1a}
.xwarm{background:#2d1f00;color:#d29922;border:1px solid #4a3200}
.xnew{background:#0d1f3d;color:#58a6ff;border:1px solid #1a3a6b}
.xcold{background:#0d2020;color:#39d0d0;border:1px solid #1a4040}
.xdead{background:#1a1a1a;color:#6e7681;border:1px solid #30363d}
.xcont{background:#0d2a1a;color:#3fb950;border:1px solid #1a4a2a}
.xdep{background:#0d2a1a;color:#3fb950}
.xai{color:#d29922;background:#1a1a1a}
.xblt{color:#58a6ff;background:#1a1a1a}
.xscr{color:#8b949e;background:#1a1a1a}
.cw{display:flex;align-items:center;gap:7px}
.cb2{height:4px;border-radius:2px;background:#f85149;min-width:3px}
.cn{font-weight:600;color:#f85149}
.acts{display:flex;gap:5px;flex-wrap:wrap}
.nm{font-weight:500;color:#e0e6f0;max-width:170px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ns{font-size:11px;color:#8b949e;margin-top:2px}
.lbox{background:#0d1117;border:1px solid #1e2d45;border-radius:7px;padding:12px;height:200px;overflow-y:auto;font-family:monospace;font-size:11px;color:#8b949e;white-space:pre-wrap;word-break:break-all}
.toast{position:fixed;bottom:20px;right:20px;background:#1e2d45;color:#58a6ff;padding:10px 16px;border-radius:8px;font-size:13px;border:1px solid #2a4060;display:none;z-index:999}
.rbadge{background:#0d2a1a;color:#3fb950;border:1px solid #1a4a2a;padding:3px 10px;border-radius:20px;font-size:11px;display:none}
</style>
</head>
<body>

<div class="topbar">
  <div class="logo">Ghost<span>Worker</span> <span style="color:#8b949e;font-size:12px;font-weight:400">Control Tower</span></div>
  <div class="live"><span class="dot"></span>{{ now }}</div>
</div>

<div class="stats">
  <div class="stat"><div class="slbl">Total Leads</div><div class="sval cb">{{ total }}</div></div>
  <div class="stat"><div class="slbl">HOT Leads</div><div class="sval cr">{{ hot }}</div></div>
  <div class="stat"><div class="slbl">Deployed</div><div class="sval cg">{{ deployed }}</div></div>
  <div class="stat"><div class="slbl">Pending AI</div><div class="sval ca">{{ pending }}</div></div>
</div>

<div class="panel">
  <div class="ptitle">Run Mission <span class="rbadge" id="running-badge">Pipeline Running...</span></div>
  <div class="qrow">
    <input class="inp" id="query-inp" placeholder='e.g. Gyms in Lucknow'>
    <button class="btn bg" id="run-btn">Run Now</button>
  </div>
  <div style="font-size:12px;color:#8b949e;margin-top:7px">Full pipeline: Scrape → AI → Build → Deploy. Auto-added to scheduler.</div>
  <div style="margin-top:14px;font-size:12px;color:#8b949e;margin-bottom:7px">Scheduled queries (every {{ scrape_interval }}h):</div>
  <div class="qtags" id="query-tags">
    {% for q in scheduled_queries %}
    <div class="qtag" data-query="{{ q }}">{{ q }}<span class="qx" data-query="{{ q }}">×</span></div>
    {% endfor %}
  </div>
</div>

<div class="panel">
  <div class="ptitle">Scheduler Settings</div>
  <div class="irow">
    <div class="iitem"><span>Scrape every</span><input type="number" id="scrape-interval" value="{{ scrape_interval }}" min="1" max="48"><span>hrs</span></div>
    <div class="iitem"><span>Pipeline every</span><input type="number" id="pipeline-interval" value="{{ pipeline_interval }}" min="1" max="24"><span>hrs</span></div>
    <div class="iitem"><span>Lifecycle every</span><input type="number" id="lifecycle-interval" value="{{ lifecycle_interval }}" min="1" max="24"><span>hrs</span></div>
    <button class="btn bb bs" id="save-config-btn">Save</button>
  </div>
</div>

<div class="sbar">
  <span class="stitle">Lead Pipeline</span>
  <span class="ssub">HOT always on top</span>
</div>

<div class="tw">
<table>
  <thead><tr>
    <th>#</th><th>Business</th><th>Niche</th><th>Status</th>
    <th>Lifecycle</th><th>Clicks</th><th>Last Seen</th><th>Actions</th>
  </tr></thead>
  <tbody>
  {% for lead in leads %}
  <tr class="{{ 'hot' if lead.lifecycle_status == 'HOT' else '' }}">
    <td style="color:#8b949e;font-size:12px;font-weight:600;">{{ lead.id }}</td>
    <td>
      <div class="nm" title="{{ lead.business_name }}">{{ lead.business_name[:30] }}{% if lead.business_name|length > 30 %}…{% endif %}</div>
      <div class="ns">{{ lead.city or '—' }}</div>
    </td>
    <td><span class="bx xnew">{{ lead.niche or '—' }}</span></td>
    <td>
      {% if lead.status=='Deployed' %}<span class="bx xdep">Deployed</span>
      {% elif lead.status=='AI_Complete' %}<span class="bx xai">AI Done</span>
      {% elif lead.status=='Built' %}<span class="bx xblt">Built</span>
      {% elif lead.status=='Contacted' %}<span class="bx xcont">Contacted</span>
      {% else %}<span class="bx xscr">{{ lead.status }}</span>{% endif %}
    </td>
    <td>
      {% if lead.lifecycle_status=='HOT' %}<span class="bx xhot">HOT</span>
      {% elif lead.lifecycle_status=='WARM' %}<span class="bx xwarm">WARM</span>
      {% elif lead.lifecycle_status=='COLD' %}<span class="bx xcold">COLD</span>
      {% elif lead.lifecycle_status=='DEAD' %}<span class="bx xdead">DEAD</span>
      {% elif lead.lifecycle_status=='Contacted' %}<span class="bx xcont">Sent</span>
      {% else %}<span class="bx xnew">NEW</span>{% endif %}
    </td>
    <td>
      {% if lead.click_count and lead.click_count > 0 %}
      <div class="cw"><div class="cb2" style="width:{{ [lead.click_count*10,60]|min }}px"></div><span class="cn">{{ lead.click_count }}</span></div>
      {% else %}<span style="color:#30363d">—</span>{% endif %}
    </td>
    <td style="font-size:12px;color:#8b949e">{{ time_ago(lead.last_clicked) }}</td>
    <td>
      <div class="acts">
        {% if lead.s3_url %}<a class="btn bk bs" href="{{ lead.s3_url }}" target="_blank">Site</a>{% endif %}
        {% if lead.tracker_url %}<button class="btn bk bs copy-btn" data-url="{{ lead.tracker_url }}">Link</button>{% endif %}
        
        
        {% if lead.phone %}
        {% set wa_msg = '' %}
        {% if lead.outreach_stage == 1 %}{% set wa_msg = lead.wa_draft_1 or '' %}
        {% elif lead.outreach_stage == 2 %}{% set wa_msg = lead.wa_draft_hot or lead.wa_draft_1 or '' %}
        {% elif lead.outreach_stage == 3 %}{% set wa_msg = lead.wa_draft_2 or '' %}
        {% elif lead.outreach_stage == 4 %}{% set wa_msg = lead.wa_draft_3 or '' %}
        {% elif lead.outreach_stage == 5 %}{% set wa_msg = lead.wa_draft_4 or '' %}
        {% endif %}
        <a class="btn bg bs"
        href="https://wa.me/{{ lead.phone|replace('+','')|replace(' ','')|replace('-','') }}{% if wa_msg %}?text={{ wa_msg|replace('[LINK]', lead.tracker_url or '')|urlencode }}{% endif %}"
        target="_blank"
        title="Stage {{ lead.outreach_stage }} message">WA S{{ lead.outreach_stage }}</a>
        {% endif %}
        

        {% if lead.outreach_paused %}
        <button class="btn bb bs pause-btn" data-id="{{ lead.id }}" data-paused="1" style="background:#d29922">▶ Resume</button>
        {% else %}
        <button class="btn br bs pause-btn" data-id="{{ lead.id }}" data-paused="0">⏸ Pause</button>
        {% endif %}
        <button class="btn bk bs msg-btn" data-id="{{ lead.id }}">📋 Msgs</button>
        <button class="btn bb bs status-btn" data-id="{{ lead.id }}" data-status="Contacted">Sent</button>
        <button class="btn br bs delete-btn" data-id="{{ lead.id }}">Del</button>
      </div>
      <!-- Message drawer -->
      <div class="msg-drawer" id="msgs-{{ lead.id }}" style="display:none;margin-top:10px;background:#0d1117;border:1px solid #1e2d45;border-radius:8px;padding:12px;font-size:12px;max-width:500px">
        <div style="color:#8b949e;margin-bottom:8px;font-size:11px;text-transform:uppercase;letter-spacing:1px">Messages for ID:{{ lead.id }}</div>

        {% if lead.wa_draft_1 %}
        <div style="margin-bottom:10px">
          <div style="color:#3fb950;font-size:11px;margin-bottom:4px">📱 Cold WA (Stage 1)</div>
          <div style="color:#e0e6f0;line-height:1.5;white-space:pre-wrap">{{ lead.wa_draft_1 }}</div>
          <button class="btn bk bs copy-btn" data-url="{{ lead.wa_draft_1 }}" style="margin-top:5px">Copy</button>
        </div>
        {% endif %}

        {% if lead.wa_draft_hot %}
        <div style="margin-bottom:10px;border-left:3px solid #f85149;padding-left:8px">
          <div style="color:#f85149;font-size:11px;margin-bottom:4px">🔥 HOT STRIKE WA</div>
          <div style="color:#e0e6f0;line-height:1.5;white-space:pre-wrap">{{ lead.wa_draft_hot }}</div>
          <button class="btn br bs copy-btn" data-url="{{ lead.wa_draft_hot }}" style="margin-top:5px">Copy</button>
        </div>
        {% endif %}

        {% if lead.wa_draft_2 %}
        <div style="margin-bottom:10px">
          <div style="color:#d29922;font-size:11px;margin-bottom:4px">📱 Followup 1 WA</div>
          <div style="color:#e0e6f0;line-height:1.5;white-space:pre-wrap">{{ lead.wa_draft_2 }}</div>
          <button class="btn bk bs copy-btn" data-url="{{ lead.wa_draft_2 }}" style="margin-top:5px">Copy</button>
        </div>
        {% endif %}

        {% if lead.wa_draft_3 %}
        <div style="margin-bottom:10px">
          <div style="color:#58a6ff;font-size:11px;margin-bottom:4px">📱 Followup 2 WA</div>
          <div style="color:#e0e6f0;line-height:1.5;white-space:pre-wrap">{{ lead.wa_draft_3 }}</div>
          <button class="btn bk bs copy-btn" data-url="{{ lead.wa_draft_3 }}" style="margin-top:5px">Copy</button>
        </div>
        {% endif %}

        {% if lead.wa_draft_4 %}
        <div style="margin-bottom:10px">
          <div style="color:#6e7681;font-size:11px;margin-bottom:4px">📱 Final WA</div>
          <div style="color:#e0e6f0;line-height:1.5;white-space:pre-wrap">{{ lead.wa_draft_4 }}</div>
          <button class="btn bk bs copy-btn" data-url="{{ lead.wa_draft_4 }}" style="margin-top:5px">Copy</button>
        </div>
        {% endif %}

        <div style="color:#8b949e;font-size:11px;margin-top:8px">Stage: {{ lead.outreach_stage or 0 }} | Followups: {{ lead.followup_count or 0 }} | Paused: {{ 'Yes' if lead.outreach_paused else 'No' }}</div>
      </div>
    </td>
    
  </tr>
  {% endfor %}
  </tbody>
</table>
</div>

<div class="panel">
  <div class="ptitle">Live Logs <button class="btn bk bs" id="refresh-logs-btn" style="margin-left:auto">Refresh</button></div>
  <div class="lbox" id="log-box">Loading...</div>
</div>

<div class="toast" id="toast"></div>

<script src="/static/dashboard.js"></script>

<script>
// Wire up all buttons using event listeners — no inline onclick needed
document.addEventListener('DOMContentLoaded', function() {

  // Run query button
  document.getElementById('run-btn').addEventListener('click', runQuery);

  // Save config button
  document.getElementById('save-config-btn').addEventListener('click', saveConfig);

  // Refresh logs button
  document.getElementById('refresh-logs-btn').addEventListener('click', refreshLogs);

  // Copy link buttons
  document.querySelectorAll('.copy-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      copyText(this.dataset.url, this);
    });
  });

  // Status buttons
  document.querySelectorAll('.status-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      updateStatus(parseInt(this.dataset.id), this.dataset.status, this);
    });
  });

  // Delete buttons
  document.querySelectorAll('.delete-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      const id = parseInt(this.dataset.id);
      if (!confirm('Delete this lead permanently?')) return;
      fetch('/api/delete_lead', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({lead_id: id})
      }).then(r => r.json()).then(d => {
        if (d.success) { showToast('Lead deleted'); setTimeout(() => location.reload(), 600); }
        else showToast(d.error, true);
      });
    });
  });

  // Remove query tags
  document.querySelectorAll('.qx').forEach(function(el) {
    el.addEventListener('click', function() {
      removeQuery(this.dataset.query);
    });
  });

  // Enter key on query input
  document.getElementById('query-inp').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') runQuery();
  });

  // Load logs
  refreshLogs();
  setInterval(refreshLogs, 15000);
  setInterval(() => location.reload(), 60000);
});
</script>
</body>
</html>"""


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
                  CASE lifecycle_status
                    WHEN 'HOT' THEN 1 WHEN 'WARM' THEN 2 WHEN 'NEW' THEN 3
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
