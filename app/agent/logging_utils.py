"""Structured logging setup for the upload agent.

Uses structlog with JSON output for machine-readable logs and
human-readable console output for development.

Key log event names used throughout the agent:
    - session_discovered: new session folder found during scan
    - session_stable: session folder passed stability checks
    - manifest_created: manifest generated and hashed for a session
    - register_started: backend registration request initiated
    - register_duplicate: backend reports session already uploaded
    - upload_started: file upload to S3 initiated
    - upload_file_succeeded: single file upload completed
    - upload_completed: all files for a session uploaded
    - session_failed: session processing failed (includes error details)

Typical usage::

    from agent.logging_utils import setup_logging, get_logger

    setup_logging(log_dir="./logs")
    logger = get_logger("scanner")
    logger.info("session_discovered", session_id="abc", session_path="/data/abc", profile="battery")
"""

from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler

import structlog


def setup_logging(log_dir: str, log_level: str = "INFO") -> None:
    """Configure structlog with JSON output to file and console.

    Args:
        log_dir: Directory for log files. Created if it does not exist.
        log_level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    os.makedirs(log_dir, exist_ok=True)

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Shared structlog processors applied before formatter dispatch
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # Configure structlog to use stdlib integration
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # JSON formatter for file output (format_exc_info here, not in shared chain,
    # because ConsoleRenderer handles exceptions natively and warns otherwise)
    json_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )

    # Human-readable formatter for console output
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    # File handler with daily rotation, keeping 30 days
    log_file = os.path.join(log_dir, "agent.log")
    file_handler = TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(json_formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(console_formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    # Clear existing handlers to avoid duplicate output on repeated calls
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def get_logger(name: str = "agent") -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger with the given name.

    Args:
        name: Logger name, typically the module or component name.

    Returns:
        A structlog BoundLogger instance.
    """
    return structlog.get_logger(name)
