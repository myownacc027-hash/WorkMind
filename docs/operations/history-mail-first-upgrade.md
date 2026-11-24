# Historical Outlook import: mail-first recovery and XLSX correction

This procedure applies to an existing WorkMind installation with non-empty
PostgreSQL, Qdrant, and CAS. Do not delete database volumes or reset history
cursors.

## Why change the pipeline?

The historical importer already saves Outlook attachments and queues attachment
jobs independently of email indexing. Running the importer in a loop without
running the attachment worker after each pass allows the entire mail archive to
become searchable before processing costly documents.

Separately, CAS uses SHA-256 filenames without extensions. The primary XLSX
extractor must pass a binary file stream to openpyxl rather than a CAS pathname.
The previous behavior forced valid XLSX files into the slower OOXML fallback.

## Safe switch on a deployed workstation

1. Do not modify the running installation. Prepare the patch and tests in
   a separate branch or checkout first.
2. Identify the automation runner and its active child process. Stop only the
   runner; do not terminate PostgreSQL, Qdrant, embedding service, Outlook or
   an active import/attachment worker. The previous auto runner lacks a
   cooperative stop protocol, so the exact stop procedure depends on its
   current child state.
3. Let any in-flight worker/importer finish. Verify that no import or attachment
   worker remains. Investigate stale processing jobs and index consistency
   if any process ended unexpectedly.
4. Create and verify a full backup containing PostgreSQL, CAS and Qdrant.
5. Apply the patch to the stopped deployment, run targeted tests followed
   by the normal test suite, and check WorkMind infrastructure health.
6. Resume with the repository-native mail-first runner (Windows PowerShell 5.1),
   passing either a directory for a NEW backup, or the path to an
   ALREADY VERIFIED backup. Never pass both parameters together:

       powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\history\run_mail_first.ps1 -BackupRoot "<backup-directory>"

       powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\history\run_mail_first.ps1 -VerifiedBackupPath "<verified-backup-directory>"

   The second form verifies the manifest, PostgreSQL dump, CAS file layout,
   and Qdrant snapshot hash without copying the database a second time.
   If the database has changed since the backup, note that changes made
   after the snapshot will not be in that recovery point.

   It uses existing cursors, imports 2,000 messages per pass, and indexes
   their email texts while leaving attachment jobs queued. It never uses
   --reset. It also has a --MailOnly switch to stop after all mail is indexed.
7. To request a cooperative pause from the NEW runner, create an empty file
   named mail-first-stop.request in the configured WORKMIND_HOME/run directory.
   The runner checks that flag between email passes and before starting its
   attachment phase. It does NOT interrupt an in-flight importer or worker.
   Remove the flag deliberately before resuming. This flag is NOT supported by
   the earlier auto runner.
8. Once all eligible folders complete, the runner processes the queued
   attachments and checks PostgreSQL/Qdrant index and outstanding jobs.
8. Reprocessing previously truncated XLSX/XLS is an explicit independent
   operation and does not happen automatically after a code update.

## Inspect without mutation

    docker exec workmind-postgres psql -U workmind -d workmind -P pager=off -c "SELECT source_kind, status, COUNT(*) AS folders, SUM(seen_count) AS seen FROM history_import_states GROUP BY source_kind, status ORDER BY source_kind, status;"

    docker exec workmind-postgres psql -U workmind -d workmind -P pager=off -c "SELECT status, job_type, COUNT(*) FROM processing_jobs GROUP BY status, job_type ORDER BY job_type, status;"

## Targeted XLSX reprocessing

Only after the extractor passes tests and mail-first import is stable,
preview candidates:

    .\.venv\Scripts\workmind-attachments-backfill.exe --extensions .xlsx,.xlsm --statuses indexed --reindex-indexed --truncated-only

This is a dry run; jobs are only queued when --apply is explicitly supplied.

Do not reindex all XLSX automatically. The 5-million-character extraction limit
has not changed, so previously truncated worksheets may remain truncated even
after the primary parser is fixed. Complete row-preserving spreadsheet indexing
needs a separate design and benchmarks.

## Key invariants

- Historical cursors, identities, CAS objects and original attachments remain.
- No change to embedding model, vector dimensionality, chunk size or overlap.
- Unsupported files remain available as original CAS objects.
- While a worker operates, database/Qdrant counts can differ temporarily.
  Validate full consistency after workers stop.
- Mail import completed does not mean all attachments have been indexed.
