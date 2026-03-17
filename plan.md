Lab Data Uploader Agent Development Plan

1. Goal

Build a reusable local uploader agent that runs on lab PCs, detects when an experiment session has completed, generates a manifest, avoids duplicate uploads, uploads raw data to S3 through a backend upload flow, and records local upload state for retry and auditing.

This agent should be independent from any single instrument control repository so it can be reused across multiple lab workflows and devices.

⸻

2. Scope

In scope
	•	Monitor one or more configured local session root folders
	•	Detect when a session folder is complete using file-stability rules
	•	Generate session manifests and file checksums
	•	Store local state in SQLite
	•	Register uploads with backend API
	•	Upload files to S3 using backend-provided presigned URLs
	•	Detect duplicate session uploads using session_id + manifest_hash
	•	Retry failed uploads safely
	•	Write structured logs
	•	Support configuration-driven deployment on different lab PCs

Out of scope for v1
	•	Streaming uploads during experiment execution
	•	Direct database ingestion from the agent
	•	Dashboard logic or KPI calculation
	•	Parsing experiment-specific file content
	•	Tight coupling to any orchestrator or instrument runtime

⸻

3. Architectural Decision

Repository strategy

Use a standalone repository.

Rationale:
	•	reusable across instruments and workflows
	•	clear separation of responsibilities
	•	independent deployment lifecycle
	•	easier testing and versioning
	•	easier future productization as a general lab uploader component

Recommended repository name:
	•	lab-data-uploader
	•	or session-sync-agent

⸻

4. Functional Requirements

4.1 Session monitoring

The agent must:
	•	watch one or more configured session root directories
	•	discover new session folders
	•	ignore temporary and excluded files
	•	rescan at a configurable interval

4.2 Session completion detection

The agent must support file-based completion detection.

Initial rule set:
	•	no file count changes within a configurable stable window
	•	no total-size changes within a configurable stable window
	•	no file modification within a configurable stable window
	•	optional required marker files such as:
	•	session_summary.json
	•	finished.flag
	•	log-based completion marker in a known log file

4.3 Manifest generation

For each completed session, the agent must generate a canonical manifest containing:
	•	session_id
	•	machine_id
	•	lab_id
	•	session_path
	•	list of files with:
	•	relative path
	•	size
	•	sha256 checksum
	•	modified time
	•	file_count
	•	total_bytes
	•	schema_version

The agent must compute a deterministic manifest_hash from the canonical manifest.

4.4 Local deduplication

The agent must maintain local upload state and avoid re-uploading a session when the same session_id + manifest_hash has already been uploaded successfully.

4.5 Upload registration

The agent must call a backend API to register a candidate session upload before sending files.

Expected outcomes:
	•	upload required
	•	duplicate manifest already known
	•	updated session version required
	•	partial file upload allowed in future versions

4.6 File upload

The agent must upload files through backend-provided presigned URLs.

4.7 Completion callback

After upload, the agent must call a backend completion endpoint with:
	•	session_id
	•	manifest_hash
	•	uploaded file list
	•	total bytes uploaded

4.8 Retry and recovery

The agent must:
	•	retry failed registrations and uploads with backoff
	•	preserve failed sessions for later retry
	•	distinguish upload failure from processing failure on the AWS side

⸻

5. Non-Functional Requirements
	•	Reusable: no coupling to experiment-specific logic
	•	Configurable: path- and profile-driven behavior
	•	Reliable: safe retry, resumable state, no silent drops
	•	Traceable: structured logs and persistent local state
	•	Deployable: easy installation as a service on lab PCs
	•	Extensible: future support for orchestrator signals, partial uploads, multiple session profiles

⸻

6. Recommended Tech Stack

Language

Python 3.11+

Reason:
	•	fast to build
	•	easy deployment in lab environments
	•	strong file-system and HTTP libraries
	•	aligns with existing scientific software ecosystems

Core libraries
	•	pydantic for config and data models
	•	pyyaml for config loading
	•	requests or httpx for backend API calls
	•	hashlib for checksums
	•	sqlite3 or SQLAlchemy for local state
	•	tenacity for retries
	•	watchdog optional, though polling is acceptable for v1
	•	structlog or standard logging with JSON formatter
	•	typer or argparse for CLI

⸻

7. Configuration Model

Use a YAML config file.

Example:

agent:
  machine_id: labpc-01
  lab_id: sdl1
  scan_interval_seconds: 60
  stable_window_seconds: 300
  timezone: America/Toronto

