# Монитор исторического импорта (Windows PowerShell 5.1).
# Только чтение. Не блокирует Outlook и не изменяет рабочий каталог.
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

function Format-RunnerState {
    param([string] $State)
    switch ($State) {
        'RUNNING' { 'работает' }
        'FAILED' { 'остановлен с ошибкой' }
        'PAUSED_BY_REQUEST' { 'приостановлен по запросу' }
        'MAIL_COMPLETE_ATTACHMENTS_PENDING' { 'почта завершена; обработка вложений ожидает запуска' }
        'MAIL_COMPLETE_ATTACHMENTS_RUNNING' { 'почта завершена; обработка вложений выполняется' }
        'COMPLETED' { 'завершён' }
        default { $State }
    }
}

function Format-RunnerLine {
    param([string] $Line)
    if ($Line -match '^(.*?)Mail status=(\S+) seen=(\d+) created=(\d+)$') {
        $state = $Matches[2]
        $label = if ($state -eq 'completed') { 'архив завершён' } elseif ($state -eq 'paused') { 'партия завершена, импорт продолжается' } else { $state }
        return ('{0} {1}: обработано={2}, новых={3}' -f $Matches[1], $label, $Matches[3], $Matches[4])
    }
    if ($Line -match '^(.*?)=== MAIL HISTORY PASS (\d+) ===$') {
        return ('{0} Начат проход № {1}' -f $Matches[1], $Matches[2])
    }
    if ($Line -match '^(.*?)(mail|index)-(\d+)-try(\d+) exit=(\d+)$') {
        $action = if ($Matches[2] -eq 'mail') { 'Импорт' } else { 'Проверка индекса' }
        return ('{0}{1}, проход {2}, попытка {3}: код завершения {4}' -f $Matches[1], $action, $Matches[3], $Matches[4], $Matches[5])
    }
    if ($Line -match '^(.*?)([A-Za-z]): free = ([\d.,]+) GB$') {
        return ('{0}Свободно на диске {1}: {2} ГБ' -f $Matches[1], $Matches[2], $Matches[3])
    }
    return $Line
}

function Format-EventLine {
    param($Event)
    $time = ([string]$Event.timestamp)
    if ($time.Length -ge 19) { $time = $time.Substring(11, 8) }
    switch ($Event.event) {
        'history_import_started' { "$time - Начат импорт; лимит $($Event.max_messages) писем." }
        'history_folder_started' { "$time - Начата папка: $($Event.folder)" }
        'history_folder_finished' { "$time - Этап чтения папки завершён (полностью: $($Event.completed))." }
        'history_table_window_started' { "$time - Чтение метаданных за период $($Event.lower) - $($Event.upper)." }
        'history_table_batch' { "$time - Прочитано метаданных: $($Event.rows_scanned)." }
        'history_table_window_read' { "$time - Метаданные: $($Event.rows_scanned) записей за $($Event.duration_seconds) сек." }
        'history_table_ready' { "$time - Метаданные готовы: $($Event.candidates) кандидатов." }
        'history_message_progress' { "$time - Обработано писем: $($Event.seen), новых: $($Event.created)." }
        'history_indexing_started' { "$time - Начата индексация писем." }
        'processing_batch_completed' { "$time - Обработано заданий: $($Event.jobs), чанков: $($Event.chunks)." }
        'history_index_batch' { "$time - Пакет $($Event.batch): взято $($Event.claimed), выполнено $($Event.completed)." }
        'outlook_text_normalized' { "$time - Исправлена некорректная кодировка текста Outlook ($($Event.reason))." }
        'attachment_sync_failed' { "$time - Ошибка сохранения вложения: $($Event.attachment_name). $($Event.error)" }
        'history_message_failed' { "$time - Ошибка обработки письма: $($Event.error)" }
        'history_folder_connection_lost' { "$time - Потеряна связь с Outlook: $($Event.error)" }
        default { "$time - $($Event.event): $($Event.error)" }
    }
}

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
        return 'нет данных'
    }
    if ($Seconds -lt 60) { return ('{0:N0} сек.' -f $Seconds) }
    if ($Seconds -lt 3600) { return ('{0:N1} мин.' -f ($Seconds / 60)) }
    return ('{0:N1} ч.' -f ($Seconds / 3600))
}

