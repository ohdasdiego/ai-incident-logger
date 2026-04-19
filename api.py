"""
api.py
Flask API serving incident log data to the dashboard UI.
Run with: gunicorn api:app --bind 127.0.0.1:5001 --workers 2
(Port 5001 to avoid conflict with ai-infra-monitor on 5000)
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__, template_folder="templates")
CORS(app)

INCIDENT_LOG = Path(__file__).parent / "logs" / "incidents.jsonl"
STATE_FILE   = Path(__file__).parent / "logs" / "alert_state.json"


def load_incidents() -> list:
    if not INCIDENT_LOG.exists():
        return []
    try:
        with open(INCIDENT_LOG) as f:
            return [json.loads(line) for line in f if line.strip()]
    except Exception:
        return []


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/api/incidents")
def incidents():
    """
    Returns incidents with optional filters:
    ?severity=red|yellow
    ?limit=N
    ?offset=N
    """
    all_incidents = load_incidents()

    severity = request.args.get("severity")
    if severity:
        all_incidents = [i for i in all_incidents if i.get("severity") == severity]

    # Most recent first
    all_incidents = list(reversed(all_incidents))

    total = len(all_incidents)
    limit  = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    page   = all_incidents[offset:offset + limit]

    return jsonify({
        "total": total,
        "limit": limit,
        "offset": offset,
        "incidents": page,
    })


@app.route("/api/summary")
def summary():
    """High-level stats for the dashboard header."""
    all_incidents = load_incidents()

    total    = len(all_incidents)
    red      = sum(1 for i in all_incidents if i.get("severity") == "red")
    yellow   = sum(1 for i in all_incidents if i.get("severity") == "yellow")
    notified = sum(1 for i in all_incidents if i.get("notified"))
    resolved = sum(1 for i in all_incidents if i.get("resolved"))

    # Last 24h
    now = datetime.now(timezone.utc).timestamp()
    last_24h = sum(
        1 for i in all_incidents
        if (now - datetime.fromisoformat(i["timestamp"]).timestamp()) < 86400
    )

    # Most recent incident
    latest = all_incidents[-1] if all_incidents else None

    # Active alerts (currently in cooldown)
    state = load_state()
    import time
    active = [k for k, v in state.items() if (time.time() - v) < 300]

    return jsonify({
        "total_incidents": total,
        "red": red,
        "yellow": yellow,
        "notified": notified,
        "resolved": resolved,
        "last_24h": last_24h,
        "active_alerts": len(active),
        "latest_incident": latest,
    })


@app.route("/api/incidents/<string:incident_id>/acknowledge", methods=["POST"])
def acknowledge(incident_id):
    """Mark an incident as acknowledged by its unique ID."""
    all_incidents = load_incidents()
    match = next((i for i, inc in enumerate(all_incidents) if inc.get("id") == incident_id), None)
    if match is None:
        return jsonify({"error": "Incident not found"}), 404

    all_incidents[match]["acknowledged"] = True
    all_incidents[match]["acknowledged_at"] = datetime.now(timezone.utc).isoformat()

    with open(INCIDENT_LOG, "w") as f:
        for entry in all_incidents:
            f.write(json.dumps(entry) + "\n")

    return jsonify({"ok": True})


@app.route("/api/incidents/<string:incident_id>/resolve", methods=["POST"])
def resolve(incident_id):
    """Mark an incident as resolved by its unique ID."""
    all_incidents = load_incidents()
    match = next((i for i, inc in enumerate(all_incidents) if inc.get("id") == incident_id), None)
    if match is None:
        return jsonify({"error": "Incident not found"}), 404

    all_incidents[match]["resolved"] = True
    all_incidents[match]["resolved_at"] = datetime.now(timezone.utc).isoformat()

    with open(INCIDENT_LOG, "w") as f:
        for entry in all_incidents:
            f.write(json.dumps(entry) + "\n")

    return jsonify({"ok": True})


@app.route("/api/incidents/clear-resolved", methods=["POST"])
def clear_resolved():
    """Remove all resolved incidents from the log."""
    all_incidents = load_incidents()
    remaining = [i for i in all_incidents if not i.get("resolved")]
    with open(INCIDENT_LOG, "w") as f:
        for entry in remaining:
            f.write(json.dumps(entry) + "\n")
    return jsonify({"ok": True, "removed": len(all_incidents) - len(remaining)})


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5001)
