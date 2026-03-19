"""Polling loop orchestration for the upload agent."""

from __future__ import annotations

import threading

from agent.completion_detector import CompletionDetector
from agent.dedup import DeduplicationChecker
from agent.logging_utils import get_logger
from agent.manifest import compute_manifest_hash, generate_manifest, save_manifest
from agent.models import AppConfig, CandidateSession, SessionManifest
from agent.scanner import SessionScanner
from agent.state_db import StateDB
from agent.step_functions import StepFunctionsTrigger
from agent.uploader import FileUploader


class UploadScheduler:
    """Orchestrates the full upload pipeline: scan, detect, manifest, dedup, upload, trigger."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._logger = get_logger("scheduler")

        self._db = StateDB(config.storage.local_state_db)
        self._db.init_db()

        self._scanner = SessionScanner(config)
        self._detector = CompletionDetector(config.agent.stable_window_seconds)
        self._dedup = DeduplicationChecker(self._db)
        self._uploader = FileUploader(config.upload)

        # Step Functions trigger (optional — skip if ARN is empty)
        self._sfn: StepFunctionsTrigger | None = None
        if config.upload.step_function_arn:
            self._sfn = StepFunctionsTrigger(
                arn=config.upload.step_function_arn,
                region=config.upload.s3_region,
            )

        self._shutdown = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_loop(self) -> None:
        """Main polling loop. Runs until stop() is called or interrupted."""
        self._logger.info("scheduler_started")
        while not self._shutdown.is_set():
            self.run_once()
            self._shutdown.wait(timeout=self._config.agent.scan_interval_seconds)
        self._logger.info("scheduler_stopped")

    def run_once(self) -> None:
        """Execute a single scan cycle implementing the full upload flow."""
        self._logger.info("scan_cycle_start")

        # Step 1 - Scan for candidate sessions
        candidates = self._scanner.scan()
        self._logger.info("scan_complete", sessions_found=len(candidates))

        # Step 2 - Process each candidate
        for candidate in candidates:
            try:
                self._process_candidate(candidate)
            except Exception as exc:
                self._logger.error(
                    "session_failed",
                    session_id=candidate.session_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        # Step 3 - Retry failed sessions
        self._retry_failed_sessions()

        self._logger.info("scan_cycle_end")

    def stop(self) -> None:
        """Signal the scheduler to stop after the current cycle."""
        self._logger.info("scheduler_stop_requested")
        self._shutdown.set()

    def close(self) -> None:
        """Release resources held by the scheduler."""
        pass  # boto3 clients don't need explicit close

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    def _process_candidate(self, candidate: CandidateSession) -> None:
        """Process a single candidate session through the upload pipeline."""
        session_id = candidate.session_id
        session_path = candidate.session_path
        profile_name = candidate.profile_name

        # Skip if already uploading
        existing = self._db.get_session(session_id)
        if existing and existing["status"] == "uploading":
            self._logger.debug(
                "session_skip",
                session_id=session_id,
                reason="already_uploading",
            )
            return

        # Detect completion
        profile = self._config.profiles[profile_name]
        result = self._detector.check(session_path, profile)

        if not result.is_complete:
            self._db.upsert_session(
                session_id=session_id,
                session_path=session_path,
                profile=profile_name,
                manifest_hash="",
                status="waiting_for_stable",
                file_count=0,
                total_bytes=0,
            )
            self._logger.info(
                "session_waiting",
                session_id=session_id,
                reason=result.reason,
            )
            return

        self._logger.info("session_stable", session_id=session_id)

        # Build manifest
        manifest = generate_manifest(
            session_path=session_path,
            session_id=session_id,
            machine_id=self._config.agent.machine_id,
            lab_id=self._config.agent.lab_id,
            ignore_patterns=profile.ignore_patterns,
        )

        # Compute manifest hash
        manifest_hash = compute_manifest_hash(manifest)
        self._logger.info(
            "manifest_created",
            session_id=session_id,
            manifest_hash=manifest_hash,
            file_count=manifest.file_count,
            total_bytes=manifest.total_bytes,
        )

        # Save manifest to cache
        save_manifest(manifest, manifest_hash, self._config.storage.manifest_cache_dir)

        # Local dedup check
        dedup_result = self._dedup.check(session_id, manifest_hash)
        if dedup_result.is_duplicate:
            self._db.upsert_session(
                session_id=session_id,
                session_path=session_path,
                profile=profile_name,
                manifest_hash=manifest_hash,
                status="duplicate",
                file_count=manifest.file_count,
                total_bytes=manifest.total_bytes,
            )
            self._logger.info(
                "session_duplicate_local",
                session_id=session_id,
                manifest_hash=manifest_hash,
            )
            return

        # Mark as ready, then upload
        self._db.upsert_session(
            session_id=session_id,
            session_path=session_path,
            profile=profile_name,
            manifest_hash=manifest_hash,
            status="ready_to_upload",
            file_count=manifest.file_count,
            total_bytes=manifest.total_bytes,
        )

        self._upload_and_trigger(
            session_id=session_id,
            session_path=session_path,
            profile_name=profile_name,
            manifest=manifest,
            manifest_hash=manifest_hash,
        )

    def _upload_and_trigger(
        self,
        session_id: str,
        session_path: str,
        profile_name: str,
        manifest: SessionManifest,
        manifest_hash: str,
    ) -> None:
        """Upload files to S3 and trigger Step Functions on success."""

        # Mark as uploading
        self._db.update_session_status(session_id, "uploading")
        self._logger.info(
            "upload_started",
            session_id=session_id,
            manifest_hash=manifest_hash,
        )

        # Upload files directly to S3
        upload_result = self._uploader.upload_session(
            session_path=session_path,
            manifest=manifest,
        )

        if upload_result.success:
            self._db.update_session_status(session_id, "uploaded")
            self._logger.info(
                "upload_completed",
                session_id=session_id,
                manifest_hash=manifest_hash,
                files_uploaded=len(upload_result.uploaded_files),
                total_bytes=upload_result.total_bytes_uploaded,
            )

            # Record each file upload in DB
            for file_entry in manifest.files:
                if file_entry.relative_path in upload_result.uploaded_files:
                    self._db.record_file_upload(
                        session_id=session_id,
                        manifest_hash=manifest_hash,
                        relative_path=file_entry.relative_path,
                        sha256=file_entry.sha256,
                        size=file_entry.size,
                        status="uploaded",
                    )

            # Trigger Step Functions (if configured)
            if self._sfn:
                try:
                    self._sfn.trigger(
                        session_id=session_id,
                        manifest_hash=manifest_hash,
                        uploaded_files=upload_result.uploaded_files,
                        total_bytes=upload_result.total_bytes_uploaded,
                    )
                except Exception as exc:
                    self._logger.error(
                        "step_function_trigger_failed",
                        session_id=session_id,
                        error=str(exc),
                    )
        else:
            self._db.update_session_status(
                session_id, "failed", error=upload_result.error
            )
            self._db.increment_retry_count(session_id)
            self._logger.error(
                "session_failed",
                session_id=session_id,
                error=upload_result.error,
                failed_files=upload_result.failed_files,
            )

    def _retry_failed_sessions(self) -> None:
        """Re-attempt upload for failed sessions under max retries."""
        max_retries = self._config.upload.max_retries
        failed_sessions = self._db.get_failed_sessions()

        for session_row in failed_sessions:
            if session_row["retry_count"] >= max_retries:
                self._logger.warning(
                    "session_retry_exhausted",
                    session_id=session_row["session_id"],
                    retry_count=session_row["retry_count"],
                )
                continue

            session_id = session_row["session_id"]
            session_path = session_row["session_path"]
            profile_name = session_row["profile"]
            manifest_hash = session_row["manifest_hash"]

            self._logger.info(
                "session_retry",
                session_id=session_id,
                retry_count=session_row["retry_count"],
            )

            try:
                profile = self._config.profiles[profile_name]
                manifest = generate_manifest(
                    session_path=session_path,
                    session_id=session_id,
                    machine_id=self._config.agent.machine_id,
                    lab_id=self._config.agent.lab_id,
                    ignore_patterns=profile.ignore_patterns,
                )

                self._upload_and_trigger(
                    session_id=session_id,
                    session_path=session_path,
                    profile_name=profile_name,
                    manifest=manifest,
                    manifest_hash=manifest_hash,
                )
            except Exception as exc:
                self._db.update_session_status(
                    session_id, "failed", error=str(exc)
                )
                self._db.increment_retry_count(session_id)
                self._logger.error(
                    "session_retry_failed",
                    session_id=session_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
