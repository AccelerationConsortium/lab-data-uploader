"""Tests for SessionScanner session folder discovery."""

from __future__ import annotations

import json
from pathlib import Path


from agent.models import (
    AgentConfig,
    AppConfig,
    SessionProfile,
    StorageConfig,
    UploadConfig,
    WatchConfig,
    WatchRoot,
)
from agent.scanner import SessionScanner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    roots: list[WatchRoot],
    profiles: dict[str, SessionProfile] | None = None,
) -> AppConfig:
    """Build a minimal AppConfig for scanner tests."""
    return AppConfig(
        agent=AgentConfig(machine_id="test-pc", lab_id="test-lab"),
        watch=WatchConfig(session_roots=roots),
        profiles=profiles or {},
        upload=UploadConfig(s3_bucket="test-bucket"),
        storage=StorageConfig(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScanDiscoversFolders:
    def test_discovers_session_subdirectories(self, tmp_path: Path) -> None:
        """Scanner should return one CandidateSession per subdirectory."""
        (tmp_path / "session_a").mkdir()
        (tmp_path / "session_b").mkdir()
        # Plain files should be ignored.
        (tmp_path / "stray_file.txt").write_text("ignored")

        config = _make_config(
            roots=[WatchRoot(path=str(tmp_path), profile="p")],
            profiles={"p": SessionProfile()},
        )
        results = SessionScanner(config).scan()

        ids = sorted(c.session_id for c in results)
        assert ids == ["session_a", "session_b"]
        for c in results:
            assert c.profile_name == "p"
            assert c.discovered_at  # non-empty ISO timestamp


class TestIgnorePatterns:
    def test_ignores_matching_directories(self, tmp_path: Path) -> None:
        (tmp_path / "good_session").mkdir()
        (tmp_path / "cache.tmp").mkdir()
        (tmp_path / "data.lock").mkdir()

        config = _make_config(
            roots=[WatchRoot(path=str(tmp_path), profile="p")],
            profiles={"p": SessionProfile(ignore_patterns=["*.tmp", "*.lock"])},
        )
        results = SessionScanner(config).scan()

        assert len(results) == 1
        assert results[0].session_id == "good_session"

    def test_no_ignore_patterns_returns_all(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()

        config = _make_config(
            roots=[WatchRoot(path=str(tmp_path), profile="p")],
            profiles={"p": SessionProfile(ignore_patterns=[])},
        )
        results = SessionScanner(config).scan()

        assert len(results) == 2


class TestSessionIdFromMetadata:
    def test_reads_session_id_from_metadata_json(self, tmp_path: Path) -> None:
        sess = tmp_path / "folder_name"
        sess.mkdir()
        (sess / "metadata.json").write_text(
            json.dumps({"session_id": "META-001"}), encoding="utf-8"
        )

        config = _make_config(
            roots=[WatchRoot(path=str(tmp_path), profile="p")],
            profiles={
                "p": SessionProfile(metadata_files=["metadata.json"])
            },
        )
        results = SessionScanner(config).scan()

        assert len(results) == 1
        assert results[0].session_id == "META-001"

    def test_falls_back_to_second_metadata_file(self, tmp_path: Path) -> None:
        sess = tmp_path / "folder_x"
        sess.mkdir()
        # First metadata file does not exist; second one has the id.
        (sess / "backup_meta.json").write_text(
            json.dumps({"session_id": "BACKUP-42"}), encoding="utf-8"
        )

        config = _make_config(
            roots=[WatchRoot(path=str(tmp_path), profile="p")],
            profiles={
                "p": SessionProfile(
                    metadata_files=["primary.json", "backup_meta.json"]
                )
            },
        )
        results = SessionScanner(config).scan()

        assert results[0].session_id == "BACKUP-42"

    def test_skips_metadata_without_session_id_key(self, tmp_path: Path) -> None:
        sess = tmp_path / "folder_y"
        sess.mkdir()
        (sess / "metadata.json").write_text(
            json.dumps({"other_key": "value"}), encoding="utf-8"
        )

        config = _make_config(
            roots=[WatchRoot(path=str(tmp_path), profile="p")],
            profiles={
                "p": SessionProfile(metadata_files=["metadata.json"])
            },
        )
        results = SessionScanner(config).scan()

        assert results[0].session_id == "folder_y"


class TestSessionIdFallbackToFolderName:
    def test_uses_folder_name_when_no_metadata(self, tmp_path: Path) -> None:
        (tmp_path / "my_session_2025").mkdir()

        config = _make_config(
            roots=[WatchRoot(path=str(tmp_path), profile="p")],
            profiles={"p": SessionProfile(metadata_files=[])},
        )
        results = SessionScanner(config).scan()

        assert results[0].session_id == "my_session_2025"

    def test_uses_folder_name_when_metadata_is_invalid_json(
        self, tmp_path: Path
    ) -> None:
        sess = tmp_path / "bad_meta_session"
        sess.mkdir()
        (sess / "metadata.json").write_text("NOT VALID JSON", encoding="utf-8")

        config = _make_config(
            roots=[WatchRoot(path=str(tmp_path), profile="p")],
            profiles={
                "p": SessionProfile(metadata_files=["metadata.json"])
            },
        )
        results = SessionScanner(config).scan()

        assert results[0].session_id == "bad_meta_session"


class TestNonExistentRoot:
    def test_non_existent_root_returns_empty(self, tmp_path: Path) -> None:
        config = _make_config(
            roots=[
                WatchRoot(
                    path=str(tmp_path / "does_not_exist"), profile="p"
                )
            ],
            profiles={"p": SessionProfile()},
        )
        results = SessionScanner(config).scan()

        assert results == []


class TestEmptyRoot:
    def test_empty_root_returns_empty(self, tmp_path: Path) -> None:
        config = _make_config(
            roots=[WatchRoot(path=str(tmp_path), profile="p")],
            profiles={"p": SessionProfile()},
        )
        results = SessionScanner(config).scan()

        assert results == []


class TestMultipleRoots:
    def test_scans_multiple_roots(self, tmp_path: Path) -> None:
        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        root_a.mkdir()
        root_b.mkdir()
        (root_a / "sess1").mkdir()
        (root_b / "sess2").mkdir()

        config = _make_config(
            roots=[
                WatchRoot(path=str(root_a), profile="pa"),
                WatchRoot(path=str(root_b), profile="pb"),
            ],
            profiles={
                "pa": SessionProfile(),
                "pb": SessionProfile(),
            },
        )
        results = SessionScanner(config).scan()

        ids = sorted(c.session_id for c in results)
        assert ids == ["sess1", "sess2"]
