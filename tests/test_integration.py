"""Integration tests for the full upload pipeline.

These tests simulate the scheduler processing sessions end-to-end
with a mocked backend API.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from agent.models import AppConfig
from agent.scheduler import UploadScheduler


@pytest.fixture()
def session_root(tmp_path):
    """Create a session root with a complete session folder."""
    root = tmp_path / "sessions"
    root.mkdir()
    session = root / "session_001"
    session.mkdir()

    # Write data files
    (session / "data.csv").write_text("a,b,c\n1,2,3\n")
    (session / "log.txt").write_text("experiment completed\n")

    # Write metadata
    meta = {"session_id": "SES-001", "experiment": "test"}
    (session / "metadata.json").write_text(json.dumps(meta))

    # Write required marker
    (session / "session_summary.json").write_text('{"status":"done"}')

    return root


@pytest.fixture()
def app_config(tmp_path, session_root):
    """Create a valid AppConfig pointing to tmp dirs."""
    return AppConfig(
        agent={
            "machine_id": "test-pc",
            "lab_id": "test-lab",
            "scan_interval_seconds": 1,
            "stable_window_seconds": 0,  # instant stability for testing
        },
        watch={"session_roots": [{"path": str(session_root), "profile": "test_profile"}]},
        profiles={
            "test_profile": {
                "required_markers": ["session_summary.json"],
                "ignore_patterns": ["*.tmp"],
                "metadata_files": ["metadata.json"],
            }
        },
        upload={
            "api_base_url": "https://api.test.com",
            "request_timeout_seconds": 5,
            "max_retries": 3,
            "initial_backoff_seconds": 1,
        },
        storage={
            "local_state_db": str(tmp_path / "state" / "test.db"),
            "manifest_cache_dir": str(tmp_path / "manifests"),
            "log_dir": str(tmp_path / "logs"),
        },
    )


# --------------------------------------------------------------------------
# Test: Successful end-to-end upload
# --------------------------------------------------------------------------


@respx.mock
def test_full_upload_pipeline(app_config, session_root):
    """Simulate a complete session upload cycle: scan -> detect -> manifest -> register -> upload -> complete."""

    # Mock register-session endpoint
    register_route = respx.post("https://api.test.com/register-session").mock(
        return_value=httpx.Response(
            200,
            json={
                "action": "upload_required",
                "upload_id": "upload-001",
                "presigned_urls": {
                    "data.csv": "https://s3.test.com/data.csv?signed=1",
                    "log.txt": "https://s3.test.com/log.txt?signed=1",
                    "metadata.json": "https://s3.test.com/metadata.json?signed=1",
                    "session_summary.json": "https://s3.test.com/session_summary.json?signed=1",
                },
            },
        )
    )

    # Mock S3 presigned upload endpoints
    s3_route = respx.put(url__startswith="https://s3.test.com/").mock(
        return_value=httpx.Response(200)
    )

    # Mock complete-session endpoint
    complete_route = respx.post("https://api.test.com/complete-session").mock(
        return_value=httpx.Response(200, json={"status": "ok", "message": "ingestion started"})
    )

    from agent.logging_utils import setup_logging

    setup_logging(app_config.storage.log_dir)

    scheduler = UploadScheduler(app_config)

    # First scan: detector sees session for the first time -> not_stable
    scheduler.run_once()

    # Second scan: with stable_window=0, session should now be stable
    scheduler.run_once()

    # Verify register was called
    assert register_route.called
    register_body = json.loads(register_route.calls.last.request.content)
    assert register_body["session_id"] == "SES-001"
    assert register_body["machine_id"] == "test-pc"
    assert register_body["lab_id"] == "test-lab"

    # Verify S3 uploads happened (4 files)
    assert s3_route.call_count == 4

    # Verify complete was called
    assert complete_route.called
    complete_body = json.loads(complete_route.calls.last.request.content)
    assert complete_body["session_id"] == "SES-001"
    assert len(complete_body["uploaded_files"]) == 4

    # Verify DB state is 'uploaded'
    session_row = scheduler._db.get_session("SES-001")
    assert session_row is not None
    assert session_row["status"] == "uploaded"
    assert session_row["uploaded_at"] is not None

    scheduler.close()


# --------------------------------------------------------------------------
# Test: Duplicate upload is skipped
# --------------------------------------------------------------------------


@respx.mock
def test_duplicate_upload_skipped(app_config, session_root):
    """After a successful upload, a second scan should detect the duplicate and skip."""

    respx.post("https://api.test.com/register-session").mock(
        return_value=httpx.Response(
            200,
            json={
                "action": "upload_required",
                "upload_id": "upload-001",
                "presigned_urls": {
                    "data.csv": "https://s3.test.com/data.csv?signed=1",
                    "log.txt": "https://s3.test.com/log.txt?signed=1",
                    "metadata.json": "https://s3.test.com/metadata.json?signed=1",
                    "session_summary.json": "https://s3.test.com/session_summary.json?signed=1",
                },
            },
        )
    )
    respx.put(url__startswith="https://s3.test.com/").mock(
        return_value=httpx.Response(200)
    )
    complete_route = respx.post("https://api.test.com/complete-session").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    from agent.logging_utils import setup_logging

    setup_logging(app_config.storage.log_dir)

    scheduler = UploadScheduler(app_config)

    # Two scans to complete first upload
    scheduler.run_once()
    scheduler.run_once()
    assert complete_route.call_count == 1

    # Third scan should detect duplicate and NOT call complete again
    scheduler.run_once()
    assert complete_route.call_count == 1  # still 1, not called again

    session_row = scheduler._db.get_session("SES-001")
    # Local dedup detects same manifest_hash → marks as duplicate (no re-upload)
    assert session_row["status"] in ("uploaded", "duplicate")

    scheduler.close()


# --------------------------------------------------------------------------
# Test: Backend reports duplicate
# --------------------------------------------------------------------------


@respx.mock
def test_backend_duplicate_response(app_config, session_root):
    """When backend says 'duplicate', agent should mark session accordingly."""

    respx.post("https://api.test.com/register-session").mock(
        return_value=httpx.Response(
            200,
            json={"action": "duplicate", "upload_id": "", "presigned_urls": {}},
        )
    )

    from agent.logging_utils import setup_logging

    setup_logging(app_config.storage.log_dir)

    scheduler = UploadScheduler(app_config)

    # Two scans: first for stability, second for registration
    scheduler.run_once()
    scheduler.run_once()

    session_row = scheduler._db.get_session("SES-001")
    assert session_row is not None
    assert session_row["status"] == "duplicate"

    scheduler.close()


# --------------------------------------------------------------------------
# Test: Failed upload then retry
# --------------------------------------------------------------------------


@respx.mock
def test_failed_upload_then_retry(app_config, session_root):
    """When upload fails, session is marked failed and retried on next cycle."""

    call_count = {"register": 0}

    def register_side_effect(request):
        call_count["register"] += 1
        return httpx.Response(
            200,
            json={
                "action": "upload_required",
                "upload_id": f"upload-{call_count['register']:03d}",
                "presigned_urls": {
                    "data.csv": "https://s3.test.com/data.csv?signed=1",
                    "log.txt": "https://s3.test.com/log.txt?signed=1",
                    "metadata.json": "https://s3.test.com/metadata.json?signed=1",
                    "session_summary.json": "https://s3.test.com/session_summary.json?signed=1",
                },
            },
        )

    respx.post("https://api.test.com/register-session").mock(side_effect=register_side_effect)

    # First: S3 uploads fail
    respx.put(url__startswith="https://s3.test.com/").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )

    from agent.logging_utils import setup_logging

    setup_logging(app_config.storage.log_dir)

    scheduler = UploadScheduler(app_config)

    # Scan 1: detect for first time
    scheduler.run_once()
    # Scan 2: stable -> register -> upload fails
    scheduler.run_once()

    session_row = scheduler._db.get_session("SES-001")
    assert session_row["status"] == "failed"
    assert session_row["retry_count"] >= 1

    # Now make S3 succeed for retry
    respx.reset()
    respx.post("https://api.test.com/register-session").mock(side_effect=register_side_effect)
    respx.put(url__startswith="https://s3.test.com/").mock(
        return_value=httpx.Response(200)
    )
    respx.post("https://api.test.com/complete-session").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    # Scan 3: retry should succeed
    scheduler.run_once()

    session_row = scheduler._db.get_session("SES-001")
    assert session_row["status"] == "uploaded"

    scheduler.close()


# --------------------------------------------------------------------------
# Test: Session content change triggers re-upload
# --------------------------------------------------------------------------


@respx.mock
def test_session_change_triggers_new_version(app_config, session_root):
    """If session content changes after upload, new manifest_hash triggers re-upload."""

    respx.post("https://api.test.com/register-session").mock(
        return_value=httpx.Response(
            200,
            json={
                "action": "upload_required",
                "upload_id": "upload-001",
                "presigned_urls": {
                    "data.csv": "https://s3.test.com/data.csv?signed=1",
                    "log.txt": "https://s3.test.com/log.txt?signed=1",
                    "metadata.json": "https://s3.test.com/metadata.json?signed=1",
                    "session_summary.json": "https://s3.test.com/session_summary.json?signed=1",
                },
            },
        )
    )
    respx.put(url__startswith="https://s3.test.com/").mock(
        return_value=httpx.Response(200)
    )
    complete_route = respx.post("https://api.test.com/complete-session").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    from agent.logging_utils import setup_logging

    setup_logging(app_config.storage.log_dir)

    scheduler = UploadScheduler(app_config)

    # Complete first upload
    scheduler.run_once()
    scheduler.run_once()
    assert complete_route.call_count == 1

    first_hash = scheduler._db.get_session("SES-001")["manifest_hash"]

    # Modify session content - add a new file
    session_dir = session_root / "session_001"
    (session_dir / "extra_data.csv").write_text("x,y\n10,20\n")

    # Reset detector snapshots so it re-evaluates stability
    scheduler._detector._snapshots.clear()

    # Update presigned URLs to include new file
    respx.reset()
    respx.post("https://api.test.com/register-session").mock(
        return_value=httpx.Response(
            200,
            json={
                "action": "upload_required",
                "upload_id": "upload-002",
                "presigned_urls": {
                    "data.csv": "https://s3.test.com/data.csv?signed=2",
                    "log.txt": "https://s3.test.com/log.txt?signed=2",
                    "metadata.json": "https://s3.test.com/metadata.json?signed=2",
                    "session_summary.json": "https://s3.test.com/session_summary.json?signed=2",
                    "extra_data.csv": "https://s3.test.com/extra_data.csv?signed=2",
                },
            },
        )
    )
    respx.put(url__startswith="https://s3.test.com/").mock(
        return_value=httpx.Response(200)
    )
    respx.post("https://api.test.com/complete-session").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    # Two scans: first re-detects, second uploads new version
    scheduler.run_once()
    scheduler.run_once()

    session_row = scheduler._db.get_session("SES-001")
    assert session_row["status"] == "uploaded"
    # Manifest hash should have changed
    assert session_row["manifest_hash"] != first_hash

    scheduler.close()


# --------------------------------------------------------------------------
# Test: Empty root folder
# --------------------------------------------------------------------------


def test_empty_root_folder(app_config, tmp_path):
    """Agent handles empty session root gracefully."""
    empty_root = tmp_path / "empty_sessions"
    empty_root.mkdir()

    app_config.watch.session_roots[0].path = str(empty_root)

    from agent.logging_utils import setup_logging

    setup_logging(app_config.storage.log_dir)

    scheduler = UploadScheduler(app_config)
    scheduler.run_once()  # should not raise

    scheduler.close()


# --------------------------------------------------------------------------
# Test: Temp files ignored
# --------------------------------------------------------------------------


@respx.mock
def test_temp_files_ignored(app_config, session_root):
    """Files matching ignore_patterns (*.tmp) should not appear in manifest."""
    session_dir = session_root / "session_001"
    (session_dir / "temp_data.tmp").write_text("temporary data")

    respx.post("https://api.test.com/register-session").mock(
        return_value=httpx.Response(
            200,
            json={
                "action": "upload_required",
                "upload_id": "upload-001",
                "presigned_urls": {
                    "data.csv": "https://s3.test.com/data.csv?signed=1",
                    "log.txt": "https://s3.test.com/log.txt?signed=1",
                    "metadata.json": "https://s3.test.com/metadata.json?signed=1",
                    "session_summary.json": "https://s3.test.com/session_summary.json?signed=1",
                },
            },
        )
    )
    respx.put(url__startswith="https://s3.test.com/").mock(
        return_value=httpx.Response(200)
    )
    respx.post("https://api.test.com/complete-session").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    from agent.logging_utils import setup_logging

    setup_logging(app_config.storage.log_dir)

    scheduler = UploadScheduler(app_config)
    scheduler.run_once()
    scheduler.run_once()

    session_row = scheduler._db.get_session("SES-001")
    assert session_row is not None
    # temp_data.tmp should NOT be counted (4 files, not 5)
    assert session_row["file_count"] == 4

    scheduler.close()
