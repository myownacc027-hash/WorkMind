$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Executable = Join-Path `
    $ProjectRoot `
    ".venv\Scripts\workmind-sync-now.exe"

if (-not (Test-Path $Executable)) {
    exit 10
}

Set-Location $ProjectRoot

& $Executable `
    --lookback-days 7 `
    --max-per-folder 500 `
    --overlap-minutes 30 `
    --reconcile-days 14 `
    --reconcile-interval-hours 24 `
    --reconcile-stale-hours 36 `
    --reconcile-max-per-folder 0 `
    --batch-size 100 `
    --max-batches 50

exit $LASTEXITCODE
