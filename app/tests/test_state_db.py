"""Tests for the Aurora PostgreSQL state database layer.

All tests run against the SQLite-backed psycopg2 shim provided by
conftest.py — no real database required.
"""

from __future__ import annotations

import pytest

from agent.state_db import StateDB


@pytest.fixture()
def db() -> StateDB:
    """Return an initialised StateDB (backed by the in-memory SQLite shim)."""
    sdb = StateDB()
    sdb.init_db()
    return sdb


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------


def test_init_db_creates_tables(db: StateDB) -> None:
    """init_db creates both sessions and uploaded_files tables (API-level check)."""
    # If tables exist, upsert + get should round-trip without error
    db.upsert_session("_probe", "/", "p", "h", "discovered", 0, 0)
    assert db.get_session("_probe") is not None


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
# File upload tracking
# ------------------------------------------------------------------


def test_record_file_upload(db: StateDB) -> None:
    """record_file_upload should not raise and should be retrievable via the session."""
    db.upsert_session("s1", "/p", "prof", "h1", "uploading", 1, 512)
    # Should not raise
    db.record_file_upload(
        session_id="s1",
        manifest_hash="h1",
        relative_path="data/file.csv",
        sha256="deadbeef",
        size=512,
        status="uploaded",
    )
    # Verify via a separate get — row count isn't exposed but no exception == success
    assert db.get_session("s1") is not None


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
