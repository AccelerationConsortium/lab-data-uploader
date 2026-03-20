"""Aurora PostgreSQL state database for tracking session uploads.

Connection DSN is read from the ``DATABASE_URL`` environment variable so that
the secret never appears in config files or code.  The ECS task definition
injects this value from AWS Secrets Manager.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import psycopg2
import psycopg2.extras


class StateDB:
    """Aurora PostgreSQL access layer for upload state.

    Each public method opens its own connection.  This keeps the class
    thread-safe without external locking and avoids long-lived idle
    connections in the Aurora serverless tier.
    """

    def __init__(self) -> None:
        self._dsn = os.environ["DATABASE_URL"]

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _connect(self) -> psycopg2.extensions.connection:
        return psycopg2.connect(self._dsn)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------

    def init_db(self) -> None:
        """Create tables if they do not already exist."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
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
                cur.execute(
                    """\
                    CREATE TABLE IF NOT EXISTS uploaded_files (
                        id            BIGSERIAL PRIMARY KEY,
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
            with conn.cursor() as cur:
                cur.execute(
                    """\
                    INSERT INTO sessions
                        (session_id, session_path, profile, manifest_hash,
                         status, file_count, total_bytes, retry_count,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, %s)
                    ON CONFLICT (session_id) DO UPDATE SET
                        session_path  = EXCLUDED.session_path,
                        profile       = EXCLUDED.profile,
                        manifest_hash = EXCLUDED.manifest_hash,
                        status        = EXCLUDED.status,
                        file_count    = EXCLUDED.file_count,
                        total_bytes   = EXCLUDED.total_bytes,
                        updated_at    = EXCLUDED.updated_at
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

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return a session row as a dict, or *None* if not found."""
        with self._connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM sessions WHERE session_id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
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
            with conn.cursor() as cur:
                if uploaded_at:
                    cur.execute(
                        """\
                        UPDATE sessions
                           SET status      = %s,
                               updated_at  = %s,
                               uploaded_at = %s,
                               last_error  = %s
                         WHERE session_id  = %s
                        """,
                        (status, now, uploaded_at, error, session_id),
                    )
                else:
                    cur.execute(
                        """\
                        UPDATE sessions
                           SET status     = %s,
                               updated_at = %s,
                               last_error = %s
                         WHERE session_id = %s
                        """,
                        (status, now, error, session_id),
                    )

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
            with conn.cursor() as cur:
                cur.execute(
                    """\
                    INSERT INTO uploaded_files
                        (session_id, manifest_hash, relative_path, sha256, size, upload_status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (session_id, manifest_hash, relative_path, sha256, size, status),
                )

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    def get_pending_sessions(self) -> list[dict[str, Any]]:
        """Return sessions that still need processing."""
        with self._connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """\
                    SELECT * FROM sessions
                     WHERE status IN ('discovered', 'waiting_for_stable',
                                      'ready_to_register', 'failed')
                    """
                )
                rows = cur.fetchall()
        return [dict(r) for r in rows]

    def get_failed_sessions(self) -> list[dict[str, Any]]:
        """Return sessions whose status is ``'failed'``."""
        with self._connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM sessions WHERE status = 'failed'")
                rows = cur.fetchall()
        return [dict(r) for r in rows]

    def increment_retry_count(self, session_id: str) -> None:
        """Bump the retry counter and touch ``updated_at``."""
        now = self._now()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """\
                    UPDATE sessions
                       SET retry_count = retry_count + 1,
                           updated_at  = %s
                     WHERE session_id  = %s
                    """,
                    (now, session_id),
                )