function Show-Stage {
    param([long] $Done, [long] $Remaining, [double] $Elapsed,
          [bool] $CanEstimate)
    $total = $Done + $Remaining
    if ($total -gt 0) {
        Write-Host ('Выполнено: {0:N1}% ({1} из {2})' -f (100.0 * $Done / $total), $Done, $total)
    }
    Write-Host "Обработано: $Done"
    Write-Host "Осталось: $Remaining"
    if ($Remaining -eq 0) {
        Write-Host 'По счётчикам этап завершён; возможны итоговые проверки.'
    } elseif ($CanEstimate -and $Done -gt 0 -and $Elapsed -gt 0) {
        $rate = $Done / $Elapsed
        $eta = $Remaining / $rate
        Write-Host ('Средняя скорость: {0:N1} заданий/мин.' -f ($rate * 60))
        Write-Host ('Осталось времени (оценка): ' + (Format-Duration $eta))
        if ($eta -lt 31536000) {
            Write-Host ('Ожидаемое завершение этапа: ' + (Get-Date).AddSeconds($eta).ToString('dd.MM.yyyy HH:mm:ss'))
        }
    } else {
        Write-Host 'Прогноз: пока нет данных или обработка приостановлена.'
    }
}

function Show-FolderEstimate {
    param($Folder, [double] $NowEpoch, [bool] $Active, [int] $OtherFolders)
    if ($null -eq $Folder.cursor -or $null -eq $Folder.cutoff) {
        Write-Host 'Прогноз по папке недоступен: нет даты курсора.'
        return
    }
    $cursor = [long]$Folder.cursor
    $cutoff = [long]$Folder.cutoff
    $daysLeft = [math]::Max(0.0, ($cursor - $cutoff) / 86400.0)
    Write-Host ('Достигнутая дата письма: ' + [DateTimeOffset]::FromUnixTimeSeconds($cursor).ToLocalTime().ToString('dd.MM.yyyy HH:mm'))
    Write-Host ('Нижняя граница импорта: ' + [DateTimeOffset]::FromUnixTimeSeconds($cutoff).ToLocalTime().ToString('dd.MM.yyyy HH:mm'))
    Write-Host ('Оставшийся период в папке: {0:N1} дней (не количество писем)' -f $daysLeft)

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
        Write-Host ('Осталось писем в этой папке (оценка): ~{0:N0}' -f $estimatedRows)
        Write-Host ('До нижней границы папки (оценка): ~' + (Format-Duration $eta))
        Write-Host ('Основа прогноза: {0:N0} писем за {1:N1} календарных дней архива / {2:N1} мин. работы' -f $seen, ($span / 86400.0), ($elapsed / 60.0))
        Write-Host 'Прогноз условный: предполагает прежнюю плотность писем и скорость; письма без даты не учтены.'
        if ($OtherFolders -gt 0) {
            Write-Host 'Остальные папки в этот прогноз не включены.'
        }
    } elseif ($daysLeft -eq 0) {
        Write-Host 'Граница дат достигнута; могут остаться письма без даты и итоговые проверки.'
    } else {
        Write-Host 'Прогноз по папке: накопление статистики (от 5 мин., 3 дней архива и 100 писем).'
    }
}

