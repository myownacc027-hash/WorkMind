<#
.SYNOPSIS
    Read-only verification of a WorkMind backup.

.DESCRIPTION
    Checks a completed backup without modifying any data:
      - manifest.json structure
      - PostgreSQL dump exists and SHA-256 matches manifest
      - CAS: file count and total bytes match manifest
      - CAS: every filename is a valid SHA-256
      - Qdrant snapshot SHA-256 (if snapshot was saved)
      - Metadata files present
      - No .partial marker

    Exit 0 = BACKUP VERIFY: PASS
    Exit 1 = BACKUP VERIFY: FAIL

.PARAMETER BackupPath
    Full path to the backup directory to verify. Required.

.PARAMETER VerifyAllHashes
    Compute SHA-256 of every CAS file and compare to filename.
    This is thorough but slow for large CAS (~4 GB = ~5-10 min).

.EXAMPLE
    .\verify_backup.ps1 -BackupPath "C:\AIKnowledge\workmind-backups\WorkMindBackup-20260908-120000"
    .\verify_backup.ps1 -BackupPath "...\WorkMindBackup-..." -VerifyAllHashes
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $BackupPath,

    [switch] $VerifyAllHashes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir   = $PSScriptRoot
$ProjectRoot = (Resolve-Path "$ScriptDir\..\.." ).Path
$Venv        = "$ProjectRoot\.venv\Scripts\python.exe"
$Engine      = "$ScriptDir\_verify_engine.py"

if (-not (Test-Path $Venv)) {
    Write-Error "Python venv not found at: $Venv"
    exit 1
}
if (-not (Test-Path $Engine)) {
    Write-Error "Verify engine not found at: $Engine"
    exit 1
}
if (-not (Test-Path $BackupPath)) {
    Write-Error "Backup path not found: $BackupPath"
    exit 1
}

$EngineArgs = @("--backup-path", $BackupPath)
if ($VerifyAllHashes) { $EngineArgs += "--verify-all-hashes" }

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host " WorkMind Backup Verification" -ForegroundColor Cyan
Write-Host " Path: $BackupPath" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

& $Venv $Engine @EngineArgs
$ExitCode = $LASTEXITCODE

exit $ExitCode
