param(
    [int]$IntervalMinutes = 10
)

$ErrorActionPreference = "Stop"

if ($IntervalMinutes -lt 5) {
    throw "IntervalMinutes must be at least 5."
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot "run_scheduled_knowledge_extraction.ps1"
$HiddenRunner = Join-Path $PSScriptRoot "run_scheduled_knowledge_extraction_hidden.vbs"
$TaskName = "WorkMind Knowledge Extraction"

$Action = New-ScheduledTaskAction `
    -Execute "wscript.exe" `
    -Argument "`"$HiddenRunner`" `"$Runner`"" `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Process WorkMind structured-knowledge extraction queue." `
    -Force | Out-Null

Write-Host "Scheduled task installed: $TaskName"
Write-Host "Interval minutes: $IntervalMinutes"
Write-Host "Provider is read from KNOWLEDGE_EXTRACTION_PROVIDER."
Write-Host "provider=agent is processed through MCP and the scheduled worker will skip."
Write-Host "provider=openai requires KNOWLEDGE_EXTRACTION_API_KEY."
