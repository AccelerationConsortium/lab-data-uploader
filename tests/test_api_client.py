"""Tests for the UploadAPIClient backend interactions."""

from __future__ import annotations

import httpx
import pytest
import respx

from agent.api_client import UploadAPIClient
from agent.models import UploadConfig

BASE_URL = "https://api.example.com"


@pytest.fixture()
def config() -> UploadConfig:
    return UploadConfig(
        api_base_url=BASE_URL,
        request_timeout_seconds=5,
    )


@pytest.fixture()
def client(config: UploadConfig) -> UploadAPIClient:
    c = UploadAPIClient(config)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Register session tests
# ---------------------------------------------------------------------------


class TestRegisterSessionUploadRequired:
    @respx.mock
    def test_returns_upload_required(self, client: UploadAPIClient) -> None:
        respx.post(f"{BASE_URL}/register-session").mock(
            return_value=httpx.Response(
                200,
                json={
                    "action": "upload_required",
                    "presigned_urls": {
                        "data/file1.csv": "https://s3.example.com/presigned1",
                        "data/file2.csv": "https://s3.example.com/presigned2",
                    },
                    "upload_id": "upload-abc-123",
                },
            )
        )

        result = client.register_session(
            session_id="session-001",
            machine_id="labpc-01",
            lab_id="sdl1",
            manifest_hash="abc123",
            file_count=2,
            total_bytes=1024,
            schema_version="1.0",
        )

        assert result.action == "upload_required"
        assert len(result.presigned_urls) == 2
        assert result.upload_id == "upload-abc-123"

        # Verify request was sent
        assert respx.calls.last.request is not None


class TestRegisterSessionDuplicate:
    @respx.mock
    def test_returns_duplicate(self, client: UploadAPIClient) -> None:
        respx.post(f"{BASE_URL}/register-session").mock(
            return_value=httpx.Response(
                200,
                json={
                    "action": "duplicate",
                    "presigned_urls": {},
                    "upload_id": "",
                },
            )
        )

        result = client.register_session(
            session_id="session-001",
            machine_id="labpc-01",
            lab_id="sdl1",
            manifest_hash="abc123",
            file_count=2,
            total_bytes=1024,
            schema_version="1.0",
        )

        assert result.action == "duplicate"
        assert result.presigned_urls == {}
        assert result.upload_id == ""


# ---------------------------------------------------------------------------
# Complete session tests
# ---------------------------------------------------------------------------


class TestCompleteSession:
    @respx.mock
    def test_complete_success(self, client: UploadAPIClient) -> None:
        respx.post(f"{BASE_URL}/complete-session").mock(
            return_value=httpx.Response(
                200,
                json={"status": "ok", "message": "Upload complete"},
            )
        )

        result = client.complete_session(
            session_id="session-001",
            manifest_hash="abc123",
            uploaded_files=["data/file1.csv", "data/file2.csv"],
            total_bytes=1024,
        )

        assert result.status == "ok"
        assert result.message == "Upload complete"

        # Verify request payload
        req = respx.calls.last.request
        import json

        body = json.loads(req.content)
        assert body["session_id"] == "session-001"
        assert body["uploaded_files"] == ["data/file1.csv", "data/file2.csv"]
        assert body["total_bytes"] == 1024


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestTimeoutHandling:
    @respx.mock
    def test_register_timeout_raises(self, client: UploadAPIClient) -> None:
        respx.post(f"{BASE_URL}/register-session").mock(
            side_effect=httpx.ReadTimeout("Connection timed out")
        )

        with pytest.raises(httpx.TimeoutException):
            client.register_session(
                session_id="session-001",
                machine_id="labpc-01",
                lab_id="sdl1",
                manifest_hash="abc123",
                file_count=2,
                total_bytes=1024,
                schema_version="1.0",
            )

    @respx.mock
    def test_complete_timeout_raises(self, client: UploadAPIClient) -> None:
        respx.post(f"{BASE_URL}/complete-session").mock(
            side_effect=httpx.ReadTimeout("Connection timed out")
        )

        with pytest.raises(httpx.TimeoutException):
            client.complete_session(
                session_id="session-001",
                manifest_hash="abc123",
                uploaded_files=["data/file1.csv"],
                total_bytes=512,
            )


class TestHTTPErrorHandling:
    @respx.mock
    def test_register_4xx_raises(self, client: UploadAPIClient) -> None:
        respx.post(f"{BASE_URL}/register-session").mock(
            return_value=httpx.Response(400, json={"error": "bad request"})
        )

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.register_session(
                session_id="session-001",
                machine_id="labpc-01",
                lab_id="sdl1",
                manifest_hash="abc123",
                file_count=2,
                total_bytes=1024,
                schema_version="1.0",
            )

        assert exc_info.value.response.status_code == 400

    @respx.mock
    def test_register_5xx_raises(self, client: UploadAPIClient) -> None:
        respx.post(f"{BASE_URL}/register-session").mock(
            return_value=httpx.Response(500, json={"error": "internal server error"})
        )

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.register_session(
                session_id="session-001",
                machine_id="labpc-01",
                lab_id="sdl1",
                manifest_hash="abc123",
                file_count=2,
                total_bytes=1024,
                schema_version="1.0",
            )

        assert exc_info.value.response.status_code == 500

    @respx.mock
    def test_complete_5xx_raises(self, client: UploadAPIClient) -> None:
        respx.post(f"{BASE_URL}/complete-session").mock(
            return_value=httpx.Response(502, json={"error": "bad gateway"})
        )

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.complete_session(
                session_id="session-001",
                manifest_hash="abc123",
                uploaded_files=["data/file1.csv"],
                total_bytes=512,
            )

        assert exc_info.value.response.status_code == 502


# ---------------------------------------------------------------------------
# Context manager tests
# ---------------------------------------------------------------------------


class TestContextManager:
    @respx.mock
    def test_works_as_context_manager(
        self, config: UploadConfig,
    ) -> None:
        respx.post(f"{BASE_URL}/register-session").mock(
            return_value=httpx.Response(
                200,
                json={"action": "duplicate", "presigned_urls": {}, "upload_id": ""},
            )
        )

        with UploadAPIClient(config) as api:
            result = api.register_session(
                session_id="session-001",
                machine_id="labpc-01",
                lab_id="sdl1",
                manifest_hash="abc123",
                file_count=2,
                total_bytes=1024,
                schema_version="1.0",
            )
            assert result.action == "duplicate"
