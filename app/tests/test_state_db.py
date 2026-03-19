"""Tests for the SQLite state database layer."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent.state_db import StateDB


@pytest.fixture()
def db(tmp_path: Path) -> StateDB:
    """Return an initialised in-tmp-dir StateDB."""
    sdb = StateDB(str(tmp_path / "sub" / "state.db"))
    sdb.init_db()
    return sdb


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------


def test_init_db_creates_tables(db: StateDB) -> None:
    """init_db should create both sessions and uploaded_files tables."""
    conn = sqlite3.connect(db.db_path)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "sessions" in tables
    assert "uploaded_files" in tables


def test_init_db_idempotent(db: StateDB) -> None:
    """Calling init_db twice must not raise."""
    db.init_db()  # second call


# ------------------------------------------------------------------
# Upsert / Get
# ------------------------------------------------------------------


def test_upsert_and_get_session(db: StateDB) -> None:
    db.upsert_session(
        session_id="sess-1",
        session_path="/data/sess-1",
        profile="battery",
        manifest_hash="abc123",
        status="discovered",
        file_count=5,
        total_bytes=1024,
    )
    row = db.get_session("sess-1")
    assert row is not None
    assert row["session_id"] == "sess-1"
    assert row["session_path"] == "/data/sess-1"
    assert row["profile"] == "battery"
    assert row["manifest_hash"] == "abc123"
    assert row["status"] == "discovered"
    assert row["file_count"] == 5
    assert row["total_bytes"] == 1024
    assert row["retry_count"] == 0
    assert row["created_at"] is not None
    assert row["updated_at"] is not None


def test_upsert_updates_existing_session(db: StateDB) -> None:
    db.upsert_session("s1", "/p", "prof", "h1", "discovered", 1, 100)
    db.upsert_session("s1", "/p2", "prof2", "h2", "uploading", 2, 200)
    row = db.get_session("s1")
    assert row is not None
    assert row["session_path"] == "/p2"
    assert row["manifest_hash"] == "h2"
    assert row["status"] == "uploading"
    assert row["file_count"] == 2
    assert row["total_bytes"] == 200


def test_get_session_returns_none_for_missing(db: StateDB) -> None:
    assert db.get_session("nonexistent") is None


# ------------------------------------------------------------------
# Status transitions
# ------------------------------------------------------------------


def test_update_session_status(db: StateDB) -> None:
    db.upsert_session("s1", "/p", "prof", "h", "discovered", 1, 100)
    db.update_session_status("s1", "uploading")
    row = db.get_session("s1")
    assert row is not None
    assert row["status"] == "uploading"
    assert row["uploaded_at"] is None


def test_update_session_status_to_uploaded_sets_uploaded_at(db: StateDB) -> None:
    db.upsert_session("s1", "/p", "prof", "h", "uploading", 1, 100)
    db.update_session_status("s1", "uploaded")
    row = db.get_session("s1")
    assert row is not None
    assert row["status"] == "uploaded"
    assert row["uploaded_at"] is not None


def test_update_session_status_with_error(db: StateDB) -> None:
    db.upsert_session("s1", "/p", "prof", "h", "uploading", 1, 100)
    db.update_session_status("s1", "failed", error="timeout")
    row = db.get_session("s1")
    assert row is not None
    assert row["status"] == "failed"
    assert row["last_error"] == "timeout"


# ------------------------------------------------------------------
# Deduplication
# ------------------------------------------------------------------


def test_is_duplicate_true_when_uploaded(db: StateDB) -> None:
    db.upsert_session("s1", "/p", "prof", "h1", "uploaded", 1, 100)
    assert db.is_duplicate("s1", "h1") is True


def test_is_duplicate_false_when_not_uploaded(db: StateDB) -> None:
    db.upsert_session("s1", "/p", "prof", "h1", "discovered", 1, 100)
    assert db.is_duplicate("s1", "h1") is False


def test_is_duplicate_false_when_hash_differs(db: StateDB) -> None:
    db.upsert_session("s1", "/p", "prof", "h1", "uploaded", 1, 100)
    assert db.is_duplicate("s1", "different_hash") is False


def test_is_duplicate_false_when_session_missing(db: StateDB) -> None:
    assert db.is_duplicate("missing", "h1") is False


# ------------------------------------------------------------------
# File upload tracking
# ------------------------------------------------------------------


def test_record_file_upload(db: StateDB) -> None:
    db.record_file_upload(
        session_id="s1",
        manifest_hash="h1",
        relative_path="data/file.csv",
        sha256="deadbeef",
        size=512,
        status="uploaded",
    )
    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM uploaded_files").fetchone()
    conn.close()
    assert row is not None
    assert row["session_id"] == "s1"
    assert row["relative_path"] == "data/file.csv"
    assert row["sha256"] == "deadbeef"
    assert row["size"] == 512
    assert row["upload_status"] == "uploaded"


# ------------------------------------------------------------------
# Pending / Failed queries
# ------------------------------------------------------------------


def test_get_pending_sessions(db: StateDB) -> None:
    db.upsert_session("s1", "/p", "prof", "h", "discovered", 1, 100)
    db.upsert_session("s2", "/p", "prof", "h", "waiting_for_stable", 1, 100)
    db.upsert_session("s3", "/p", "prof", "h", "ready_to_register", 1, 100)
    db.upsert_session("s4", "/p", "prof", "h", "failed", 1, 100)
    db.upsert_session("s5", "/p", "prof", "h", "uploaded", 1, 100)
    db.upsert_session("s6", "/p", "prof", "h", "uploading", 1, 100)

    pending = db.get_pending_sessions()
    pending_ids = {r["session_id"] for r in pending}
    assert pending_ids == {"s1", "s2", "s3", "s4"}


def test_get_failed_sessions(db: StateDB) -> None:
    db.upsert_session("s1", "/p", "prof", "h", "failed", 1, 100)
    db.upsert_session("s2", "/p", "prof", "h", "uploaded", 1, 100)
    db.upsert_session("s3", "/p", "prof", "h", "failed", 1, 100)

    failed = db.get_failed_sessions()
    failed_ids = {r["session_id"] for r in failed}
    assert failed_ids == {"s1", "s3"}


# ------------------------------------------------------------------
# Retry count
# ------------------------------------------------------------------


def test_increment_retry_count(db: StateDB) -> None:
    db.upsert_session("s1", "/p", "prof", "h", "failed", 1, 100)
    assert db.get_session("s1")["retry_count"] == 0

    db.increment_retry_count("s1")
    assert db.get_session("s1")["retry_count"] == 1

    db.increment_retry_count("s1")
    assert db.get_session("s1")["retry_count"] == 2


def test_increment_retry_updates_timestamp(db: StateDB) -> None:
    db.upsert_session("s1", "/p", "prof", "h", "failed", 1, 100)
    ts_before = db.get_session("s1")["updated_at"]
    db.increment_retry_count("s1")
    ts_after = db.get_session("s1")["updated_at"]
    assert ts_after >= ts_before
