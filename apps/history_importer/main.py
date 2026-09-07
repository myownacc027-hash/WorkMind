from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator

import structlog
import typer
from sqlalchemy import select

from apps.history_importer.common import (
    now_iso,
    write_status,
)
from apps.outlook_collector.outlook import (
    MAIL_ITEM_CLASS,
    _folder_entry_id,
    _folder_path,
    _safe_text,
    _to_utc,
    connect_outlook,
    ingest_mail_item,
)
from apps.processing_worker.worker import run_batch
from packages.common.config import get_settings
from packages.common.locking import single_instance_lock
from packages.common.logging import configure_logging
from packages.database.models import (
    HistoryImportState,
    Mailbox,
)
from packages.database.repositories import (
    get_or_create_mailbox,
)
from packages.database.session import SessionLocal

app = typer.Typer(
    add_completion=False,
    help=(
        "Import historical corporate Outlook mail from the "
        "current mailbox and Online Archive."
    ),
)
logger = structlog.get_logger(__name__)
OL_MAIL_ITEM = 0


@dataclass(slots=True)
class HistoricalFolder:
    store_id: str
    store_display_name: str
    source_kind: str
    folder_id: str
    folder_path: str
    date_attr: str
    folder: Any | None = None
    reconnect_count: int = 0


class OutlookFolderUnavailable(RuntimeError):
    """The Outlook/Exchange folder COM object became unavailable."""


def _find_store(
    namespace: Any,
    display_name: str,
) -> Any:
    target = display_name.strip().casefold()
    available: list[str] = []
    exact: list[Any] = []
    partial: list[Any] = []
    stores = namespace.Stores

    for index in range(1, stores.Count + 1):
        store = stores.Item(index)
        current = _safe_text(
            store.DisplayName
        ).strip()
        available.append(current)
        if current.casefold() == target:
            exact.append(store)
        elif target and target in current.casefold():
            partial.append(store)

    if len(exact) == 1:
        return exact[0]
    if not exact and len(partial) == 1:
        return partial[0]

    raise RuntimeError(
        f"Outlook store not found or ambiguous: {display_name}. "
        f"Available: {', '.join(available)}"
    )


def _is_excluded(
    folder: Any,
    excluded: set[str],
) -> bool:
    name = _safe_text(
        getattr(folder, "Name", "")
    ).strip().casefold()
    return name in excluded


def _is_mail_folder(folder: Any) -> bool:
    try:
        return int(folder.DefaultItemType) == OL_MAIL_ITEM
    except Exception:
        return True


def _date_attr(folder: Any) -> str:
    name = _safe_text(
        getattr(folder, "Name", "")
    ).strip().casefold()
    if name in {
        "отправленные",
        "sent items",
        "sent",
    }:
        return "SentOn"
    return "ReceivedTime"


def _walk_folders(
    parent: Any,
    *,
    excluded: set[str],
) -> Iterator[Any]:
    try:
        count = parent.Folders.Count
    except Exception:
        return

    for index in range(1, count + 1):
        try:
            folder = parent.Folders.Item(index)
        except Exception as exc:
            logger.warning(
                "history_folder_unavailable",
                parent=_folder_path(parent),
                index=index,
                error=str(exc),
            )
            continue

        if _is_excluded(folder, excluded):
            continue
        if _is_mail_folder(folder):
            yield folder
        yield from _walk_folders(
            folder,
            excluded=excluded,
        )


def _folder_specs(
    store: Any,
    *,
    source_kind: str,
    excluded: set[str],
) -> list[HistoricalFolder]:
    display_name = _safe_text(
        store.DisplayName
    ).strip()
    store_id = (
        _safe_text(
            getattr(store, "StoreID", "")
        ).strip()
        or f"display:{display_name}"
    )
    root = store.GetRootFolder()
    result: list[HistoricalFolder] = []

    for folder in _walk_folders(
        root,
        excluded=excluded,
    ):
        folder_id = (
            _folder_entry_id(folder)
            or _folder_path(folder)
        )
        result.append(
            HistoricalFolder(
                store_id=store_id,
                store_display_name=display_name,
                source_kind=source_kind,
                folder_id=folder_id,
                folder_path=_folder_path(folder),
                date_attr=_date_attr(folder),
            )
        )
    return result




