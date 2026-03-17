# Lab Data Uploader

An ECS-based agent that detects completed experiment sessions from lab PC shared directories and uploads them to S3.

## How It Works

```
Lab PCs (shared dirs)         ECS Task                    AWS
┌────────────────────┐       ┌──────────────┐           ┌─────────────────┐
│ /mnt/labpc-01/     │       │              │           │  S3 Bucket      │
│   session-01/      │◀─VPN─▶│  Uploader    │──boto3──▶ │  session-01/    │
│   session-02/      │       │  Agent       │           │  session-02/    │
│ /mnt/labpc-02/     │       │              │──trigger─▶│  Step Functions │
│   session-03/      │       │              │           │  (validation)   │
└────────────────────┘       └──────────────┘           └─────────────────┘
  Tailscale / VPN              IAM Role
```

1. **Scan** — watches shared directories (mounted via Tailscale/VPN) for session folders
2. **Detect completion** — waits for marker files and file stability (no writes for N seconds)
3. **Generate manifest** — lists all files with SHA256 checksums
4. **Deduplicate** — skips sessions already uploaded (by session ID + manifest hash)
5. **Upload** — uploads files directly to S3 via boto3 (IAM role on ECS)
6. **Trigger** — invokes Step Functions for post-upload validation (optional)
7. **Track state** — records progress in a local SQLite database

## Project Structure

```
src/agent/
├── cli.py                 # CLI entry point (typer)
├── scheduler.py           # Main polling loop
├── scanner.py             # Session directory scanner
├── completion_detector.py # File stability detection
├── manifest.py            # Manifest generation + hashing
├── dedup.py               # Deduplication checker
├── uploader.py            # Direct S3 upload (boto3)
├── step_functions.py      # Step Functions trigger
├── state_db.py            # SQLite state tracking
├── retry.py               # Exponential backoff retry
└── models.py              # Pydantic config + data models
```

## Quick Start

### Install

```bash
pip install git+https://github.com/SissiFeng/lab-data-uploader.git
```

### Configure

```bash
cp configs/example.config.yaml config.yaml
```

Key settings:

```yaml
agent:
  machine_id: labpc-01
  lab_id: sdl1
  scan_interval_seconds: 60
  stable_window_seconds: 300

watch:
  session_roots:
    - path: "/mnt/labpc-01/sessions"
      profile: battery_session

profiles:
  battery_session:
    required_markers:
      - "session_summary.json"
    ignore_patterns:
      - "*.tmp"
      - "*.lock"

upload:
  s3_bucket: "battery-etl-dev-data"
  s3_region: "ca-central-1"
  step_function_arn: ""  # optional
```

### Run

```bash
uploader-agent run --config config.yaml
```

Other commands:

```bash
uploader-agent scan-once --config config.yaml
uploader-agent validate-config --config config.yaml
uploader-agent print-manifest --session-path /path/to/session --config config.yaml
```

## Update

```bash
pip install --upgrade git+https://github.com/SissiFeng/lab-data-uploader.git
```

## Development

```bash
git clone https://github.com/SissiFeng/lab-data-uploader.git
cd lab-data-uploader
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -q
```
