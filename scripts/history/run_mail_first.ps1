# WorkMind mail-first historical import runner (Windows PowerShell 5.1).
# Uses the existing DB cursors. Never resets history or clears storage.
# Stops cooperatively between passes when mail-first-stop.request exists.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $BackupRoot,
    [ValidateRange(1, 10000)]
    [int] $BatchMessages = 2000,
    [ValidateRange(1, 100000)]
    [int] $MinFreeGB = 100,
    [switch] $MailOnly
)

$ErrorActionPreference = 'Stop'
$Project = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$ExeDir = Join-Path $Project '.venv\Scripts'
$Python = Join-Path $ExeDir 'python.exe'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'WorkMind virtual environment is missing.'
}
Set-Location $Project
$dataOutput = @(& $Python -c 'from packages.common.config import get_settings; print(get_settings().workmind_home.resolve())')
if ($LASTEXITCODE -ne 0 -or $dataOutput.Count -eq 0) {
    throw 'Could not resolve WORKMIND_HOME from WorkMind settings.'
}
$DataDir = ([string]$dataOutput[-1]).Trim()
if (-not (Test-Path -LiteralPath $DataDir -PathType Container)) {
    throw ('WorkMind data directory does not exist: ' + $DataDir)
}
$RunDir = Join-Path $DataDir 'run'
$LogsDir = Join-Path $DataDir 'logs'
$StopFlag = Join-Path $RunDir 'mail-first-stop.request'
$ResultFile = Join-Path $RunDir 'history-full-result.txt'
$HistoryStatus = Join-Path $RunDir 'history-import-status.json'

New-Item -ItemType Directory -Force -Path $RunDir, $LogsDir | Out-Null
$SessionDir = Join-Path $LogsDir ('history-mail-first-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force -Path $SessionDir | Out-Null
$Summary = Join-Path $SessionDir 'summary.log'
$Mutex = New-Object System.Threading.Mutex($false, 'Local\WorkMindHistoryAutoRunner')
$HasMutex = $false

function Log {
    param([string] $Message)
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $Message
    Write-Host $line
    Add-Content -LiteralPath $Summary -Value $line -Encoding UTF8
}

function Status {
    param([string] $State)
    Set-Content -LiteralPath $ResultFile -Value ($State + ' ' + (Get-Date -Format s) + ' logs=' + $SessionDir) -Encoding UTF8
}

function Check-Stop {
    if (Test-Path -LiteralPath $StopFlag -PathType Leaf) {
        Log ('Stop requested: ' + $StopFlag)
        Status 'PAUSED_BY_REQUEST'
        return $true
    }
    return $false
}

function Check-FreeSpace {
    $drive = [System.IO.Path]::GetPathRoot($DataDir)
    if ($drive -notmatch '^[A-Za-z]:\\$') {
        throw 'History runner currently supports local Windows data drives only.'
    }
    $name = $drive.Substring(0, 1)
    $free = [math]::Round((Get-PSDrive -Name $name).Free / 1GB, 2)
    Log ($name + ': free = ' + $free + ' GB')
    if ($free -lt $MinFreeGB) {
        throw ('Insufficient free disk space. Required: ' + $MinFreeGB + ' GB.')
    }
}

function Check-Services {
    $dockerVersion = & docker info --format '{{.ServerVersion}}'
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$dockerVersion)) {
        throw 'Docker Desktop is offline. Open Docker Desktop first.'
    }
    $volumes = @(& docker volume ls --format '{{.Name}}')
    if ($LASTEXITCODE -ne 0) { throw 'Docker volume enumeration failed.' }
    foreach ($volume in @('workmind_workmind_postgres_data', 'workmind_workmind_qdrant_data')) {
        if ($volumes -notcontains $volume) {
            throw ('Existing WorkMind Docker volume not found: ' + $volume)
        }
    }
    $pg = & docker exec workmind-postgres pg_isready -U workmind -d workmind
    if ($LASTEXITCODE -ne 0) { throw ('PostgreSQL is unavailable: ' + $pg) }
    $qdrant = Invoke-RestMethod -Uri 'http://127.0.0.1:6333/collections/workmind_chunks' -TimeoutSec 10
    if ($qdrant.status -ne 'ok') { throw 'Qdrant is unavailable.' }
    $embed = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 10
    if ($embed.status -ne 'ready') { throw 'Embedding service is not ready.' }
}

function Captured {
    param(
        [string] $Exe,
        [string[]] $CliArgs,
        [string] $Label
    )
    $outFile = Join-Path $SessionDir ($Label + '.stdout.log')
    $errFile = Join-Path $SessionDir ($Label + '.stderr.log')
    $proc = Start-Process -FilePath (Join-Path $ExeDir $Exe) -ArgumentList $CliArgs -WorkingDirectory $Project -PassThru -Wait -NoNewWindow -RedirectStandardOutput $outFile -RedirectStandardError $errFile
    $exitCode = [int]$proc.ExitCode
    Log ($Label + ' exit=' + $exitCode)
    if ($exitCode -ne 0) {
        foreach ($line in @(Get-Content -LiteralPath $errFile -Tail 20 -Encoding UTF8)) {
            Log ('  ' + $line)
        }
    }
    return $exitCode
}

