"""
alerter.py
Reads metrics from ai-infra-monitor, detects threshold breaches,
generates incident summaries via Claude, and sends Telegram alerts.
Run via cron every 5 minutes.

v2: incidents stored in SQLite via db.py
"""

import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

import db

load_dotenv()

# ── Config ────────────────────────────────────────────────
METRICS_FILE    = Path(os.getenv("METRICS_FILE", "../ai-infra-monitor/data/metrics.json"))
STATE_FILE      = Path(__file__).parent / "logs" / "alert_state.json"

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT   = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY")
MODEL           = "claude-haiku-4-5-20251001"

ONCALL_WEBHOOK_URL    = os.getenv("ONCALL_WEBHOOK_URL", "")
ONCALL_WEBHOOK_SECRET = os.getenv("ONCALL_WEBHOOK_SECRET", "")

COOLDOWN_SECONDS = 1800  # 30 minutes

THRESHOLDS = {
    "cpu":       {"yellow": 80,  "red": 95},
    "memory":    {"yellow": 85,  "red": 92},
    "disk":      {"yellow": 80,  "red": 90},
    "processes": {"yellow": 300, "red": 500},
}


# ── Cooldown state (flat JSON — lightweight, not incident data) ───
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def is_cooling_down(state: dict, key: str) -> bool:
    return (time.time() - state.get(key, 0)) < COOLDOWN_SECONDS


def mark_alerted(state: dict, key: str):
    state[key] = time.time()


# ── Metric evaluation ─────────────────────────────────────
def evaluate(metrics: dict) -> list[dict]:
    latest = metrics.get("latest", {})
    alerts = []

    cpu = latest.get("cpu_percent", 0)
    if cpu >= THRESHOLDS["cpu"]["red"]:
        alerts.append({"key": "cpu", "severity": "red", "metric": "CPU", "value": cpu, "threshold": THRESHOLDS["cpu"]["red"]})
    elif cpu >= THRESHOLDS["cpu"]["yellow"]:
        alerts.append({"key": "cpu", "severity": "yellow", "metric": "CPU", "value": cpu, "threshold": THRESHOLDS["cpu"]["yellow"]})

    mem = latest.get("memory", {}).get("percent", 0)
    if mem >= THRESHOLDS["memory"]["red"]:
        alerts.append({"key": "memory", "severity": "red", "metric": "Memory", "value": mem, "threshold": THRESHOLDS["memory"]["red"]})
    elif mem >= THRESHOLDS["memory"]["yellow"]:
        alerts.append({"key": "memory", "severity": "yellow", "metric": "Memory", "value": mem, "threshold": THRESHOLDS["memory"]["yellow"]})

    for disk in latest.get("disks", []):
        mount = disk["mountpoint"]
        if mount.startswith("/snap/"):
            continue
        pct   = disk["percent"]
        key   = f"disk_{mount}"
        if pct >= THRESHOLDS["disk"]["red"]:
            alerts.append({"key": key, "severity": "red", "metric": f"Disk {mount}", "value": pct, "threshold": THRESHOLDS["disk"]["red"]})
        elif pct >= THRESHOLDS["disk"]["yellow"]:
            alerts.append({"key": key, "severity": "yellow", "metric": f"Disk {mount}", "value": pct, "threshold": THRESHOLDS["disk"]["yellow"]})

    procs = latest.get("process_count", 0)
    if procs >= THRESHOLDS["processes"]["red"]:
        alerts.append({"key": "processes", "severity": "red", "metric": "Process count", "value": procs, "threshold": THRESHOLDS["processes"]["red"]})
    elif procs >= THRESHOLDS["processes"]["yellow"]:
        alerts.append({"key": "processes", "severity": "yellow", "metric": "Process count", "value": procs, "threshold": THRESHOLDS["processes"]["yellow"]})

    return alerts


# ── Claude incident summary ───────────────────────────────
def generate_incident_summary(alert: dict, metrics: dict) -> str:
    latest = metrics.get("latest", {})

    prompt = f"""You are an infrastructure operations analyst. Generate a concise incident summary for the following alert.

Alert: {alert['metric']} is at {alert['value']}% (threshold: {alert['threshold']}%, severity: {alert['severity'].upper()})

Current system snapshot:
- CPU: {latest.get('cpu_percent')}%
- Memory: {latest.get('memory', {}).get('percent')}%
- Processes: {latest.get('process_count')}
- Uptime: {latest.get('uptime_hours')} hours

Respond in this exact format (3 lines, no extra text):
CAUSE: <likely cause in one sentence>
IMPACT: <potential impact in one sentence>
ACTION: <recommended immediate action in one sentence>"""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 200,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"].strip()


