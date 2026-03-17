"""Tests for the FileUploader (direct S3 upload via boto3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from agent.models import FileEntry, SessionManifest, UploadConfig, UploadResult
from agent.uploader import FileUploader


@pytest.fixture()
def config() -> UploadConfig:
    return UploadConfig(
        s3_bucket="test-bucket",
        s3_region="us-east-1",
        s3_prefix="lab-data",
        max_retries=3,
        initial_backoff_seconds=1,
    )


@pytest.fixture()
def mock_s3():
    with patch("agent.uploader.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        yield mock_client


@pytest.fixture()
def uploader(config: UploadConfig, mock_s3) -> FileUploader:
    return FileUploader(config)


@pytest.fixture()
def sample_file(tmp_path: Path) -> str:
    """Create a temporary file with known content."""
    p = tmp_path / "data" / "file1.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"col1,col2\nval1,val2\n")
    return str(tmp_path)


@pytest.fixture()
def manifest(sample_file: str) -> SessionManifest:
    return SessionManifest(
        session_id="session-001",
        machine_id="labpc-01",
        lab_id="sdl1",
        session_path=sample_file,
        files=[
            FileEntry(
                relative_path="data/file1.csv",
                size=20,
                sha256="abc123",
                modified_time="2024-01-01T00:00:00",
            ),
        ],
        file_count=1,
        total_bytes=20,
    )


# ---------------------------------------------------------------------------
# UploadResult model tests
# ---------------------------------------------------------------------------


class TestUploadResult:
    def test_default_fields(self) -> None:
        result = UploadResult(success=True)
        assert result.success is True
        assert result.uploaded_files == []
        assert result.failed_files == []
        assert result.total_bytes_uploaded == 0
        assert result.error is None

    def test_populated_fields(self) -> None:
        result = UploadResult(
            success=False,
            uploaded_files=["a.txt"],
            failed_files=["b.txt"],
            total_bytes_uploaded=100,
            error="1 file(s) failed to upload",
        )
        assert result.success is False
        assert result.uploaded_files == ["a.txt"]
        assert result.failed_files == ["b.txt"]
        assert result.total_bytes_uploaded == 100
        assert result.error == "1 file(s) failed to upload"


# ---------------------------------------------------------------------------
# S3 key building
# ---------------------------------------------------------------------------


class TestBuildKey:
    def test_key_with_prefix(self, uploader: FileUploader) -> None:
        key = uploader._build_key("ses-001", "data/file.csv")
        assert key == "lab-data/ses-001/data/file.csv"

    def test_key_without_prefix(self, mock_s3) -> None:
        cfg = UploadConfig(s3_bucket="b", s3_region="us-east-1", s3_prefix="")
        up = FileUploader(cfg)
        key = up._build_key("ses-001", "data/file.csv")
        assert key == "ses-001/data/file.csv"


# ---------------------------------------------------------------------------
# upload_session tests
# ---------------------------------------------------------------------------


class TestUploadSessionSuccess:
    def test_all_files_succeed(
        self, uploader: FileUploader, mock_s3, sample_file: str, manifest: SessionManifest
    ) -> None:
        mock_s3.put_object.return_value = {}

        result = uploader.upload_session(sample_file, manifest)

        assert result.success is True
        assert result.uploaded_files == ["data/file1.csv"]
        assert result.failed_files == []
        assert result.total_bytes_uploaded == 20
        assert result.error is None

        # Verify put_object was called (file + manifest)
        assert mock_s3.put_object.call_count == 2

        # Verify S3 key for the data file
        first_call = mock_s3.put_object.call_args_list[0]
        assert first_call.kwargs["Bucket"] == "test-bucket"
        assert first_call.kwargs["Key"] == "lab-data/session-001/data/file1.csv"


class TestUploadSessionFailure:
    def test_tracks_failures(self, mock_s3, tmp_path: Path) -> None:
        session_dir = tmp_path
        (session_dir / "a.txt").write_bytes(b"aaa")
        (session_dir / "b.txt").write_bytes(b"bbb")

        multi_manifest = SessionManifest(
            session_id="session-002",
            machine_id="labpc-01",
            lab_id="sdl1",
            session_path=str(session_dir),
            files=[
                FileEntry(
                    relative_path="a.txt", size=3, sha256="aaa",
                    modified_time="2024-01-01T00:00:00",
                ),
                FileEntry(
                    relative_path="b.txt", size=3, sha256="bbb",
                    modified_time="2024-01-01T00:00:00",
                ),
            ],
            file_count=2,
            total_bytes=6,
        )

        # a.txt succeeds, b.txt fails with ClientError
        error_response = {"Error": {"Code": "500", "Message": "Internal Error"}}

        def put_side_effect(**kwargs):
            key = kwargs.get("Key", "")
            if "b.txt" in key:
                raise ClientError(error_response, "PutObject")
            return {}

        mock_s3.put_object.side_effect = put_side_effect

        cfg = UploadConfig(s3_bucket="b", s3_region="us-east-1", s3_prefix="")
        uploader = FileUploader(cfg)
        result = uploader.upload_session(str(session_dir), multi_manifest)

        assert result.success is False
        assert result.uploaded_files == ["a.txt"]
        assert result.failed_files == ["b.txt"]
        assert result.total_bytes_uploaded == 3
        assert result.error is not None
