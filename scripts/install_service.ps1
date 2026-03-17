#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install or uninstall the uploader-agent as a Windows service using NSSM.

.DESCRIPTION
    Uses NSSM (Non-Sucking Service Manager) to manage the uploader-agent
    as a Windows service. NSSM must be installed and available on PATH.

.PARAMETER Action
    install   - Install and start the service
    uninstall - Stop and remove the service

.PARAMETER Config
    Path to config YAML file (default: configs\example.config.yaml)

.PARAMETER WorkDir
    Working directory for the service (default: current directory)

.PARAMETER PythonPath
    Path to python executable (default: auto-detected)

.EXAMPLE
    .\install_service.ps1 install -Config "C:\agent\configs\my.config.yaml"
    .\install_service.ps1 uninstall
#>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("install", "uninstall")]
    [string]$Action,

    [string]$Config = "configs\example.config.yaml",

    [string]$WorkDir = (Get-Location).Path,

    [string]$PythonPath = ""
)

$ServiceName = "uploader-agent"
$ErrorActionPreference = "Stop"

function Find-Nssm {
    $nssm = Get-Command nssm -ErrorAction SilentlyContinue
    if (-not $nssm) {
        Write-Error "NSSM not found. Install from https://nssm.cc/ and add to PATH."
        exit 1
    }
    return $nssm.Source
}

function Find-Python {
    if ($PythonPath -and (Test-Path $PythonPath)) {
        return $PythonPath
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    $py3 = Get-Command python3 -ErrorAction SilentlyContinue
    if ($py3) { return $py3.Source }
    Write-Error "Python not found. Install Python 3.11+ or pass -PythonPath."
    exit 1
}

function Install-AgentService {
    $nssm = Find-Nssm
    $python = Find-Python

    # Resolve config to absolute path if relative
    if (-not [System.IO.Path]::IsPathRooted($Config)) {
        $Config = Join-Path $WorkDir $Config
    }

    if (-not (Test-Path $Config)) {
        Write-Error "Config file not found: $Config"
        exit 1
    }

    Write-Host "Installing $ServiceName Windows service..."
    Write-Host "  Config:  $Config"
    Write-Host "  WorkDir: $WorkDir"
    Write-Host "  Python:  $python"

    # Install service
    & $nssm install $ServiceName $python "-m" "agent.cli" "run" "--config" $Config

    # Configure working directory
    & $nssm set $ServiceName AppDirectory $WorkDir

    # Configure logging
    $logDir = Join-Path $WorkDir "logs"
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    & $nssm set $ServiceName AppStdout (Join-Path $logDir "service_stdout.log")
    & $nssm set $ServiceName AppStderr (Join-Path $logDir "service_stderr.log")
    & $nssm set $ServiceName AppRotateFiles 1
    & $nssm set $ServiceName AppRotateBytes 10485760

    # Configure restart on failure
    & $nssm set $ServiceName AppExit Default Restart
    & $nssm set $ServiceName AppRestartDelay 30000

    # Set description
    & $nssm set $ServiceName Description "Lab Data Uploader Agent"
    & $nssm set $ServiceName DisplayName "Lab Data Uploader Agent"

    # Start the service
    & $nssm start $ServiceName

    Write-Host ""
    Write-Host "$ServiceName installed and started."
    Write-Host "  Status:  nssm status $ServiceName"
    Write-Host "  Logs:    $logDir"
    Write-Host "  Stop:    nssm stop $ServiceName"
    Write-Host "  Restart: nssm restart $ServiceName"
}

function Uninstall-AgentService {
    $nssm = Find-Nssm

    Write-Host "Uninstalling $ServiceName Windows service..."

    # Stop if running
    $status = & $nssm status $ServiceName 2>&1
    if ($status -match "SERVICE_RUNNING") {
        & $nssm stop $ServiceName
        Write-Host "  Stopped $ServiceName"
    }

    # Remove service
    & $nssm remove $ServiceName confirm
    Write-Host "$ServiceName uninstalled."
}

switch ($Action) {
    "install"   { Install-AgentService }
    "uninstall" { Uninstall-AgentService }
}
