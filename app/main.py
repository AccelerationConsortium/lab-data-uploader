"""Lab Data Uploader Agent — FastAPI entry point with background scheduler."""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from agent.config import load_config
from agent.logging_utils import get_logger, setup_logging
from agent.scheduler import UploadScheduler

_scheduler: UploadScheduler | None = None
_scheduler_thread: threading.Thread | None = None


def _run_scheduler(scheduler: UploadScheduler) -> None:
    """Run the upload scheduler loop in a background thread."""
    scheduler.run_loop()


@asynccontextmanager
async def lifespan(application: FastAPI):  # noqa: ARG001
    """Start scheduler on startup, stop on shutdown."""
    global _scheduler, _scheduler_thread

    config_path = Path(os.environ.get("AGENT_CONFIG", "/app/config.yaml"))
    cfg = load_config(config_path)
    setup_logging(cfg.storage.log_dir)
    logger = get_logger("main")

    roots = [r.path for r in cfg.watch.session_roots]
    logger.info(
        "agent_startup",
        machine_id=cfg.agent.machine_id,
        lab_id=cfg.agent.lab_id,
        session_roots=roots,
        scan_interval=cfg.agent.scan_interval_seconds,
    )

    _scheduler = UploadScheduler(cfg)
    _scheduler_thread = threading.Thread(target=_run_scheduler, args=(_scheduler,), daemon=True)
    _scheduler_thread.start()

    logger.info("health_server_ready", port=int(os.environ.get("PORT", "8000")))
    yield

    logger.info("agent_shutdown", reason="lifespan_shutdown")
    if _scheduler:
        _scheduler.stop()
    if _scheduler_thread:
        _scheduler_thread.join(timeout=10)


app = FastAPI(title="Lab Data Uploader Agent", lifespan=lifespan)


@app.get("/health")
def health():
    """Health check endpoint for ALB."""
    return {"status": "healthy"}
