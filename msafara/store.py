"""Tiny index of trips for the operator list view. The authoritative trip state lives in the LangGraph
checkpointer (same SQLite file); this table only makes listing cheap."""
import json
import sqlite3
import threading
from pathlib import Path

DB_PATH = str(Path(__file__).resolve().parent.parent / "msafara_runtime.db")
_lock = threading.Lock()
_conn = None


def _c():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("""CREATE TABLE IF NOT EXISTS trip_index(
            trip_id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, status TEXT, gate TEXT, summary TEXT)""")
        _conn.commit()
    return _conn


def upsert(trip: dict, gate: dict | None):
    b = trip.get("brief") or {}
    summary = dict(city=b.get("city"), pax=b.get("pax"), nights=b.get("nights"), request=trip["request_text"][:140],
                   services={s["service_id"]: s["status"] for s in trip["services"]})
    last = trip["audit"][-1]["ts"] if trip["audit"] else trip["created_at"]
    with _lock:
        c = _c()
        c.execute("""INSERT INTO trip_index(trip_id,created_at,updated_at,status,gate,summary) VALUES(?,?,?,?,?,?)
                     ON CONFLICT(trip_id) DO UPDATE SET updated_at=excluded.updated_at,status=excluded.status,
                     gate=excluded.gate,summary=excluded.summary""",
                  (trip["trip_id"], trip["created_at"], last, trip["status"], (gate or {}).get("gate"), json.dumps(summary)))
        c.commit()


def list_trips(limit: int = 100) -> list[dict]:
    with _lock:
        rows = _c().execute("SELECT trip_id,created_at,updated_at,status,gate,summary FROM trip_index ORDER BY created_at DESC LIMIT ?",
                            (limit,)).fetchall()
    return [dict(trip_id=r[0], created_at=r[1], updated_at=r[2], status=r[3], gate=r[4], **json.loads(r[5])) for r in rows]
