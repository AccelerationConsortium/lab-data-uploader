"""Server-side session tracking with SQLite."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class SessionStore:
    """Tracks registered and completed sessions on the server side."""

    def __init__(self, db_path: str = "./service_state/sessions.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id    TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL,
                    machine_id    TEXT,
                    lab_id        TEXT,
                    file_count    INTEGER,
                    total_bytes   INTEGER,
                    status        TEXT DEFAULT 'registered',
                    upload_id     TEXT,
                    created_at    TEXT,
                    completed_at  TEXT,
                    PRIMARY KEY (session_id, manifest_hash)
                )
            """)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def find_session(self, session_id: str, manifest_hash: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ? AND manifest_hash = ?",
                (session_id, manifest_hash),
            ).fetchone()
            return dict(row) if row else None

    def register_session(
        self,
        session_id: str,
        manifest_hash: str,
        machine_id: str,
        lab_id: str,
        file_count: int,
        total_bytes: int,
        upload_id: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sessions
                    (session_id, manifest_hash, machine_id, lab_id, file_count,
                     total_bytes, status, upload_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'registered', ?, ?)
                """,
                (session_id, manifest_hash, machine_id, lab_id,
                 file_count, total_bytes, upload_id, self._now()),
            )

    def complete_session(self, session_id: str, manifest_hash: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE sessions SET status = 'completed', completed_at = ?
                WHERE session_id = ? AND manifest_hash = ?
                """,
                (self._now(), session_id, manifest_hash),
            )
            return cursor.rowcount > 0

    def list_sessions(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
