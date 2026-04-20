"""
db.py
SQLite database layer for ai-incident-logger v2.
Handles schema creation, migrations, and all read/write operations.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH      = Path(__file__).parent / "logs" / "incidents.db"
JSONL_PATH   = Path(__file__).parent / "logs" / "incidents.jsonl"

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
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

CREATE TABLE IF NOT EXISTS incidents_archive (
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
"""


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist, then migrate JSONL if present."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    _migrate_jsonl()


def _migrate_jsonl():
    """One-time import of existing incidents.jsonl into SQLite."""
    if not JSONL_PATH.exists():
        return

    with get_conn() as conn:
        existing = {row[0] for row in conn.execute("SELECT id FROM incidents")}
        archived = {row[0] for row in conn.execute("SELECT id FROM incidents_archive")}
        already_imported = existing | archived

        imported = 0
        with open(JSONL_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if e.get("id") in already_imported:
                    continue

                if e.get("resolved"):
                    conn.execute("""
                        INSERT OR IGNORE INTO incidents_archive
                        (id, timestamp, metric, severity, value, threshold, summary,
                         notified, acknowledged, acknowledged_at, resolved_at, archived_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        e.get("id"), e.get("timestamp"), e.get("metric"),
                        e.get("severity"), e.get("value"), e.get("threshold"),
                        e.get("summary"), int(e.get("notified", False)),
                        int(e.get("acknowledged", False)), e.get("acknowledged_at"),
                        e.get("resolved_at", e.get("timestamp")),
                        datetime.now(timezone.utc).isoformat(),
                    ))
                else:
                    conn.execute("""
                        INSERT OR IGNORE INTO incidents
                        (id, timestamp, metric, severity, value, threshold, summary,
                         notified, acknowledged, acknowledged_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (
                        e.get("id"), e.get("timestamp"), e.get("metric"),
                        e.get("severity"), e.get("value"), e.get("threshold"),
                        e.get("summary"), int(e.get("notified", False)),
                        int(e.get("acknowledged", False)), e.get("acknowledged_at"),
                    ))
                imported += 1

        if imported:
            print(f"[db] Migrated {imported} incidents from JSONL to SQLite.")


# ── Write operations ──────────────────────────────────────

def insert_incident(incident: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO incidents
            (id, timestamp, metric, severity, value, threshold, summary, notified)
            VALUES (:id, :timestamp, :metric, :severity, :value, :threshold, :summary, :notified)
        """, incident)


def acknowledge_incident(incident_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("""
            UPDATE incidents SET acknowledged=1, acknowledged_at=?
            WHERE id=? AND acknowledged=0
        """, (datetime.now(timezone.utc).isoformat(), incident_id))
        return cur.rowcount > 0


def resolve_incident(incident_id: str) -> bool:
    """Move incident from active table to archive."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM incidents WHERE id=?", (incident_id,)
        ).fetchone()
        if not row:
            return False
        conn.execute("""
            INSERT OR IGNORE INTO incidents_archive
            (id, timestamp, metric, severity, value, threshold, summary,
             notified, acknowledged, acknowledged_at, resolved_at, archived_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            row["id"], row["timestamp"], row["metric"], row["severity"],
            row["value"], row["threshold"], row["summary"],
            row["notified"], row["acknowledged"], row["acknowledged_at"],
            now, now,
        ))
        conn.execute("DELETE FROM incidents WHERE id=?", (incident_id,))
        return True


def clear_archive() -> int:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM incidents_archive")
        return cur.rowcount


# ── Read operations ───────────────────────────────────────

def _row_to_dict(row) -> dict:
    return dict(row)


def get_incidents(severity=None, limit=50, offset=0) -> tuple[int, list]:
    """Returns (total_count, page_of_incidents) most recent first."""
    where = "WHERE severity=?" if severity else ""
    params_count = (severity,) if severity else ()

    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM incidents {where}", params_count
        ).fetchone()[0]

        params_page = (*params_count, limit, offset)
        rows = conn.execute(
            f"SELECT * FROM incidents {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            params_page,
        ).fetchall()

    return total, [_row_to_dict(r) for r in rows]


def get_summary() -> dict:
    with get_conn() as conn:
        total    = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        red      = conn.execute("SELECT COUNT(*) FROM incidents WHERE severity='red'").fetchone()[0]
        yellow   = conn.execute("SELECT COUNT(*) FROM incidents WHERE severity='yellow'").fetchone()[0]
        notified = conn.execute("SELECT COUNT(*) FROM incidents WHERE notified=1").fetchone()[0]
        archived = conn.execute("SELECT COUNT(*) FROM incidents_archive").fetchone()[0]

        # Last 24h (active only)
        last_24h = conn.execute("""
            SELECT COUNT(*) FROM incidents
            WHERE timestamp >= datetime('now', '-1 day')
        """).fetchone()[0]

        latest_row = conn.execute(
            "SELECT * FROM incidents ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

    return {
        "total_incidents": total,
        "red": red,
        "yellow": yellow,
        "notified": notified,
        "resolved": archived,
        "last_24h": last_24h,
        "latest_incident": _row_to_dict(latest_row) if latest_row else None,
    }


def get_all_active() -> list:
    """All active incidents for view_logs.py CLI."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM incidents ORDER BY timestamp ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_archive(limit=50, offset=0) -> tuple[int, list]:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM incidents_archive").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM incidents_archive ORDER BY resolved_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return total, [_row_to_dict(r) for r in rows]
