"""Tests for agent.logging_utils module."""

from __future__ import annotations

import json
import os

from agent.logging_utils import get_logger, setup_logging


def test_setup_logging_creates_log_file(tmp_path: str) -> None:
    """setup_logging should create the log directory and agent.log file."""
    log_dir = str(tmp_path / "logs")
    setup_logging(log_dir=log_dir, log_level="DEBUG")

    logger = get_logger("test")
    logger.info("session_discovered", session_id="test-001", session_path="/data/test-001")

    log_file = os.path.join(log_dir, "agent.log")
    assert os.path.exists(log_file), "agent.log should be created"

    with open(log_file, encoding="utf-8") as f:
        lines = f.readlines()

    assert len(lines) >= 1, "Log file should contain at least one line"

    record = json.loads(lines[0])
    assert record["event"] == "session_discovered"
    assert record["session_id"] == "test-001"
    assert record["session_path"] == "/data/test-001"
    assert record.get("log_level", record.get("level")) == "info"
    assert "timestamp" in record


def test_get_logger_returns_bound_logger() -> None:
    """get_logger should return a structlog-compatible logger."""
    logger = get_logger("mymodule")
    # structlog.get_logger returns a BoundLoggerLazyProxy which wraps BoundLogger
    assert hasattr(logger, "info")
    assert hasattr(logger, "error")
    assert hasattr(logger, "warning")


def test_log_with_exception_info(tmp_path: str) -> None:
    """Logging with exc_info should include exception details in JSON output."""
    log_dir = str(tmp_path / "logs")
    setup_logging(log_dir=log_dir, log_level="DEBUG")

    logger = get_logger("test_exc")
    try:
        raise ValueError("something went wrong")
    except ValueError:
        logger.error("session_failed", session_id="err-001", exc_info=True)

    log_file = os.path.join(log_dir, "agent.log")
    with open(log_file, encoding="utf-8") as f:
        lines = f.readlines()

    # Find the session_failed line
    error_lines = [line for line in lines if "session_failed" in line]
    assert len(error_lines) >= 1

    record = json.loads(error_lines[0])
    assert record["event"] == "session_failed"
    assert record["session_id"] == "err-001"
    assert "ValueError" in record.get("exception", "")
