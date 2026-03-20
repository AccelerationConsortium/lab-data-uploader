"""Integration tests for the full upload pipeline.

These tests simulate the scheduler processing sessions end-to-end
with mocked S3 and Step Functions.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent.models import AppConfig
from agent.scheduler import UploadScheduler


@pytest.fixture()
def session_root(tmp_path):
    """Create a session root with a complete session folder."""
    root = tmp_path / "sessions"
    root.mkdir()
    session = root / "session_001"
    session.mkdir()

    (session / "data.csv").write_text("a,b,c\n1,2,3\n")
    (session / "log.txt").write_text("experiment completed\n")

    meta = {"session_id": "SES-001", "experiment": "test"}
    (session / "metadata.json").write_text(json.dumps(meta))
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
            "stable_window_seconds": 0,
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
            "s3_bucket": "test-bucket",
            "s3_region": "us-east-1",
            "s3_prefix": "lab",
            "max_retries": 3,
            "initial_backoff_seconds": 1,
        },
        storage={
            "manifest_cache_dir": str(tmp_path / "manifests"),
            "log_dir": str(tmp_path / "logs"),
        },
    )


@pytest.fixture()
def mock_s3():
    with patch("agent.uploader.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.put_object.return_value = {}
        yield mock_client


# --------------------------------------------------------------------------
# Test: Successful end-to-end upload
# --------------------------------------------------------------------------


def test_full_upload_pipeline(app_config, session_root, mock_s3):
    """Simulate a complete session upload: scan -> detect -> manifest -> upload to S3."""
    from agent.logging_utils import setup_logging

    setup_logging(app_config.storage.log_dir)

    scheduler = UploadScheduler(app_config)

    # First scan: detector sees session for the first time -> not_stable
    scheduler.run_once()

    # Second scan: with stable_window=0, session should now be stable
    scheduler.run_once()

    # Verify S3 uploads happened (4 data files + manifest.json + _COMPLETE)
    assert mock_s3.put_object.call_count == 6

    # Verify S3 keys include the prefix
    keys = [call.kwargs["Key"] for call in mock_s3.put_object.call_args_list]
    assert all(k.startswith("lab/SES-001/") for k in keys)
    assert "lab/SES-001/manifest.json" in keys
    assert "lab/SES-001/_COMPLETE" in keys

    # Verify DB state
    session_row = scheduler._db.get_session("SES-001")
    assert session_row is not None
    assert session_row["status"] == "uploaded"
    assert session_row["uploaded_at"] is not None

    scheduler.close()


# --------------------------------------------------------------------------
# Test: Duplicate upload is skipped
# --------------------------------------------------------------------------


def test_duplicate_upload_skipped(app_config, session_root, mock_s3):
    """After a successful upload the session folder is moved to processed/.
    A third scan finds no sessions in the root and makes no new S3 calls."""
    from agent.logging_utils import setup_logging

    setup_logging(app_config.storage.log_dir)

    scheduler = UploadScheduler(app_config)

    # Two scans to complete first upload
    scheduler.run_once()
    scheduler.run_once()
    first_put_count = mock_s3.put_object.call_count

    # Third scan: folder has been moved, nothing to upload
    scheduler.run_once()
    assert mock_s3.put_object.call_count == first_put_count  # no new uploads

    scheduler.close()


# --------------------------------------------------------------------------
# Test: Failed upload then retry
# --------------------------------------------------------------------------


def test_failed_upload_then_retry(app_config, session_root, mock_s3):
    """When upload fails, session is marked failed and retried on next cycle."""
    from botocore.exceptions import ClientError

    from agent.logging_utils import setup_logging

    setup_logging(app_config.storage.log_dir)

    # First: S3 uploads fail
    error_response = {"Error": {"Code": "500", "Message": "Internal Error"}}
    mock_s3.put_object.side_effect = ClientError(error_response, "PutObject")

    scheduler = UploadScheduler(app_config)

    scheduler.run_once()
    scheduler.run_once()

    session_row = scheduler._db.get_session("SES-001")
    assert session_row["status"] == "failed"
    assert session_row["retry_count"] >= 1

    # Now make S3 succeed for retry
    mock_s3.put_object.side_effect = None
    mock_s3.put_object.return_value = {}

    scheduler.run_once()

    session_row = scheduler._db.get_session("SES-001")
    assert session_row["status"] == "uploaded"

    scheduler.close()


# --------------------------------------------------------------------------
# Test: Session content change triggers re-upload
# --------------------------------------------------------------------------


def test_second_independent_session_uploaded(app_config, session_root, mock_s3):
    """A second session appearing after the first is uploaded independently."""
    from agent.logging_utils import setup_logging

    setup_logging(app_config.storage.log_dir)

    scheduler = UploadScheduler(app_config)

    # Upload first session
    scheduler.run_once()
    scheduler.run_once()
    first_put_count = mock_s3.put_object.call_count

    # A completely new session folder with a different session_id appears
    session2 = session_root / "session_002"
    session2.mkdir()
    (session2 / "data.csv").write_text("x,y\n10,20\n")
    (session2 / "metadata.json").write_text('{"session_id": "SES-002"}')
    (session2 / "session_summary.json").write_text('{"status":"done"}')

    # Two scans: detect + upload
    scheduler.run_once()
    scheduler.run_once()

    assert mock_s3.put_object.call_count > first_put_count

    row = scheduler._db.get_session("SES-002")
    assert row is not None
    assert row["status"] == "uploaded"

    scheduler.close()


# --------------------------------------------------------------------------
# Test: Empty root folder
# --------------------------------------------------------------------------


def test_empty_root_folder(app_config, tmp_path, mock_s3):
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


def test_temp_files_ignored(app_config, session_root, mock_s3):
    """Files matching ignore_patterns (*.tmp) should not appear in manifest."""
    session_dir = session_root / "session_001"
    (session_dir / "temp_data.tmp").write_text("temporary data")

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


# --------------------------------------------------------------------------
# Test: _COMPLETE marker uploaded as last S3 key
# --------------------------------------------------------------------------


def test_complete_marker_uploaded(app_config, session_root, mock_s3):
    """The _COMPLETE marker is written to S3 after all files and manifest.json."""
    from agent.logging_utils import setup_logging

    setup_logging(app_config.storage.log_dir)

    scheduler = UploadScheduler(app_config)
    scheduler.run_once()
    scheduler.run_once()

    keys = [call.kwargs["Key"] for call in mock_s3.put_object.call_args_list]

    # _COMPLETE must be present
    assert "lab/SES-001/_COMPLETE" in keys

    # _COMPLETE must be the very last upload
    assert keys[-1] == "lab/SES-001/_COMPLETE"

    # manifest.json must come before _COMPLETE
    manifest_idx = keys.index("lab/SES-001/manifest.json")
    complete_idx = keys.index("lab/SES-001/_COMPLETE")
    assert manifest_idx < complete_idx

    scheduler.close()
