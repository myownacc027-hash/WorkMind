param(
    [string]$TaskName = "WorkMind Mail Sync"
)

$ErrorActionPreference = "Stop"

$Task = Get-ScheduledTask `
    -TaskName $TaskName `
    -ErrorAction SilentlyContinue

if ($null -eq $Task) {
    Write-Host "Task not found: $TaskName"
    exit 0
}

Unregister-ScheduledTask `
    -TaskName $TaskName `
    -Confirm:$false

Write-Host "Scheduled task removed: $TaskName"
