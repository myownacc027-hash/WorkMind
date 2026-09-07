from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Iterator, TypeVar

import typer
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from packages.common.config import get_settings
from packages.common.locking import single_instance_lock
from packages.database.models import (
    Attachment,
    DocumentChunk,
    EmailLocation,
    EmailMessage,
    EmailThread,
    HistoryImportState,
    ProcessingJob,
)
from packages.database.session import SessionLocal
from packages.search.indexer import delete_points
from packages.search.qdrant import get_qdrant_client

app = typer.Typer(
    add_completion=False,
    help=(
        "Remove an excluded Outlook folder from WorkMind while "
        "preserving messages that also exist in another folder."
    ),
)
T = TypeVar("T")


def _batched(
    values: Iterable[T],
    size: int = 500,
) -> Iterator[list[T]]:
    batch: list[T] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _path_components(value: str) -> list[str]:
    return [
        part.strip().casefold()
        for part in re.split(r"[\\/]+", value)
        if part.strip()
    ]


def _matches_folder(
    path: str,
    folder_name: str,
) -> bool:
    return folder_name.strip().casefold() in _path_components(path)


def _affected_locations(
    session: Session,
    *,
    folder_name: str,
    store_name: str,
) -> list[EmailLocation]:
    query = select(EmailLocation)
    if store_name:
        query = query.where(
            func.lower(EmailLocation.store_display_name)
            == store_name.strip().casefold()
        )

    # Use a broad database filter, then enforce exact path-component
    # matching in Python to avoid deleting similarly named folders.
    query = query.where(
        func.lower(EmailLocation.folder_path).contains(
            folder_name.strip().casefold()
        )
    )
    return [
        location
        for location in session.scalars(query)
        if _matches_folder(
            location.folder_path,
            folder_name,
        )
    ]


def _all_locations_for_messages(
    session: Session,
    message_ids: set[uuid.UUID],
) -> dict[uuid.UUID, list[EmailLocation]]:
    result: dict[uuid.UUID, list[EmailLocation]] = {
        message_id: []
        for message_id in message_ids
    }
    for batch in _batched(message_ids):
        locations = session.scalars(
            select(EmailLocation).where(
                EmailLocation.message_id.in_(batch)
            )
        )
        for location in locations:
            result.setdefault(
                location.message_id,
                [],
            ).append(location)
    return result


