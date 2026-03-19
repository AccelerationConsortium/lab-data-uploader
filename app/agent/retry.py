"""Retry wrappers and backoff policies for the upload agent."""

from __future__ import annotations

from typing import Any

import httpx
import structlog
import tenacity

logger = structlog.get_logger("retry")


def _log_retry(retry_state: tenacity.RetryCallState) -> None:
    """Log each retry attempt with structured context."""
    exception = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "retry_attempt",
        attempt=retry_state.attempt_number,
        wait_seconds=round(retry_state.next_action.sleep, 2) if retry_state.next_action else 0,  # type: ignore[union-attr]
        error=str(exception) if exception else None,
        error_type=type(exception).__name__ if exception else None,
    )


def with_retry(max_retries: int = 3, initial_backoff: float = 1.0) -> Any:
    """Retry decorator with exponential backoff.

    Args:
        max_retries: Maximum number of attempts before giving up.
        initial_backoff: Multiplier for exponential backoff wait time.

    Returns:
        A tenacity retry decorator.
    """
    return tenacity.retry(
        stop=tenacity.stop_after_attempt(max_retries),
        wait=tenacity.wait_exponential(multiplier=initial_backoff, min=1, max=300),
        retry=tenacity.retry_if_exception_type(
            (httpx.TimeoutException, httpx.ConnectError, ConnectionError, OSError)
        ),
        before_sleep=_log_retry,
        reraise=True,
    )