while ($true) {
    try {
        Clear-Host
        $now = Get-Date
        $runnerResult = Read-RunnerResult
        Write-Host '=== WORKMIND: МОНИТОР ИМПОРТА ПОЧТЫ ===' -ForegroundColor Cyan
        Write-Host "Текущее время: $now"

        if ($null -eq $runnerResult -or -not (Test-Path -LiteralPath $runnerResult.Directory -PathType Container)) {
            Write-Host 'Не найден подтверждённый сеанс импорта.' -ForegroundColor Yellow
            Start-Sleep -Seconds $RefreshSeconds
            continue
        }

        $dir = $runnerResult.Directory
        if ($dir -ne $currentSession) {
            $samples = @{}
            $currentSession = $dir
        }
        Write-Host "Каталог журнала: $dir"
        Write-Host "Состояние сеанса: $(Format-RunnerState $runnerResult.State)"
        $processes = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='python.exe'")
        $runners = @($processes | Where-Object {
            $_.Name -eq 'powershell.exe' -and $_.CommandLine -match 'run_mail_first\.ps1'
        })
        $runnerIds = @($runners | ForEach-Object { $_.ProcessId })
        $children = @($processes | Where-Object {
            $_.Name -eq 'python.exe' -and $_.ParentProcessId -in $runnerIds -and
            $_.CommandLine -match 'apps\.history_importer\.main'
        })
        Write-Host "Управляющих процессов: $($runners.Count)"
        Write-Host "Запущено импортёров: $($children.Count)"

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
        Write-Host "Завершено проходов в сеансе: $passes"
        Write-Host "Новых писем за завершённые проходы: $confirmed"
        Write-Host ''
        Write-Host '=== ЖУРНАЛ УПРАВЛЯЮЩЕГО ПРОЦЕССА ==='
        $summary | Select-Object -Last 4 | ForEach-Object { Write-Host (Format-RunnerLine $_) }

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
        Write-Host '=== ОБЩИЙ ИМПОРТ ИСТОРИИ ===' -ForegroundColor Cyan
        if ($null -ne $db -and $null -ne $status -and $status.primary_store -and $status.archive_store) {
            $stores = @($status.primary_store, $status.archive_store)
            $folders = @($db.folders | Where-Object { $_.store -in $stores })
            $mailboxes = @($folders | Select-Object -ExpandProperty mailbox_id -Unique)
            if ($mailboxes.Count -eq 1) {
                $completed = @($folders | Where-Object { $_.status -eq 'completed' })
                $unfinished = @($folders | Where-Object { $_.status -ne 'completed' })
                $discovered = [int]$status.totals.folders_discovered
                Write-Host "Папок со статусом завершено: $($completed.Count)"
                Write-Host "Из известных папок не завершено: $($unfinished.Count)"
                Write-Host "Папок с сохранённым состоянием: $($folders.Count)"
                if ($discovered -gt 0) {
                    Write-Host "Обнаружено папок в последнем отчёте: $discovered"
                    if ($discovered -gt $folders.Count) {
                        Write-Host ("Разница между обнаруженными папками и сохранёнными состояниями: {0}" -f ($discovered - $folders.Count))
                    }
                    Write-Host 'Количество папок не равно проценту писем; полный актуальный список папок не сохранён.'
                }
                $seenAll = ($folders | Measure-Object -Property seen -Sum).Sum
                $createdAll = ($folders | Measure-Object -Property created -Sum).Sum
                Write-Host "Накопленные счётчики истории: обработано=$seenAll, новых=$createdAll"
                $errors = @($folders | Where-Object { $_.has_error })
                Write-Host "Папок с неустранённой последней ошибкой: $($errors.Count)"
                $activeFolder = @($unfinished | Where-Object { $_.folder -eq $currentFolder })
                if ($activeFolder.Count -eq 1) {
                    $other = [math]::Max(0, [math]::Max($discovered, $folders.Count) - $completed.Count - 1)
                    Write-Host ''
                    Write-Host "Текущая папка: $($activeFolder[0].folder)"
                    Show-FolderEstimate $activeFolder[0] ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) $stageActive $other
                }
                Write-Host 'Точный остаток и время завершения всего архива пока неизвестны.'
            } else {
                Write-Host 'Не удалось однозначно определить область исторического импорта.' -ForegroundColor Yellow
            }
        } else {
            Write-Host 'Не удалось прочитать состояние базы или исторического импорта.' -ForegroundColor Yellow
        }

        Write-Host ''
        if ($runners.Count -eq 0) {
            Write-Host "СОСТОЯНИЕ: управляющий процесс не запущен ($(Format-RunnerState $runnerResult.State))" -ForegroundColor Yellow
        } elseif ($null -eq $file -or $children.Count -eq 0) {
            Write-Host 'СОСТОЯНИЕ: управляющий процесс работает (запуск / проверка / повтор)' -ForegroundColor Cyan
        } else {
            Write-Host "=== ПРОХОД $pass / $($file.Name) ===" -ForegroundColor Cyan
            Write-Host "Последняя запись: $($file.LastWriteTime)"
            Write-Host "Без новых записей (сек.): $idle"
            if ($stageActive) {
                Write-Host 'СОСТОЯНИЕ: РАБОТАЕТ' -ForegroundColor Green
            } else {
                Write-Host 'СОСТОЯНИЕ: НЕТ СВЕЖИХ ЗАПИСЕЙ (проверьте журнал)' -ForegroundColor Yellow
            }
            $indexStart = $events | Where-Object { $_.event -eq 'history_indexing_started' } | Select-Object -Last 1
            if ($null -ne $indexStart) {
                Write-Host 'Этап: ИНДЕКСАЦИЯ ПИСЕМ'
                $done = [long]0
                $chunks = [long]0
                foreach ($event in $events) {
                    if ($event.event -eq 'history_index_batch') { $done += [long]$event.completed }
                    if ($event.event -eq 'processing_batch_completed') { $chunks += [long]$event.chunks }
                }
                Write-Host "Чанков проиндексировано за попытку: $chunks"
                if ($null -ne $db -and $null -ne $db.queue) {
                    $q = $db.queue
                    $remaining = [long]$q.pending + [long]$q.processing + [long]$q.other
                    Write-Host "Очередь индексации: ожидают=$($q.pending), в работе=$($q.processing), прочие=$($q.other), из ожидающих отложены=$($q.delayed)"
                    $elapsed = ($now - [datetime]$indexStart.timestamp).TotalSeconds
                    Show-Stage $done $remaining $elapsed ($stageActive -and $q.other -eq 0 -and $q.delayed -eq 0)
                } else {
                    Write-Host "Заданий выполнено за попытку: $done"
                    Write-Host 'Не удалось прочитать очередь индексации.'
                }
            } else {
                Write-Host 'Этап: ИМПОРТ ИЗ OUTLOOK'
                $start = $events | Where-Object { $_.event -eq 'history_import_started' } | Select-Object -First 1
                $progress = $events | Where-Object { $_.event -eq 'history_message_progress' } | Select-Object -Last 1
                if ($null -ne $start -and $null -ne $progress) {
                    $done = [long]$progress.seen
                    $remaining = [math]::Max(0, [long]$start.max_messages - $done)
                    $elapsed = ([datetime]$progress.timestamp - [datetime]$start.timestamp).TotalSeconds
                    Write-Host "Новых писем в текущей попытке: $($progress.created)"
                    Write-Host 'Прогноз только до лимита текущего прохода, без индексации.'
                    Show-Stage $done $remaining $elapsed $stageActive
                } else {
                    Write-Host 'Ожидание первых данных о прогрессе импорта.'
                }
            }
            Write-Host ''
            Write-Host '=== ПОСЛЕДНИЕ СОБЫТИЯ ==='
            $events | Select-Object -Last 3 | ForEach-Object { Write-Host (Format-EventLine $_) }
        }
    } catch {
        Write-Host ('Ошибка чтения мониторинга: ' + $_.Exception.Message) -ForegroundColor Yellow
    }
    Start-Sleep -Seconds $RefreshSeconds
}
