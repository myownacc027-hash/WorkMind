"""Read-only, resumable-import-aware inventory of remaining Outlook candidates.

Never ingest, open attachment contents, or write database state. The default
counts table candidates (possibly non-MailItems); verify-mail uses the same
MailItem acceptance and EntryID checks as the importer. A snapshot is not a
transactional view of Exchange: moves/deletions/arrivals can change it later.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy import select

from apps.history_importer import main as history
from packages.common.config import get_settings
from packages.common.locking import single_instance_lock
from packages.common.logging import configure_logging
from packages.database.models import HistoryImportState
from packages.database.session import SessionLocal

logger = structlog.get_logger(__name__)
VERSION = 1


def count_remaining(
    spec: history.HistoricalFolder,
    state: Any,
    *,
    since: datetime,
    verify_mail: bool,
) -> int:
    if spec.folder is None:
        raise history.OutlookFolderUnavailable("Inventory folder is not open.")
    namespace = spec.folder.Session
    committed_ids = history._cursor_group_ids(state)
    checkpoint = history._cursor_utc(state.cursor_timestamp)
    resume_at = (
        checkpoint if committed_ids else history._legacy_resume_at(namespace, spec, state)
    )
    count = 0
    for _table, candidates in history._history_windows(
        spec, since=since, resume_at=resume_at,
        undated_only=bool(committed_ids) and checkpoint is None,
        resumed=bool(state.cursor_entry_id),
    ):
        if verify_mail:
            count += sum(
                1 for _ in history._yield_history_candidates(
                    namespace, spec, candidates,
                    checkpoint_time=checkpoint, committed_ids=committed_ids,
                )
            )
        else:
            # Only open the checkpoint group, where canonical EntryID checks
            # are needed to exclude already-committed aliases. Elsewhere count
            # rows without opening mail; the fast count may include non-mail.
            boundary = []
            for row in candidates:
                if committed_ids and row[1] == checkpoint:
                    boundary.append(row)
                else:
                    count += 1
            count += sum(
                1 for _ in history._yield_history_candidates(
                    namespace, spec, boundary,
                    checkpoint_time=checkpoint, committed_ids=committed_ids,
                )
            )
        logger.info("history_inventory_progress", folder=spec.folder_path, remaining=count)
    return count


def folder_baseline(state: Any | None, since: datetime) -> tuple[Any, datetime, bool]:
    if state is None:
        state = SimpleNamespace(
            id=None, status="pending", cursor_entry_id=None, cursor_timestamp=None,
            metadata_json={}, oldest_allowed_at=None, seen_count=0, created_count=0,
            failed_count=0,
        )
    # Do not publish an inventory against a baseline the import would reset.
    if state.oldest_allowed_at is not None and state.oldest_allowed_at > since:
        raise RuntimeError("Inventory cutoff would reset an existing history state.")
    cutoff = min(since, state.oldest_allowed_at or since)
    complete = (
        state.status == "completed"
        and state.oldest_allowed_at is not None
        and state.oldest_allowed_at <= since
    )
    return state, cutoff, complete


def atomic_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def inventory(*, since: datetime, verify_mail: bool, destination: Path, run_id: str) -> None:
    settings = get_settings()
    primary_name = settings.history_primary_store or settings.outlook_account_smtp
    archive_name = settings.history_archive_store or (
        "\u0421\u0435\u0442\u0435\u0432\u043e\u0439 \u0430\u0440\u0445\u0438\u0432 \u2014 "
        f"{settings.outlook_account_smtp}"
    )
    if not primary_name or not archive_name:
        raise RuntimeError("History stores are not configured.")
    started_at = datetime.now(UTC)
    with single_instance_lock(settings.outlook_operation_lock_file) as acquired:
        if not acquired:
            raise RuntimeError("Another Outlook operation is active. Inventory skipped.")
        application, namespace = history.connect_outlook()
        excluded = {name.casefold() for name in settings.history_excluded_folders}
        specs = [
            *history._folder_specs(history._find_store(namespace, primary_name), source_kind="primary", excluded=excluded),
            *history._folder_specs(history._find_store(namespace, archive_name), source_kind="archive", excluded=excluded),
        ]
        keys = [(spec.store_id, spec.folder_id) for spec in specs]
        if len(keys) != len(set(keys)) or not specs:
            raise RuntimeError("Inventory folder scope is empty or ambiguous.")
        with SessionLocal() as session:
            states = {
                (state.outlook_store_id, state.folder_entry_id): state
                for state in session.scalars(
                    select(HistoryImportState).where(
                        HistoryImportState.outlook_store_id.in_({spec.store_id for spec in specs})
                    )
                )
            }
        # All fields are loaded. The SQL session is closed before remote COM work.
        plans = [(spec, *folder_baseline(states.get((spec.store_id, spec.folder_id)), since)) for spec in specs]
        rows = []
        for spec, state, cutoff, complete in plans:
            logger.info("history_inventory_folder_started", folder=spec.folder_path, completed=complete)
            remaining = 0
            if not complete:
                folder_application, folder_namespace, folder = history._open_live_folder(spec)
                try:
                    remaining = count_remaining(spec, state, since=cutoff, verify_mail=verify_mail)
                finally:
                    spec.folder = None
                    folder = folder_namespace = folder_application = None
            rows.append({
                "store_id": spec.store_id, "folder_id": spec.folder_id,
                "folder": spec.folder_path, "source_kind": spec.source_kind,
                "state_id": str(state.id) if state.id is not None else None,
                "cutoff": int(round(cutoff.timestamp())),
                "seen": state.seen_count, "created": state.created_count,
                "failed": state.failed_count, "completed": complete,
                "remaining": remaining,
            })
            logger.info("history_inventory_folder_finished", folder=spec.folder_path, remaining=remaining)
        snapshot = {
            "version": VERSION, "run_id": run_id,
            "mode": "verified_mail" if verify_mail else "metadata_candidates",
            "since": since.isoformat(),
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "primary_store": primary_name, "archive_store": archive_name,
            "folders": rows,
            "baseline_seen": sum(row["seen"] for row in rows),
            "remaining_at_snapshot": sum(row["remaining"] for row in rows),
        }
        atomic_snapshot(destination, snapshot)
        logger.info("history_inventory_completed", folders=len(rows), remaining=snapshot["remaining_at_snapshot"], mode=snapshot["mode"])
        _ = application, namespace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--since-at", required=True)
    parser.add_argument("--mode", choices=("metadata", "verify-mail"), default="metadata")
    args = parser.parse_args()
    if not re.fullmatch(r"history-mail-first-\d{8}-\d{6}", args.run_id):
        parser.error("Invalid runner session ID.")
    since = history._resolve_since(5, args.since_at)
    settings = get_settings()
    configure_logging(settings.log_level)
    directory = settings.logs_dir / args.run_id
    if not directory.is_dir():
        parser.error("Runner session directory does not exist.")
    destination = directory / "history-inventory.json"
    if destination.exists():
        parser.error("Inventory already exists for this runner session.")
    inventory(since=since, verify_mail=args.mode == "verify-mail", destination=destination, run_id=args.run_id)


if __name__ == "__main__":
    main()
