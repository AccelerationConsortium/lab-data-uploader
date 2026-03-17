"""Typer CLI for the Lab Data Uploader Agent."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from agent.config import load_config
from agent.logging_utils import get_logger, setup_logging
from agent.manifest import compute_manifest_hash, generate_manifest
from agent.scheduler import UploadScheduler

app = typer.Typer(name="uploader-agent", help="Lab Data Uploader Agent")


@app.command()
def run(
    config: Path = typer.Option(..., "--config", help="Path to config YAML"),
) -> None:
    """Start the agent scheduler loop."""
    cfg = load_config(config)
    setup_logging(cfg.storage.log_dir)
    logger = get_logger("cli")

    roots = [r.path for r in cfg.watch.session_roots]
    logger.info(
        "agent_startup",
        machine_id=cfg.agent.machine_id,
        lab_id=cfg.agent.lab_id,
        session_roots=roots,
        scan_interval=cfg.agent.scan_interval_seconds,
    )

    typer.echo(f"Uploader Agent started  machine_id={cfg.agent.machine_id}  lab_id={cfg.agent.lab_id}")
    typer.echo(f"Watching: {roots}")
    typer.echo(f"Scan interval: {cfg.agent.scan_interval_seconds}s")

    scheduler = UploadScheduler(cfg)
    try:
        scheduler.run_loop()
    except KeyboardInterrupt:
        logger.info("agent_shutdown", reason="keyboard_interrupt")
        typer.echo("\nShutting down gracefully.")
        scheduler.stop()
    finally:
        scheduler.close()


@app.command()
def scan_once(
    config: Path = typer.Option(..., "--config", help="Path to config YAML"),
) -> None:
    """Run a single scan cycle and report discovered sessions."""
    cfg = load_config(config)
    setup_logging(cfg.storage.log_dir)
    logger = get_logger("cli")

    scheduler = UploadScheduler(cfg)
    try:
        scheduler.run_once()
    finally:
        scheduler.close()

    logger.info("scan_once_complete")


@app.command()
def validate_config(
    config: Path = typer.Option(..., "--config", help="Path to config YAML"),
) -> None:
    """Validate a config file and print a summary."""
    try:
        cfg = load_config(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise SystemExit(1)

    typer.echo("Config is valid.")
    typer.echo(f"  machine_id: {cfg.agent.machine_id}")
    typer.echo(f"  lab_id: {cfg.agent.lab_id}")
    typer.echo(f"  scan_interval: {cfg.agent.scan_interval_seconds}s")
    typer.echo(f"  stable_window: {cfg.agent.stable_window_seconds}s")
    typer.echo(f"  session_roots: {len(cfg.watch.session_roots)}")
    for root in cfg.watch.session_roots:
        typer.echo(f"    - {root.path} (profile: {root.profile})")
    typer.echo(f"  profiles: {list(cfg.profiles.keys())}")
    typer.echo(f"  api_base_url: {cfg.upload.api_base_url}")
    typer.echo(f"  state_db: {cfg.storage.local_state_db}")


@app.command()
def print_manifest(
    session: Path = typer.Option(..., "--session", help="Session folder path"),
    config: Path = typer.Option(..., "--config", help="Path to config YAML"),
) -> None:
    """Generate and print a manifest for a session folder."""
    cfg = load_config(config)

    if not session.is_dir():
        typer.echo(f"Session path is not a directory: {session}", err=True)
        raise SystemExit(1)

    manifest = generate_manifest(
        session_path=str(session),
        session_id=session.name,
        machine_id=cfg.agent.machine_id,
        lab_id=cfg.agent.lab_id,
    )
    manifest_hash = compute_manifest_hash(manifest)

    typer.echo(json.dumps(manifest.model_dump(), indent=2, sort_keys=True))
    typer.echo(f"\nmanifest_hash: {manifest_hash}")


if __name__ == "__main__":
    app()
