# Incident Logger

A lightweight alerting and incident tracking system that monitors infrastructure metrics, generates structured incident summaries, delivers real-time alerts via Telegram, and displays a live incident dashboard.

> Works alongside [infra-monitor](https://github.com/ohdasdiego/ai-infra-monitor) to provide a complete monitoring and alerting pipeline.

---

## Live Demo

🔗 **[View incident dashboard →](https://incidents.ado-runner.com)**

---

## What It Does

**Background (cron, every 5 min):**
1. Reads live metrics from `infra-monitor`
2. Detects threshold breaches (CPU, memory, disk, processes)
3. Generates a structured incident summary (cause, impact, action)
4. Sends Telegram alert with severity details
5. Logs incident to `incidents.jsonl` with cooldown to prevent duplicates

**Dashboard (Flask, always-on):**
- Live incident feed with severity filtering
- Stats: total, critical, warning, active alerts, last 24h
- Per-incident: summary, metric value, Telegram delivery status
- Acknowledge and Resolve actions per incident
- Auto-refreshes every 60 seconds

### Example Telegram Alert

```
🔴 RED ALERT — CPU
━━━━━━━━━━━━━━━━━━━━
📊 Value: 96% (threshold: 95%)
🕐 Time: 2026-04-18 03:00 UTC

CAUSE: Sustained high CPU likely caused by a runaway process or spike in workload.
IMPACT: System responsiveness may degrade; other services could be affected.
ACTION: Run `top` or `ps aux --sort=-%cpu` to identify and investigate the offending process.
```

---

## Architecture

```
cron (every 5 min)
  └── alerter.py
        ├── reads ../ai-infra-monitor/data/metrics.json
        ├── evaluates thresholds
        ├── analysis engine → incident summary
        ├── Telegram alert
        └── logs/incidents.jsonl

gunicorn (always-on, port 5001)
  └── api.py (Flask)
        ├── GET  /                     → dashboard UI
        ├── GET  /api/incidents        → incident list (filterable, paginated)
        ├── GET  /api/summary          → stats
        ├── POST /api/incidents/:id/acknowledge
        └── POST /api/incidents/:id/resolve
```

---

## Thresholds

| Metric | Warning 🟡 | Critical 🔴 |
|---|---|---|
| CPU | > 80% | > 95% |
| Memory | > 85% | > 92% |
| Disk | > 80% | > 90% |
| Processes | > 300 | > 500 |

> Memory thresholds are tuned for a 2GB VPS running multiple services. A 1GB host will idle at 70–80%, so aggressive thresholds produce noise. Tune to your baseline.

---

## Setup

### Prerequisites
- [infra-monitor](https://github.com/ohdasdiego/ai-infra-monitor) installed and running
- Telegram bot token + chat ID (see below)
- Anthropic API key

### 1. Get Telegram credentials

1. Message **@BotFather** on Telegram → send `/newbot`
2. Copy the bot token
3. Start a chat with your bot, then visit:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Send any message to the bot, refresh the URL, copy your `chat.id`

### 2. Install

```bash
git clone https://github.com/ohdasdiego/ai-incident-logger.git
cd ai-incident-logger
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
mkdir -p logs
```

### 3. Configure

```bash
cp .env.example .env
nano .env
```

### 4. Test alerter

```bash
python alerter.py
# "All clear" = healthy, or you'll see an alert + Telegram message
```

### 5. Start dashboard

```bash
gunicorn api:app --bind 0.0.0.0:5001
# Visit http://your-vps-ip:5001
```

### 6. Set up cron

```bash
crontab -e
```
```cron
*/5 * * * * cd /home/YOUR_USER/ai-incident-logger && venv/bin/python alerter.py >> logs/cron.log 2>&1
```

### 7. Run as systemd service

```bash
nano incident-logger.service   # replace YOUR_LINUX_USER
sudo cp incident-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now incident-logger
```

### 8. View logs from terminal

```bash
python view_logs.py
python view_logs.py --last 10 --severity red
```

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python |
| Analysis engine | Anthropic Claude API (`claude-haiku-4-5`) |
| Alerting | Telegram Bot API |
| Dashboard | Flask + Gunicorn |
| Frontend | Vanilla HTML/CSS/JS |
| Scheduler | cron |
| Storage | JSONL flat file (SQLite migration planned — see Roadmap) |

---

## Roadmap

### v2 — SQLite Migration

The current storage layer uses append-only JSONL flat files, which works well at single-host scale. The planned v2 migration will move to **SQLite** for proper relational storage and archiving.

**Why SQLite:**
- Zero external dependencies — single `.db` file on disk
- Full SQL querying: filter by severity, time range, metric type
- Proper archive table separate from the active incident feed
- Built into Python standard library — no new deps

**Planned schema:**

```sql
-- Active and acknowledged incidents
CREATE TABLE incidents (
    id          TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    metric      TEXT NOT NULL,
    severity    TEXT NOT NULL,
    value       REAL NOT NULL,
    threshold   REAL NOT NULL,
    summary     TEXT,
    notified    INTEGER DEFAULT 0,
    acknowledged INTEGER DEFAULT 0,
    acknowledged_at TEXT
);

-- Resolved incidents moved here on resolution
CREATE TABLE incidents_archive (
    id           TEXT PRIMARY KEY,
    timestamp    TEXT NOT NULL,
    metric       TEXT NOT NULL,
    severity     TEXT NOT NULL,
    value        REAL NOT NULL,
    threshold    REAL NOT NULL,
    summary      TEXT,
    notified     INTEGER DEFAULT 0,
    acknowledged INTEGER DEFAULT 0,
    acknowledged_at TEXT,
    resolved_at  TEXT NOT NULL,
    archived_at  TEXT NOT NULL
);
```

**Behavior change:** Resolving an incident moves the row from `incidents` → `incidents_archive` instead of flagging it in-place. The active dashboard only queries `incidents`; a separate archive view queries `incidents_archive`.

This mirrors how production alerting platforms (PagerDuty, OpsGenie) separate active from historical incident records.

---

## Author

**Diego Perez** · [github.com/ohdasdiego](https://github.com/ohdasdiego)
