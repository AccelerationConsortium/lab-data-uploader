#!/usr/bin/env python
"""Run the uploader agent locally for development."""
import subprocess
import sys


def main():
    config = sys.argv[1] if len(sys.argv) > 1 else "configs/example.config.yaml"
    subprocess.run([sys.executable, "-m", "agent.cli", "run", "--config", config])


if __name__ == "__main__":
    main()
