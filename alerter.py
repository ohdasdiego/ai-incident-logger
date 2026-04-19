"""
alerter.py
Reads metrics from ai-infra-monitor, detects threshold breaches,
generates Claude AI incident summaries, and sends Telegram alerts.
Run via cron every 5 minutes.

Designed to be lightweight — no database, no web server.
State is tracked in a simple JSON file to avoid duplicate alerts.
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────
METRICS_FILE    = Path(os.getenv("METRICS_FILE", "../ai-infra-monitor/data/metrics.json"))
STATE_FILE      = Path(__file__).parent / "logs" / "alert_state.json"
INCIDENT_LOG    = Path(__file__).parent / "logs" / "incidents.jsonl"

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT   = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY")
MODEL           = "claude-haiku-4-5-20251001"

# How long to suppress repeat alerts for the same issue (seconds)
COOLDOWN_SECONDS = 1800  # 30 minutes

# Thresholds — tuned for a 2GB VPS running multiple services
THRESHOLDS = {
    "cpu": {"yellow": 80, "red": 95},
    "memory": {"yellow": 85, "red": 92},
    "disk": {"yellow": 80, "red": 90},
    "processes": {"yellow": 300, "red": 500},
}


# ── State management (prevents duplicate alerts) ──────────
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
    last = state.get(key, 0)
    return (time.time() - last) < COOLDOWN_SECONDS


def mark_alerted(state: dict, key: str):
    state[key] = time.time()


# ── Metric evaluation ─────────────────────────────────────
def evaluate(metrics: dict) -> list[dict]:
    """
    Returns a list of triggered alerts:
    [{"key": str, "severity": "yellow"|"red", "metric": str, "value": float, "threshold": float}]
    """
    latest = metrics.get("latest", {})
    alerts = []

    # CPU
    cpu = latest.get("cpu_percent", 0)
    if cpu >= THRESHOLDS["cpu"]["red"]:
        alerts.append({"key": "cpu", "severity": "red", "metric": "CPU", "value": cpu, "threshold": THRESHOLDS["cpu"]["red"]})
    elif cpu >= THRESHOLDS["cpu"]["yellow"]:
        alerts.append({"key": "cpu", "severity": "yellow", "metric": "CPU", "value": cpu, "threshold": THRESHOLDS["cpu"]["yellow"]})

    # Memory
    mem = latest.get("memory", {}).get("percent", 0)
    if mem >= THRESHOLDS["memory"]["red"]:
        alerts.append({"key": "memory", "severity": "red", "metric": "Memory", "value": mem, "threshold": THRESHOLDS["memory"]["red"]})
    elif mem >= THRESHOLDS["memory"]["yellow"]:
        alerts.append({"key": "memory", "severity": "yellow", "metric": "Memory", "value": mem, "threshold": THRESHOLDS["memory"]["yellow"]})

    # Disks
    for disk in latest.get("disks", []):
        mount = disk["mountpoint"]
        pct = disk["percent"]
        key = f"disk_{mount}"
        if pct >= THRESHOLDS["disk"]["red"]:
            alerts.append({"key": key, "severity": "red", "metric": f"Disk {mount}", "value": pct, "threshold": THRESHOLDS["disk"]["red"]})
        elif pct >= THRESHOLDS["disk"]["yellow"]:
            alerts.append({"key": key, "severity": "yellow", "metric": f"Disk {mount}", "value": pct, "threshold": THRESHOLDS["disk"]["yellow"]})

    # Processes
    procs = latest.get("process_count", 0)
    if procs >= THRESHOLDS["processes"]["red"]:
        alerts.append({"key": "processes", "severity": "red", "metric": "Process count", "value": procs, "threshold": THRESHOLDS["processes"]["red"]})
    elif procs >= THRESHOLDS["processes"]["yellow"]:
        alerts.append({"key": "processes", "severity": "yellow", "metric": "Process count", "value": procs, "threshold": THRESHOLDS["processes"]["yellow"]})

    return alerts


# ── Claude incident summary ───────────────────────────────
def generate_incident_summary(alert: dict, metrics: dict) -> str:
    latest = metrics.get("latest", {})

    prompt = f"""You are a NOC analyst assistant. Generate a concise incident summary for the following alert.

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
    icon = "🔴" if alert["severity"] == "red" else "🟡"
    severity = alert["severity"].upper()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return (
        f"{icon} *{severity} ALERT — {alert['metric']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Value: `{alert['value']}%` (threshold: `{alert['threshold']}%`)\n"
        f"🕐 Time: `{ts}`\n\n"
        f"{summary}\n\n"
        f"🔗 [Live Metrics](https://monitor.ado-runner.com) · [Incidents](https://incidents.ado-runner.com)\n"
        f"_AI Incident Logger · ai-infra-monitor_"
    )


# ── Incident log ──────────────────────────────────────────
def log_incident(alert: dict, summary: str, notified: bool):
    entry = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metric": alert["metric"],
        "severity": alert["severity"],
        "value": alert["value"],
        "threshold": alert["threshold"],
        "summary": summary,
        "notified": notified,
    }
    INCIDENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(INCIDENT_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Main ──────────────────────────────────────────────────
def main():
    if not METRICS_FILE.exists():
        print("No metrics file found. Is ai-infra-monitor running?")
        return

    with open(METRICS_FILE) as f:
        metrics = json.load(f)

    alerts = evaluate(metrics)

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
            summary = f"CAUSE: Unable to generate AI summary ({e})\nIMPACT: Unknown\nACTION: Investigate manually."

        notified = False
        try:
            message = format_telegram_message(alert, summary)
            send_telegram(message)
            notified = True
            print(f"  → Telegram alert sent.")
        except Exception as e:
            print(f"  → Telegram failed: {e}")

        log_incident(alert, summary, notified)
        mark_alerted(state, key)

    save_state(state)


if __name__ == "__main__":
    main()