def _retryable_outlook_error(exc: Exception) -> bool:
    if isinstance(exc, AttributeError):
        return True

    message = str(exc).casefold()
    tokens = (
        "item.items",
        "interface not supported",
        "интерфейс не поддерживается",
        "microsoft exchange",
        "ошибка при входе на сервер",
        "rpc server",
        "rpc-сервер",
        "disconnected",
        "connection",
        "соединен",
        "operation failed",
        "операция не выполнена",
    )
    return any(token in message for token in tokens)


def _open_live_folder(
    spec: HistoricalFolder,
    *,
    attempts: int = 5,
    delay_seconds: float = 5.0,
) -> tuple[Any, Any, Any]:
    """Return fresh Application, NameSpace, and Folder COM objects."""
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            application, namespace = connect_outlook()
            folder = namespace.GetFolderFromID(
                spec.folder_id,
                spec.store_id,
            )

            # Probe the wrapper now. A stale pywin32 wrapper can exist
            # while failing only when the Items property is requested.
            _ = folder.Items
            spec.folder = folder

            if attempt > 1:
                logger.info(
                    "history_folder_reconnected",
                    store=spec.store_display_name,
                    folder=spec.folder_path,
                    attempt=attempt,
                )
            return application, namespace, folder
        except Exception as exc:
            last_error = exc
            spec.folder = None
            logger.warning(
                "history_folder_reconnect_wait",
                store=spec.store_display_name,
                folder=spec.folder_path,
                attempt=attempt,
                max_attempts=attempts,
                error=str(exc),
            )
            if attempt < attempts:
                time.sleep(delay_seconds)

    raise OutlookFolderUnavailable(
        "Could not reopen Outlook folder "
        f"{spec.folder_path!r} after {attempts} attempts: "
        f"{last_error}"
    ) from last_error


def _resilient_history_items(
    spec: HistoricalFolder,
    *,
    since: datetime,
    state: HistoryImportState,
    reconnect_attempts: int = 5,
) -> Iterator[tuple[Any, datetime | None]]:
    """Resume the same folder after Outlook or Exchange reconnects."""
    reconnects = 0

    while True:
        application = None
        namespace = None
        folder = None
        try:
            application, namespace, folder = _open_live_folder(
                spec,
                attempts=5,
            )
            yield from _history_items(
                spec,
                since=since,
                state=state,
            )
            return
        except Exception as exc:
            if not _retryable_outlook_error(exc):
                raise

            reconnects += 1
            spec.reconnect_count += 1
            spec.folder = None

            logger.warning(
                "history_folder_connection_lost",
                store=spec.store_display_name,
                folder=spec.folder_path,
                reconnect=reconnects,
                max_reconnects=reconnect_attempts,
                cursor_entry_id=state.cursor_entry_id,
                error=str(exc),
            )

            if reconnects >= reconnect_attempts:
                raise OutlookFolderUnavailable(
                    "Outlook/Exchange connection remained unavailable "
                    f"for folder {spec.folder_path!r}: {exc}"
                ) from exc

            time.sleep(5.0)
        finally:
            # Drop live wrappers before the next folder/reconnect.
            spec.folder = None
            folder = None
            namespace = None
            application = None
def _item_time(
    item: Any,
    date_attr: str,
) -> datetime | None:
    preferred = _to_utc(
        getattr(item, date_attr, None)
    )
    if preferred:
        return preferred
    alternative = (
        "SentOn"
        if date_attr == "ReceivedTime"
        else "ReceivedTime"
    )
    return _to_utc(
        getattr(item, alternative, None)
    )


def _history_items(
    spec: HistoricalFolder,
    *,
    since: datetime,
    state: HistoryImportState,
) -> Iterator[tuple[Any, datetime | None]]:
    if spec.folder is None:
        raise OutlookFolderUnavailable(
            f"Folder is not open: {spec.folder_path}"
        )

    try:
        items = spec.folder.Items
        try:
            items.Sort(f"[{spec.date_attr}]", True)
        except Exception:
            fallback = (
                "SentOn"
                if spec.date_attr == "ReceivedTime"
                else "ReceivedTime"
            )
            items.Sort(f"[{fallback}]", True)
        item_count = int(items.Count)
    except Exception as exc:
        raise OutlookFolderUnavailable(
            f"Could not open Items for {spec.folder_path}: {exc}"
        ) from exc

    cursor_found = not bool(state.cursor_entry_id)

    for index in range(1, item_count + 1):
        try:
            item = items.Item(index)
            if (
                int(getattr(item, "Class", 0))
                != MAIL_ITEM_CLASS
            ):
                continue

            occurred_at = _item_time(
                item,
                spec.date_attr,
            )
            if occurred_at and occurred_at < since:
                break

            if not cursor_found:
                entry_id = _safe_text(
                    getattr(item, "EntryID", "")
                )
                if entry_id == state.cursor_entry_id:
                    cursor_found = True
                    continue
                if (
                    state.cursor_timestamp
                    and occurred_at
                    and occurred_at < state.cursor_timestamp
                ):
                    cursor_found = True
                else:
                    continue

            yield item, occurred_at
        except Exception as exc:
            if _retryable_outlook_error(exc):
                raise OutlookFolderUnavailable(
                    "Outlook connection was lost while reading "
                    f"{spec.folder_path}, item {index}: {exc}"
                ) from exc
            logger.warning(
                "history_item_skipped",
                folder=spec.folder_path,
                index=index,
                error=str(exc),
            )


