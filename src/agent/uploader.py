"""Presigned URL file upload logic."""

from __future__ import annotations

import os

import httpx
import structlog

from agent.models import SessionManifest, UploadConfig, UploadResult
from agent.retry import with_retry

logger = structlog.get_logger("uploader")


class FileUploader:
    """Uploads session files to S3 using backend-provided presigned URLs."""

    def __init__(self, config: UploadConfig) -> None:
        self._config = config

    def upload_session(
        self,
        session_path: str,
        presigned_urls: dict[str, str],
        manifest: SessionManifest,
    ) -> UploadResult:
        """Upload all files in a session manifest.

        Args:
            session_path: Absolute path to the session directory.
            presigned_urls: Mapping of relative_path to presigned S3 URL.
            manifest: The session manifest describing files to upload.

        Returns:
            UploadResult with per-file success/failure tracking.
        """
        uploaded: list[str] = []
        failed: list[str] = []
        total_bytes = 0

        for file_entry in manifest.files:
            presigned_url = presigned_urls.get(file_entry.relative_path)
            if presigned_url is None:
                logger.error(
                    "upload_file_missing_url",
                    session_id=manifest.session_id,
                    relative_path=file_entry.relative_path,
                )
                failed.append(file_entry.relative_path)
                continue

            file_path = os.path.join(session_path, file_entry.relative_path)
            ok = self.upload_file(file_path, presigned_url)

            if ok:
                uploaded.append(file_entry.relative_path)
                total_bytes += file_entry.size
            else:
                failed.append(file_entry.relative_path)

        success = len(failed) == 0

        return UploadResult(
            success=success,
            uploaded_files=uploaded,
            failed_files=failed,
            total_bytes_uploaded=total_bytes,
            error=f"{len(failed)} file(s) failed to upload" if failed else None,
        )

    def upload_file(self, file_path: str, presigned_url: str) -> bool:
        """Upload a single file to a presigned URL with retry.

        Args:
            file_path: Absolute path to the local file.
            presigned_url: Presigned S3 URL for PUT upload.

        Returns:
            True on success, False on failure.
        """
        try:
            self._upload_with_retry(file_path, presigned_url)
            logger.info(
                "upload_file_succeeded",
                file_path=file_path,
            )
            return True
        except Exception as exc:
            logger.error(
                "upload_file_failed",
                file_path=file_path,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False

    @with_retry(max_retries=3, initial_backoff=1.0)
    def _upload_with_retry(self, file_path: str, presigned_url: str) -> None:
        """PUT file content to presigned URL. Retried on transient errors."""
        with open(file_path, "rb") as f:
            data = f.read()

        response = httpx.put(
            presigned_url,
            content=data,
            timeout=self._config.request_timeout_seconds,
        )
        response.raise_for_status()
