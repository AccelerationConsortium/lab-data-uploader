"""Tests for local duplicate detection."""

from __future__ import annotations

import pytest

from agent.dedup import DeduplicationChecker
from agent.state_db import StateDB


@pytest.fixture()
def db() -> StateDB:
    """Return an initialised StateDB (backed by the in-memory SQLite shim)."""
    sdb = StateDB()
    sdb.init_db()
    return sdb


@pytest.fixture()
def checker(db: StateDB) -> DeduplicationChecker:
    return DeduplicationChecker(db)


# ------------------------------------------------------------------
# No existing session
# ------------------------------------------------------------------


def test_no_existing_session_not_duplicate(checker: DeduplicationChecker) -> None:
    """A brand-new session_id should never be considered a duplicate."""
    result = checker.check("new-session", "hash123")
    assert result.is_duplicate is False
    assert result.existing_status is None
    assert result.existing_uploaded_at is None


# ------------------------------------------------------------------
# Same session_id + manifest_hash + status='uploaded' -> duplicate
# ------------------------------------------------------------------


def test_same_hash_uploaded_is_duplicate(
    db: StateDB, checker: DeduplicationChecker
) -> None:
    """Session with matching hash and 'uploaded' status is a duplicate."""
    db.upsert_session("s1", "/data/s1", "prof", "hash-a", "uploaded", 3, 1024)
    db.update_session_status("s1", "uploaded")  # sets uploaded_at

    result = checker.check("s1", "hash-a")
    assert result.is_duplicate is True
    assert result.existing_status == "uploaded"
    assert result.existing_uploaded_at is not None


# ------------------------------------------------------------------
# Same session_id + different manifest_hash -> not duplicate (new version)
# ------------------------------------------------------------------


def test_different_hash_not_duplicate(
    db: StateDB, checker: DeduplicationChecker
) -> None:
    """A changed manifest_hash means a new version, not a duplicate."""
    db.upsert_session("s1", "/data/s1", "prof", "hash-old", "uploaded", 3, 1024)

    result = checker.check("s1", "hash-new")
    assert result.is_duplicate is False
    assert result.existing_status == "uploaded"


# ------------------------------------------------------------------
# Same session_id + manifest_hash + status='failed' -> not duplicate (retry)
# ------------------------------------------------------------------


def test_same_hash_failed_not_duplicate(
    db: StateDB, checker: DeduplicationChecker
) -> None:
    """A previously failed upload should be retried, not skipped."""
    db.upsert_session("s1", "/data/s1", "prof", "hash-a", "failed", 3, 1024)

    result = checker.check("s1", "hash-a")
    assert result.is_duplicate is False
    assert result.existing_status == "failed"


# ------------------------------------------------------------------
# Same session_id + manifest_hash + status='uploading' -> not duplicate
# ------------------------------------------------------------------


def test_same_hash_uploading_not_duplicate(
    db: StateDB, checker: DeduplicationChecker
) -> None:
    """An interrupted upload should be retried, not skipped."""
    db.upsert_session("s1", "/data/s1", "prof", "hash-a", "uploading", 3, 1024)

    result = checker.check("s1", "hash-a")
    assert result.is_duplicate is False
    assert result.existing_status == "uploading"
