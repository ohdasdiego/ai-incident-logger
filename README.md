# AI Incident Logger

A lightweight alerting and incident tracking system that monitors infrastructure metrics, generates AI-powered incident summaries using Claude, delivers real-time alerts via Telegram, and logs everything to a SQLite database with a live dashboard.

> Works alongside [ai-infra-monitor](https://github.com/ohdasdiego/ai-infra-monitor) to provide a complete monitoring and alerting pipeline.

---

## Live Demo

🔗 **[View incident dashboard →](https://incidents.ado-runner.com)**

---

## What It Does

**Background (cron, every 5 min):**
1. Reads live metrics from `ai-infra-monitor`
2. Detects threshold breaches (CPU, memory, disk, processes)
3. Calls Claude API → structured incident summary (cause, impact, action)
4. Sends Telegram alert with severity and AI analysis
5. Writes incident to SQLite with cooldown to prevent duplicates

**Dashboard (Flask, always-on):**
- Live incident feed with severity filtering
- Stats: total, critical, warning, active alerts, last 24h
- Per-incident: AI summary, metric value, Telegram delivery status
- Acknowledge and Resolve actions per incident
- Resolved incidents move to archive (separate table, not deleted)
- Auto-refreshes every 60 seconds

---

## Sample Output

### Telegram Alert

Fires immediately when a threshold is breached:

```
🔴 RED ALERT — CPU
━━━━━━━━━━━━━━━━━━━━
📊 Value: 96.4% (threshold: 95%)
🕐 Time: 2026-04-20 06:47 UTC

CAUSE: Sustained high CPU likely caused by a runaway process or spike in workload.
IMPACT: System responsiveness may degrade; other services could be affected.
ACTION: Run `top` or `ps aux --sort=-%cpu` to identify and investigate the offending process.

🔗 Live Metrics · Incidents
```

```
🟡 YELLOW ALERT — Memory
━━━━━━━━━━━━━━━━━━━━
📊 Value: 87.2% (threshold: 85%)
🕐 Time: 2026-04-20 06:47 UTC

CAUSE: Memory usage elevated, possibly due to a memory leak or increased load.
IMPACT: Risk of OOM if usage continues to rise.
ACTION: Run `free -h` and `ps aux --sort=-%mem` to identify the highest consumers.

🔗 Live Metrics · Incidents
```

### Terminal Log Viewer

```
$ python view_logs.py --last 5

────────────────────────────────────────────────────────────
  Active Incidents  (2 of 2)
────────────────────────────────────────────────────────────

🟡 YELLOW — Memory
  Time:      2026-04-20T06:47:31+00:00
  Value:     87.2% (threshold: 85.0%)
  Telegram:  ✓ sent
  ACK:       —

CAUSE: Memory usage elevated, possibly due to a memory leak or increased load.
IMPACT: Risk of OOM if usage continues to rise.
ACTION: Run `free -h` and `ps aux --sort=-%mem` to identify the highest consumers.

────────────────────────────────────────────────────────────

🔴 RED — CPU
  Time:      2026-04-20T06:47:31+00:00
  Value:     96.4% (threshold: 95.0%)
  Telegram:  ✓ sent
  ACK:       —

CAUSE: Sustained high CPU likely caused by a runaway process or spike in workload.
IMPACT: System responsiveness may degrade; other services could be affected.
ACTION: Run `top` or `ps aux --sort=-%cpu` to identify and investigate the offending process.

────────────────────────────────────────────────────────────
```

### SQLite Query (direct)

```
$ sqlite3 logs/incidents.db "SELECT metric, severity, value, timestamp FROM incidents;"

Memory|yellow|87.2|2026-04-20T06:47:31+00:00
CPU|red|96.4|2026-04-20T06:47:31+00:00
```

### Cron Log (all clear)

```
$ tail -f logs/cron.log

[06:45:01] All clear — no thresholds breached.
[06:50:02] All clear — no thresholds breached.
[06:55:01] All clear — no thresholds breached.
[07:00:02] [RED] CPU at 96.4% — generating incident summary...
  → Telegram alert sent.
[07:05:01] [RED] CPU at 96.4% — cooldown active, skipping.
```

---

## Architecture

```
cron (every 5 min)
  └── alerter.py
        ├── reads ../ai-infra-monitor/data/metrics.json
        ├── evaluates thresholds
        ├── Claude API → incident summary
        ├── Telegram alert
        └── db.py → SQLite (incidents table)

gunicorn (always-on, port 5001)
  └── api.py (Flask)
        ├── GET  /                          → dashboard UI
        ├── GET  /api/incidents             → active incident list (filterable, paginated)
        ├── GET  /api/incidents/archive     → resolved incident archive
        ├── GET  /api/summary               → stats
        ├── POST /api/incidents/:id/acknowledge
        └── POST /api/incidents/:id/resolve → moves row to incidents_archive
```

### Storage: Dual Output

Every incident is written to **two places** simultaneously:

| Output | Purpose |
|---|---|
| **Telegram** | Instant push notification — you know immediately without checking anything |
| **SQLite** | Persistent record — queryable history, powers the dashboard, ACK/resolve workflow |

This mirrors how production alerting platforms (PagerDuty, OpsGenie) work: push notification for awareness, database for tracking and audit trail.

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

## Database Schema

```sql
-- Active and acknowledged incidents
CREATE TABLE incidents (
    id               TEXT PRIMARY KEY,
    timestamp        TEXT NOT NULL,
    metric           TEXT NOT NULL,
    severity         TEXT NOT NULL,
    value            REAL NOT NULL,
    threshold        REAL NOT NULL,
    summary          TEXT,
    notified         INTEGER DEFAULT 0,
    acknowledged     INTEGER DEFAULT 0,
    acknowledged_at  TEXT
);

-- Resolved incidents moved here on resolution
CREATE TABLE incidents_archive (
    id               TEXT PRIMARY KEY,
    timestamp        TEXT NOT NULL,
    metric           TEXT NOT NULL,
    severity         TEXT NOT NULL,
    value            REAL NOT NULL,
    threshold        REAL NOT NULL,
    summary          TEXT,
    notified         INTEGER DEFAULT 0,
    acknowledged     INTEGER DEFAULT 0,
    acknowledged_at  TEXT,
    resolved_at      TEXT NOT NULL,
    archived_at      TEXT NOT NULL
);
```

Resolving an incident moves the row from `incidents` → `incidents_archive`. The active dashboard only queries `incidents`; a separate archive view queries `incidents_archive`. This mirrors how production alerting platforms separate active from historical records.

---

## Setup

### Prerequisites
- [ai-infra-monitor](https://github.com/ohdasdiego/ai-infra-monitor) installed and running
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

### 8. View incidents from terminal

```bash
# Active incidents
python view_logs.py

# Filter by severity
python view_logs.py --severity red

# View resolved/archived
python view_logs.py --archive

# Direct SQLite query
sqlite3 logs/incidents.db "SELECT metric, severity, value FROM incidents;"
```

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3 |
| AI analysis | Anthropic Claude API (`claude-haiku-4-5`) |
| Alerting | Telegram Bot API |
| Storage | SQLite (via `db.py`) |
| Dashboard | Flask + Gunicorn |
| Frontend | Vanilla HTML/CSS/JS |
| Scheduler | cron |
| Process management | systemd |

---

## Author

**Diego Perez** · [github.com/ohdasdiego](https://github.com/ohdasdiego)
