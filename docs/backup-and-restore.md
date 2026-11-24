# WorkMind — Backup and Restore

> **Document version:** 1.0 · WorkMind 0.12.1 · 2026-09-08

---

## Table of Contents

1. [Source of Truth](#source-of-truth)
2. [What Is Backed Up](#what-is-backed-up)
3. [Backup Storage Model](#backup-storage-model)
4. [Running a Backup](#running-a-backup)
5. [Verifying a Backup](#verifying-a-backup)
6. [Restore Test (isolated)](#restore-test-isolated)
7. [Production Restore](#production-restore)
8. [Restoring on a New PC](#restoring-on-a-new-pc)
9. [Role of Qdrant](#role-of-qdrant)
10. [GitHub Is Not a Data Backup](#github-is-not-a-data-backup)
11. [Backup Root and Disaster Recovery](#backup-root-and-disaster-recovery)
12. [Retention Policy (Future)](#retention-policy-future)
13. [Script Reference](#script-reference)

---

## Source of Truth

WorkMind knowledge data lives in two places that together form the **authoritative source of truth**:

| Component | Contains | Backup mandatory? |
|---|---|---|
| **PostgreSQL** (`workmind` database) | All email metadata, attachments, entities, claims, document chunks, extraction jobs, Alembic schema | ✅ Yes |
| **CAS** (`C:\AIKnowledge\archive\objects`) | Attachment binary files, content-addressed by SHA-256 | ✅ Yes |
| **Qdrant** (`workmind_chunks` collection) | Embedding vectors for semantic search | ⚡ Derived — can be rebuilt |
| **GitHub** (`main` branch) | Application code only | ❌ Not a data backup |

> [!IMPORTANT]
> **PostgreSQL + CAS = source of truth.** If you have these two, you can fully recover WorkMind.
> Qdrant is a derived index — losing it means re-embedding, not data loss.

---

## What Is Backed Up

One backup is a self-contained directory:

```
WorkMindBackup-YYYYMMDD-HHMMSS/
  manifest.json            ← human-readable backup descriptor
  postgres/
      workmind.dump        ← pg_dump -Fc (PostgreSQL custom format)
  cas/
      objects/aa/bb/<sha256>   ← exact mirror of CAS object tree
  qdrant/
      workmind_chunks-*.snapshot   ← Qdrant snapshot (if API available)
  metadata/
      git.txt              ← commit hash + tag at backup time
      alembic.txt          ← Alembic revision
      environment.json     ← Python version, OS, WorkMind version
```

The `manifest.json` records all sizes, SHA-256 checksums, and counts — you can assess a backup without running WorkMind.

**What is NOT included:**
- Database credentials / `.env` secrets
- Outlook mailbox credentials or API keys
- Application logs (`C:\AIKnowledge\logs\`)
- Processed text extractions (regenerable from CAS)

---

## Backup Storage Model

> [!WARNING]
> **A backup on the same disk as your data is a local recovery copy, not disaster recovery.**
> If drive C: fails physically, both your data and backup are lost.

| BackupRoot location | Protection against | Protects C: disk failure? |
|---|---|---|
| `C:\AIKnowledge\workmind-backups\` | Accidental deletion, DB corruption, CAS corruption | ❌ No |
| `D:\WorkMindBackups\` | + drive C: failure | ✅ Yes (if D: is separate) |
| `\\NAS\Backups\WorkMind\` | + single-machine failure | ✅ Yes |
| Cloud (rclone to S3/B2) | + local disaster | ✅ Yes |

**Recommendation:** use at minimum a separate physical drive (D:) and ideally an offsite copy.

---

## Running a Backup

```powershell
# Local recovery copy (same disk — OK for testing, NOT for disaster recovery)
.\scripts\backup\backup_workmind.ps1 -BackupRoot "C:\AIKnowledge\workmind-backups"

# External drive (recommended for real protection)
.\scripts\backup\backup_workmind.ps1 -BackupRoot "D:\WorkMindBackups"

# NAS
.\scripts\backup\backup_workmind.ps1 -BackupRoot "\\NAS\Backups\WorkMind"

# Skip Qdrant snapshot (faster, Qdrant is down)
.\scripts\backup\backup_workmind.ps1 -BackupRoot "D:\WorkMindBackups" -SkipQdrant
```

**What the backup does:**
1. Waits for any running `WorkMind Mail Sync` task to finish
2. Temporarily disables the scheduled task
3. Runs `pg_dump -Fc` (PostgreSQL custom format)
4. Copies all CAS objects with SHA-256 verification
5. Creates a Qdrant collection snapshot (if API available)
6. Writes `manifest.json` with checksums
7. Re-enables the scheduled task (always — even if backup fails)

**Typical timing** (WorkMind 0.12.1 baseline):
- PostgreSQL dump: ~1–2 min (584 MB compressed)
- CAS copy: ~4–5 min (4.9 GB, 8618 objects)
- Qdrant snapshot + download: ~2 min (5.7 GB)
- **Total: ~8–10 min**

---

## Verifying a Backup

```powershell
# Fast verify (manifest + SHA-256 of dump + CAS count/bytes + filename structure)
.\scripts\backup\verify_backup.ps1 -BackupPath "C:\AIKnowledge\workmind-backups\WorkMindBackup-20260908-175619"

# Full verify (also SHA-256 every CAS file — slow, ~5-10 min for 4.9 GB)
.\scripts\backup\verify_backup.ps1 -BackupPath "...\WorkMindBackup-..." -VerifyAllHashes
```

**Checks performed:**
- `manifest.json` parses correctly
- PostgreSQL dump exists + SHA-256 matches manifest
- CAS file count matches manifest
- CAS total bytes match manifest
- Every CAS filename is a valid SHA-256 (path consistency)
- Qdrant snapshot exists + SHA-256 matches (if snapshot was saved)
- All metadata files present
- No `.partial` marker (backup completed)

**Exit codes:** `0` = PASS, `1` = FAIL

---

## Restore Test (isolated)

> [!IMPORTANT]
> Always run a restore test **before** you need it in a real disaster.

By default, restore runs into a temporary isolated environment:
- Database: `workmind_restore_test_YYYYMMDD` (auto-created and deleted)
- CAS: `C:\AIKnowledge\restore-test-YYYYMMDD\` (auto-created and deleted)

```powershell
# Default safe test restore — creates temp DB and CAS, verifies, cleans up
.\scripts\backup\restore_workmind.ps1 -BackupPath "C:\AIKnowledge\workmind-backups\WorkMindBackup-20260908-175619"

# Keep test resources for manual inspection
.\scripts\backup\restore_workmind.ps1 -BackupPath "...\WorkMindBackup-..." -SkipCleanup

# Dry run (shows what would happen, no changes)
.\scripts\backup\restore_workmind.ps1 -BackupPath "...\WorkMindBackup-..." -DryRun
```

**Verified after restore:**
- Alembic revision matches backup
- Attachment count matches
- StoredObject count matches
- DocumentChunk count matches
- Unlinked attachments = 0
- Orphan stored objects = 0
- Spot-check: 5 random StoredObjects → file exists in restored CAS + SHA-256 matches

---

## Production Restore

> [!CAUTION]
> Production restore **destroys the existing database**. There is no undo.
> Only do this when the production database is genuinely unrecoverable.

```powershell
# Stop WorkMind services first
Disable-ScheduledTask -TaskName "WorkMind Mail Sync"
# Stop mail sync, embedding service, MCP server manually if running

# Production restore — DESTRUCTIVE
.\scripts\backup\restore_workmind.ps1 `
    -BackupPath "C:\AIKnowledge\workmind-backups\WorkMindBackup-20260908-175619" `
    -TargetDatabase "workmind" `
    -TargetWorkmindHome "C:\AIKnowledge" `
    -ConfirmDestructiveRestore

# Verify production DB after restore
# (run application health checks, pytest, spot-check queries)

# Re-enable services
Enable-ScheduledTask -TaskName "WorkMind Mail Sync"
```

**After production restore:**
1. Verify `alembic upgrade head` is not needed (revision should match)
2. Rebuild Qdrant if no snapshot was restored (see [Role of Qdrant](#role-of-qdrant))
3. Restart embedding service if needed

---

## Restoring on a New PC

1. **Install prerequisites:**
   - Python 3.11+
   - Docker Desktop
   - Git

2. **Clone the repo at the stable tag:**
   ```powershell
   git clone https://github.com/myownacc027-hash/WorkMind.git
   cd WorkMind
   git checkout stable-post-cas-migration-2026-09-08
   ```

3. **Copy `.env`** from your backup or secrets manager.

4. **Start Docker services:**
   ```powershell
   docker compose up -d
   ```

5. **Create Python venv and install deps:**
   ```powershell
   python -m venv .venv
   .venv\Scripts\pip install -e .
   ```

6. **Restore from backup:**
   ```powershell
   # Restore PostgreSQL and CAS to new machine
   .\scripts\backup\restore_workmind.ps1 `
       -BackupPath "\\NAS\Backups\WorkMind\WorkMindBackup-20260908-175619" `
       -TargetDatabase "workmind" `
       -TargetWorkmindHome "C:\AIKnowledge" `
       -ConfirmDestructiveRestore
   ```

7. **Restore Qdrant** (if snapshot available):
   ```powershell
   # Upload snapshot to new Qdrant instance via API
   # See Qdrant docs: PUT /collections/{name}/snapshots/upload
   ```
   Or rebuild from scratch (see below).

8. **Register scheduled task** if needed:
   ```powershell
   # Re-register WorkMind Mail Sync task
   # (see apps/scheduler/ or project documentation)
   ```

---

## Role of Qdrant

Qdrant stores **embedding vectors** used for semantic search. It is a **derived index** — all the information needed to rebuild it exists in PostgreSQL (`document_chunks` table) and the CAS.

| Scenario | Impact |
|---|---|
| Qdrant data lost, snapshot available | Restore snapshot → instant recovery |
| Qdrant data lost, no snapshot | Rebuild by re-embedding DocumentChunks → semantic search restored |
| Qdrant data lost, no backup at all | **No knowledge loss** — all text is in PostgreSQL + CAS |

**Rebuild Qdrant from source of truth:**
```powershell
# Re-index all document chunks (slow — 2M+ chunks)
.venv\Scripts\python.exe -m apps.indexer reindex
```

> [!NOTE]
> The Qdrant snapshot in the backup is an **accelerator**, not a requirement.
> PostgreSQL + CAS is the real source of truth.

---

## GitHub Is Not a Data Backup

The WorkMind GitHub repository (`https://github.com/myownacc027-hash/WorkMind`) contains:
- Application code ✅
- Alembic migrations ✅
- Configuration templates ✅

GitHub does **NOT** contain:
- PostgreSQL database ❌
- CAS attachment files ❌
- Qdrant vectors ❌
- User data ❌
- `.env` secrets ❌

Losing the GitHub repository means losing code only — knowledge data is safe if you have PostgreSQL + CAS backups.

---

## Backup Root and Disaster Recovery

All backup scripts accept a `-BackupRoot` parameter. **Never hardcode a specific path in scripts.**

```powershell
# Examples:
-BackupRoot "C:\AIKnowledge\workmind-backups"   # local copy (same disk)
-BackupRoot "D:\WorkMindBackups"                 # separate drive
-BackupRoot "E:\WorkMindBackups"                 # USB or second drive
-BackupRoot "\\NAS\Backups\WorkMind"             # network share
```

To automate backups to an external location, create a scheduled task:
```powershell
# Example: daily backup to D: at 03:00
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument '-NonInteractive -File "C:\...\WorkMind\scripts\backup\backup_workmind.ps1" -BackupRoot "D:\WorkMindBackups"'
$trigger = New-ScheduledTaskTrigger -Daily -At "03:00"
Register-ScheduledTask -TaskName "WorkMind Daily Backup" -Action $action -Trigger $trigger
```

---

## Retention Policy (BACKUP-02 — planned, NOT enabled)

> [!IMPORTANT]
> **Постоянное резервное копирование и ротация** отслеживаются в [issue #8](https://github.com/myownacc027-hash/WorkMind/issues/8). **Разовая очистка после пятилетнего импорта** — отдельная [issue #7](https://github.com/myownacc027-hash/WorkMind/issues/7) и [чек-лист](operations/post-import-cleanup.md). Эти задачи не заменяют одна другую.
>
> **На 24.11.2026** автоматическая очистка ещё не реализована. Не удаляйте старые архивы автоматически и не устанавливайте расписание ротации до завершения тестов и явного включения.

### Целевая политика для рабочей версии

- При безопасном расписании создавать **полную согласованную резервную копию** PostgreSQL и CAS; по возможности включать Qdrant snapshot, чтобы не тратить время на повторную генерацию миллионов embeddings.
- После успешной записи и **проверки новой копии** хранить две последние проверенные recovery points и одну последнюю месячную, если она не совпадает с ними. **Не более трёх обычных локальных полных архивов** в установившемся режиме; отдельно защищённые точечные копии до релиза/инцидента считать исключениями, но учитывать их размер и пересматривать срок защиты.
- Перед очисткой сравнивать доступное место с заданными ограничениями и учитывать дополнительное место для создаваемой копии. Если достаточно места нет, не удалять последнюю проверенную копию ради создания следующей — остановить операцию и сообщить о проблеме.
- Для каждой копии учитывать: timestamp, размер, manifest, результат проверки, флаг ручной защиты, тип (обычная/перед изменением), физическое место хранения.
- Не использовать только возраст каталога как доказательство пригодности бэкапа. Незавершённые, повреждённые, непроверенные копии и посторонние каталоги не считать подходящей заменой предыдущей recovery point.
- Выполнять очистку только в разрешённом backup root, без следования через junctions/symlinks/reparse points, с dry-run, отдельным разрешением на apply, журналом операций и блокировкой параллельных запусков.
- Хранить как минимум одну независимую recovery point **вне физического диска с активной базой** (NAS/другой носитель/удалённое хранилище). Бэкап на D: при данных на D: защищает от ошибок/повреждения, но не от отказа этого диска.

**Ориентир по объёму на 24.11.2026:** одна полная копия около 33,5 ГБ (PostgreSQL 1,8 ГБ + CAS 13,3 ГБ + Qdrant 18,3 ГБ). Три таких копии занимают примерно 100,5 ГБ, не считая пространства на создание следующей; объём будет расти вместе с данными. Прежняя идея 7 ежедневных + 4 еженедельных + 6 ежемесячных означала до 17 полных копий (около 570 ГБ сегодня) и **отменена как значение по умолчанию**.

**Важно:** текущий backup engine отключает только известную задачу `WorkMind Mail Sync`; он пока не гарантирует согласованный снимок при работающих импортёрах и worker-процессах. BACKUP-02 должен согласовать writers/lock/остановку заданий перед созданием согласованного набора PostgreSQL+CAS+Qdrant. До этого используйте проверенный безопасный интервал.

Автоматическая ротация должна запускаться после успешного backup и verify, но **не удалять** предыдущие точки при сбое нового бэкапа, ошибке проверки, расхождении инвентаризации или признаках активной миграции. Внедрение только после тестов `dry-run`/`apply` и согласования владельцем.

---

## Script Reference

| Script | Purpose | Key parameters |
|---|---|---|
| `backup_workmind.ps1` | Create a full backup | `-BackupRoot`, `-SkipQdrant`, `-NoVerifyCas` |
| `verify_backup.ps1` | Read-only verify a backup | `-BackupPath`, `-VerifyAllHashes` |
| `restore_workmind.ps1` | Restore from backup | `-BackupPath`, `-TargetDatabase`, `-TargetWorkmindHome`, `-ConfirmDestructiveRestore`, `-DryRun`, `-SkipCleanup` |

Internal Python engines (called by PS wrappers):
- `scripts/backup/_backup_engine.py`
- `scripts/backup/_restore_engine.py`
- `scripts/backup/_verify_engine.py`
