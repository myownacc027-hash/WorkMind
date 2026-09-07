# Stable baseline: 2026-09-07 pre-STORAGE-02

This document records the last verified WorkMind state before the bulk legacy-attachment migration planned in STORAGE-02.

## Identity

- WorkMind version: `0.12.1`
- Baseline date: `2026-09-07`
- Baseline name/tag: `stable-pre-storage02-2026-09-07`
- Alembic head: `0011_content_addressed_storage`
- Storage state: mixed legacy paths plus CAS; new supported Outlook attachments use CAS

## Verification evidence

| Check | Recorded result |
| --- | ---: |
| Full test suite | `236 passed` |
| Outlook -> CAS | working |
| Scheduled Task `LastTaskResult` | `0` |
| CAS integrity | OK |
| `stored_objects` | `5` |
| `linked_attachments` | `6` |
| `attachment_errors` | `0` |
| `orphan_stored_objects` | `0` |
| `broken_stored_objects` | `0` |

The full test suite was rerun from the stable local source on 2026-09-07 and completed with `236 passed`. Database counts, Scheduled Task state and live Outlook/CAS integrity are operational measurements recorded at the baseline; they are not embedded as data in this repository.

## What is stable here

- migration `0011_content_addressed_storage` is applied and is the single Alembic head;
- CAS stores binary objects by SHA-256 and supports multiple attachment links to one object;
- Outlook ingestion creates and links CAS objects successfully;
- attachment extraction resolves CAS objects and retains the original filename as a format hint;
- legacy attachment paths remain readable before bulk migration;
- the scheduled mail synchronization completed successfully;
- no known attachment errors, orphan objects or broken objects were present at the checkpoint.

## External dependencies

- Windows 10/11 and classic Outlook in the collector's interactive user session;
- Python 3.11–3.13 and the dependencies declared in `pyproject.toml`;
- PostgreSQL 17 (`postgres:17-alpine` in the current Compose profile);
- Qdrant 1.18 (`qdrant/qdrant:v1.18.0` in the current Compose profile);
- local multilingual embedding model `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` and the embedding service;
- a configured local storage root (`WORKMIND_HOME`);
- Windows Task Scheduler entries for mail synchronization and any enabled workers;
- a configured Outlook mailbox and a local `.env`, neither of which is part of the repository.

## Backlog after this baseline

### Storage and deployment

- **STORAGE-02** — dry-run, canary and then resumable bulk backfill of existing legacy attachments into CAS; preserve rollback until integrity is proven.
- **STORAGE-03** — switch remaining readers/maintenance paths to canonical stored objects and retire legacy-path assumptions safely.
- **STORAGE-04** — reclaim verified duplicate/legacy files only after reference, hash and recovery checks.
- **STORAGE-OBS-01** — operational metrics, integrity audit, orphan/broken-object reporting and actionable alerts.
- **DEPLOY-01** — reproducible bootstrap/deployment package, environment validation, Scheduled Task installation and deployment check for a new PC/mailbox.

### Search

- build a repeatable relevance evaluation set for exact, lexical and semantic retrieval;
- tune hybrid/RRF ranking, thresholds, source filters and per-source snippet limits using measured results;
- add index reconciliation and a documented Qdrant rebuild path from PostgreSQL chunks;
- improve diagnostics so search results expose source, score channel and evidence trail without leaking private content.

### Knowledge extraction (KE)

- run a bounded production pilot and quality evaluation for entity reuse, duplicate risk and temporal updates;
- strengthen identity resolution and conflict handling before broader automatic apply;
- add queue/provider observability, retry diagnostics and controlled historical backfill procedures;
- keep validate/apply parity, immutable evidence checks and explicit remote-processing consent as release gates.

## Reproduction note

This tag captures source code and documentation only. Reproducing a running installation also requires new local configuration, PostgreSQL, Qdrant, the embedding model/service, Outlook, storage directories and Scheduled Tasks. Existing mail, documents, databases, vector indexes and secrets are intentionally excluded.

