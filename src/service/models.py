"""Request and response models for the upload service."""

from __future__ import annotations

from pydantic import BaseModel


# -- Requests --


class RegisterSessionRequest(BaseModel):
    session_id: str
    machine_id: str
    lab_id: str
    manifest_hash: str
    file_count: int
    total_bytes: int
    schema_version: str = "1.0"
    files: list[str] = []  # relative file paths for presigned URL generation


class CompleteSessionRequest(BaseModel):
    session_id: str
    manifest_hash: str
    uploaded_files: list[str]
    total_bytes: int


# -- Responses --


class RegisterSessionResponse(BaseModel):
    action: str  # "upload_required" | "duplicate"
    upload_id: str = ""
    presigned_urls: dict[str, str] = {}


class CompleteSessionResponse(BaseModel):
    status: str
    message: str = ""
