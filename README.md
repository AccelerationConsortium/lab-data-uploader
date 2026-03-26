# Lab Data Uploader

An ECS-based agent that watches lab PC shared directories for completed experiment sessions, uploads them to S3, and drops a `_COMPLETE` marker that triggers downstream ETL via EventBridge.

## Architecture

```
Lab PCs (NFS shares)          ECS Task                         AWS
┌──────────────────────┐      ┌────────────────────────┐      ┌──────────────────────────┐
│ /mnt/lab1/sessions/  │      │                        │      │ S3 Bucket                │
│   session-001/       │      │  SessionScanner        │      │   {session_id}/          │
│   session-002/  ◀────┼─rw───┼▶ CompletionDetector   ├─────▶│     *.csv / *.json       │
│   processed/         │      │  FileUploader          │      │     manifest.json        │
│     session-001/     │      │  UploadScheduler       │      │     _COMPLETE  ◀─────┐   │
└──────────────────────┘      │                        │      └──────────────────────┼───┘
  Tailscale VPN + NFS (rw)    │  StateDB               │                             │
                              │  (Aurora PostgreSQL)   │      ┌──────────────────────┴───┐
                              └────────────────────────┘      │ EventBridge Rule          │
                                IAM Role (ECS task role)       │ (key suffix: /_COMPLETE)  │
                                DATABASE_URL from              └──────────────┬────────────┘
                                Secrets Manager                               │
                                                                              ▼
                                                               ┌──────────────────────────┐
                                                               │ Step Functions           │
                                                               │ (ETL / validation)       │
                                                               └──────────────────────────┘
```

## How It Works

Each scan cycle runs the following pipeline:

1. **Scan** — `SessionScanner` walks NFS-mounted directories for session folders, skipping the reserved `processed/` subdirectory.
2. **Detect completion** — `CompletionDetector` waits for required marker files (e.g. `session_summary.json`) and file stability (no writes for N seconds).
3. **Generate manifest** — lists all session files with SHA256 checksums and metadata.
4. **Upload** — `FileUploader` streams files to S3 via boto3 using the ECS task IAM role. On full success, uploads `manifest.json` then an empty `_COMPLETE` object as the final key.
5. **Trigger (event-driven)** — EventBridge detects the `_COMPLETE` key and invokes Step Functions automatically. The agent has no direct knowledge of downstream consumers.
6. **Move** — the session folder is moved to `processed/` on the NFS share, so the next scan skips it without any database lookup.
7. **Track state** — every session transition is recorded in Aurora PostgreSQL (injected via `DATABASE_URL` from Secrets Manager), surviving ECS task restarts.

Failed uploads are retried with exponential backoff up to `max_retries`. If the NFS move fails after a successful upload, the DB retains `status=uploaded` and the move is retried on the next scan cycle.

## S3 Key Layout

```
{s3_prefix}/{session_id}/
    data.csv
    metadata.json
    session_summary.json
    manifest.json          # file list + SHA256 checksums
    _COMPLETE              # empty sentinel; triggers EventBridge → Step Functions
```

## Configuration

`config.yaml` (mounted into the ECS container):

```yaml
agent:
  machine_id: ecs-uploader
  lab_id: sdl1
  scan_interval_seconds: 60
  stable_window_seconds: 300

watch:
  session_roots:
    - path: "/mnt/lab1/sessions"
      profile: battery_session

profiles:
  battery_session:
    required_markers:
      - "session_summary.json"
    ignore_patterns:
      - "*.tmp"
      - "*.lock"
    metadata_files:
      - "metadata.json"

upload:
  s3_bucket: "battery-etl-dev-data"
  s3_region: "ca-central-1"
  s3_prefix: ""
  max_retries: 10
  initial_backoff_seconds: 30

storage:
  manifest_cache_dir: "/data/state/manifests"
  log_dir: "/data/logs"
```

`DATABASE_URL` is injected at runtime by ECS from AWS Secrets Manager — it never appears in config files or source code.

## Project Structure

```
├── .github/workflows/       # CI/CD (build + push to ECR, deploy to ECS)
├── app/                     # Docker build context
│   ├── Dockerfile
│   ├── entrypoint.sh        # Tailscale init → NFS mount (rw) → agent start
│   ├── main.py
│   ├── requirements.txt
│   ├── config.yaml          # Runtime config (ECS environment)
│   ├── configs/
│   │   └── example.config.yaml
│   └── agent/
│       ├── cli.py           # Typer CLI (run / scan-once / validate-config / print-manifest)
│       ├── scheduler.py     # Main polling loop
│       ├── scanner.py       # NFS directory walker
│       ├── completion_detector.py
│       ├── manifest.py      # SHA256 manifest generation
│       ├── uploader.py      # S3 upload + _COMPLETE marker
│       ├── state_db.py      # Aurora PostgreSQL state layer
│       ├── models.py        # Pydantic models
│       ├── config.py        # Config loader
│       ├── retry.py         # Exponential backoff decorator
│       └── logging_utils.py # structlog setup
├── platform/                # Terraform (ECS, Aurora, IAM, Secrets Manager)
│   ├── platform.yml
│   └── vars/
│       ├── dev.tfvars
│       └── prod.tfvars
└── README.md
```

## AWS Services Used

| Service | Role |
|---|---|
| **ECS (Fargate)** | Runs the agent container |
| **S3** | Session file storage; `_COMPLETE` key acts as event source |
| **EventBridge** | Watches `s3:ObjectCreated` with `/_COMPLETE` suffix, routes to Step Functions |
| **Step Functions** | Post-upload ETL and validation pipeline |
| **Aurora PostgreSQL** | Persistent upload state (survives ECS task restarts) |
| **Secrets Manager** | Stores `DATABASE_URL`; injected into ECS task as environment variable |
| **IAM** | Task role grants S3 write and Secrets Manager read permissions |
| **ECR** | Stores the agent Docker image |
| **Tailscale** | VPN tunnel from ECS to lab PCs for NFS access |

## Development

```bash
git clone https://github.com/SissiFeng/lab-data-uploader.git
cd lab-data-uploader
python -m venv .venv && source .venv/bin/activate
pip install -e "./app[dev]"
pytest app/tests/ -q
```

### CLI Commands

```bash
# Validate a config file
uploader-agent validate-config --config app/config.yaml

# Run a single scan cycle (useful for debugging)
uploader-agent scan-once --config app/config.yaml

# Print the manifest that would be generated for a session folder
uploader-agent print-manifest --session /path/to/session --config app/config.yaml
```
