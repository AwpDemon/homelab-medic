"""SQLite persistence: incidents and heal history."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at TEXT NOT NULL,
    service TEXT NOT NULL,
    check_detail TEXT NOT NULL,
    cause TEXT,
    confidence REAL,
    action TEXT,
    reasoning TEXT,
    diagnosis_source TEXT,
    heal_attempted INTEGER DEFAULT 0,
    heal_success INTEGER,
    heal_detail TEXT,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS heals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    service TEXT NOT NULL,
    success INTEGER NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_heals_service_ts ON heals (service, ts);
CREATE INDEX IF NOT EXISTS idx_incidents_service ON incidents (service, resolved_at);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # ── incidents ──────────────────────────────────────────────────────────
    def open_incident(self, service: str, check_detail: str, cause: str,
                      confidence: float, action: str, reasoning: str,
                      source: str) -> int:
        cur = self._db.execute(
            "INSERT INTO incidents (opened_at, service, check_detail, cause,"
            " confidence, action, reasoning, diagnosis_source)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (_utcnow(), service, check_detail, cause, confidence, action,
             reasoning, source))
        self._db.commit()
        return int(cur.lastrowid)

    def record_heal(self, incident_id: int, service: str, success: bool,
                    detail: str) -> None:
        now = _utcnow()
        self._db.execute(
            "UPDATE incidents SET heal_attempted=1, heal_success=?, heal_detail=?"
            " WHERE id=?", (int(success), detail, incident_id))
        self._db.execute(
            "INSERT INTO heals (ts, service, success, detail) VALUES (?,?,?,?)",
            (now, service, int(success), detail))
        if success:
            self._db.execute("UPDATE incidents SET resolved_at=? WHERE id=?",
                             (now, incident_id))
        self._db.commit()

    def resolve_open_incidents(self, service: str) -> int:
        """Called when a service checks UP: close any dangling incidents."""
        cur = self._db.execute(
            "UPDATE incidents SET resolved_at=? WHERE service=? AND resolved_at IS NULL",
            (_utcnow(), service))
        self._db.commit()
        return cur.rowcount

    def has_open_incident(self, service: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM incidents WHERE service=? AND resolved_at IS NULL LIMIT 1",
            (service,)).fetchone()
        return row is not None

    def recent_heal_times(self, service: str) -> list[datetime]:
        rows = self._db.execute(
            "SELECT ts FROM heals WHERE service=? ORDER BY ts DESC LIMIT 50",
            (service,)).fetchall()
        return [datetime.fromisoformat(r["ts"]) for r in rows]

    def incident(self, incident_id: int) -> dict:
        row = self._db.execute("SELECT * FROM incidents WHERE id=?",
                               (incident_id,)).fetchone()
        return dict(row) if row else {}

    def recent_incidents(self, limit: int = 20) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
