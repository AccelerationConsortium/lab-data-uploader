"""Tests for manifest generation and hashing."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from agent.manifest import (
    _compute_file_sha256,
    compute_manifest_hash,
    generate_manifest,
    save_manifest,
)
from agent.models import FileEntry, SessionManifest


@pytest.fixture()
def session_dir(tmp_path: Path) -> Path:
    """Create a temporary session directory with known files."""
    # data/results.csv
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    csv_file = data_dir / "results.csv"
    csv_file.write_text("x,y\n1,2\n3,4\n")

    # README.txt at root
    readme = tmp_path / "README.txt"
    readme.write_text("experiment notes")

    # image.png (binary content)
    img = tmp_path / "image.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # Files that should be ignored
    tmp_file = tmp_path / "scratch.tmp"
    tmp_file.write_text("temporary")

    lock_file = tmp_path / "proc.lock"
    lock_file.write_text("locked")

    return tmp_path


IGNORE_PATTERNS = ["*.tmp", "*.lock"]


class TestGenerateManifest:
    def test_returns_correct_file_count(self, session_dir: Path) -> None:
        manifest = generate_manifest(
            session_path=str(session_dir),
            session_id="sess-001",
            machine_id="pc-01",
            lab_id="lab1",
            ignore_patterns=IGNORE_PATTERNS,
        )
        # README.txt, image.png, data/results.csv
        assert manifest.file_count == 3

    def test_returns_correct_total_bytes(self, session_dir: Path) -> None:
        manifest = generate_manifest(
            session_path=str(session_dir),
            session_id="sess-001",
            machine_id="pc-01",
            lab_id="lab1",
            ignore_patterns=IGNORE_PATTERNS,
        )
        expected_size = (
            len("x,y\n1,2\n3,4\n")
            + len("experiment notes")
            + len(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        )
        assert manifest.total_bytes == expected_size

    def test_files_sorted_by_relative_path(self, session_dir: Path) -> None:
        manifest = generate_manifest(
            session_path=str(session_dir),
            session_id="sess-001",
            machine_id="pc-01",
            lab_id="lab1",
            ignore_patterns=IGNORE_PATTERNS,
        )
        paths = [f.relative_path for f in manifest.files]
        assert paths == sorted(paths)

    def test_ignore_patterns_exclude_files(self, session_dir: Path) -> None:
        manifest = generate_manifest(
            session_path=str(session_dir),
            session_id="sess-001",
            machine_id="pc-01",
            lab_id="lab1",
            ignore_patterns=IGNORE_PATTERNS,
        )
        paths = [f.relative_path for f in manifest.files]
        assert not any(p.endswith(".tmp") for p in paths)
        assert not any(p.endswith(".lock") for p in paths)

    def test_no_ignore_patterns_includes_all(self, session_dir: Path) -> None:
        manifest = generate_manifest(
            session_path=str(session_dir),
            session_id="sess-001",
            machine_id="pc-01",
            lab_id="lab1",
            ignore_patterns=[],
        )
        # All 5 files should be included
        assert manifest.file_count == 5

    def test_schema_version_is_set(self, session_dir: Path) -> None:
        manifest = generate_manifest(
            session_path=str(session_dir),
            session_id="sess-001",
            machine_id="pc-01",
            lab_id="lab1",
            ignore_patterns=IGNORE_PATTERNS,
        )
        assert manifest.schema_version == "1.0"

    def test_metadata_fields(self, session_dir: Path) -> None:
        manifest = generate_manifest(
            session_path=str(session_dir),
            session_id="sess-001",
            machine_id="pc-01",
            lab_id="lab1",
            ignore_patterns=IGNORE_PATTERNS,
        )
        assert manifest.session_id == "sess-001"
        assert manifest.machine_id == "pc-01"
        assert manifest.lab_id == "lab1"
        assert manifest.session_path == str(session_dir)


class TestComputeManifestHash:
    def test_deterministic_same_input(self, session_dir: Path) -> None:
        manifest = generate_manifest(
            session_path=str(session_dir),
            session_id="sess-001",
            machine_id="pc-01",
            lab_id="lab1",
            ignore_patterns=IGNORE_PATTERNS,
        )
        hash1 = compute_manifest_hash(manifest)
        hash2 = compute_manifest_hash(manifest)
        assert hash1 == hash2

    def test_hash_changes_when_files_change(self, session_dir: Path) -> None:
        manifest1 = generate_manifest(
            session_path=str(session_dir),
            session_id="sess-001",
            machine_id="pc-01",
            lab_id="lab1",
            ignore_patterns=IGNORE_PATTERNS,
        )
        hash1 = compute_manifest_hash(manifest1)

        # Modify a file
        (session_dir / "README.txt").write_text("updated notes")

        manifest2 = generate_manifest(
            session_path=str(session_dir),
            session_id="sess-001",
            machine_id="pc-01",
            lab_id="lab1",
            ignore_patterns=IGNORE_PATTERNS,
        )
        hash2 = compute_manifest_hash(manifest2)

        assert hash1 != hash2

    def test_hash_is_valid_sha256_hex(self, session_dir: Path) -> None:
        manifest = generate_manifest(
            session_path=str(session_dir),
            session_id="sess-001",
            machine_id="pc-01",
            lab_id="lab1",
            ignore_patterns=IGNORE_PATTERNS,
        )
        h = compute_manifest_hash(manifest)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestSaveManifest:
    def test_writes_valid_json(self, session_dir: Path, tmp_path: Path) -> None:
        manifest = generate_manifest(
            session_path=str(session_dir),
            session_id="sess-001",
            machine_id="pc-01",
            lab_id="lab1",
            ignore_patterns=IGNORE_PATTERNS,
        )
        h = compute_manifest_hash(manifest)
        cache_dir = tmp_path / "cache"
        filepath = save_manifest(manifest, h, str(cache_dir))

        assert os.path.exists(filepath)
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)

        assert data["session_id"] == "sess-001"
        assert data["file_count"] == 3
        assert len(data["files"]) == 3

    def test_filename_format(self, session_dir: Path, tmp_path: Path) -> None:
        manifest = generate_manifest(
            session_path=str(session_dir),
            session_id="sess-001",
            machine_id="pc-01",
            lab_id="lab1",
            ignore_patterns=IGNORE_PATTERNS,
        )
        h = compute_manifest_hash(manifest)
        cache_dir = tmp_path / "cache"
        filepath = save_manifest(manifest, h, str(cache_dir))

        expected_name = f"sess-001_{h}.json"
        assert Path(filepath).name == expected_name

    def test_creates_cache_dir_if_missing(self, session_dir: Path, tmp_path: Path) -> None:
        manifest = generate_manifest(
            session_path=str(session_dir),
            session_id="sess-001",
            machine_id="pc-01",
            lab_id="lab1",
            ignore_patterns=IGNORE_PATTERNS,
        )
        h = compute_manifest_hash(manifest)
        cache_dir = tmp_path / "nested" / "cache"
        filepath = save_manifest(manifest, h, str(cache_dir))
        assert os.path.exists(filepath)


class TestComputeFileSha256:
    def test_known_value(self, tmp_path: Path) -> None:
        content = b"hello world"
        f = tmp_path / "test.bin"
        f.write_bytes(content)

        result = _compute_file_sha256(str(f))
        expected = hashlib.sha256(content).hexdigest()
        assert result == expected

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")

        result = _compute_file_sha256(str(f))
        expected = hashlib.sha256(b"").hexdigest()
        assert result == expected

    def test_large_file_multi_chunk(self, tmp_path: Path) -> None:
        # File larger than 8KB chunk size
        content = b"A" * 20000
        f = tmp_path / "large.bin"
        f.write_bytes(content)

        result = _compute_file_sha256(str(f))
        expected = hashlib.sha256(content).hexdigest()
        assert result == expected
