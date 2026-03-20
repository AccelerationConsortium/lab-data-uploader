"""Shared pytest fixtures.

Provides a SQLite-backed psycopg2 shim so that state_db tests run in CI
without a real Aurora instance.  The shim:
  - translates %s placeholders  →  ?
  - translates BIGSERIAL PRIMARY KEY  →  INTEGER PRIMARY KEY AUTOINCREMENT
  - honours cursor_factory=RealDictCursor via a dict-mode cursor wrapper
  - sets DATABASE_URL to a dummy value (never actually used)
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# SQL translation helpers
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"%s")
_BIGSERIAL_RE = re.compile(r"\bBIGSERIAL\b", re.IGNORECASE)


def _translate(sql: str) -> str:
    """Convert PostgreSQL-style SQL to SQLite-compatible SQL."""
    sql = _PLACEHOLDER_RE.sub("?", sql)
    sql = _BIGSERIAL_RE.sub("INTEGER", sql)
    return sql


# ---------------------------------------------------------------------------
# Fake cursor
# ---------------------------------------------------------------------------


class _FakeCursor:
    """SQLite cursor wrapper that mimics psycopg2 cursor behaviour."""

    def __init__(self, conn: sqlite3.Connection, dict_mode: bool = False) -> None:
        self._conn = conn
        self._dict_mode = dict_mode
        self._cur = conn.cursor()

    # context-manager support
    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._cur.execute(_translate(sql), params)

    def fetchone(self) -> dict[str, Any] | tuple[Any, ...] | None:
        row = self._cur.fetchone()
        if row is None:
            return None
        if self._dict_mode:
            cols = [d[0] for d in self._cur.description]
            return dict(zip(cols, row))
        return row

    def fetchall(self) -> list[dict[str, Any]] | list[tuple[Any, ...]]:
        rows = self._cur.fetchall()
        if self._dict_mode and rows:
            cols = [d[0] for d in self._cur.description]
            return [dict(zip(cols, r)) for r in rows]
        return rows

    def close(self) -> None:
        self._cur.close()


# ---------------------------------------------------------------------------
# Fake connection
# ---------------------------------------------------------------------------


class _FakeConn:
    """SQLite connection wrapper that mimics psycopg2 connection behaviour."""

    def __init__(self, sqlite_conn: sqlite3.Connection) -> None:
        self._conn = sqlite_conn

    # context-manager: psycopg2 connections commit/rollback on exit
    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, exc_type: Any, *args: object) -> None:
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()

    def cursor(self, cursor_factory: Any = None) -> _FakeCursor:
        import psycopg2.extras

        dict_mode = cursor_factory is psycopg2.extras.RealDictCursor
        return _FakeCursor(self._conn, dict_mode=dict_mode)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Pytest fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_psycopg2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace psycopg2.connect with a SQLite-backed shim for all tests."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")

    # One shared in-memory SQLite DB per test (fresh each time)
    sqlite_conn = sqlite3.connect(":memory:", check_same_thread=False)
    fake_conn = _FakeConn(sqlite_conn)

    def _fake_connect(dsn: str) -> _FakeConn:  # noqa: ARG001
        return fake_conn

    with patch("agent.state_db.psycopg2.connect", side_effect=_fake_connect):
        yield
