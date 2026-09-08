<#
.SYNOPSIS
    Restore WorkMind from a backup (PostgreSQL + CAS).

.DESCRIPTION
    Restores PostgreSQL database and CAS objects from a backup created by
    backup_workmind.ps1. By default runs in SAFE TEST mode:

      - Restores to a temporary database: workmind_restore_test_YYYYMMDD
      - Restores CAS to: C:\AIKnowledge\restore-test-YYYYMMDD\objects
      - Verifies counts, Alembic revision, and CAS spot-checks
      - Automatically cleans up test resources after verification

    To restore to production, you MUST explicitly pass:
      -ConfirmDestructiveRestore
      -TargetDatabase workmind

    WARNING: Production restore DESTROYS the existing database. There is no undo.

.PARAMETER BackupPath
    Full path to the backup directory (e.g. C:\...\WorkMindBackup-20260908-120000).
    Required.

.PARAMETER TargetDatabase
    PostgreSQL database name to restore into.
    Default (safe): workmind_restore_test_YYYYMMDD

.PARAMETER TargetWorkmindHome
    Root directory for the restored CAS objects.
    Default (safe): C:\AIKnowledge\restore-test-YYYYMMDD

.PARAMETER ConfirmDestructiveRestore
    REQUIRED if TargetDatabase is a production database name ('workmind').
    Without this flag, any attempt to restore to the production DB is blocked.

.PARAMETER DryRun
    Show what would be done without making any changes.

.PARAMETER SkipCleanup
    Do not delete test database and CAS directory after successful test restore.

.EXAMPLE
    # Safe test restore (default)
    .\restore_workmind.ps1 -BackupPath "C:\AIKnowledge\workmind-backups\WorkMindBackup-20260908-120000"

    # Keep test resources for manual inspection
    .\restore_workmind.ps1 -BackupPath "...\WorkMindBackup-..." -SkipCleanup

    # Dry run
    .\restore_workmind.ps1 -BackupPath "...\WorkMindBackup-..." -DryRun

    # PRODUCTION RESTORE (destructive!)
    .\restore_workmind.ps1 `
        -BackupPath "...\WorkMindBackup-20260908-120000" `
        -TargetDatabase "workmind" `
        -TargetWorkmindHome "C:\AIKnowledge" `
        -ConfirmDestructiveRestore
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $BackupPath,

    [string] $TargetDatabase = "",

    [string] $TargetWorkmindHome = "",

    [switch] $ConfirmDestructiveRestore,

    [switch] $DryRun,

    [switch] $SkipCleanup,

    [switch] $NoVerifyHashes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir   = $PSScriptRoot
$ProjectRoot = (Resolve-Path "$ScriptDir\..\.." ).Path
$Venv        = "$ProjectRoot\.venv\Scripts\python.exe"
$Engine      = "$ScriptDir\_restore_engine.py"

if (-not (Test-Path $Venv)) {
    Write-Error "Python venv not found at: $Venv"
    exit 1
}
if (-not (Test-Path $Engine)) {
    Write-Error "Restore engine not found at: $Engine"
    exit 1
}
if (-not (Test-Path $BackupPath)) {
    Write-Error "Backup path not found: $BackupPath"
    exit 1
}

# ── Safety gate ──────────────────────────────────────────────────────────
$PRODUCTION_DB = "workmind"
if ($TargetDatabase -eq $PRODUCTION_DB -and -not $ConfirmDestructiveRestore) {
    Write-Host ""
    Write-Host "╔══════════════════════════════════════════════════════╗" -ForegroundColor Red
    Write-Host "║  SAFETY BLOCK: PRODUCTION RESTORE REQUIRES           ║" -ForegroundColor Red
    Write-Host "║  -ConfirmDestructiveRestore                          ║" -ForegroundColor Red
    Write-Host "║                                                      ║" -ForegroundColor Red
    Write-Host "║  This will DESTROY the current 'workmind' database.  ║" -ForegroundColor Red
    Write-Host "║  There is no undo. Make sure you have a valid backup ║" -ForegroundColor Red
    Write-Host "║  and have stopped all WorkMind services first.       ║" -ForegroundColor Red
    Write-Host "╚══════════════════════════════════════════════════════╝" -ForegroundColor Red
    Write-Host ""
    exit 1
}

# ── Build args ───────────────────────────────────────────────────────────
$EngineArgs = @("--backup-path", $BackupPath)

if ($TargetDatabase)        { $EngineArgs += @("--target-database", $TargetDatabase) }
if ($TargetWorkmindHome) {
    $CasRoot = "$TargetWorkmindHome\archive\objects"
    $EngineArgs += @("--target-cas-root", $CasRoot)
}
if ($ConfirmDestructiveRestore) { $EngineArgs += "--confirm-destructive-restore" }
if ($DryRun)                    { $EngineArgs += "--dry-run" }
if ($SkipCleanup)               { $EngineArgs += "--skip-cleanup" }
if ($NoVerifyHashes)            { $EngineArgs += "--no-verify-hashes" }

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host " WorkMind Restore" -ForegroundColor Cyan
Write-Host " Backup   : $BackupPath" -ForegroundColor Cyan
if ($TargetDatabase) { Write-Host " Target DB: $TargetDatabase" -ForegroundColor Cyan }
if ($DryRun)         { Write-Host " Mode     : DRY RUN" -ForegroundColor Yellow }
if ($ConfirmDestructiveRestore) {
    Write-Host " Mode     : DESTRUCTIVE PRODUCTION RESTORE" -ForegroundColor Red
}
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

& $Venv $Engine @EngineArgs
$ExitCode = $LASTEXITCODE

Write-Host ""
if ($ExitCode -eq 0) {
    Write-Host "RESTORE SUCCEEDED" -ForegroundColor Green
} else {
    Write-Host "RESTORE FAILED (exit $ExitCode)" -ForegroundColor Red
}

exit $ExitCode
