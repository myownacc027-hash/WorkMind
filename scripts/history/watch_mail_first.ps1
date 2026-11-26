# Read-only monitor for a running mail-first history import (Windows PS 5.1).
# Can run outside the checkout. Does not acquire Outlook/runner locks.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string] $DataDir,
    [ValidateRange(5, 300)]
    [int] $RefreshSeconds = 15
)

$ErrorActionPreference = 'Stop'
$runDir = Join-Path $DataDir 'run'
$resultFile = Join-Path $runDir 'history-full-result.txt'
$statusFile = Join-Path $runDir 'history-import-status.json'
$samples = @{}
$currentSession = ''

function Read-RunnerResult {
    if (-not (Test-Path -LiteralPath $resultFile -PathType Leaf)) { return $null }
    $raw = (Get-Content -LiteralPath $resultFile -Raw -Encoding UTF8).Trim()
    if ($raw -match '^(\S+)\s+\S+\s+logs=(.+)$') {
        return [pscustomobject]@{ State = $Matches[1]; Directory = $Matches[2] }
    }
    return $null
}

function Read-HistoryStatus {
    try {
        return (Get-Content -LiteralPath $statusFile -Raw -Encoding UTF8 | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Read-Snapshot {
    # One consistent read-only snapshot. Never poll Outlook or modify jobs.
    $sql = @'
BEGIN READ ONLY;
SET LOCAL statement_timeout = '3s';
SET LOCAL lock_timeout = '1s';
SELECT json_build_object(
  'folders', COALESCE((
    SELECT json_agg(json_build_object(
      'id', id, 'mailbox_id', mailbox_id, 'store', store_display_name,
      'folder', folder_path, 'status', status, 'seen', seen_count,
      'created', created_count, 'failed', failed_count,
      'has_error', last_error IS NOT NULL,
      'cursor', extract(epoch from cursor_timestamp)::bigint,
      'cutoff', extract(epoch from oldest_allowed_at)::bigint,
      'updated', extract(epoch from updated_at)::bigint
    )) FROM history_import_states
  ), '[]'::json),
  'queue', (
    SELECT json_build_object(
      'pending', count(*) FILTER (WHERE status = 'pending'),
      'delayed', count(*) FILTER (WHERE status = 'pending' AND available_at > now()),
      'processing', count(*) FILTER (WHERE status = 'processing'),
      'other', count(*) FILTER (WHERE status NOT IN ('pending', 'processing'))
    ) FROM processing_jobs
      WHERE job_type = 'index_email' AND source_type = 'email'
      AND status <> 'completed'
  )
);
COMMIT;
'@
    $args = @('exec', '-e', 'PGCLIENTENCODING=UTF8', 'workmind-postgres',
        'psql', '-X', '-q', '-U', 'workmind', '-d', 'workmind',
        '-v', 'ON_ERROR_STOP=1', '-t', '-A', '-c', $sql)
    $oldEncoding = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        $raw = @(& docker @args 2>$null)
        if ($LASTEXITCODE -ne 0) { return $null }
        return (($raw -join [Environment]::NewLine) | ConvertFrom-Json)
    } catch {
        return $null
    } finally {
        [Console]::OutputEncoding = $oldEncoding
    }
}

function Read-Events {
    param([string] $Path)
    foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
        if ($line -notmatch '^\{.*"event"\s*:') { continue }
        try {
            $line | ConvertFrom-Json -ErrorAction Stop
        } catch {
            # A line can be incomplete while the importer is writing it.
        }
    }
}

function Format-Duration {
    param([double] $Seconds)
    if ($Seconds -lt 0 -or [double]::IsNaN($Seconds) -or [double]::IsInfinity($Seconds)) {
        return 'unavailable'
    }
    if ($Seconds -lt 60) { return ('{0:N0} sec' -f $Seconds) }
    if ($Seconds -lt 3600) { return ('{0:N1} min' -f ($Seconds / 60)) }
    return ('{0:N1} h' -f ($Seconds / 3600))
}

function Show-Stage {
    param([long] $Done, [long] $Remaining, [double] $Elapsed,
          [bool] $CanEstimate)
    $total = $Done + $Remaining
    if ($total -gt 0) {
        Write-Host ('Progress: {0:N1}% ({1} / {2})' -f (100.0 * $Done / $total), $Done, $total)
    }
    Write-Host "Done: $Done"
    Write-Host "Remaining: $Remaining"
    if ($Remaining -eq 0) {
        Write-Host 'ETA: stage work counted as complete'
    } elseif ($CanEstimate -and $Done -gt 0 -and $Elapsed -gt 0) {
        $rate = $Done / $Elapsed
        $eta = $Remaining / $rate
        Write-Host ('Average speed: {0:N1} items/min' -f ($rate * 60))
        Write-Host ('ETA: ' + (Format-Duration $eta))
        if ($eta -lt 31536000) {
            Write-Host ('Estimated finish: ' + (Get-Date).AddSeconds($eta).ToString('yyyy-MM-dd HH:mm:ss'))
        }
    } else {
        Write-Host 'ETA: unavailable (not enough data or inactive/blocked)'
    }
}

function Show-FolderEstimate {
    param($Folder, [double] $NowEpoch, [bool] $Active, [int] $OtherFolders)
    if ($null -eq $Folder.cursor -or $null -eq $Folder.cutoff) {
        Write-Host 'Current folder date estimate: unavailable (no dated cursor)'
        return
    }
    $cursor = [long]$Folder.cursor
    $cutoff = [long]$Folder.cutoff
    $daysLeft = [math]::Max(0.0, ($cursor - $cutoff) / 86400.0)
    Write-Host ('Cursor date: ' + [DateTimeOffset]::FromUnixTimeSeconds($cursor).ToLocalTime().ToString('yyyy-MM-dd HH:mm'))
    Write-Host ('Folder cutoff: ' + [DateTimeOffset]::FromUnixTimeSeconds($cutoff).ToLocalTime().ToString('yyyy-MM-dd HH:mm'))
    Write-Host ('Remaining date range in this folder: {0:N1} days (not mail count)' -f $daysLeft)

    $key = [string]$Folder.id
    $current = [pscustomobject]@{ At = $NowEpoch; Cursor = $cursor; Seen = [long]$Folder.seen }
    if (-not $samples.ContainsKey($key)) { $samples[$key] = @() }
    $old = @($samples[$key])
    if ($old.Count -gt 0 -and
        ($current.Cursor -gt $old[-1].Cursor -or $current.Seen -lt $old[-1].Seen)) {
        $old = @()
    }
    # Keep a rolling one-hour sample. Include indexing time in wall time.
    $old = @($old | Where-Object { ($NowEpoch - $_.At) -le 3600 })
    $samples[$key] = @($old + $current)
    $base = $old | Where-Object {
        ($NowEpoch - $_.At) -ge 300 -and
        ($_.Cursor - $cursor) -ge 3 * 86400 -and
        ($current.Seen - $_.Seen) -ge 100
    } | Select-Object -First 1

    if ($null -ne $base -and $Active -and $daysLeft -gt 0 -and -not $Folder.has_error) {
        $span = $base.Cursor - $cursor
        $seen = $current.Seen - $base.Seen
        $elapsed = $NowEpoch - $base.At
        $estimatedRows = [math]::Round(($cursor - $cutoff) * $seen / $span)
        $eta = ($cursor - $cutoff) * $elapsed / $span
        Write-Host ('Estimated remaining mail in this folder: ~{0:N0}' -f $estimatedRows)
        Write-Host ('Estimated time to folder cutoff: ~' + (Format-Duration $eta))
        Write-Host ('Sample: {0:N0} mail over {1:N1} historical days / {2:N1} elapsed min' -f $seen, ($span / 86400.0), ($elapsed / 60.0))
        Write-Host 'Estimate assumes similar mail density and throughput; undated mail excluded.'
        if ($OtherFolders -gt 0) {
            Write-Host 'Other folders are not included in this estimate.'
        }
    } elseif ($daysLeft -eq 0) {
        Write-Host 'Date cutoff reached; undated mail and final checks may remain.'
    } else {
        Write-Host 'Folder ETA: collecting sample (>=5 min, >=3 historical days, >=100 mail).'
    }
}

while ($true) {
    try {
        Clear-Host
        $now = Get-Date
        $runnerResult = Read-RunnerResult
        Write-Host '=== WORKMIND HISTORY MONITOR ===' -ForegroundColor Cyan
        Write-Host "Time: $now"

        if ($null -eq $runnerResult -or -not (Test-Path -LiteralPath $runnerResult.Directory -PathType Container)) {
            Write-Host 'No authoritative runner session found.' -ForegroundColor Yellow
            Start-Sleep -Seconds $RefreshSeconds
            continue
        }

        $dir = $runnerResult.Directory
        if ($dir -ne $currentSession) {
            $samples = @{}
            $currentSession = $dir
        }
        Write-Host "Session: $dir"
        Write-Host "Runner result: $($runnerResult.State)"
        $processes = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='python.exe'")
        $runners = @($processes | Where-Object {
            $_.Name -eq 'powershell.exe' -and $_.CommandLine -match 'run_mail_first\.ps1'
        })
        $runnerIds = @($runners | ForEach-Object { $_.ProcessId })
        $children = @($processes | Where-Object {
            $_.Name -eq 'python.exe' -and $_.ParentProcessId -in $runnerIds -and
            $_.CommandLine -match 'apps\.history_importer\.main'
        })
        Write-Host "Runner processes: $($runners.Count)"
        Write-Host "Importer launches: $($children.Count)"

        $summaryPath = Join-Path $dir 'summary.log'
        $summary = @()
        if (Test-Path -LiteralPath $summaryPath -PathType Leaf) {
            $summary = @(Get-Content -LiteralPath $summaryPath -Encoding UTF8)
        }
        $confirmed = [long]0
        $passes = 0
        foreach ($line in $summary) {
            if ($line -match 'Mail status=.*created=(\d+)') {
                $confirmed += [long]$Matches[1]
                $passes++
            }
        }
        Write-Host "Finished mail passes in session: $passes"
        Write-Host "New mail in finished passes: $confirmed"
        Write-Host ''
        Write-Host '=== RUNNER LOG ==='
        $summary | Select-Object -Last 4 | ForEach-Object { Write-Host $_ }

        $status = Read-HistoryStatus
        $passLine = $summary | Where-Object { $_ -match 'MAIL HISTORY PASS \d+' } | Select-Object -Last 1
        $file = $null
        $events = @()
        $stageActive = $false
        $idle = 0
        $currentFolder = $null
        if ($passLine -match 'MAIL HISTORY PASS (\d+)') {
            $pass = [int]$Matches[1]
            $file = Get-ChildItem -LiteralPath $dir -File -Filter "mail-$pass-try*.stdout.log" |
                Sort-Object Name -Descending | Select-Object -First 1
            if ($null -ne $file) {
                $idle = [math]::Max(0, [int]($now - $file.LastWriteTime).TotalSeconds)
                $events = @(Read-Events -Path $file.FullName)
                $stageActive = $runnerResult.State -eq 'RUNNING' -and $children.Count -gt 0 -and $idle -lt 120
                $folderEvent = $events | Where-Object { $_.event -eq 'history_folder_started' } | Select-Object -Last 1
                if ($null -ne $folderEvent) { $currentFolder = $folderEvent.folder }
            }
        }

        $db = Read-Snapshot
        Write-Host ''
        Write-Host '=== OVERALL HISTORICAL MAIL ===' -ForegroundColor Cyan
        if ($null -ne $db -and $null -ne $status -and $status.primary_store -and $status.archive_store) {
            $stores = @($status.primary_store, $status.archive_store)
            $folders = @($db.folders | Where-Object { $_.store -in $stores })
            $mailboxes = @($folders | Select-Object -ExpandProperty mailbox_id -Unique)
            if ($mailboxes.Count -eq 1) {
                $completed = @($folders | Where-Object { $_.status -eq 'completed' })
                $unfinished = @($folders | Where-Object { $_.status -ne 'completed' })
                $discovered = [int]$status.totals.folders_discovered
                Write-Host "Completed folder states: $($completed.Count)"
                Write-Host "Unfinished folder states: $($unfinished.Count)"
                Write-Host "Known folder states: $($folders.Count)"
                if ($discovered -gt 0) {
                    Write-Host "Folders discovered in last status: $discovered"
                    if ($discovered -gt $folders.Count) {
                        Write-Host ("At least {0} discovered folders not represented by known state count" -f ($discovered - $folders.Count))
                    }
                    Write-Host 'Folder counts are not weighted by mail count; live folder roster is not persisted.'
                }
                $seenAll = ($folders | Measure-Object -Property seen -Sum).Sum
                $createdAll = ($folders | Measure-Object -Property created -Sum).Sum
                Write-Host "Historical state counters: seen=$seenAll created=$createdAll"
                $errors = @($folders | Where-Object { $_.has_error })
                Write-Host "Folders with last error: $($errors.Count)"
                $activeFolder = @($unfinished | Where-Object { $_.folder -eq $currentFolder })
                if ($activeFolder.Count -eq 1) {
                    $other = [math]::Max(0, [math]::Max($discovered, $folders.Count) - $completed.Count - 1)
                    Write-Host ''
                    Write-Host "Current folder: $($activeFolder[0].folder)"
                    Show-FolderEstimate $activeFolder[0] ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) $stageActive $other
                }
                Write-Host 'Whole archive mail count / ETA: unknown until remaining folders are enumerated.'
            } else {
                Write-Host 'Overall folder state scope unavailable or ambiguous.' -ForegroundColor Yellow
            }
        } else {
            Write-Host 'Database snapshot or history status unavailable.' -ForegroundColor Yellow
        }

        Write-Host ''
        if ($runners.Count -eq 0) {
            Write-Host "STATUS: NO RUNNER ($($runnerResult.State))" -ForegroundColor Yellow
        } elseif ($null -eq $file -or $children.Count -eq 0) {
            Write-Host 'STATUS: RUNNER ACTIVE; STARTING / CHECKING / RETRYING' -ForegroundColor Cyan
        } else {
            Write-Host "=== PASS $pass / $($file.Name) ===" -ForegroundColor Cyan
            Write-Host "Last log write: $($file.LastWriteTime)"
            Write-Host "Idle seconds: $idle"
            if ($stageActive) {
                Write-Host 'STATUS: ACTIVE' -ForegroundColor Green
            } else {
                Write-Host 'STATUS: NO RECENT ACTIVITY (inspect runner log)' -ForegroundColor Yellow
            }
            $indexStart = $events | Where-Object { $_.event -eq 'history_indexing_started' } | Select-Object -Last 1
            if ($null -ne $indexStart) {
                Write-Host 'Stage: EMAIL INDEXING'
                $done = [long]0
                $chunks = [long]0
                foreach ($event in $events) {
                    if ($event.event -eq 'history_index_batch') { $done += [long]$event.completed }
                    if ($event.event -eq 'processing_batch_completed') { $chunks += [long]$event.chunks }
                }
                Write-Host "Chunks indexed in this attempt: $chunks"
                if ($null -ne $db -and $null -ne $db.queue) {
                    $q = $db.queue
                    $remaining = [long]$q.pending + [long]$q.processing + [long]$q.other
                    Write-Host "Email queue (all mail): pending=$($q.pending) processing=$($q.processing) other=$($q.other) delayed=$($q.delayed)"
                    $elapsed = ($now - [datetime]$indexStart.timestamp).TotalSeconds
                    Show-Stage $done $remaining $elapsed ($stageActive -and $q.other -eq 0 -and $q.delayed -eq 0)
                } else {
                    Write-Host "Completed jobs in this attempt: $done"
                    Write-Host 'Queue status unavailable.'
                }
            } else {
                Write-Host 'Stage: OUTLOOK MAIL IMPORT'
                $start = $events | Where-Object { $_.event -eq 'history_import_started' } | Select-Object -First 1
                $progress = $events | Where-Object { $_.event -eq 'history_message_progress' } | Select-Object -Last 1
                if ($null -ne $start -and $null -ne $progress) {
                    $done = [long]$progress.seen
                    $remaining = [math]::Max(0, [long]$start.max_messages - $done)
                    $elapsed = ([datetime]$progress.timestamp - [datetime]$start.timestamp).TotalSeconds
                    Write-Host "New mail in this attempt: $($progress.created)"
                    Write-Host 'ETA scope: current import limit, excludes indexing.'
                    Show-Stage $done $remaining $elapsed $stageActive
                } else {
                    Write-Host 'Waiting for first import progress event.'
                }
            }
            Write-Host ''
            Write-Host '=== LATEST EVENTS ==='
            Get-Content -LiteralPath $file.FullName -Encoding UTF8 -Tail 3 | ForEach-Object { Write-Host $_ }
        }
    } catch {
        Write-Host ('Monitor read failed: ' + $_.Exception.Message) -ForegroundColor Yellow
    }
    Start-Sleep -Seconds $RefreshSeconds
}
