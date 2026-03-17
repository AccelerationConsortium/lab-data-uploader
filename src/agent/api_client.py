"""Backend API client for upload registration and completion."""

from __future__ import annotations

import httpx
import structlog

from agent.models import CompleteResponse, RegisterResponse, UploadConfig

logger = structlog.get_logger("api_client")


class UploadAPIClient:
    """HTTP client for the backend upload API.

    Supports use as a context manager for automatic resource cleanup.
    """

    def __init__(self, config: UploadConfig) -> None:
        self._base_url = config.api_base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=config.request_timeout_seconds,
        )

    # -- Context manager --------------------------------------------------

    def __enter__(self) -> UploadAPIClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    # -- API methods ------------------------------------------------------

    def register_session(
        self,
        session_id: str,
        machine_id: str,
        lab_id: str,
        manifest_hash: str,
        file_count: int,
        total_bytes: int,
        schema_version: str,
        files: list[str] | None = None,
    ) -> RegisterResponse:
        """Register a candidate session upload with the backend.

        Returns a RegisterResponse indicating whether upload is required,
        the session is a duplicate, or an updated version is needed.
        """
        url = f"{self._base_url}/register-session"
        payload: dict = {
            "session_id": session_id,
            "machine_id": machine_id,
            "lab_id": lab_id,
            "manifest_hash": manifest_hash,
            "file_count": file_count,
            "total_bytes": total_bytes,
            "schema_version": schema_version,
        }
        if files is not None:
            payload["files"] = files

        logger.info(
            "register_started",
            session_id=session_id,
            manifest_hash=manifest_hash,
        )

        try:
            response = self._client.post(url, json=payload)
            response.raise_for_status()
        except httpx.TimeoutException:
            logger.error(
                "register_timeout",
                session_id=session_id,
                url=url,
            )
            raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                "register_http_error",
                session_id=session_id,
                status_code=exc.response.status_code,
                url=url,
            )
            raise
        except httpx.ConnectError:
            logger.error(
                "register_connection_error",
                session_id=session_id,
                url=url,
            )
            raise

        return RegisterResponse.model_validate(response.json())

    def complete_session(
        self,
        session_id: str,
        manifest_hash: str,
        uploaded_files: list[str],
        total_bytes: int,
    ) -> CompleteResponse:
        """Notify the backend that all files for a session have been uploaded."""
        url = f"{self._base_url}/complete-session"
        payload = {
            "session_id": session_id,
            "manifest_hash": manifest_hash,
            "uploaded_files": uploaded_files,
            "total_bytes": total_bytes,
        }

        logger.info(
            "complete_started",
            session_id=session_id,
            manifest_hash=manifest_hash,
            file_count=len(uploaded_files),
        )

        try:
            response = self._client.post(url, json=payload)
            response.raise_for_status()
        except httpx.TimeoutException:
            logger.error(
                "complete_timeout",
                session_id=session_id,
                url=url,
            )
            raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                "complete_http_error",
                session_id=session_id,
                status_code=exc.response.status_code,
                url=url,
            )
            raise
        except httpx.ConnectError:
            logger.error(
                "complete_connection_error",
                session_id=session_id,
                url=url,
            )
            raise

        return CompleteResponse.model_validate(response.json())
