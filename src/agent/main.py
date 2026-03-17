"""Entry point for running the agent as ``python -m agent``."""

from __future__ import annotations

import signal
import sys

from agent.cli import app


def _handle_signal(signum: int, _frame: object) -> None:
    """Handle SIGINT/SIGTERM for graceful shutdown."""
    sig_name = signal.Signals(signum).name
    print(f"\nReceived {sig_name}, shutting down.")
    sys.exit(0)


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

if __name__ == "__main__":
    app()
