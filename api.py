"""
api.py
Flask API serving incident data to the dashboard UI.
Run with: gunicorn api:app --bind 127.0.0.1:5001 --workers 2

v2: reads/writes go through SQLite via db.py
"""

from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

import db

app = Flask(__name__, template_folder="templates")
CORS(app)

db.init_db()


@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/api/incidents")
def incidents():
    """
    Returns active incidents with optional filters:
    ?severity=red|yellow
    ?limit=N  (default 50)
    ?offset=N (default 0)
    """
    severity = request.args.get("severity") or None
    limit    = int(request.args.get("limit", 50))
    offset   = int(request.args.get("offset", 0))

    total, page = db.get_incidents(severity=severity, limit=limit, offset=offset)

    return jsonify({
        "total":     total,
        "limit":     limit,
        "offset":    offset,
        "incidents": page,
    })


@app.route("/api/incidents/archive")
def archive():
    """Returns resolved (archived) incidents."""
    limit  = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    total, page = db.get_archive(limit=limit, offset=offset)
    return jsonify({"total": total, "limit": limit, "offset": offset, "incidents": page})


@app.route("/api/summary")
def summary():
    """High-level stats for the dashboard header."""
    data = db.get_summary()

    # Active alerts: pull cooldown state from flat JSON (separate concern)
    import json, time
    state_file = Path(__file__).parent / "logs" / "alert_state.json"
    active = 0
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            active = sum(1 for v in state.values() if (time.time() - v) < 300)
        except Exception:
            pass

    data["active_alerts"] = active
    return jsonify(data)


@app.route("/api/incidents/<string:incident_id>/acknowledge", methods=["POST"])
def acknowledge(incident_id):
    if db.acknowledge_incident(incident_id):
        return jsonify({"ok": True})
    return jsonify({"error": "Incident not found or already acknowledged"}), 404


@app.route("/api/incidents/<string:incident_id>/resolve", methods=["POST"])
def resolve(incident_id):
    if db.resolve_incident(incident_id):
        return jsonify({"ok": True})
    return jsonify({"error": "Incident not found"}), 404


@app.route("/api/incidents/clear-resolved", methods=["POST"])
def clear_resolved():
    removed = db.clear_archive()
    return jsonify({"ok": True, "removed": removed})


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5001)