function Retry-Task {
    param(
        [string] $Exe,
        [string[]] $CliArgs,
        [string] $Label
    )
    for ($try = 1; $try -le 6; $try++) {
        Check-Services
        Check-FreeSpace
        $code = Captured -Exe $Exe -CliArgs $CliArgs -Label ($Label + '-try' + $try)
        if ($code -eq 0) { return }
        if ($try -ge 6) { throw ($Label + ' failed after six attempts.') }
        Log ('Retry ' + $Label + ' in 120 seconds.')
        Start-Sleep -Seconds 120
    }
}

function Backup-Checkpoint {
    if (-not (Test-Path -LiteralPath $BackupRoot -PathType Container)) {
        New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
    }
    $started = Get-Date
    Log 'Creating new PostgreSQL + CAS + Qdrant backup.'
    $backupFile = Join-Path $Project 'scripts\backup\backup_workmind.ps1'
    $verifyFile = Join-Path $Project 'scripts\backup\verify_backup.ps1'
    $backupLog = Join-Path $SessionDir 'backup.log'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $backupFile -BackupRoot $BackupRoot *> $backupLog
    if ($LASTEXITCODE -ne 0) { throw ('Backup failed; inspect ' + $backupLog) }
    $candidate = Get-ChildItem -LiteralPath $BackupRoot -Directory -Filter 'WorkMindBackup-*' |
        Where-Object { $_.CreationTime -ge $started.AddMinutes(-1) -and (Test-Path -LiteralPath (Join-Path $_.FullName 'manifest.json')) } |
        Sort-Object CreationTime -Descending | Select-Object -First 1
    if (-not $candidate) { throw 'No new backup with manifest was found.' }
    $verifyLog = Join-Path $SessionDir 'backup-verify.log'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $verifyFile -BackupPath $candidate.FullName -VerifyAllHashes *> $verifyLog
    if ($LASTEXITCODE -ne 0) { throw ('Backup verification failed; inspect ' + $verifyLog) }
    Log ('Verified backup: ' + $candidate.FullName)
}

try {
    $HasMutex = $Mutex.WaitOne(0)
    if (-not $HasMutex) { throw 'Another WorkMind history auto runner is active.' }
    Status 'RUNNING'
    Log ('Mail-first runner started: ' + $SessionDir)
    if (-not @(Get-Process -Name 'OUTLOOK' -ErrorAction SilentlyContinue).Count) {
        throw 'Classic Outlook must be running in this user session.'
    }
    if (Check-Stop) { return }
    Check-Services
    Check-FreeSpace
    Backup-Checkpoint
    Retry-Task -Exe 'workmind-index-admin.exe' -CliArgs @('check') -Label 'initial-index-check'

    $finishedMail = $false
    for ($pass = 1; $pass -le 10000; $pass++) {
        if (Check-Stop) { return }
        Log ('=== MAIL HISTORY PASS ' + $pass + ' ===')
        Retry-Task -Exe 'workmind-history-import.exe' -CliArgs @(
            '--years', '5', '--max-messages', [string]$BatchMessages,
            '--index', '--max-index-batches', '10000'
        ) -Label ('mail-' + $pass)

        $history = Get-Content -LiteralPath $HistoryStatus -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($history.status -notin @('paused', 'completed')) {
            throw ('Unexpected history status: ' + $history.status)
        }
        if ([int]$history.totals.failed -gt 0) { throw 'Mail importer reported failed items.' }
        if ($history.status -eq 'paused' -and [int]$history.totals.seen -eq 0) {
            throw 'Mail importer did not advance.'
        }
        Log ('Mail status=' + $history.status + ' seen=' + $history.totals.seen + ' created=' + $history.totals.created)
        Retry-Task -Exe 'workmind-index-admin.exe' -CliArgs @('check') -Label ('index-' + $pass)
        if ($history.status -eq 'completed') {
            $finishedMail = $true
            break
        }
    }
    if (-not $finishedMail) { throw 'Maximum mail passes reached.' }
    Log 'Mail import completed.'
    Status 'MAIL_COMPLETE_ATTACHMENTS_PENDING'

    if ($MailOnly -or (Check-Stop)) { return }

    Log '=== FINAL ATTACHMENTS ==='
    Status 'MAIL_COMPLETE_ATTACHMENTS_RUNNING'
    Retry-Task -Exe 'workmind-attachments-worker.exe' -CliArgs @(
        '--batch-size', '10', '--until-empty', '--max-batches', '100000'
    ) -Label 'attachments-final'
    Retry-Task -Exe 'workmind-index-admin.exe' -CliArgs @('check') -Label 'index-final'
    $health = & (Join-Path $ExeDir 'workmind-mcp-check.exe') | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $health.ok) {
        throw 'Final WorkMind health check failed.'
    }
    Status 'COMPLETED'
    Log 'All mail and attachments completed.'
}
catch {
    Log ('FAILED: ' + $_.Exception.Message)
    Status 'FAILED'
    exit 1
}
finally {
    if ($HasMutex) { $Mutex.ReleaseMutex() }
    $Mutex.Dispose()
}