watch:
  session_roots:
    - path: "D:/LabData/BatterySessions"
      profile: battery_session
    - path: "D:/LabData/CameraRuns"
      profile: camera_session

profiles:
  battery_session:
    required_markers:
      - "session_summary.json"
    ignore_patterns:
      - "*.tmp"
      - "*.lock"
    metadata_files:
      - "metadata.json"
      - "run.log"

  camera_session:
    required_markers:
      - "capture_done.flag"
    ignore_patterns:
      - "*.part"
    metadata_files:
      - "capture_meta.json"

upload:
  api_base_url: "https://backend.example.com/api/uploads"
  auth_token_env: "UPLOAD_AGENT_TOKEN"
  request_timeout_seconds: 60
  max_retries: 10
  initial_backoff_seconds: 30

storage:
  local_state_db: "./state/upload_state.db"
  manifest_cache_dir: "./state/manifests"
  log_dir: "./logs"


⸻

8. Local State Design

Use SQLite for v1.

Table: sessions

Columns:
	•	session_id TEXT
	•	session_path TEXT
	•	profile TEXT
	•	manifest_hash TEXT
	•	status TEXT
	•	file_count INTEGER
	•	total_bytes INTEGER
	•	retry_count INTEGER
	•	last_error TEXT
	•	created_at TEXT
	•	updated_at TEXT
	•	uploaded_at TEXT NULL

Suggested statuses
	•	discovered
	•	waiting_for_stable
	•	ready_to_register
	•	duplicate
	•	uploading
	•	uploaded
	•	failed

Optional second table:

Table: uploaded_files

Columns:
	•	session_id
	•	manifest_hash
	•	relative_path
	•	sha256
	•	size
	•	upload_status

⸻

9. Session Identity Rules

Session ID

The agent needs a deterministic way to identify a session.

Priority order:
	1.	read from known metadata file if present
	2.	read from folder naming convention
	3.	fallback to folder name

Manifest hash

Compute SHA256 of a canonical serialized manifest where:
	•	files are sorted by relative path
	•	timestamps use a consistent format
	•	excluded files are not included

This ensures duplicate detection is stable across rescans.

⸻

10. Upload Flow

Step 1. Scan

Periodically scan configured roots.

Step 2. Detect completion

Check whether session folder is stable and markers are present.

Step 3. Build manifest

Create manifest and compute manifest_hash.

Step 4. Local dedup check

Skip if identical session version is already uploaded locally.

Step 5. Register with backend

POST /register-session

Payload example:

{
  "session_id": "session_ABC123",
  "machine_id": "labpc-01",
  "lab_id": "sdl1",
  "manifest_hash": "abc123",
  "file_count": 18,
  "total_bytes": 91238123,
  "schema_version": "1.0"
}

Step 6. Upload files

Use returned presigned URLs.

Step 7. Complete upload

POST /complete-session

Step 8. Persist success

Mark local state as uploaded.

⸻

11. Error Handling Strategy

Network/API failure
	•	retry with exponential backoff
	•	leave local status as failed after retry limit
	•	do not delete any source files

Interrupted upload
	•	mark upload as failed
	•	retry on next cycle
	•	design for future multipart upload support

Session changes after upload

If a session folder changes and generates a different manifest_hash, treat it as a new version and re-register with backend.

Corrupt manifest or unreadable file
	•	log specific file path
	•	mark session failed
	•	continue processing other sessions

⸻

12. Logging Requirements

Use structured logs.

Every major action should include:
	•	timestamp
	•	machine_id
	•	session_id
	•	profile
	•	event type
	•	status
	•	error details if any

Key log events:
	•	session_discovered
	•	session_stable
	•	manifest_created
	•	register_started
	•	register_duplicate
	•	upload_started
	•	upload_file_succeeded
	•	upload_completed
	•	session_failed

⸻

13. Code Structure Requirements

Recommended structure:

lab-data-uploader/
  README.md
  plan.md
  pyproject.toml
  .env.example
  configs/
    example.config.yaml
  scripts/
    install_service.ps1
    install_service.sh
    run_local.py
  src/
    agent/
      __init__.py
      main.py
      cli.py
      config.py
      models.py
      scanner.py
      completion_detector.py
      manifest.py
      dedup.py
      state_db.py
      api_client.py
      uploader.py
      retry.py
      logging_utils.py
      scheduler.py
  tests/
    test_config.py
    test_completion_detector.py
    test_manifest.py
    test_dedup.py
    test_state_db.py
    test_api_client.py
    fixtures/
      sample_sessions/