# ── Telegram ──────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    response = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT,
        "text": message,
        "parse_mode": "Markdown",
    }, timeout=10)
    response.raise_for_status()


def format_telegram_message(alert: dict, summary: str) -> str:
    icon     = "🔴" if alert["severity"] == "red" else "🟡"
    severity = alert["severity"].upper()
    ts       = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return (
        f"{icon} *{severity} ALERT — {alert['metric']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Value: `{alert['value']}%` (threshold: `{alert['threshold']}%`)\n"
        f"🕐 Time: `{ts}`\n\n"
        f"{summary}\n\n"
        f"🔗 [Live Metrics](https://monitor.ado-runner.com) · [Incidents](https://incidents.ado-runner.com)\n"
        f"_Incident Logger · infra-monitor_"
    )


# ── On-Call Assistant webhook ────────────────────────────────

def fire_oncall_webhook(alert: dict, summary: str, metrics: dict) -> bool:
    """POST alert to On-Call Assistant. Returns True on success."""
    if not ONCALL_WEBHOOK_URL:
        return False

    severity_map = {"red": "high", "yellow": "medium"}
    severity = severity_map.get(alert["severity"], "medium")

    latest = metrics.get("latest", {})
    cpu = latest.get("cpu_percent", "?")
    mem = latest.get("memory", {}).get("percent", "?")

    # Extract CAUSE/IMPACT/ACTION lines from summary for description
    desc_lines = [summary, f"CPU: {cpu}% | Memory: {mem}%"]
    description = "\n".join(desc_lines)

    payload = {
        "title": f"{alert['metric']} alert on claw-gateway1 — {alert['value']}% (threshold: {alert['threshold']}%)",
        "description": description,
        "severity": severity,
        "host": "claw-gateway1",
        "metric": alert["key"],
        "value": alert["value"]
    }

    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}

    if ONCALL_WEBHOOK_SECRET:
        sig = hmac.new(ONCALL_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = f"sha256={sig}"

    try:
        resp = requests.post(ONCALL_WEBHOOK_URL, data=body, headers=headers, timeout=10)
        resp.raise_for_status()
        incident_id = resp.json().get("incident_id", "?")
        print(f"  → On-Call Assistant: incident #{incident_id} opened.")
        return True
    except Exception as e:
        print(f"  → On-Call webhook failed: {e}")
        return False


# ── Main ──────────────────────────────────────────────────
def main():
    db.init_db()

    if not METRICS_FILE.exists():
        print("No metrics file found. Is ai-infra-monitor running?")
        return

    with open(METRICS_FILE) as f:
        metrics = json.load(f)

    alerts  = evaluate(metrics)

    if not alerts:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] All clear — no thresholds breached.")
        return

    state = load_state()

    for alert in alerts:
        key = alert["key"]

        if is_cooling_down(state, key):
            print(f"[{alert['severity'].upper()}] {alert['metric']} at {alert['value']}% — cooldown active, skipping.")
            continue

        print(f"[{alert['severity'].upper()}] {alert['metric']} at {alert['value']}% — generating incident summary...")

        try:
            summary = generate_incident_summary(alert, metrics)
        except Exception as e:
            summary = f"CAUSE: Unable to generate summary ({e})\nIMPACT: Unknown\nACTION: Investigate manually."

        notified = False

        # Route to On-Call Assistant if configured (preferred) — it owns the Telegram alert
        if ONCALL_WEBHOOK_URL:
            notified = fire_oncall_webhook(alert, summary, metrics)
        
        # Fallback: send Telegram directly if On-Call is not configured or failed
        if not notified:
            try:
                message = format_telegram_message(alert, summary)
                send_telegram(message)
                notified = True
                print(f"  → Telegram alert sent (direct — On-Call not available).")
            except Exception as e:
                print(f"  → Telegram failed: {e}")

        db.insert_incident({
            "id":        str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metric":    alert["metric"],
            "severity":  alert["severity"],
            "value":     alert["value"],
            "threshold": alert["threshold"],
            "summary":   summary,
            "notified":  int(notified),
        })

        mark_alerted(state, key)

    save_state(state)


if __name__ == "__main__":
    main()
