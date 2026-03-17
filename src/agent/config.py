"""Load and validate YAML configuration."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from agent.models import AppConfig


def load_config(path: str | Path) -> AppConfig:
    """Load a YAML config file and return a validated AppConfig.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the YAML is unparseable or fails validation.
    """
    config_path = Path(path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw_text = config_path.read_text(encoding="utf-8")

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping at top level, got {type(data).__name__}")

    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Config validation failed for {config_path}:\n{exc}") from exc
