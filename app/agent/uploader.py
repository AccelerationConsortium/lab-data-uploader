"""Direct S3 file upload using boto3."""

from __future__ import annotations

import json
import os

import boto3
import structlog
from botocore.exceptions import ClientError

from agent.models import SessionManifest, UploadConfig, UploadResult
from agent.retry import with_retry

logger = structlog.get_logger("uploader")


class FileUploader:
    """Uploads session files directly to S3 using boto3 (IAM role auth on ECS)."""

    def __init__(self, config: UploadConfig) -> None:
        self._config = config
        self._bucket = config.s3_bucket
        self._prefix = config.s3_prefix.strip("/")
        self._s3 = boto3.client("s3", region_name=config.s3_region)

    def upload_session(
        self,
        session_path: str,
        manifest: SessionManifest,
    ) -> UploadResult:
        """Upload all files in a session manifest to S3.

        S3 key pattern: {prefix}/{session_id}/{relative_path}

        Args:
            session_path: Absolute path to the session directory.
            manifest: The session manifest describing files to upload.

        Returns:
            UploadResult with per-file success/failure tracking.
        """
        uploaded: list[str] = []
        failed: list[str] = []
        total_bytes = 0

        for file_entry in manifest.files:
            file_path = os.path.join(session_path, file_entry.relative_path)
            s3_key = self._build_key(manifest.session_id, file_entry.relative_path)

            ok = self._upload_file(file_path, s3_key)
            if ok:
                uploaded.append(file_entry.relative_path)
                total_bytes += file_entry.size
            else:
                failed.append(file_entry.relative_path)

        # Upload manifest JSON alongside session files
        if uploaded:
            self._upload_manifest(manifest)

        success = len(failed) == 0

        return UploadResult(
            success=success,
            uploaded_files=uploaded,
            failed_files=failed,
            total_bytes_uploaded=total_bytes,
            error=f"{len(failed)} file(s) failed to upload" if failed else None,
        )

    def _build_key(self, session_id: str, relative_path: str) -> str:
        """Build the S3 object key."""
        if self._prefix:
            return f"{self._prefix}/{session_id}/{relative_path}"
        return f"{session_id}/{relative_path}"

    def _upload_file(self, file_path: str, s3_key: str) -> bool:
        """Upload a single file to S3 with retry."""
        try:
            self._put_object_with_retry(file_path, s3_key)
            logger.info("upload_file_succeeded", file_path=file_path, s3_key=s3_key)
            return True
        except Exception as exc:
            logger.error(
                "upload_file_failed",
                file_path=file_path,
                s3_key=s3_key,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False

    @with_retry(max_retries=3, initial_backoff=1.0)
    def _put_object_with_retry(self, file_path: str, s3_key: str) -> None:
        """PUT file to S3. Retried on transient errors."""
        with open(file_path, "rb") as f:
            self._s3.put_object(Bucket=self._bucket, Key=s3_key, Body=f)

    def _upload_manifest(self, manifest: SessionManifest) -> None:
        """Upload manifest.json alongside the session files."""
        s3_key = self._build_key(manifest.session_id, "manifest.json")
        try:
            body = json.dumps(manifest.model_dump(), indent=2, sort_keys=True)
            self._s3.put_object(Bucket=self._bucket, Key=s3_key, Body=body.encode())
            logger.info("manifest_uploaded", s3_key=s3_key)
        except ClientError as exc:
            logger.warning("manifest_upload_failed", s3_key=s3_key, error=str(exc))
