<#
.SYNOPSIS
    Back up WorkMind (PostgreSQL + CAS + Qdrant snapshot).

.DESCRIPTION
    Creates a self-contained, timestamped backup directory under BackupRoot.
    Disables the WorkMind Mail Sync scheduled task while backup runs,
    and re-enables it unconditionally when done.

    The backup includes:
      - PostgreSQL dump (pg_dump -Fc) with SHA-256
      - CAS objects (C:\AIKnowledge\archive\objects) with SHA-256 verification
      - Qdrant collection snapshot (if API is available)
      - manifest.json describing the backup

    IMPORTANT: If BackupRoot is on the same drive as WorkMind data (C:),
    this is a LOCAL RECOVERY COPY — not protection against physical disk failure.
    For disaster recovery, point BackupRoot to an external drive or NAS:
        -BackupRoot "D:\WorkMindBackups"
        -BackupRoot "\\NAS\Backups\WorkMind"

.PARAMETER BackupRoot
    Parent directory where the backup folder will be created.
    Default: C:\AIKnowledge\workmind-backups

.PARAMETER NoVerifyCas
    Skip SHA-256 hash verification of CAS files after copy (faster, less safe).

.PARAMETER SkipQdrant
    Skip Qdrant snapshot (use if Qdrant is down).

.EXAMPLE
    .\backup_workmind.ps1 -BackupRoot "D:\WorkMindBackups"
    .\backup_workmind.ps1 -BackupRoot "C:\AIKnowledge\workmind-backups"
    .\backup_workmind.ps1 -BackupRoot "C:\AIKnowledge\workmind-backups" -SkipQdrant
#>

[CmdletBinding()]
param(
    [string] $BackupRoot = "C:\AIKnowledge\workmind-backups",
    [switch] $NoVerifyCas,
    [switch] $SkipQdrant
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Locate project root (this script lives in scripts\backup\)
$ScriptDir   = $PSScriptRoot
$ProjectRoot = (Resolve-Path "$ScriptDir\..\.." ).Path
$Venv        = "$ProjectRoot\.venv\Scripts\python.exe"
$Engine      = "$ScriptDir\_backup_engine.py"

if (-not (Test-Path $Venv)) {
    Write-Error "Python venv not found at: $Venv"
    exit 1
}
if (-not (Test-Path $Engine)) {
    Write-Error "Backup engine not found at: $Engine"
    exit 1
}

# Warn if backup is on the same drive as WorkMind data
$BackupDrive = (Split-Path -Qualifier $BackupRoot).TrimEnd(':').ToUpper()
if ($BackupDrive -eq "C") {
    Write-Warning ""
    Write-Warning "*** BackupRoot is on drive C: ***"
    Write-Warning "This is a LOCAL RECOVERY COPY only."
    Write-Warning "It does NOT protect against physical failure of drive C:."
    Write-Warning "For disaster recovery, use an external drive:"
    Write-Warning "    -BackupRoot D:\WorkMindBackups"
    Write-Warning ""
}

# Build argument list
$EngineArgs = @("--backup-root", $BackupRoot)
if ($NoVerifyCas)  { $EngineArgs += "--no-verify-cas" }
if ($SkipQdrant)   { $EngineArgs += "--skip-qdrant" }

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host " WorkMind Backup" -ForegroundColor Cyan
Write-Host " BackupRoot : $BackupRoot" -ForegroundColor Cyan
Write-Host " Project    : $ProjectRoot" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

# Run engine
& $Venv $Engine @EngineArgs
$ExitCode = $LASTEXITCODE

Write-Host ""
if ($ExitCode -eq 0) {
    Write-Host "BACKUP SUCCEEDED" -ForegroundColor Green
} else {
    Write-Host "BACKUP FAILED (exit $ExitCode)" -ForegroundColor Red
}

exit $ExitCode
