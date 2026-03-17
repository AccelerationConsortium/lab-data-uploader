"""Pydantic v2 models for configuration and session manifests."""

from __future__ import annotations

from dataclasses import dataclass, field
import time

from pydantic import BaseModel


# --- Completion detection models ---


@dataclass
class SessionSnapshot:
    session_path: str
    file_count: int
    total_size: int
    max_mtime: float
    snapshot_at: float = field(default_factory=time.time)


@dataclass
class CompletionResult:
    is_complete: bool
    reason: str  # stable_window_passed, markers_present, not_stable, missing_markers


# --- Scanner models ---


class CandidateSession(BaseModel):
    session_id: str
    session_path: str
    profile_name: str
    discovered_at: str  # ISO 8601 timestamp


# --- Config models ---


class AgentConfig(BaseModel):
    machine_id: str
    lab_id: str
    scan_interval_seconds: int = 60
    stable_window_seconds: int = 300
    timezone: str = "UTC"


class WatchRoot(BaseModel):
    path: str
    profile: str


class SessionProfile(BaseModel):
    required_markers: list[str] = []
    ignore_patterns: list[str] = []
    metadata_files: list[str] = []


class UploadConfig(BaseModel):
    api_base_url: str
    request_timeout_seconds: int = 60
    max_retries: int = 10
    initial_backoff_seconds: int = 30


class StorageConfig(BaseModel):
    local_state_db: str = "./state/upload_state.db"
    manifest_cache_dir: str = "./state/manifests"
    log_dir: str = "./logs"


class WatchConfig(BaseModel):
    session_roots: list[WatchRoot]


class AppConfig(BaseModel):
    agent: AgentConfig
    watch: WatchConfig
    profiles: dict[str, SessionProfile]
    upload: UploadConfig
    storage: StorageConfig


# --- Manifest models ---


class FileEntry(BaseModel):
    relative_path: str
    size: int
    sha256: str
    modified_time: str


class SessionManifest(BaseModel):
    session_id: str
    machine_id: str
    lab_id: str
    session_path: str
    files: list[FileEntry]
    file_count: int
    total_bytes: int
    schema_version: str = "1.0"


# --- Deduplication models ---


@dataclass
class DeduplicationResult:
    is_duplicate: bool
    existing_status: str | None = None
    existing_uploaded_at: str | None = None


# --- API response models ---


class RegisterResponse(BaseModel):
    action: str  # "upload_required" | "duplicate" | "updated_version"
    presigned_urls: dict[str, str] = {}
    upload_id: str = ""


class CompleteResponse(BaseModel):
    status: str
    message: str = ""


# --- Upload result models ---


class UploadResult(BaseModel):
    success: bool
    uploaded_files: list[str] = []
    failed_files: list[str] = []
    total_bytes_uploaded: int = 0
    error: str | None = None
