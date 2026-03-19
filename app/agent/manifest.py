"""Manifest generation and hashing for session uploads."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from .models import FileEntry, SessionManifest


def _compute_file_sha256(file_path: str) -> str:
    """Compute SHA256 hex digest of a file, reading in 8KB chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def generate_manifest(
    session_path: str,
    session_id: str,
    machine_id: str,
    lab_id: str,
    ignore_patterns: list[str] | None = None,
) -> SessionManifest:
    """Walk session_path recursively and build a SessionManifest.

    Files matching any of the ignore_patterns (fnmatch) are excluded.
    The files list is sorted by relative_path for deterministic ordering.
    """
    if ignore_patterns is None:
        ignore_patterns = []

    root = Path(session_path)
    entries: list[FileEntry] = []

    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            # Check ignore patterns against the filename
            if any(fnmatch(fname, pat) for pat in ignore_patterns):
                continue

            full_path = Path(dirpath) / fname
            rel_path = full_path.relative_to(root).as_posix()

            # Also check ignore patterns against the relative path
            if any(fnmatch(rel_path, pat) for pat in ignore_patterns):
                continue

            stat = full_path.stat()
            modified_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

            entries.append(
                FileEntry(
                    relative_path=rel_path,
                    size=stat.st_size,
                    sha256=_compute_file_sha256(str(full_path)),
                    modified_time=modified_dt.isoformat(),
                )
            )

    # Sort by relative_path for deterministic ordering
    entries.sort(key=lambda e: e.relative_path)

    total_bytes = sum(e.size for e in entries)

    return SessionManifest(
        session_id=session_id,
        machine_id=machine_id,
        lab_id=lab_id,
        session_path=str(root),
        files=entries,
        file_count=len(entries),
        total_bytes=total_bytes,
        schema_version="1.0",
    )


def compute_manifest_hash(manifest: SessionManifest) -> str:
    """Compute a deterministic SHA256 hash of the canonical manifest JSON."""
    data = manifest.model_dump()
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def save_manifest(manifest: SessionManifest, manifest_hash: str, cache_dir: str) -> str:
    """Save manifest JSON to cache_dir/{session_id}_{manifest_hash}.json.

    Returns the file path of the saved manifest.
    """
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    filename = f"{manifest.session_id}_{manifest_hash}.json"
    filepath = cache / filename

    data = manifest.model_dump()
    filepath.write_text(
        json.dumps(data, sort_keys=True, indent=2), encoding="utf-8"
    )
    return str(filepath)
