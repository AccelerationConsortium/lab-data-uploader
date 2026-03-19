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
├── .github/workflows/     # CI/CD
│   └── deploy.yml
├── app/                   # Application (Docker context)
│   ├── Dockerfile
│   ├── entrypoint.sh
│   ├── main.py            # Entry point
│   ├── requirements.txt
│   ├── config.yaml        # ECS runtime config
│   └── agent/             # Python package
├── platform/              # Infrastructure
│   ├── platform           # Module resolver script
│   ├── platform.yml
│   └── vars/
│       ├── dev.tfvars
│       ├── test.tfvars
│       └── prod.tfvars
├── .gitignore
└── README.md
```

## Development

```bash
git clone https://github.com/SissiFeng/lab-data-uploader.git
cd lab-data-uploader
python -m venv .venv && source .venv/bin/activate
pip install -e "./app[dev]"
pytest app/tests/ -q
```
