$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Executable = Join-Path `
    $ProjectRoot `
    ".venv\Scripts\workmind-documents-import.exe"

if (-not (Test-Path $Executable)) {
    exit 10
}

Set-Location $ProjectRoot
& $Executable --apply
exit $LASTEXITCODE
