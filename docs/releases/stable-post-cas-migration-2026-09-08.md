# Stable post-CAS migration — 2026-09-08

This checkpoint records completion of the WorkMind attachment content-addressed storage migration.

## Baseline

- WorkMind 0.12.1.
- Alembic head: `0011_content_addressed_storage`.
- Previous baseline: `stable-pre-storage02-2026-09-07`.
- PostgreSQL remains the structured source of truth.
- Attachment binary objects are stored in SHA-256 content-addressed storage.

## STORAGE-02 result

At the final migration checkpoint:

- all Attachment rows were linked to StoredObject;
- unlinked Attachment rows: 0;
- Attachment.archive_path values were migrated to canonical CAS paths;
- StoredObject rows: 8,607;
- orphan StoredObject rows: 0;
- broken CAS objects after full SHA-256 verification: 0;
- 37,909 verified legacy attachment copies were removed;
- legacy data removed: 6.188 GiB;
- DocumentChunk data was unchanged by the migration.

The counters above are a point-in-time migration snapshot. Live mailbox synchronization can increase Attachment, StoredObject and DocumentChunk counts after this checkpoint.

## STORAGE-04 result

The remaining 11 legacy files were audited separately.

All 11 were confirmed as duplicates of live CAS objects:

- matching SHA-256;
- valid StoredObject;
- valid CAS file;
- live Attachment reference;
- no Attachment using the old legacy path.

They were then removed.

Final legacy attachment files remaining: 0.

## Validation

- Full test suite: `236 passed`.
- Outlook Mail Sync completed successfully after migration.
- Windows Scheduled Task state: Ready.
- LastTaskResult: 0.
- CAS integrity verification: passed.

## Repository boundary

Local runtime data and sensitive configuration are not part of this checkpoint.

The repository excludes:

- `.env` and other local environment files;
- WorkMind runtime data;
- local backups and diagnostics;
- one-off STORAGE migration scripts;
- migration reports and context bundles.

This tag records the application/code baseline and the verified migration state. It is not a backup of PostgreSQL, Qdrant or CAS data.
