"""Tests for the FileUploader and retry logic."""

from __future__ import annotations

import os

import httpx
import pytest
import respx

from agent.models import FileEntry, SessionManifest, UploadConfig, UploadResult
from agent.uploader import FileUploader

PRESIGNED_BASE = "https://s3.example.com"


@pytest.fixture()
def config() -> UploadConfig:
    return UploadConfig(
        api_base_url="https://api.example.com",
        request_timeout_seconds=5,
        max_retries=3,
        initial_backoff_seconds=1,
    )


@pytest.fixture()
def uploader(config: UploadConfig) -> FileUploader:
    return FileUploader(config)


@pytest.fixture()
def sample_file(tmp_path: object) -> str:
    """Create a temporary file with known content."""
    from pathlib import Path

    p = Path(str(tmp_path)) / "data" / "file1.csv"
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
# upload_file tests
# ---------------------------------------------------------------------------


class TestUploadFileSuccess:
    @respx.mock
    def test_upload_file_returns_true(
        self, uploader: FileUploader, sample_file: str
    ) -> None:
        presigned_url = f"{PRESIGNED_BASE}/upload/file1.csv"
        respx.put(presigned_url).mock(return_value=httpx.Response(200))

        file_path = os.path.join(sample_file, "data", "file1.csv")
        result = uploader.upload_file(file_path, presigned_url)

        assert result is True
        assert respx.calls.call_count == 1

        # Verify the file content was sent
        req = respx.calls.last.request
        assert req.content == b"col1,col2\nval1,val2\n"


class TestUploadFileRetryOnTimeout:
    @respx.mock
    def test_retries_on_timeout_then_succeeds(
        self, uploader: FileUploader, sample_file: str
    ) -> None:
        presigned_url = f"{PRESIGNED_BASE}/upload/file1.csv"

        # First call times out, second succeeds
        route = respx.put(presigned_url)
        route.side_effect = [
            httpx.ReadTimeout("Connection timed out"),
            httpx.Response(200),
        ]

        file_path = os.path.join(sample_file, "data", "file1.csv")
        result = uploader.upload_file(file_path, presigned_url)

        assert result is True
        assert respx.calls.call_count == 2

    @respx.mock
    def test_retries_exhausted_returns_false(
        self, uploader: FileUploader, sample_file: str
    ) -> None:
        presigned_url = f"{PRESIGNED_BASE}/upload/file1.csv"

        # All 3 attempts time out
        respx.put(presigned_url).mock(
            side_effect=httpx.ReadTimeout("Connection timed out")
        )

        file_path = os.path.join(sample_file, "data", "file1.csv")
        result = uploader.upload_file(file_path, presigned_url)

        assert result is False
        assert respx.calls.call_count == 3


class TestUploadFileConnectError:
    @respx.mock
    def test_retries_on_connect_error(
        self, uploader: FileUploader, sample_file: str
    ) -> None:
        presigned_url = f"{PRESIGNED_BASE}/upload/file1.csv"

        route = respx.put(presigned_url)
        route.side_effect = [
            httpx.ConnectError("Connection refused"),
            httpx.Response(200),
        ]

        file_path = os.path.join(sample_file, "data", "file1.csv")
        result = uploader.upload_file(file_path, presigned_url)

        assert result is True
        assert respx.calls.call_count == 2


class TestUploadFileHTTPError:
    @respx.mock
    def test_http_error_not_retried(
        self, uploader: FileUploader, sample_file: str
    ) -> None:
        """HTTP status errors (4xx/5xx) are not transient and should not be retried."""
        presigned_url = f"{PRESIGNED_BASE}/upload/file1.csv"
        respx.put(presigned_url).mock(return_value=httpx.Response(403))

        file_path = os.path.join(sample_file, "data", "file1.csv")
        result = uploader.upload_file(file_path, presigned_url)

        assert result is False
        # Should only be called once since HTTPStatusError is not retried
        assert respx.calls.call_count == 1


# ---------------------------------------------------------------------------
# upload_session tests
# ---------------------------------------------------------------------------


class TestUploadSessionAllSuccess:
    @respx.mock
    def test_all_files_succeed(
        self, uploader: FileUploader, sample_file: str, manifest: SessionManifest
    ) -> None:
        presigned_urls = {
            "data/file1.csv": f"{PRESIGNED_BASE}/upload/file1.csv",
        }
        respx.put(f"{PRESIGNED_BASE}/upload/file1.csv").mock(
            return_value=httpx.Response(200)
        )

        result = uploader.upload_session(sample_file, presigned_urls, manifest)

        assert result.success is True
        assert result.uploaded_files == ["data/file1.csv"]
        assert result.failed_files == []
        assert result.total_bytes_uploaded == 20
        assert result.error is None


class TestUploadSessionWithFailures:
    @respx.mock
    def test_tracks_failures(self, uploader: FileUploader, tmp_path: object) -> None:
        from pathlib import Path

        session_dir = Path(str(tmp_path))
        (session_dir / "a.txt").write_bytes(b"aaa")
        (session_dir / "b.txt").write_bytes(b"bbb")

        multi_manifest = SessionManifest(
            session_id="session-002",
            machine_id="labpc-01",
            lab_id="sdl1",
            session_path=str(session_dir),
            files=[
                FileEntry(
                    relative_path="a.txt",
                    size=3,
                    sha256="aaa",
                    modified_time="2024-01-01T00:00:00",
                ),
                FileEntry(
                    relative_path="b.txt",
                    size=3,
                    sha256="bbb",
                    modified_time="2024-01-01T00:00:00",
                ),
            ],
            file_count=2,
            total_bytes=6,
        )

        presigned_urls = {
            "a.txt": f"{PRESIGNED_BASE}/upload/a.txt",
            "b.txt": f"{PRESIGNED_BASE}/upload/b.txt",
        }

        # a.txt succeeds, b.txt fails
        respx.put(f"{PRESIGNED_BASE}/upload/a.txt").mock(
            return_value=httpx.Response(200)
        )
        respx.put(f"{PRESIGNED_BASE}/upload/b.txt").mock(
            return_value=httpx.Response(500)
        )

        result = uploader.upload_session(
            str(session_dir), presigned_urls, multi_manifest
        )

        assert result.success is False
        assert result.uploaded_files == ["a.txt"]
        assert result.failed_files == ["b.txt"]
        assert result.total_bytes_uploaded == 3
        assert result.error is not None


class TestUploadSessionMissingURL:
    @respx.mock
    def test_missing_presigned_url_counts_as_failure(
        self, uploader: FileUploader, sample_file: str, manifest: SessionManifest
    ) -> None:
        # No presigned URL provided for the file
        presigned_urls: dict[str, str] = {}

        result = uploader.upload_session(sample_file, presigned_urls, manifest)

        assert result.success is False
        assert result.failed_files == ["data/file1.csv"]
        assert result.uploaded_files == []
