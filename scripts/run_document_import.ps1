$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
& "$ProjectRoot\.venv\Scripts\workmind-documents-import.exe" --apply
exit $LASTEXITCODE
