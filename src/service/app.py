"""FastAPI upload service — handles session registration, presigned URL generation, and completion.

Usage:
    cd /path/to/upload-agent
    PYTHONPATH=src uvicorn service.app:app --reload --port 8000

Environment variables:
    UPLOAD_SERVICE_TOKEN  — Bearer token the agent must send (default: "dev-token")
    S3_BUCKET             — S3 bucket name (default: "battery-etl-dev-data")
    S3_REGION             — AWS region (default: "ca-central-1")
    S3_PREFIX             — Optional key prefix inside the bucket (default: "")
    PRESIGN_EXPIRES       — Presigned URL expiry in seconds (default: 3600)
    SERVICE_DB_PATH       — SQLite path for session tracking (default: "./service_state/sessions.db")
"""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request

from service.models import (
    CompleteSessionRequest,
    CompleteSessionResponse,
    RegisterSessionRequest,
    RegisterSessionResponse,
)
from service.s3_client import S3PresignedClient
from service.store import SessionStore

logger = logging.getLogger("upload-service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(
    title="Lab Data Upload Service",
    description="Backend API for the lab-data-uploader agent. Manages session registration, S3 presigned URLs, and upload completion.",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_store: SessionStore | None = None
_s3: S3PresignedClient | None = None


def get_store() -> SessionStore:
    global _store
    if _store is None:
        db_path = os.environ.get("SERVICE_DB_PATH", "./service_state/sessions.db")
        _store = SessionStore(db_path=db_path)
    return _store


def get_s3() -> S3PresignedClient:
    global _s3
    if _s3 is None:
        bucket = os.environ.get("S3_BUCKET", "battery-etl-dev-data")
        region = os.environ.get("S3_REGION", "ca-central-1")
        prefix = os.environ.get("S3_PREFIX", "")
        _s3 = S3PresignedClient(bucket=bucket, region=region, prefix=prefix)
    return _s3


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

EXPECTED_TOKEN = os.environ.get("UPLOAD_SERVICE_TOKEN", "dev-token")


async def verify_token(request: Request) -> None:
    """Simple Bearer token verification."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth.removeprefix("Bearer ").strip()
    if token != EXPECTED_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post(
    "/register-session",
    response_model=RegisterSessionResponse,
    dependencies=[Depends(verify_token)],
)
async def register_session(
    body: RegisterSessionRequest,
    store: SessionStore = Depends(get_store),
    s3: S3PresignedClient = Depends(get_s3),
) -> RegisterSessionResponse:
    """Register a candidate session upload.

    - If the same session_id + manifest_hash already exists and is completed,
      returns action="duplicate".
    - Otherwise generates presigned PUT URLs for each file and returns
      action="upload_required".

    The agent sends a ``files`` list (relative paths from the manifest) so
    that the service can generate presigned PUT URLs in a single round trip.
    """
    # Check for duplicate
    existing = store.find_session(body.session_id, body.manifest_hash)
    if existing and existing["status"] == "completed":
        logger.info("Duplicate session: %s / %s", body.session_id, body.manifest_hash)
        return RegisterSessionResponse(action="duplicate")

    upload_id = str(uuid.uuid4())

    # Generate presigned PUT URLs for each file
    presigned_urls: dict[str, str] = {}
    if body.files:
        expires = int(os.environ.get("PRESIGN_EXPIRES", "3600"))
        presigned_urls = s3.generate_presigned_urls(
            session_id=body.session_id,
            file_paths=body.files,
            expires_in=expires,
        )

    # Persist registration
    store.register_session(
        session_id=body.session_id,
        manifest_hash=body.manifest_hash,
        machine_id=body.machine_id,
        lab_id=body.lab_id,
        file_count=body.file_count,
        total_bytes=body.total_bytes,
        upload_id=upload_id,
    )

    logger.info(
        "Session registered: %s / %s (upload_id=%s, files=%d, bytes=%d)",
        body.session_id,
        body.manifest_hash,
        upload_id,
        body.file_count,
        body.total_bytes,
    )

    return RegisterSessionResponse(
        action="upload_required",
        upload_id=upload_id,
        presigned_urls=presigned_urls,
    )


@app.post(
    "/complete-session",
    response_model=CompleteSessionResponse,
    dependencies=[Depends(verify_token)],
)
async def complete_session(
    body: CompleteSessionRequest,
    store: SessionStore = Depends(get_store),
) -> CompleteSessionResponse:
    """Mark a session upload as completed."""
    ok = store.complete_session(body.session_id, body.manifest_hash)

    if not ok:
        logger.warning(
            "Complete called for unknown session: %s / %s",
            body.session_id,
            body.manifest_hash,
        )
        raise HTTPException(status_code=404, detail="Session not found or not registered")

    logger.info(
        "Session completed: %s / %s (%d files, %d bytes)",
        body.session_id,
        body.manifest_hash,
        len(body.uploaded_files),
        body.total_bytes,
    )

    return CompleteSessionResponse(status="ok", message="upload completed")


@app.get("/sessions")
async def list_sessions(
    store: SessionStore = Depends(get_store),
) -> list[dict]:
    """List recent sessions (for debugging)."""
    return store.list_sessions()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
