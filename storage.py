"""
SQLite persistence for vendor requests. Each request's full state (as
JSON) is stored keyed by request_id, so the graph can be invoked once,
exit, and be picked up again later by a different process (a cron job,
a webhook handler) without losing anything.

Kept deliberately simple — one table, state stored as a JSON blob rather
than normalized columns. Fine for MVP; if you need to query on individual
fields a lot later (e.g. "all requests for vendor X"), that's when it's
worth breaking need/vendor/status out into real columns.
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "vendor_requests.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendor_requests (
            request_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    return conn

def save(state_dict: dict):
    conn = get_connection()
    conn.execute(
        """INSERT INTO vendor_requests (request_id, status, state_json, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(request_id) DO UPDATE SET
             status=excluded.status,
             state_json=excluded.state_json,
             updated_at=excluded.updated_at""",
        (state_dict["request_id"], state_dict["status"], json.dumps(state_dict, default=str)),
    )
    conn.commit()
    conn.close()

def load(request_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT state_json FROM vendor_requests WHERE request_id = ?", (request_id,)
    ).fetchone()
    conn.close()
    return json.loads(row[0]) if row else None

def list_by_status(status: str | None = None) -> list[dict]:
    conn = get_connection()
    if status:
        rows = conn.execute(
            "SELECT state_json FROM vendor_requests WHERE status = ?", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT state_json FROM vendor_requests").fetchall()
    conn.close()
    return [json.loads(r[0]) for r in rows]

def list_by_statuses(statuses: list[str]) -> list[dict]:
    """Like list_by_status but for several statuses at once — used for the
    timeout sweep, which needs both AWAITING_RESPONSE and NEGOTIATING
    requests (see graph.py TIMEOUT_CHECKABLE_STATUSES)."""
    if not statuses:
        return []
    conn = get_connection()
    placeholders = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"SELECT state_json FROM vendor_requests WHERE status IN ({placeholders})", statuses
    ).fetchall()
    conn.close()
    return [json.loads(r[0]) for r in rows]

def list_all_summary() -> list[dict]:
    """Lightweight view for a queue/dashboard — no need to load full state."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT request_id, status, updated_at FROM vendor_requests ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [{"request_id": r[0], "status": r[1], "updated_at": r[2]} for r in rows]

# ---- Sourcing requests (multi-vendor competitive sourcing) ----

def ensure_sourcing_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sourcing_requests (
            sourcing_id TEXT PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

def save_sourcing(sourcing_dict: dict):
    conn = get_connection()
    ensure_sourcing_table(conn)
    conn.execute(
        """INSERT INTO sourcing_requests (sourcing_id, state_json, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(sourcing_id) DO UPDATE SET
             state_json=excluded.state_json, updated_at=excluded.updated_at""",
        (sourcing_dict["sourcing_id"], json.dumps(sourcing_dict, default=str)),
    )
    conn.commit()
    conn.close()

def load_sourcing(sourcing_id: str) -> dict | None:
    conn = get_connection()
    ensure_sourcing_table(conn)
    row = conn.execute(
        "SELECT state_json FROM sourcing_requests WHERE sourcing_id = ?", (sourcing_id,)
    ).fetchone()
    conn.close()
    return json.loads(row[0]) if row else None

def list_all_sourcing_summary() -> list[dict]:
    conn = get_connection()
    ensure_sourcing_table(conn)
    rows = conn.execute(
        "SELECT sourcing_id, updated_at FROM sourcing_requests ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [{"sourcing_id": r[0], "updated_at": r[1]} for r in rows]
