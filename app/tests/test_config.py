"""Tests for config loading and Pydantic model validation."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agent.config import load_config
from agent.models import AppConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_YAML = textwrap.dedent("""\
    agent:
      machine_id: labpc-01
      lab_id: sdl1

    watch:
      session_roots:
        - path: /data/sessions
          profile: default

    profiles:
      default:
        required_markers:
          - "done.flag"

    upload:
      s3_bucket: "test-bucket"

    storage: {}
""")


def _write(tmp_path: Path, content: str, name: str = "config.yaml") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLoadValidConfig:
    def test_loads_example_config(self) -> None:
        cfg = load_config(Path(__file__).resolve().parent.parent / "configs" / "example.config.yaml")
        assert isinstance(cfg, AppConfig)
        assert cfg.agent.machine_id == "labpc-01"
        assert cfg.agent.lab_id == "sdl1"
        assert len(cfg.watch.session_roots) == 2
        assert "battery_session" in cfg.profiles
        assert "camera_session" in cfg.profiles

    def test_loads_minimal_valid_yaml(self, tmp_path: Path) -> None:
        cfg = load_config(_write(tmp_path, VALID_YAML))
        assert cfg.agent.machine_id == "labpc-01"
        assert cfg.watch.session_roots[0].profile == "default"
        assert cfg.profiles["default"].required_markers == ["done.flag"]
        assert cfg.upload.s3_bucket == "test-bucket"


class TestDefaultValues:
    def test_agent_defaults(self, tmp_path: Path) -> None:
        cfg = load_config(_write(tmp_path, VALID_YAML))
        assert cfg.agent.scan_interval_seconds == 60
        assert cfg.agent.stable_window_seconds == 300
        assert cfg.agent.timezone == "UTC"

    def test_upload_defaults(self, tmp_path: Path) -> None:
        cfg = load_config(_write(tmp_path, VALID_YAML))
        assert cfg.upload.s3_bucket == "test-bucket"
        assert cfg.upload.s3_region == "ca-central-1"
        assert cfg.upload.s3_prefix == ""
        assert cfg.upload.max_retries == 10
        assert cfg.upload.initial_backoff_seconds == 30

    def test_storage_defaults(self, tmp_path: Path) -> None:
        cfg = load_config(_write(tmp_path, VALID_YAML))
        assert cfg.storage.manifest_cache_dir == "./state/manifests"
        assert cfg.storage.log_dir == "./logs"

    def test_session_profile_defaults(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            agent:
              machine_id: m1
              lab_id: l1
            watch:
              session_roots:
                - path: /x
                  profile: bare
            profiles:
              bare: {}
            upload:
              s3_bucket: "test-bucket"
            storage: {}
        """)
        cfg = load_config(_write(tmp_path, yaml_text))
        assert cfg.profiles["bare"].required_markers == []
        assert cfg.profiles["bare"].ignore_patterns == []
        assert cfg.profiles["bare"].metadata_files == []


class TestMissingRequiredFields:
    def test_missing_agent(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            watch:
              session_roots:
                - path: /x
                  profile: p
            profiles:
              p: {}
            upload:
              s3_bucket: "test-bucket"
            storage: {}
        """)
        with pytest.raises(ValueError, match="validation failed"):
            load_config(_write(tmp_path, yaml_text))

    def test_missing_machine_id(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            agent:
              lab_id: l1
            watch:
              session_roots:
                - path: /x
                  profile: p
            profiles:
              p: {}
            upload:
              s3_bucket: "test-bucket"
            storage: {}
        """)
        with pytest.raises(ValueError, match="validation failed"):
            load_config(_write(tmp_path, yaml_text))

    def test_missing_s3_bucket(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            agent:
              machine_id: m1
              lab_id: l1
            watch:
              session_roots:
                - path: /x
                  profile: p
            profiles:
              p: {}
            upload: {}
            storage: {}
        """)
        with pytest.raises(ValueError, match="validation failed"):
            load_config(_write(tmp_path, yaml_text))


class TestInvalidInput:
    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            load_config("/nonexistent/path.yaml")

    def test_invalid_yaml_syntax(self, tmp_path: Path) -> None:
        bad_yaml = "agent:\n  machine_id: [unterminated"
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_config(_write(tmp_path, bad_yaml))

    def test_yaml_not_a_mapping(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Expected a YAML mapping"):
            load_config(_write(tmp_path, "- just\n- a\n- list\n"))

    def test_empty_yaml_file(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Expected a YAML mapping"):
            load_config(_write(tmp_path, ""))
