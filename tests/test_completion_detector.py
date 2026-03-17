"""Tests for the CompletionDetector."""

from __future__ import annotations

import os
import time

import pytest

from agent.completion_detector import CompletionDetector
from agent.models import SessionProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: str, content: str = "data") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFirstScan:
    """First scan must always return not_stable."""

    def test_first_scan_returns_not_stable(self, tmp_path: str) -> None:
        session = str(tmp_path / "session1")
        os.makedirs(session)
        _write(os.path.join(session, "data.csv"), "a,b,c")

        profile = SessionProfile()
        detector = CompletionDetector(stable_window_seconds=0)

        result = detector.check(session, profile)
        assert result.is_complete is False
        assert result.reason == "not_stable"


class TestUnstableSession:
    """When files change between scans the session stays not_stable."""

    def test_file_count_change(self, tmp_path: str) -> None:
        session = str(tmp_path / "session2")
        os.makedirs(session)
        _write(os.path.join(session, "a.csv"))

        profile = SessionProfile()
        detector = CompletionDetector(stable_window_seconds=0)

        # first scan
        detector.check(session, profile)

        # add a file
        _write(os.path.join(session, "b.csv"))

        result = detector.check(session, profile)
        assert result.is_complete is False
        assert result.reason == "not_stable"

    def test_size_change(self, tmp_path: str) -> None:
        session = str(tmp_path / "session3")
        os.makedirs(session)
        fpath = os.path.join(session, "data.bin")
        _write(fpath, "short")

        profile = SessionProfile()
        detector = CompletionDetector(stable_window_seconds=0)

        detector.check(session, profile)

        # grow the file
        _write(fpath, "much longer content here")

        result = detector.check(session, profile)
        assert result.is_complete is False
        assert result.reason == "not_stable"


class TestStableWithMarkers:
    """Stable session with all required markers reports complete."""

    def test_stable_session_with_markers(self, tmp_path: str) -> None:
        session = str(tmp_path / "session4")
        os.makedirs(session)
        _write(os.path.join(session, "data.csv"))
        _write(os.path.join(session, "finished.flag"), "")

        profile = SessionProfile(required_markers=["finished.flag"])
        detector = CompletionDetector(stable_window_seconds=0)

        # first scan — always not_stable
        r1 = detector.check(session, profile)
        assert r1.is_complete is False

        # second scan — no changes, window=0 so elapsed is sufficient
        r2 = detector.check(session, profile)
        assert r2.is_complete is True
        assert r2.reason == "markers_present"


class TestStableMissingMarkers:
    """Stable session missing required markers reports missing_markers."""

    def test_missing_markers(self, tmp_path: str) -> None:
        session = str(tmp_path / "session5")
        os.makedirs(session)
        _write(os.path.join(session, "data.csv"))

        profile = SessionProfile(required_markers=["session_summary.json"])
        detector = CompletionDetector(stable_window_seconds=0)

        detector.check(session, profile)

        result = detector.check(session, profile)
        assert result.is_complete is False
        assert result.reason == "missing_markers"


class TestStableNoMarkers:
    """Session with no required markers completes once stable."""

    def test_no_markers_required(self, tmp_path: str) -> None:
        session = str(tmp_path / "session6")
        os.makedirs(session)
        _write(os.path.join(session, "data.csv"))

        profile = SessionProfile(required_markers=[])
        detector = CompletionDetector(stable_window_seconds=0)

        detector.check(session, profile)
        result = detector.check(session, profile)
        assert result.is_complete is True
        assert result.reason == "markers_present"


class TestIgnorePatterns:
    """Temp files matching ignore_patterns are excluded from snapshots."""

    def test_ignored_files_not_counted(self, tmp_path: str) -> None:
        session = str(tmp_path / "session7")
        os.makedirs(session)
        _write(os.path.join(session, "data.csv"))

        profile = SessionProfile(ignore_patterns=["*.tmp", "*.lock"])
        detector = CompletionDetector(stable_window_seconds=0)

        # first scan
        detector.check(session, profile)

        # add ignored files — should not change the snapshot
        _write(os.path.join(session, "scratch.tmp"))
        _write(os.path.join(session, "proc.lock"))

        result = detector.check(session, profile)
        # snapshot unchanged (ignored files excluded) and window=0
        assert result.is_complete is True
        assert result.reason == "markers_present"

    def test_non_ignored_file_triggers_instability(self, tmp_path: str) -> None:
        session = str(tmp_path / "session8")
        os.makedirs(session)
        _write(os.path.join(session, "data.csv"))

        profile = SessionProfile(ignore_patterns=["*.tmp"])
        detector = CompletionDetector(stable_window_seconds=0)

        detector.check(session, profile)

        # add a non-ignored file
        _write(os.path.join(session, "extra.csv"))

        result = detector.check(session, profile)
        assert result.is_complete is False
        assert result.reason == "not_stable"


class TestStableWindow:
    """The detector respects the stable_window_seconds threshold."""

    def test_window_not_elapsed(self, tmp_path: str) -> None:
        session = str(tmp_path / "session9")
        os.makedirs(session)
        _write(os.path.join(session, "data.csv"))

        profile = SessionProfile()
        # very large window — never satisfied within this test
        detector = CompletionDetector(stable_window_seconds=9999)

        detector.check(session, profile)
        result = detector.check(session, profile)
        assert result.is_complete is False
        assert result.reason == "not_stable"