def _delete_files(
    *,
    deleted_message_ids: set[uuid.UUID],
    raw_paths: list[str],
    attachment_paths: list[str],
) -> dict[str, int]:
    removed_files = 0
    removed_directories = 0
    settings = get_settings()

    directories: set[Path] = set()
    for raw_path in raw_paths:
        if raw_path:
            directories.add(Path(raw_path).parent)

    for message_id in deleted_message_ids:
        directories.add(
            settings.attachment_archive_dir
            / str(message_id)
        )

    legacy_attachment_root = (
        settings.attachment_archive_dir.resolve()
    )
    for attachment_path in attachment_paths:
        if not attachment_path:
            continue

        parent = Path(attachment_path).parent
        try:
            parent.resolve().relative_to(
                legacy_attachment_root
            )
        except ValueError:
            # Content-addressed objects are shared and live outside
            # the legacy per-message attachment tree.
            continue

        directories.add(parent)

    for directory in sorted(
        directories,
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            if directory.exists():
                file_count = sum(
                    1
                    for item in directory.rglob("*")
                    if item.is_file()
                )
                shutil.rmtree(directory)
                removed_files += file_count
                removed_directories += 1
        except OSError:
            # Database/Qdrant cleanup is authoritative. A locked local
            # file can be removed manually or by a later cleanup run.
            continue

    return {
        "removed_files": removed_files,
        "removed_directories": removed_directories,
    }


def _recount_threads(
    session: Session,
    thread_ids: set[uuid.UUID],
) -> tuple[int, int]:
    updated = 0
    deleted_threads = 0

    for thread_id in thread_ids:
        count = session.scalar(
            select(func.count())
            .select_from(EmailMessage)
            .where(EmailMessage.thread_id == thread_id)
        )
        thread = session.get(
            EmailThread,
            thread_id,
        )
        if thread is None:
            continue
        if int(count or 0) == 0:
            session.delete(thread)
            deleted_threads += 1
        else:
            thread.message_count = int(count)
            updated += 1

    return updated, deleted_threads


@app.command()
def purge(
    folder_name: str = typer.Option(
        "Журнал бесед",
        "--folder-name",
        help=(
            "Exact Outlook folder path component to remove."
        ),
    ),
    store_name: str = typer.Option(
        "",
        "--store-name",
        help=(
            "Optional exact Outlook store display name. "
            "Empty means all stores."
        ),
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help=(
            "Actually delete data. Without this flag the command "
            "only reports the impact."
        ),
    ),
) -> None:
    settings = get_settings()
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()

    with single_instance_lock(
        settings.outlook_operation_lock_file
    ) as acquired:
        if not acquired:
            raise typer.BadParameter(
                "Another WorkMind Outlook operation is running. "
                "Stop the history import and retry."
            )

        with SessionLocal() as session:
            target_locations = _affected_locations(
                session,
                folder_name=folder_name,
                store_name=store_name,
            )
            target_location_ids = {
                location.id
                for location in target_locations
            }
            affected_message_ids = {
                location.message_id
                for location in target_locations
            }
            all_locations = _all_locations_for_messages(
                session,
                affected_message_ids,
            )

            delete_message_ids: set[uuid.UUID] = set()
            preserve_message_ids: set[uuid.UUID] = set()
            remaining_location_by_message: dict[
                uuid.UUID,
                EmailLocation,
            ] = {}

            for message_id in affected_message_ids:
                remaining = [
                    location
                    for location in all_locations.get(
                        message_id,
                        [],
                    )
                    if location.id not in target_location_ids
                ]
                if remaining:
                    preserve_message_ids.add(message_id)
                    remaining_location_by_message[
                        message_id
                    ] = remaining[0]
                else:
                    delete_message_ids.add(message_id)

            chunk_count = 0
            point_ids: list[uuid.UUID] = []
            raw_paths: list[str] = []
            attachment_paths: list[str] = []
            thread_ids: set[uuid.UUID] = set()

            for batch in _batched(delete_message_ids):
                chunks = list(
                    session.scalars(
                        select(DocumentChunk).where(
                            DocumentChunk.source_type == "email",
                            DocumentChunk.source_id.in_(batch),
                        )
                    )
                )
                chunk_count += len(chunks)
                point_ids.extend(
                    chunk.qdrant_point_id
                    for chunk in chunks
                    if chunk.qdrant_point_id is not None
                )

                messages = list(
                    session.scalars(
                        select(EmailMessage).where(
                            EmailMessage.id.in_(batch)
                        )
                    )
                )
                raw_paths.extend(
                    message.raw_json_path
                    for message in messages
                    if message.raw_json_path
                )
                thread_ids.update(
                    message.thread_id
                    for message in messages
                    if message.thread_id
                )

                attachment_paths.extend(
                    path
                    for path in session.scalars(
                        select(Attachment.archive_path).where(
                            Attachment.message_id.in_(batch),
                            Attachment.archive_path.is_not(None),
                        )
                    )
                    if path
                )

            report = {
                "mode": "apply" if apply else "dry_run",
                "folder_name": folder_name,
                "store_name": store_name or None,
                "target_locations": len(target_locations),
                "affected_messages": len(affected_message_ids),
                "messages_to_delete": len(delete_message_ids),
                "messages_to_preserve": len(preserve_message_ids),
                "chunks_to_delete": chunk_count,
                "qdrant_points_to_delete": len(point_ids),
                "threads_to_recount": len(thread_ids),
            }

            if not apply:
                print(
                    json.dumps(
                        report,
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return

            manifest_path = (
                settings.run_dir
                / (
                    "purge-folder-"
                    f"{started_at.strftime('%Y%m%d-%H%M%S')}"
                    ".json"
                )
            )
            manifest_path.write_text(
                json.dumps(
                    {
                        **report,
                        "message_ids": [
                            str(value)
                            for value in sorted(
                                delete_message_ids,
                                key=str,
                            )
                        ],
                        "qdrant_point_ids": [
                            str(value)
                            for value in point_ids
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            # Remove only the excluded physical location from messages
            # that also occur elsewhere. Keep their canonical source
            # fields valid by moving them to a remaining location.
            for message_id, remaining in (
                remaining_location_by_message.items()
            ):
                message = session.get(
                    EmailMessage,
                    message_id,
                )
                if message is None:
                    continue
                if _matches_folder(
                    message.folder_path,
                    folder_name,
                ):
                    message.outlook_entry_id = (
                        remaining.outlook_entry_id
                    )
                    message.outlook_store_id = (
                        remaining.outlook_store_id
                    )
                    message.folder_path = (
                        remaining.folder_path
                    )

            for batch in _batched(target_location_ids):
                session.execute(
                    delete(EmailLocation).where(
                        EmailLocation.id.in_(batch)
                    ),
                    execution_options={
                        "synchronize_session": False
                    },
                )

            for batch in _batched(delete_message_ids):
                session.execute(
                    delete(DocumentChunk).where(
                        DocumentChunk.source_type == "email",
                        DocumentChunk.source_id.in_(batch),
                    ),
                    execution_options={
                        "synchronize_session": False
                    },
                )
                session.execute(
                    delete(ProcessingJob).where(
                        ProcessingJob.source_type == "email",
                        ProcessingJob.source_id.in_(batch),
                    ),
                    execution_options={
                        "synchronize_session": False
                    },
                )
                session.execute(
                    delete(EmailMessage).where(
                        EmailMessage.id.in_(batch)
                    ),
                    execution_options={
                        "synchronize_session": False
                    },
                )

            # Remove saved import cursor(s) for the excluded branch.
            history_states = list(
                session.scalars(
                    select(HistoryImportState)
                )
            )
            removed_states = 0
            for state in history_states:
                if _matches_folder(
                    state.folder_path,
                    folder_name,
                ):
                    session.delete(state)
                    removed_states += 1

            updated_threads, deleted_threads = _recount_threads(
                session,
                thread_ids,
            )
            session.commit()

    qdrant_deleted = 0
    qdrant_error: str | None = None
    try:
        client = get_qdrant_client()
        for batch in _batched(point_ids, size=1000):
            delete_points(client, batch)
            qdrant_deleted += len(batch)
    except Exception as exc:
        qdrant_error = str(exc)

    file_result = _delete_files(
        deleted_message_ids=delete_message_ids,
        raw_paths=raw_paths,
        attachment_paths=attachment_paths,
    )

    result = {
        **report,
        "status": (
            "completed"
            if qdrant_error is None
            else "completed_with_qdrant_warning"
        ),
        "history_states_removed": removed_states,
        "threads_updated": updated_threads,
        "threads_deleted": deleted_threads,
        "qdrant_points_deleted": qdrant_deleted,
        "qdrant_error": qdrant_error,
        **file_result,
        "manifest": str(manifest_path),
        "duration_seconds": round(
            time.monotonic() - started_monotonic,
            3,
        ),
    }
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
