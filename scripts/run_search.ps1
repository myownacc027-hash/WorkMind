param([Parameter(Mandatory=$true)][string]$Query, [int]$Limit = 10)
$ErrorActionPreference = "Stop"
& "$PSScriptRoot\..\.venv\Scripts\workmind-search.exe" $Query --limit $Limit
