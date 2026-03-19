"""Enumerate candidate session folders from configured watch roots."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from agent.models import AppConfig, CandidateSession

logger = logging.getLogger(__name__)


class SessionScanner:
    """Scans configured session root directories for candidate sessions."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def scan(self) -> list[CandidateSession]:
        """Scan all configured session roots and return discovered sessions."""
        candidates: list[CandidateSession] = []
        now = datetime.now(timezone.utc).isoformat()

        for root in self._config.watch.session_roots:
            profile_name = root.profile
            profile = self._config.profiles.get(profile_name)
            if profile is None:
                logger.warning(
                    "No profile '%s' defined for root '%s', skipping",
                    profile_name,
                    root.path,
                )
                continue

            root_path = Path(root.path)

            if not root_path.exists():
                logger.warning(
                    "Session root does not exist: %s", root_path
                )
                continue

            try:
                entries = list(root_path.iterdir())
            except PermissionError:
                logger.warning(
                    "Permission denied reading session root: %s", root_path
                )
                continue

            ignore_patterns = profile.ignore_patterns

            for entry in entries:
                if not entry.is_dir():
                    continue

                if self._matches_ignore(entry.name, ignore_patterns):
                    continue

                session_id = self._resolve_session_id(
                    entry, profile.metadata_files
                )

                candidates.append(
                    CandidateSession(
                        session_id=session_id,
                        session_path=str(entry),
                        profile_name=profile_name,
                        discovered_at=now,
                    )
                )

        return candidates

    def _resolve_session_id(
        self, session_dir: Path, metadata_files: list[str]
    ) -> str:
        """Determine session_id: prefer metadata file, fallback to folder name."""
        for meta_name in metadata_files:
            meta_path = session_dir / meta_name
            if not meta_path.is_file():
                continue
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "session_id" in data:
                    return str(data["session_id"])
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "Could not read metadata file %s: %s", meta_path, exc
                )
        return session_dir.name

    @staticmethod
    def _matches_ignore(name: str, patterns: list[str]) -> bool:
        """Return True if *name* matches any of the fnmatch *patterns*."""
        return any(fnmatch(name, pat) for pat in patterns)
