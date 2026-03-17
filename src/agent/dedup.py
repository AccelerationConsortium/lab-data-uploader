"""Local duplicate detection for session uploads."""

from __future__ import annotations

from agent.models import DeduplicationResult
from agent.state_db import StateDB


class DeduplicationChecker:
    """Check whether a session has already been uploaded successfully."""

    def __init__(self, state_db: StateDB) -> None:
        self._db = state_db

    def check(self, session_id: str, manifest_hash: str) -> DeduplicationResult:
        """Determine whether the given session+manifest is a duplicate.

        Returns a :class:`DeduplicationResult` indicating whether uploading
        should be skipped.

        Rules
        -----
        - No existing session record            -> not duplicate
        - Same manifest_hash, status='uploaded'  -> duplicate (already done)
        - Different manifest_hash                -> not duplicate (new version)
        - Same manifest_hash, status='failed'    -> not duplicate (should retry)
        - Same manifest_hash, status='uploading' -> not duplicate (interrupted)
        """
        existing = self._db.get_session(session_id)

        if existing is None:
            return DeduplicationResult(is_duplicate=False)

        if (
            existing["manifest_hash"] == manifest_hash
            and existing["status"] == "uploaded"
        ):
            return DeduplicationResult(
                is_duplicate=True,
                existing_status=existing["status"],
                existing_uploaded_at=existing.get("uploaded_at"),
            )

        # Different hash (new version), failed, uploading, or any other status
        return DeduplicationResult(
            is_duplicate=False,
            existing_status=existing["status"],
        )