def _get_state(
    session,
    *,
    mailbox_id,
    spec: HistoricalFolder,
    since: datetime,
    reset: bool,
) -> HistoryImportState:
    state = session.scalar(
        select(HistoryImportState).where(
            HistoryImportState.outlook_store_id
            == spec.store_id,
            HistoryImportState.folder_entry_id
            == spec.folder_id,
        )
    )
    if state is None:
        state = HistoryImportState(
            mailbox_id=mailbox_id,
            outlook_store_id=spec.store_id,
            store_display_name=spec.store_display_name,
            folder_entry_id=spec.folder_id,
            folder_path=spec.folder_path,
            date_attr=spec.date_attr,
            source_kind=spec.source_kind,
            status="pending",
            oldest_allowed_at=since,
            metadata_json={},
        )
        session.add(state)
        session.flush()

    needs_reset = (
        reset
        or (
            state.oldest_allowed_at is not None
            and state.oldest_allowed_at > since
        )
    )
    if needs_reset:
        state.status = "pending"
        state.cursor_entry_id = None
        state.cursor_timestamp = None
        state.oldest_allowed_at = since
        state.last_error = None
        state.seen_count = 0
        state.created_count = 0
        state.deduplicated_count = 0
        state.unchanged_count = 0
        state.failed_count = 0

    state.store_display_name = spec.store_display_name
    state.folder_path = spec.folder_path
    state.date_attr = spec.date_attr
    state.source_kind = spec.source_kind
    return state


def _drain_indexing(
    *,
    batch_size: int,
    max_batches: int,
) -> dict[str, int | bool]:
    settings = get_settings()
    totals = {
        "claimed": 0,
        "completed": 0,
        "chunks": 0,
    }
    batches = 0
    last_claimed = 0

    for _ in range(max_batches):
        result = run_batch(
            SessionLocal,
            settings=settings,
            batch_size=batch_size,
        )
        batches += 1
        last_claimed = int(result["claimed"])
        for key in totals:
            totals[key] += int(result[key])
        if last_claimed == 0:
            break
        if (
            result["claimed"] > 0
            and result["completed"] == 0
        ):
            break

    return {
        **totals,
        "batches": batches,
        "queue_drained": last_claimed == 0,
    }