Module responsibilities
	•	main.py: service entrypoint
	•	cli.py: commands such as run, scan-once, validate-config
	•	config.py: load and validate YAML config
	•	models.py: Pydantic models for config and manifest
	•	scanner.py: enumerate candidate session folders
	•	completion_detector.py: stable-window and marker checks
	•	manifest.py: generate manifest and manifest hash
	•	dedup.py: local duplicate checks
	•	state_db.py: SQLite access layer
	•	api_client.py: backend API interactions
	•	uploader.py: presigned upload logic
	•	retry.py: retry wrappers and backoff policies
	•	logging_utils.py: structured logger setup
	•	scheduler.py: polling loop orchestration

⸻

14. Coding Standards
	•	use type hints throughout
	•	keep functions small and single-purpose
	•	avoid embedding instrument-specific assumptions
	•	all file filtering must be config-driven
	•	no hardcoded paths in source code
	•	all external calls must have timeout and retry policy
	•	all state transitions must be explicit and logged
	•	use deterministic manifest serialization
	•	prefer pure functions for hashing and manifest generation

⸻

15. API Contract Assumptions

The backend should expose at least:

POST /register-session

Purpose:
	•	determine whether upload is needed
	•	return upload strategy and presigned URLs

POST /complete-session

Purpose:
	•	confirm file upload completion
	•	trigger downstream ingestion asynchronously

Potential future endpoints:
	•	POST /heartbeat
	•	POST /reconcile-session
	•	GET /health

⸻

16. Testing Plan

Unit tests
	•	config parsing
	•	file stability detection
	•	marker detection
	•	manifest generation and canonical hashing
	•	dedup rules
	•	SQLite state transitions
	•	backend payload generation

Integration tests
	•	simulate session folder completion
	•	simulate duplicate upload
	•	simulate failed upload then retry
	•	simulate session content change and versioned re-upload

Manual test scenarios
	•	empty root folder
	•	temp files that should be ignored
	•	session completed normally
	•	session modified after first upload
	•	backend unavailable
	•	network timeout during upload

⸻

17. Deployment Plan

v1 deployment mode

Run as a background service on a lab PC.

Options:
	•	Windows service via wrapper script or NSSM
	•	Linux systemd service if applicable

Required deployment artifacts
	•	packaged Python environment
	•	config file per machine
	•	secret token as environment variable
	•	writable local directory for logs and SQLite DB

⸻

18. CLI Requirements

Support at least:

uploader-agent run --config configs/example.config.yaml
uploader-agent scan-once --config configs/example.config.yaml
uploader-agent validate-config --config configs/example.config.yaml
uploader-agent print-manifest --session <path> --config configs/example.config.yaml


⸻

19. Milestone Plan

Milestone 1: skeleton
	•	initialize repo
	•	config loader
	•	CLI scaffold
	•	logger setup
	•	state DB schema

Milestone 2: local scan and completion detection
	•	session root scanning
	•	stable-window logic
	•	marker-file logic
	•	sample test fixtures

Milestone 3: manifest and dedup
	•	manifest generation
	•	manifest hashing
	•	SQLite persistence
	•	duplicate handling

Milestone 4: backend registration and upload
	•	register-session client
	•	presigned file upload
	•	complete-session client
	•	retry policy

Milestone 5: hardening
	•	structured error handling
	•	integration tests
	•	service install scripts
	•	README and operations guide

Milestone 6: pilot deployment
	•	deploy on one lab PC
	•	test with real session folders
	•	tune stable window and ignore rules
	•	collect operational feedback

⸻

20. Future Extensions
	•	orchestrator-driven completion signal
	•	multipart uploads for very large files
	•	file-level delta upload
	•	Prometheus metrics
	•	health endpoint
	•	admin reconciliation command
	•	support for plugin-based session profiles
	•	packaged desktop installer for lab deployment

⸻

21. Definition of Done for v1

v1 is done when:
	•	the agent runs as a background service on a lab PC
	•	it detects completed session folders from configured roots
	•	it generates manifests deterministically
	•	it avoids duplicate uploads locally
	•	it registers uploads with backend successfully
	•	it uploads files to S3 through presigned URLs
	•	it records success/failure state in SQLite
	•	it retries failures safely
	•	it produces logs sufficient for debugging
	•	it can be reused on another machine by changing only config and credentials