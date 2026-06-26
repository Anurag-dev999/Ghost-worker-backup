<div align="center">

# 👻 Ghost Worker

### Autonomous B2B Lead Generation & Outreach Engine

*Find businesses with no website → Build them a free demo site → Send personalised outreach → Close deals automatically*

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.x-green.svg)](https://flask.palletsprojects.com)
[![AWS](https://img.shields.io/badge/AWS-EC2+S3-orange.svg)](https://aws.amazon.com)
[![Evolution API](https://img.shields.io/badge/WhatsApp-Evolution_API-25D366.svg)](https://evolution-api.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## 🎯 What Is Ghost Worker?

Ghost Worker is a fully autonomous agency engine that:

1. **Scrapes** Google Maps for Indian SMBs with no website (4+ stars, 30+ reviews)
2. **Generates** personalised dual-vibe website content using AI (Groq/Gemini)
3. **Builds** professional demo websites from HTML templates
4. **Deploys** them live to AWS S3 in seconds
5. **Tracks** when business owners view their site (The Radar)
6. **Sends** automated WhatsApp outreach via Evolution API
7. **Manages** the full lead lifecycle from cold → hot → deal

**Your only job:** Reply to interested leads and close deals.

---

## 🏗️ Architecture

```
Google Maps (Apify)
      ↓
   Scraper
      ↓
  AI Brain (Groq/Gemini) → Dual-vibe content
      ↓
   Builder → HTML injection
      ↓
S3 Deployer → Live demo site
      ↓
The Radar → Gateway page → Click tracking
      ↓
Evolution API → WhatsApp outreach
      ↓
Dashboard → Lead management
```

---

## ⚡ Quick Start (3 Hours to Live System)

### Prerequisites
- AWS Account (free tier works)
- EC2 t3.micro instance (Ubuntu 22.04)
- Domain name (free at digitalplat.org)

### Step 1 — Launch EC2

1. Go to AWS Console → EC2 → Launch Instance
2. Choose Ubuntu 22.04 LTS, t3.micro, 20GB storage
3. Open ports: 22, 80, 443, 5000, 8080
4. Create key pair, download `.pem` file
5. Attach Elastic IP

### Step 2 — Connect and Setup

```bash
# Connect via SSH
ssh -i "your-key.pem" ubuntu@YOUR_EC2_IP

# Install dependencies
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip python3-venv nginx certbot python3-certbot-nginx docker.io docker-compose git -y
sudo systemctl start docker && sudo systemctl enable docker
sudo usermod -aG docker ubuntu

# Install PM2
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm install -g pm2
```

### Step 3 — Clone and Configure

```bash
git clone https://github.com/YOUR_USERNAME/ghost-worker.git
cd ghost-worker

# Create virtual environment
python3 -m venv ghostenv
source ghostenv/bin/activate

# Install Python packages
pip install flask gunicorn apify-client groq boto3 \
            python-dotenv schedule requests pytz tenacity \
            google-genai

# Copy and fill environment variables
cp .env.example .env
nano .env  # Fill all values
```

### Step 4 — Get API Keys

| Service | URL | Free Tier |
|---------|-----|-----------|
| Groq | console.groq.com | ✅ Free |
| Gemini | aistudio.google.com | ✅ Free |
| Apify | apify.com | ✅ $5 credits/month |
| AWS | aws.amazon.com | ✅ 12 months free |

### Step 5 — Setup AWS S3

1. Create S3 bucket (e.g. `ghostworkersites`)
2. Uncheck "Block all public access"
3. Enable Static website hosting
4. Add bucket policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "PublicRead",
    "Effect": "Allow",
    "Principal": "*",
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::YOUR-BUCKET-NAME/*"
  }]
}
```

5. Create IAM user with `AmazonS3FullAccess`, save access keys to `.env`

### Step 6 — Initialize Database

```bash
python3 init_agency.py
```

### Step 7 — Setup WhatsApp (Evolution API)

```bash
# Start Evolution API via Docker
cd ~
mkdir evolution-api && cd evolution-api
```

Create `docker-compose.yml`:

```yaml
services:
  evolution-api:
    image: atendai/evolution-api:v1.8.2
    container_name: evolution-api
    restart: always
    ports:
      - "8080:8080"
    environment:
      - SERVER_URL=http://YOUR_EC2_IP:8080
      - AUTHENTICATION_API_KEY=your_global_key
    volumes:
      - evolution_data:/evolution/instances
      - evolution_store:/evolution/store
volumes:
  evolution_data:
  evolution_store:
```

```bash
docker compose up -d

# Create WhatsApp instance
curl -X POST http://localhost:8080/instance/create \
  -H "Content-Type: application/json" \
  -H "apikey: your_global_key" \
  -d '{"instanceName":"ghost-worker","qrcode":true,"integration":"WHATSAPP-BAILEYS"}'

# Open http://YOUR_IP:8080/manager and scan QR code
```

### Step 8 — Setup Domain + HTTPS

```bash
cd ~/ghost-worker

# Configure Nginx
sudo nano /etc/nginx/sites-available/ghostworker
# (paste the nginx config from docs/nginx.conf)

sudo ln -s /etc/nginx/sites-available/ghostworker /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Get SSL certificate
sudo certbot --nginx -d yourdomain.com
```

### Step 9 — Start Everything

```bash
cd ~/ghost-worker
pm2 start ecosystem.config.js
pm2 save
pm2 startup
# Run the sudo command PM2 gives you
```

### Step 10 — Add Templates and Queries

1. Open `https://yourdomain.com/admin/templates`
2. Upload your HTML templates (use standard placeholders)
3. Upload CSS/JS assets to S3
4. Open `https://yourdomain.com/admin`
5. Add search queries in Run Mission panel
6. Set scrape interval (recommended: 8 hours)

**Ghost Worker is now live. 🎉**

---

## 📋 Standard Template Placeholders

Use these in any HTML template:

```html
{{BUSINESS_NAME}}   <!-- Full business name -->
{{CITY}}            <!-- City -->
{{RATING}}          <!-- Google star rating -->
{{REVIEWS_COUNT}}   <!-- Number of reviews -->
{{PHONE_CLEAN}}     <!-- Digits-only phone for wa.me -->
{{ADDRESS}}         <!-- Full address -->
{{TRACKER_URL}}     <!-- Tracking redirect URL -->
{{HERO}}            <!-- AI headline (5-7 words) -->
{{ABOUT}}           <!-- AI about paragraph -->
{{SERVICES}}        <!-- Comma-separated services -->
{{FOOTER_TEXT}}     <!-- Footer tagline -->
```

---

## 🗂️ File Structure

```
ghost-worker/
├── .env.example          ← Copy to .env and fill values
├── ecosystem.config.js   ← PM2 process manager config
├── requirements.txt      ← Python dependencies
│
├── init_agency.py        ← Run once to setup database
├── scraper.py            ← Google Maps lead scraping
├── ai_brain.py           ← AI content generation
├── builder.py            ← HTML template injection
├── s3_deployer.py        ← AWS S3 deployment
├── outreach_writer.py    ← AI message writing
├── outreach_engine.py    ← Automated outreach sending
├── whatsapp_sender.py    ← Evolution API integration
├── scheduler.py          ← Sniper mission scheduler
├── dashboard_tracker.py  ← Flask dashboard + radar
├── run_agency.py         ← System health check
│
├── templates/            ← HTML templates per niche
│   ├── cafe.html
│   ├── clinic.html
│   └── default.html
│
├── static/
│   └── dashboard.js      ← Dashboard JavaScript
│
└── logs/                 ← Auto-created log files
```

---

## 🎛️ Dashboard Features

- **Lead Pipeline** — view all leads with status, clicks, lifecycle
- **The Radar** — real-time click tracking with HOT lead detection
- **Run Mission** — trigger scrape + full pipeline for any query
- **Scheduler Settings** — control intervals from UI
- **Template Manager** — upload templates and S3 assets
- **Live Logs** — real-time system logs

---

## 🔄 Lead Lifecycle

```
NEW → [Cold WA sent] → Stage 1
                              ↓
                    Lead clicks tracker link
                              ↓
                         lifecycle = HOT
                              ↓
                    [HOT Strike WA sent]
                              ↓
                    48h no reply → Followup 1
                              ↓
                    48h no reply → Followup 2
                              ↓
                    30 days → Final message → DEAD
                              ↓
                    5 days → Deleted from DB + S3
```

---

## 🛡️ Security

- Two-layer dashboard auth (Nginx + Flask)
- All secrets in `.env` (never committed)
- WhatsApp bot detection prevents false HOT triggers
- Rate limiting on outreach (respects WhatsApp ToS)

---

## 📦 Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python, Flask, Gunicorn |
| Database | SQLite (WAL mode) |
| AI | Groq (Llama 3.3 70B), Gemini 2.0 Flash |
| Scraping | Apify Google Maps Crawler |
| Hosting | AWS EC2 + S3 |
| WhatsApp | Evolution API v1.8.2 |
| Process Manager | PM2 |
| SSL | Let's Encrypt (Certbot) |
| Reverse Proxy | Nginx |

---

## 🤝 Contributing

Pull requests welcome. For major changes please open an issue first.

---

## 📄 License

MIT License — free to use, modify, and distribute.

---

<div align="center">

Built with ❤️ by [Anurag](https://launchpadweb.qzz.io) · [LaunchPad Web](https://launchpadweb.qzz.io)

*Ghost Worker — Because every business deserves to be found online*

</div>
