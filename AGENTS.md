# WorkMind — developer and automation-agent instructions

## Protect live user data

- The Git repository contains code and documentation, **not** the user's PostgreSQL, CAS, Qdrant or backups.
- Never delete, truncate, reset or reinitialize a live WorkMind database, CAS object store, Qdrant persistent volumes, history cursors or checkpoints as an incidental step in development.
- Never execute `docker compose down -v`, `docker volume prune` or `docker system prune --volumes` as a cleanup shortcut.
- Before any potentially destructive operation, identify which processes, scheduled tasks, junctions/symlinks and physical volumes are involved; require explicit consent for the exact targets.
- Never add real email addresses, mailbox stores, secrets, backup files, user-specific paths or corporate content to Git.

## Historical import and completion

- Follow [historical import upgrade](docs/operations/history-mail-first-upgrade.md) for mail-first changes and safe resumption.
- **Completion of mail import is not completion of attachment processing.** Inspect both import cursors and both processing queues, and account for unsupported/empty/corrupt/truncated attachments.
- Reindexing previously indexed attachments is an explicit opt-in operation, never an automatic side effect of a code update.
- While a history or attachment process is running, do not hot-replace imported Python modules or modify its runner script in the deployed checkout.

## Mandatory post-import cleanup follow-up

- Active tracking task: [issue #7](https://github.com/myownacc027-hash/WorkMind/issues/7).
- Authoritative operational checklist: [post-import-cleanup.md](docs/operations/post-import-cleanup.md).
- Routine production backup rotation is tracked in [BACKUP-02 issue #8](https://github.com/myownacc027-hash/WorkMind/issues/8), independently from one-time post-import cleanup in issue #7. The target is two most recent verified backups plus one distinct monthly backup, not the obsolete 7/4/6 archive proposal. BACKUP-02 is NOT YET implemented: never infer that old backups may be deleted automatically.
- Keep issue #7 **open** until: mail and attachments finish, indexes/CAS are verified, a new post-import backup is verified (preferably restore-tested), the owner approves an inventory of cleanup targets, the cleanup is executed, and WorkMind passes post-cleanup checks.
- The only default cleanup behavior is **read-only inventory/dry-run**; deletion requires explicit approval for each affected category/path. Do not delete the last verified recovery point.

This file is deliberately short. The linked runbooks contain the operational details and are versioned with the project.
