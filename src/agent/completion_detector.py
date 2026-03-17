"""Session completion detection via file-stability rules and marker checks."""

from __future__ import annotations

import fnmatch
import os
import time

from .models import CompletionResult, SessionProfile, SessionSnapshot


class CompletionDetector:
    """Detect when a session folder is stable and complete.

    A session is considered complete when:
    1. File count, total size, and max mtime are unchanged across two
       consecutive snapshots separated by at least *stable_window_seconds*.
    2. All required marker files from the session profile exist.
    """

    def __init__(self, stable_window_seconds: int) -> None:
        self._stable_window = stable_window_seconds
        self._snapshots: dict[str, SessionSnapshot] = {}

    def check(self, session_path: str, profile: SessionProfile) -> CompletionResult:
        """Return a CompletionResult for the given session directory."""
        current = self._take_snapshot(session_path, profile.ignore_patterns)
        previous = self._snapshots.get(session_path)

        # First scan — cache and report not stable
        if previous is None:
            self._snapshots[session_path] = current
            return CompletionResult(is_complete=False, reason="not_stable")

        # Snapshot changed — update cache and report not stable
        if (
            current.file_count != previous.file_count
            or current.total_size != previous.total_size
            or current.max_mtime != previous.max_mtime
        ):
            self._snapshots[session_path] = current
            return CompletionResult(is_complete=False, reason="not_stable")

        # Snapshot unchanged — check elapsed time
        elapsed = current.snapshot_at - previous.snapshot_at
        if elapsed < self._stable_window:
            return CompletionResult(is_complete=False, reason="not_stable")

        # Stable window passed — check required markers
        missing = [
            marker
            for marker in profile.required_markers
            if not os.path.exists(os.path.join(session_path, marker))
        ]
        if missing:
            return CompletionResult(is_complete=False, reason="missing_markers")

        return CompletionResult(is_complete=True, reason="markers_present")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _take_snapshot(
        session_path: str, ignore_patterns: list[str]
    ) -> SessionSnapshot:
        """Walk *session_path*, skipping files that match *ignore_patterns*."""
        file_count = 0
        total_size = 0
        max_mtime = 0.0

        for dirpath, _dirnames, filenames in os.walk(session_path):
            for fname in filenames:
                if any(fnmatch.fnmatch(fname, pat) for pat in ignore_patterns):
                    continue
                full = os.path.join(dirpath, fname)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                file_count += 1
                total_size += st.st_size
                if st.st_mtime > max_mtime:
                    max_mtime = st.st_mtime
        return SessionSnapshot(
            session_path=session_path,
            file_count=file_count,
            total_size=total_size,
            max_mtime=max_mtime,
            snapshot_at=time.time(),
        )
