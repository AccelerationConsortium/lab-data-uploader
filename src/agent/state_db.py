"""SQLite state database for tracking session uploads."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class StateDB:
    """Thread-safe SQLite access layer for upload state.

    Each public method opens its own connection so the class is safe
    to use from multiple threads without external locking.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def init_db(self) -> None:
        """Create tables if they do not already exist."""
        with self._connect() as conn:
            conn.execute(
                """\
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id    TEXT PRIMARY KEY,
                    session_path  TEXT,
                    profile       TEXT,
                    manifest_hash TEXT,
                    status        TEXT,
                    file_count    INTEGER,
                    total_bytes   INTEGER,
                    retry_count   INTEGER DEFAULT 0,
                    last_error    TEXT,
                    created_at    TEXT,
                    updated_at    TEXT,
                    uploaded_at   TEXT
                )
                """
            )
            conn.execute(
                """\
                CREATE TABLE IF NOT EXISTS uploaded_files (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id    TEXT,
                    manifest_hash TEXT,
                    relative_path TEXT,
                    sha256        TEXT,
                    size          INTEGER,
                    upload_status TEXT
                )
                """
            )

    # ------------------------------------------------------------------
    # session operations
    # ------------------------------------------------------------------

    def upsert_session(
        self,
        session_id: str,
        session_path: str,
        profile: str,
        manifest_hash: str,
        status: str,
        file_count: int,
        total_bytes: int,
    ) -> None:
        """Insert a new session or update an existing one."""
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                """\
                INSERT INTO sessions
                    (session_id, session_path, profile, manifest_hash,
                     status, file_count, total_bytes, retry_count,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    session_path  = excluded.session_path,
                    profile       = excluded.profile,
                    manifest_hash = excluded.manifest_hash,
                    status        = excluded.status,
                    file_count    = excluded.file_count,
                    total_bytes   = excluded.total_bytes,
                    updated_at    = excluded.updated_at
                """,
                (
                    session_id,
                    session_path,
                    profile,
                    manifest_hash,
                    status,
                    file_count,
                    total_bytes,
                    now,
                    now,
                ),
            )

    def get_session(self, session_id: str) -> dict | None:
        """Return a session row as a dict, or *None* if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_session_status(
        self,
        session_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        """Update session status and timestamp.

        If *status* is ``'uploaded'`` the ``uploaded_at`` column is set.
        If *error* is provided the ``last_error`` column is updated.
        """
        now = self._now()
        uploaded_at = now if status == "uploaded" else None
        with self._connect() as conn:
            if uploaded_at:
                conn.execute(
                    """\
                    UPDATE sessions
                       SET status      = ?,
                           updated_at  = ?,
                           uploaded_at = ?,
                           last_error  = ?
                     WHERE session_id  = ?
                    """,
                    (status, now, uploaded_at, error, session_id),
                )
            else:
                conn.execute(
                    """\
                    UPDATE sessions
                       SET status     = ?,
                           updated_at = ?,
                           last_error = ?
                     WHERE session_id = ?
                    """,
                    (status, now, error, session_id),
                )

    # ------------------------------------------------------------------
    # deduplication
    # ------------------------------------------------------------------

    def is_duplicate(self, session_id: str, manifest_hash: str) -> bool:
        """Return *True* when the same session+manifest was already uploaded."""
        with self._connect() as conn:
            row = conn.execute(
                """\
                SELECT 1 FROM sessions
                 WHERE session_id    = ?
                   AND manifest_hash = ?
                   AND status        = 'uploaded'
                """,
                (session_id, manifest_hash),
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # file tracking
    # ------------------------------------------------------------------

    def record_file_upload(
        self,
        session_id: str,
        manifest_hash: str,
        relative_path: str,
        sha256: str,
        size: int,
        status: str,
    ) -> None:
        """Record an individual file upload result."""
        with self._connect() as conn:
            conn.execute(
                """\
                INSERT INTO uploaded_files
                    (session_id, manifest_hash, relative_path, sha256, size, upload_status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, manifest_hash, relative_path, sha256, size, status),
            )

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    def get_pending_sessions(self) -> list[dict]:
        """Return sessions that still need processing."""
        with self._connect() as conn:
            rows = conn.execute(
                """\
                SELECT * FROM sessions
                 WHERE status IN ('discovered', 'waiting_for_stable',
                                  'ready_to_register', 'failed')
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def get_failed_sessions(self) -> list[dict]:
        """Return sessions whose status is ``'failed'``."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE status = 'failed'"
            ).fetchall()
        return [dict(r) for r in rows]

    def increment_retry_count(self, session_id: str) -> None:
        """Bump the retry counter and touch ``updated_at``."""
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                """\
                UPDATE sessions
                   SET retry_count = retry_count + 1,
                       updated_at  = ?
                 WHERE session_id  = ?
                """,
                (now, session_id),
            )
