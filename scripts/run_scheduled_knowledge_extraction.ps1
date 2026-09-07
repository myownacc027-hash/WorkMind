$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Executable = Join-Path `
    $ProjectRoot `
    ".venv\Scripts\workmind-knowledge-extract-now.exe"

if (-not (Test-Path $Executable)) {
    exit 10
}

Set-Location $ProjectRoot

& $Executable `
    --max-jobs 3

exit $LASTEXITCODE
