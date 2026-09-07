param(
    [string]$TaskName = "WorkMind Document Import",
    [int]$IntervalMinutes = 10
)

$ErrorActionPreference = "Stop"

if ($IntervalMinutes -lt 5) {
    throw "IntervalMinutes must be at least 5."
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path `
    $PSScriptRoot `
    "run_scheduled_document_import.ps1"
$HiddenRunner = Join-Path `
    $PSScriptRoot `
    "run_scheduled_document_import_hidden.vbs"
$Executable = Join-Path `
    $ProjectRoot `
    ".venv\Scripts\workmind-documents-import.exe"

foreach ($Path in @($Runner, $HiddenRunner, $Executable)) {
    if (-not (Test-Path $Path)) {
        throw "Required file not found: $Path"
    }
}

$Wscript = Join-Path `
    $env:SystemRoot `
    "System32\wscript.exe"

$Action = New-ScheduledTaskAction `
    -Execute $Wscript `
    -Argument ('"' + $HiddenRunner + '"') `
    -WorkingDirectory $ProjectRoot

$LogonTrigger = New-ScheduledTaskTrigger `
    -AtLogOn `
    -User $env:USERNAME

$RecurringTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$UserId = "$env:USERDOMAIN\$env:USERNAME"

$Principal = New-ScheduledTaskPrincipal `
    -UserId $UserId `
    -LogonType Interactive `
    -RunLevel Limited

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger @($LogonTrigger, $RecurringTrigger) `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Import standalone documents into WorkMind." `
    -Force | Out-Null

Write-Host "Scheduled task installed: $TaskName"
Write-Host "Interval minutes: $IntervalMinutes"
Write-Host "Import folder: C:\AIKnowledge\import"
Write-Host "The task runs without a visible console window."
