param(
    [string]$TaskName = "WorkMind Embedding Warmup"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Executable = Join-Path `
    $ProjectRoot `
    ".venv\Scripts\workmind-embedding-start.exe"

if (-not (Test-Path $Executable)) {
    throw "Executable not found: $Executable"
}

$Action = New-ScheduledTaskAction `
    -Execute $Executable `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger `
    -AtLogOn `
    -User $env:USERNAME

$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Warm up the WorkMind embedding model at Windows logon." `
    -Force | Out-Null

Write-Host "Scheduled task installed: $TaskName"
Write-Host "Executable: $Executable"
