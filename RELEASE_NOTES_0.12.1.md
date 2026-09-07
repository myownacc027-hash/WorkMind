# WorkMind 0.12.1

WorkMind 0.12.1 adds source-metadata evidence for binary/non-text identity and preserves strict chunk-evidence requirements for claims, relations and events.

The stable pre-STORAGE-02 snapshot also includes STORAGE-01:

- Alembic head `0011_content_addressed_storage`;
- content-addressed attachment objects identified by SHA-256;
- deduplicated links from attachments to stored objects;
- Outlook ingestion through CAS with legacy-path compatibility;
- extensionless CAS blobs processed through the original filename hint.

Validation for the 2026-09-07 baseline: `236 passed`, Outlook-to-CAS verified, scheduled synchronization successful and CAS integrity verified.

See [`docs/releases/stable-pre-storage02-2026-09-07.md`](docs/releases/stable-pre-storage02-2026-09-07.md) for the recorded measurements, external dependencies and remaining backlog.

