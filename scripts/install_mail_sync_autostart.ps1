param(
    [string]$TaskName = "WorkMind Mail Sync",
    [int]$IntervalMinutes = 15
)

$ErrorActionPreference = "Stop"

if ($IntervalMinutes -lt 5) {
    throw "IntervalMinutes must be at least 5."
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path `
    $PSScriptRoot `
    "run_scheduled_mail_sync.ps1"
$Executable = Join-Path `
    $ProjectRoot `
    ".venv\Scripts\workmind-sync-now.exe"

if (-not (Test-Path $Executable)) {
    throw "Executable not found: $Executable"
}

if (-not (Test-Path $Runner)) {
    throw "Runner not found: $Runner"
}

$Wscript = Join-Path `
    $env:SystemRoot `
    "System32\wscript.exe"

$HiddenRunner = Join-Path `
    $PSScriptRoot `
    "run_scheduled_mail_sync_hidden.vbs"

if (-not (Test-Path $HiddenRunner)) {
    throw "Hidden runner not found: $HiddenRunner"
}

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
    -Description "Synchronize Outlook mail, reconcile late messages daily, and update WorkMind indexes." `
    -Force | Out-Null

Write-Host "Scheduled task installed: $TaskName"
Write-Host "Interval minutes: $IntervalMinutes"
Write-Host "The task runs without a visible console window."
Write-Host "The task skips safely when Outlook is not running."


Write-Host "Daily mail reconciliation: enabled"
Write-Host "Reconciliation window: 14 days"
Write-Host "Reconciliation interval: 24 hours"
