# Lab Data Uploader

A local agent that runs on lab PCs to automatically detect completed experiment sessions and upload them to S3.

## How It Works

```
Lab PC                                         AWS
┌─────────────────────┐                       ┌──────────────────┐
│  Session Folders     │                      │  S3 Bucket       │
│  /LabData/session-01 │──scan──▶ Agent       │                  │
│  /LabData/session-02 │         │            │  session-01/     │
│                      │         ▼            │    data.csv      │
│                      │  Detect completion   │    metadata.json │
│                      │  Build manifest      │  session-02/     │
│                      │  Dedup check         │    ...           │
│                      │         │            │                  │
│                      │         ▼            │                  │
│                      │  Upload Service ────▶│  (presigned PUT) │
└─────────────────────┘  (presigned URLs)     └──────────────────┘
```

1. **Scan** — watches configured directories for experiment session folders
2. **Detect completion** — waits for marker files and file stability (no writes for N seconds)
3. **Generate manifest** — lists all files with SHA256 checksums
4. **Deduplicate** — skips sessions already uploaded (by session ID + manifest hash)
5. **Upload** — registers with the upload service, gets presigned S3 URLs, uploads via HTTP PUT
6. **Track state** — records progress in a local SQLite database

## Project Structure

```
src/
├── agent/          # Local agent (runs on lab PCs)
│   ├── cli.py          # CLI entry point (typer)
│   ├── scheduler.py    # Main polling loop
│   ├── scanner.py      # Session directory scanner
│   ├── completion_detector.py
│   ├── manifest.py     # Manifest generation + hashing
│   ├── dedup.py        # Deduplication checker
│   ├── api_client.py   # HTTP client for upload service
│   ├── uploader.py     # Presigned URL file uploader
│   └── state_db.py     # SQLite state tracking
├── service/        # Upload service (FastAPI)
│   ├── app.py          # API endpoints
│   ├── s3_client.py    # Presigned URL generation (boto3)
│   ├── store.py        # Server-side session tracking
│   └── models.py       # Request/response models
```

## Quick Start

### Install

```bash
pip install git+https://github.com/SissiFeng/lab-data-uploader.git
```

### Configure

Copy and edit the example config:

```bash
cp configs/example.config.yaml config.yaml
```

Key settings in `config.yaml`:

```yaml
agent:
  machine_id: labpc-01
  lab_id: sdl1
  scan_interval_seconds: 60
  stable_window_seconds: 300

watch:
  session_roots:
    - path: "/path/to/sessions"
      profile: battery_session

profiles:
  battery_session:
    required_markers:
      - "session_summary.json"
    ignore_patterns:
      - "*.tmp"
      - "*.lock"
```

### Run the Agent

```bash
uploader-agent run --config config.yaml
```

Other commands:

```bash
uploader-agent scan-once --config config.yaml    # single scan cycle
uploader-agent validate-config --config config.yaml
uploader-agent print-manifest --session-path /path/to/session
```

### Run the Upload Service

```bash
pip install "lab-data-uploader[service]"

export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export S3_BUCKET="battery-etl-dev-data"
export S3_REGION="ca-central-1"

PYTHONPATH=src uvicorn service.app:app --port 8000
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
pip install -e ".[dev,service]"
pytest tests/ -q
```
