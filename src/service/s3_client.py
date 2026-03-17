"""S3 presigned URL generation using boto3."""

from __future__ import annotations

import boto3
from botocore.config import Config


class S3PresignedClient:
    """Generate presigned PUT URLs for uploading files to S3."""

    def __init__(self, bucket: str, region: str, prefix: str = "") -> None:
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._s3 = boto3.client(
            "s3",
            region_name=region,
            config=Config(signature_version="s3v4"),
        )

    def generate_presigned_urls(
        self,
        session_id: str,
        file_paths: list[str],
        expires_in: int = 3600,
    ) -> dict[str, str]:
        """Generate presigned PUT URLs for a list of file paths.

        Args:
            session_id: Session identifier, used as the S3 folder prefix.
            file_paths: List of relative file paths within the session.
            expires_in: URL expiration time in seconds (default 1 hour).

        Returns:
            Mapping of relative_path -> presigned PUT URL.
        """
        urls: dict[str, str] = {}
        for relative_path in file_paths:
            if self._prefix:
                key = f"{self._prefix}/{session_id}/{relative_path}"
            else:
                key = f"{session_id}/{relative_path}"

            url = self._s3.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                },
                ExpiresIn=expires_in,
            )
            urls[relative_path] = url

        return urls