@app.command()
def import_history(
    primary_store: str = typer.Option(
        "",
        help="Display name of the current corporate store.",
    ),
    archive_store: str = typer.Option(
        "",
        help="Display name of the Online Archive store.",
    ),
    years: int = typer.Option(
        5,
        min=1,
        max=30,
        help="Number of years to import.",
    ),
    max_messages: int = typer.Option(
        0,
        min=0,
        help=(
            "Stop after this many seen messages; "
            "0 means no limit."
        ),
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Reset saved per-folder cursors.",
    ),
    include_system_folders: bool = typer.Option(
        False,
        "--include-system-folders",
        help=(
            "Also scan Deleted, Junk, Drafts, "
            "Sync Issues, and similar folders."
        ),
    ),
    index_after_import: bool = typer.Option(
        True,
        "--index/--no-index",
        help="Index queued messages after collection.",
    ),
    index_batch_size: int = typer.Option(
        25,
        min=1,
        max=100,
    ),
    max_index_batches: int = typer.Option(
        1000,
        min=1,
        max=10000,
    ),
) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    primary_store = (
        primary_store
        or settings.history_primary_store
        or settings.outlook_account_smtp
    )
    archive_store = (
        archive_store
        or settings.history_archive_store
        or (
            "Сетевой архив — "
            f"{settings.outlook_account_smtp}"
        )
    )
    if not primary_store:
        raise typer.BadParameter(
            "Primary store is not configured."
        )
    if not archive_store:
        raise typer.BadParameter(
            "Archive store is not configured."
        )

    started_at = now_iso()
    started_monotonic = time.monotonic()
    since = datetime.now(UTC) - timedelta(
        days=years * 365 + years // 4
    )
    base_status = {
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "duration_seconds": None,
        "years": years,
        "since": since.isoformat(),
        "primary_store": primary_store,
        "archive_store": archive_store,
        "max_messages": max_messages,
    }
    write_status(
        settings.history_status_file,
        settings.history_log_file,
        base_status,
    )

    totals = {
        "folders_discovered": 0,
        "folders_completed": 0,
        "folders_skipped": 0,
        "seen": 0,
        "created": 0,
        "updated": 0,
        "deduplicated": 0,
        "unchanged": 0,
        "failed": 0,
        "skipped_unsupported": 0,
        "folder_reconnects": 0,
        "attachments": 0,
        "attachment_failed": 0,
    }
    stopped_by_limit = False

    try:
        with single_instance_lock(
            settings.outlook_operation_lock_file
        ) as acquired:
            if not acquired:
                result = {
                    **base_status,
                    "status": "skipped",
                    "reason": (
                        "another_outlook_operation_is_running"
                    ),
                    "finished_at": now_iso(),
                    "duration_seconds": round(
                        time.monotonic()
                        - started_monotonic,
                        3,
                    ),
                }
                write_status(
                    settings.history_status_file,
                    settings.history_log_file,
                    result,
                )
                print(
                    json.dumps(
                        result,
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return

            _, namespace = connect_outlook()
            primary = _find_store(
                namespace,
                primary_store,
            )
            archive = _find_store(
                namespace,
                archive_store,
            )

            excluded: set[str] = set()
            if not include_system_folders:
                excluded = {
                    value.casefold()
                    for value in (
                        settings.history_excluded_folders
                    )
                }

            specs = [
                *_folder_specs(
                    primary,
                    source_kind="primary",
                    excluded=excluded,
                ),
                *_folder_specs(
                    archive,
                    source_kind="archive",
                    excluded=excluded,
                ),
            ]
            totals["folders_discovered"] = len(specs)

            logical_mailbox_name = (
                settings.outlook_mailbox_name
                or settings.outlook_account_smtp
                or primary_store
            )
            primary_store_id = (
                _safe_text(
                    getattr(primary, "StoreID", "")
                ).strip()
                or f"display:{primary_store}"
            )

            with SessionLocal() as session:
                mailbox = get_or_create_mailbox(
                    session,
                    name=logical_mailbox_name,
                    store_id=primary_store_id,
                )
                session.commit()
                mailbox_id = mailbox.id

            for spec in specs:
                with SessionLocal() as session:
                    state = _get_state(
                        session,
                        mailbox_id=mailbox_id,
                        spec=spec,
                        since=since,
                        reset=reset,
                    )
                    already_complete = (
                        state.status == "completed"
                        and state.oldest_allowed_at
                        and state.oldest_allowed_at <= since
                    )
                    if already_complete:
                        totals["folders_skipped"] += 1
                        session.commit()
                        continue
                    state.status = "running"
                    state.last_error = None
                    session.commit()

                logger.info(
                    "history_folder_started",
                    store=spec.store_display_name,
                    folder=spec.folder_path,
                    source_kind=spec.source_kind,
                )
                reached_folder_end = True

                with SessionLocal() as session:
                    state = _get_state(
                        session,
                        mailbox_id=mailbox_id,
                        spec=spec,
                        since=since,
                        reset=False,
                    )
                    mailbox = session.get(
                        Mailbox,
                        mailbox_id,
                    )
                    if mailbox is None:
                        raise RuntimeError(
                            "Logical mailbox disappeared."
                        )

                    for item, occurred_at in _resilient_history_items(
                        spec,
                        since=since,
                        state=state,
                    ):
                        if (
                            max_messages
                            and totals["seen"]
                            >= max_messages
                        ):
                            stopped_by_limit = True
                            reached_folder_end = False
                            break

                        totals["seen"] += 1
                        state.seen_count += 1

                        try:
                            entry_id = _safe_text(
                                getattr(item, "EntryID", "")
                            ).strip()
                        except Exception as exc:
                            totals["skipped_unsupported"] += 1
                            logger.warning(
                                "history_item_unsupported",
                                store=spec.store_display_name,
                                folder=spec.folder_path,
                                error=str(exc),
                            )
                            continue

                        if not entry_id:
                            totals["skipped_unsupported"] += 1
                            logger.warning(
                                "history_item_unsupported",
                                store=spec.store_display_name,
                                folder=spec.folder_path,
                                error="Outlook item has no EntryID",
                            )
                            continue

                        try:
                            result = ingest_mail_item(
                                session,
                                settings,
                                mailbox=mailbox,
                                item=item,
                                store_id=spec.store_id,
                                store_display_name=(
                                    spec.store_display_name
                                ),
                                folder_path=spec.folder_path,
                                folder_entry_id=spec.folder_id,
                                source_kind=spec.source_kind,
                            )
                            outcome = str(
                                result["outcome"]
                            )
                            totals[outcome] += 1
                            if outcome == "created":
                                state.created_count += 1
                            elif outcome == "deduplicated":
                                state.deduplicated_count += 1
                            elif outcome == "unchanged":
                                state.unchanged_count += 1

                            totals["attachments"] += int(
                                result["attachments"]
                            )
                            totals[
                                "attachment_failed"
                            ] += int(
                                result["attachment_failed"]
                            )
                            state.cursor_entry_id = entry_id
                            state.cursor_timestamp = occurred_at
                            state.last_error = None
                            session.commit()
                        except Exception as exc:
                            session.rollback()
                            state = _get_state(
                                session,
                                mailbox_id=mailbox_id,
                                spec=spec,
                                since=since,
                                reset=False,
                            )
                            state.failed_count += 1
                            state.last_error = str(exc)
                            totals["failed"] += 1
                            reached_folder_end = False
                            session.commit()
                            logger.exception(
                                "history_message_failed",
                                store=spec.store_display_name,
                                folder=spec.folder_path,
                                error=str(exc),
                            )
                            # Preserve the cursor at the previous successful
                            # item so this failed message is retried next run.
                            break

                        if totals["seen"] % 100 == 0:
                            write_status(
                                settings.history_status_file,
                                settings.history_log_file,
                                {
                                    **base_status,
                                    "status": "running",
                                    "current_store": (
                                        spec.store_display_name
                                    ),
                                    "current_folder": (
                                        spec.folder_path
                                    ),
                                    "totals": totals,
                                },
                            )

                    if reached_folder_end:
                        state.status = "completed"
                        state.oldest_allowed_at = since
                        totals["folders_completed"] += 1
                    else:
                        state.status = "paused"
                    session.commit()

                totals["folder_reconnects"] += spec.reconnect_count
                spec.reconnect_count = 0
                logger.info(
                    "history_folder_finished",
                    store=spec.store_display_name,
                    folder=spec.folder_path,
                    completed=reached_folder_end,
                )
                if stopped_by_limit:
                    break

        # Index after releasing the Outlook lock, so regular mail sync can run.
        indexing = (
            _drain_indexing(
                batch_size=index_batch_size,
                max_batches=max_index_batches,
            )
            if index_after_import
            else {
                "claimed": 0,
                "completed": 0,
                "chunks": 0,
                "batches": 0,
                "queue_drained": False,
            }
        )
        result = {
            **base_status,
            "status": (
                "paused"
                if (
                    stopped_by_limit
                    or totals["failed"] > 0
                    or (
                        totals["folders_completed"]
                        + totals["folders_skipped"]
                        < totals["folders_discovered"]
                    )
                )
                else "completed"
            ),
            "finished_at": now_iso(),
            "duration_seconds": round(
                time.monotonic()
                - started_monotonic,
                3,
            ),
            "totals": totals,
            "indexing": indexing,
        }
        write_status(
            settings.history_status_file,
            settings.history_log_file,
            result,
        )
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )
    except OutlookFolderUnavailable as exc:
        result = {
            **base_status,
            "status": "paused",
            "reason": "outlook_temporarily_unavailable",
            "error": str(exc),
            "finished_at": now_iso(),
            "duration_seconds": round(
                time.monotonic()
                - started_monotonic,
                3,
            ),
            "totals": totals,
        }
        write_status(
            settings.history_status_file,
            settings.history_log_file,
            result,
        )
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )
        raise typer.Exit(code=2)
    except KeyboardInterrupt:
        result = {
            **base_status,
            "status": "paused",
            "reason": "keyboard_interrupt",
            "finished_at": now_iso(),
            "duration_seconds": round(
                time.monotonic()
                - started_monotonic,
                3,
            ),
            "totals": totals,
        }
        write_status(
            settings.history_status_file,
            settings.history_log_file,
            result,
        )
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )
        raise typer.Exit(code=130)


if __name__ == "__main__":
    app()
