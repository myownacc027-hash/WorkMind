$ErrorActionPreference = "Stop"
$TaskName = "WorkMind Knowledge Extraction"
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Scheduled task removed: $TaskName"
