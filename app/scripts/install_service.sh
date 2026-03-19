#!/usr/bin/env bash
# install_service.sh - Install/uninstall the uploader-agent as a systemd service on Linux.
set -euo pipefail

SERVICE_NAME="uploader-agent"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

usage() {
    echo "Usage: $0 {install|uninstall} [OPTIONS]"
    echo ""
    echo "Commands:"
    echo "  install    Install and start the systemd service"
    echo "  uninstall  Stop and remove the systemd service"
    echo ""
    echo "Install options:"
    echo "  --config PATH    Path to config YAML (default: configs/example.config.yaml)"
    echo "  --user USER      User to run the service as (default: current user)"
    echo "  --workdir DIR    Working directory (default: current directory)"
    echo "  --venv DIR       Path to virtualenv (optional)"
    exit 1
}

install_service() {
    local config="configs/example.config.yaml"
    local user
    user="$(whoami)"
    local workdir
    workdir="$(pwd)"
    local venv=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --config)  config="$2"; shift 2 ;;
            --user)    user="$2"; shift 2 ;;
            --workdir) workdir="$2"; shift 2 ;;
            --venv)    venv="$2"; shift 2 ;;
            *) echo "Unknown option: $1"; usage ;;
        esac
    done

    # Resolve absolute path for config if relative
    if [[ "$config" != /* ]]; then
        config="${workdir}/${config}"
    fi

    # Determine python executable
    local python_exec
    if [[ -n "$venv" ]]; then
        python_exec="${venv}/bin/python"
    else
        python_exec="$(command -v python3)"
    fi

    if [[ ! -f "$config" ]]; then
        echo "Error: config file not found: $config"
        exit 1
    fi

    echo "Installing ${SERVICE_NAME} systemd service..."
    echo "  Config:    $config"
    echo "  User:      $user"
    echo "  WorkDir:   $workdir"
    echo "  Python:    $python_exec"

    cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Lab Data Uploader Agent
After=network.target

[Service]
Type=simple
User=${user}
WorkingDirectory=${workdir}
ExecStart=${python_exec} -m agent.cli run --config ${config}
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl start "$SERVICE_NAME"

    echo ""
    echo "${SERVICE_NAME} installed and started."
    echo "  Status:  systemctl status ${SERVICE_NAME}"
    echo "  Logs:    journalctl -u ${SERVICE_NAME} -f"
    echo "  Stop:    systemctl stop ${SERVICE_NAME}"
    echo "  Restart: systemctl restart ${SERVICE_NAME}"
}

uninstall_service() {
    echo "Uninstalling ${SERVICE_NAME} systemd service..."

    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl stop "$SERVICE_NAME"
        echo "  Stopped ${SERVICE_NAME}"
    fi

    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl disable "$SERVICE_NAME"
        echo "  Disabled ${SERVICE_NAME}"
    fi

    if [[ -f "$UNIT_FILE" ]]; then
        rm "$UNIT_FILE"
        systemctl daemon-reload
        echo "  Removed unit file"
    fi

    echo "${SERVICE_NAME} uninstalled."
}

if [[ $# -lt 1 ]]; then
    usage
fi

command="$1"
shift

case "$command" in
    install)   install_service "$@" ;;
    uninstall) uninstall_service ;;
    *)         usage ;;
esac
