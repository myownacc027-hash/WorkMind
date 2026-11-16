param(
    [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

function Stop-WithMessage {
    param([string]$Message)
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Assert-NativeSuccess {
    param([string]$CommandName)
    if ($LASTEXITCODE -ne 0) {
        Stop-WithMessage "$CommandName failed with exit code $LASTEXITCODE."
    }
}

# WorkMind supports Python 3.11-3.13.
try {
    $pythonVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    Assert-NativeSuccess "Python version check"
    $pythonSupported = python -c "import sys; print('yes' if (3, 11) <= sys.version_info[:2] < (3, 14) else 'no')"
    Assert-NativeSuccess "Python compatibility check"
} catch {
    Stop-WithMessage "Python was not found. Install Python 3.11, 3.12, or 3.13 and reopen PowerShell."
}

if ($pythonSupported.Trim() -ne "yes") {
    Stop-WithMessage "Python $pythonVersion is unsupported. WorkMind requires Python 3.11-3.13."
}
Write-Host "Python $pythonVersion detected." -ForegroundColor Green

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env. The default password is suitable only for local development." -ForegroundColor Yellow
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    Assert-NativeSuccess "Python virtual environment creation"
}

& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
Assert-NativeSuccess "pip upgrade"
python -m pip install -e ".[windows,embeddings,dev]"
Assert-NativeSuccess "WorkMind dependency installation"

if ($SkipDocker) {
    Write-Host "Python environment completed. Docker startup and database migration were skipped." -ForegroundColor Yellow
    exit 0
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "Docker Desktop is not installed or docker.exe is not in PATH." -ForegroundColor Yellow
    Write-Host "Install it with:" -ForegroundColor Yellow
    Write-Host "  winget install -e --id Docker.DockerDesktop" -ForegroundColor Cyan
    Write-Host "Then restart Windows if requested, start Docker Desktop, reopen PowerShell, and run bootstrap.ps1 again." -ForegroundColor Yellow
    exit 2
}

try {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker engine is unavailable."
    }
} catch {
    Write-Host ""
    Write-Host "Docker Desktop is installed but its engine is not running." -ForegroundColor Yellow
    Write-Host "Start Docker Desktop, wait until it reports that the engine is running, and rerun bootstrap.ps1." -ForegroundColor Yellow
    exit 3
}

docker compose up -d
Assert-NativeSuccess "docker compose up"
python -m alembic upgrade head
Assert-NativeSuccess "Alembic migration"

Write-Host ""
Write-Host "WorkMind bootstrap completed." -ForegroundColor Green
Write-Host "Start classic Outlook, wait for folder synchronization, then run:"
Write-Host "  workmind-outlook --lookback-days 7 --max-per-folder 50" -ForegroundColor Cyan
