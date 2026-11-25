from __future__ import annotations

import ctypes
import json
import time
from ctypes import wintypes
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
    unavailable: list[int] = []
    stores = namespace.Stores

    for index in range(1, stores.Count + 1):
        try:
            store = stores.Item(index)
            current = _safe_text(store.DisplayName).strip()
        except Exception as exc:
            unavailable.append(index)
            logger.warning(
                "history_store_unavailable", index=index, error=str(exc)
            )
            continue
        available.append(current)
        if current.casefold() == target:
            exact.append(store)
        elif target and target in current.casefold():
            partial.append(store)

    if len(exact) == 1:
        return exact[0]
    if not exact and len(partial) == 1 and not unavailable:
        return partial[0]

    raise OutlookFolderUnavailable(
        f"Outlook store not found or ambiguous: {display_name}. "
        f"Available: {', '.join(available)}; unavailable indexes: {unavailable}"
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
    if isinstance(exc, (AttributeError, OutlookFolderUnavailable)):
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

            # GetTable in _history_items validates the live folder.
            # Do not open the full Items collection just to probe it.
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


# MAPI date properties return UTC, unlike built-in local-time columns.
_TABLE_RECEIVED = "http://schemas.microsoft.com/mapi/proptag/0x0E060040"
_TABLE_SENT = "http://schemas.microsoft.com/mapi/proptag/0x00390040"
_TABLE_BATCH_SIZE = 512
_TABLE_CURSOR_VERSION = 2
_TABLE_CURSOR_KEY = "history_table_cursor_version"
_TABLE_CURSOR_IDS_KEY = "history_table_cursor_group_ids"
_MIN_UTC = datetime.min.replace(tzinfo=UTC)
_INITIAL_WINDOW = timedelta(days=7)
_MAX_WINDOW = timedelta(days=90)


def _table_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime) or value.year >= 4501:
        return None
    return value.replace(tzinfo=UTC)


def _cursor_utc(value: datetime | None) -> datetime | None:
    if value is None or value.year >= 4501:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _minute_floor(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _format_dasl_utc(value: datetime) -> str:
    """DASL uses UTC clock values, formatted in the Windows user locale.

    Outlook date comparisons do not support seconds. Use the same Windows
    date/time formatting APIs as the user session rather than a fixed US
    or ISO literal which may be interpreted incorrectly by Outlook.
    """
    value = value.astimezone(UTC)

    class SystemTime(ctypes.Structure):
        _fields_ = [
            (name, wintypes.WORD)
            for name in (
                "year", "month", "weekday", "day",
                "hour", "minute", "second", "millisecond",
            )
        ]

    stamp = SystemTime(
        value.year, value.month, (value.weekday() + 1) % 7,
        value.day, value.hour, value.minute, 0, 0,
    )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    date_format = kernel.GetDateFormatEx
    date_format.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(SystemTime),
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_int, wintypes.LPCWSTR,
    ]
    date_format.restype = ctypes.c_int
    time_format = kernel.GetTimeFormatEx
    time_format.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(SystemTime),
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_int,
    ]
    time_format.restype = ctypes.c_int
    date = ctypes.create_unicode_buffer(256)
    clock = ctypes.create_unicode_buffer(256)
    # LOCALE_NAME_USER_DEFAULT, DATE_SHORTDATE / TIME_NOSECONDS.
    if not date_format(None, 1, ctypes.byref(stamp), None, date, len(date), None):
        raise ctypes.WinError(ctypes.get_last_error())
    if not time_format(None, 2, ctypes.byref(stamp), None, clock, len(clock)):
        raise ctypes.WinError(ctypes.get_last_error())
    return f"{date.value} {clock.value}"


def _effective_table_time(
    received: Any, sent: Any, date_attr: str
) -> datetime | None:
    received_at = _table_utc(received)
    sent_at = _table_utc(sent)
    if date_attr == "ReceivedTime":
        return received_at or sent_at
    return sent_at or received_at


def _history_filter(
    date_attr: str, lower: datetime | None, upper: datetime | None
) -> str:
    primary, alternate = (
        (_TABLE_RECEIVED, _TABLE_SENT)
        if date_attr == "ReceivedTime"
        else (_TABLE_SENT, _TABLE_RECEIVED)
    )
    p, a = f'"{primary}"', f'"{alternate}"'
    if lower is None:
        if upper is not None:
            raise ValueError("Undated filter cannot have an upper bound.")
        return f"@SQL=({p} IS NULL AND {a} IS NULL)"
    low = _format_dasl_utc(lower).replace("'", "''")
    high = (
        _format_dasl_utc(upper).replace("'", "''")
        if upper is not None else None
    )

    def date_range(prop: str) -> str:
        result = f"{prop} >= '{low}'"
        if high is not None:
            result += f" AND {prop} < '{high}'"
        return f"({result})"

    return f"@SQL=({date_range(p)} OR ({p} IS NULL AND {date_range(a)}))"


def _history_candidates(
    rows: list[tuple[str, datetime | None]],
    *,
    since: datetime,
    resume_at: datetime | None,
) -> list[tuple[str, datetime | None]]:
    """Sort only one bounded date window, never the entire folder."""
    return sorted(
        (
            (entry_id, occurred_at)
            for entry_id, occurred_at in rows
            if occurred_at is None
            or (occurred_at >= since and (
                resume_at is None or occurred_at <= resume_at
            ))
        ),
        key=lambda row: (row[1] or _MIN_UTC, row[0].casefold()),
        reverse=True,
    )


def _require_history_entry_id(item: Any, spec: HistoricalFolder) -> str:
    """An unreadable mail identity must never be skipped past the cursor."""
    try:
        entry_id = _safe_text(getattr(item, "EntryID", "")).strip()
        if not entry_id:
            raise ValueError("Outlook item has no EntryID.")
        return entry_id
    except Exception as exc:
        logger.warning(
            "history_item_unsupported",
            store=spec.store_display_name,
            folder=spec.folder_path,
            action="retry",
            error=str(exc),
        )
        raise OutlookFolderUnavailable(
            f"Could not read EntryID in {spec.folder_path}: {exc}"
        ) from exc


def _same_outlook_entry(namespace: Any, left: str, right: str) -> bool:
    # EntryID byte representations can differ for the same MAPI object.
    return left.casefold() == right.casefold() or bool(
        namespace.CompareEntryIDs(left, right)
    )


def _cursor_group_ids(state: HistoryImportState) -> tuple[str, ...]:
    metadata = state.metadata_json or {}
    if metadata.get(_TABLE_CURSOR_KEY) != _TABLE_CURSOR_VERSION:
        return ()
    ids = metadata.get(_TABLE_CURSOR_IDS_KEY)
    if not isinstance(ids, list) or not all(
        isinstance(value, str) and value for value in ids
    ):
        return ()
    if state.cursor_entry_id not in ids:
        return ()
    return tuple(ids)


def _record_history_cursor(
    state: HistoryImportState, entry_id: str, occurred_at: datetime | None
) -> None:
    # Persist only long-term MailItem IDs, not possibly short-term table
    # row IDs. Remember all committed IDs in the current exact timestamp
    # group so equal dates cannot cause omissions or an endless replay.
    previous = (
        _cursor_group_ids(state)
        if state.cursor_timestamp == occurred_at else ()
    )
    ids = list(dict.fromkeys((*previous, entry_id)))
    state.cursor_entry_id = entry_id
    state.cursor_timestamp = occurred_at
    state.metadata_json = {
        **(state.metadata_json or {}),
        _TABLE_CURSOR_KEY: _TABLE_CURSOR_VERSION,
        _TABLE_CURSOR_IDS_KEY: ids,
    }


def _is_mapi_not_found(exc: Exception) -> bool:
    # pywintypes.com_error may wrap the MAPI HRESULT in EXCEPINFO.scode.
    for value in (getattr(exc, "hresult", None), *exc.args):
        if isinstance(value, tuple) and len(value) >= 6:
            value = value[5]
        if isinstance(value, int) and value & 0xFFFFFFFF == 0x8004010F:
            return True
    return False


def _legacy_resume_at(
    namespace: Any, spec: HistoricalFolder, state: HistoryImportState
) -> datetime | None:
    saved = _cursor_utc(state.cursor_timestamp)
    if not state.cursor_entry_id or saved is None:
        return None
    # Old cursors may predate MAPI UTC columns. Reconcile the stored date
    # with the live object's MAPI date once, without enumerating its folder.
    try:
        item = namespace.GetItemFromID(state.cursor_entry_id, spec.store_id)
    except Exception as exc:
        if not _is_mapi_not_found(exc):
            raise
        logger.warning("history_legacy_cursor_missing", folder=spec.folder_path)
        return saved
    values = item.PropertyAccessor.GetProperties((_TABLE_RECEIVED, _TABLE_SENT))
    current = _effective_table_time(values[0], values[1], spec.date_attr)
    return max(saved, current) if current is not None else saved


def _read_history_window(
    spec: HistoricalFolder,
    lower: datetime | None,
    upper: datetime | None,
) -> tuple[Any, list[tuple[str, datetime | None]]]:
    started = time.monotonic()
    logger.info(
        "history_table_window_started",
        folder=spec.folder_path,
        lower=lower.isoformat() if lower else None,
        upper=upper.isoformat() if upper else None,
        undated=lower is None,
    )
    table = spec.folder.GetTable(_history_filter(spec.date_attr, lower, upper))
    table.Columns.RemoveAll()
    for column in ("EntryID", _TABLE_RECEIVED, _TABLE_SENT):
        table.Columns.Add(column)
    rows: dict[str, tuple[str, datetime | None]] = {}
    while not table.EndOfTable:
        batch = table.GetArray(_TABLE_BATCH_SIZE)
        if not batch:
            raise OutlookFolderUnavailable("Outlook returned an empty table batch.")
        for row in batch:
            entry_id = _safe_text(row[0]).strip()
            if not entry_id:
                raise OutlookFolderUnavailable("Outlook table row has no EntryID.")
            occurred_at = _effective_table_time(row[1], row[2], spec.date_attr)
            if lower is None:
                if occurred_at is not None:
                    raise OutlookFolderUnavailable("Undated filter returned a dated row.")
            elif occurred_at is None or occurred_at < lower or (
                upper is not None and occurred_at >= upper
            ):
                raise OutlookFolderUnavailable("Outlook date filter returned an out-of-range row.")
            key = entry_id.casefold()
            previous = rows.get(key)
            if previous is not None and previous[1] != occurred_at:
                raise OutlookFolderUnavailable("Outlook table changed during enumeration.")
            rows[key] = (entry_id, occurred_at)
        logger.info(
            "history_table_batch", folder=spec.folder_path, rows_scanned=len(rows),
        )
    logger.info(
        "history_table_window_read",
        folder=spec.folder_path,
        rows_scanned=len(rows),
        duration_seconds=round(time.monotonic() - started, 3),
    )
    # Keep the table alive while its row IDs are used by GetItemFromID.
    return table, list(rows.values())


def _yield_history_candidates(
    namespace: Any,
    spec: HistoricalFolder,
    candidates: list[tuple[str, datetime | None]],
    *,
    checkpoint_time: datetime | None,
    committed_ids: tuple[str, ...],
) -> Iterator[tuple[Any, datetime | None]]:
    known = {value.casefold() for value in committed_ids}
    for entry_id, occurred_at in candidates:
        at_checkpoint = occurred_at == checkpoint_time and bool(committed_ids)
        if at_checkpoint and entry_id.casefold() in known:
            continue
        item = namespace.GetItemFromID(entry_id, spec.store_id)
        if int(item.Class) != MAIL_ITEM_CLASS:
            continue
        live_id = _require_history_entry_id(item, spec)
        if not _same_outlook_entry(namespace, entry_id, live_id):
            raise OutlookFolderUnavailable("Outlook item identity changed.")
        if at_checkpoint and (
            live_id.casefold() in known
            or any(_same_outlook_entry(namespace, live_id, old) for old in committed_ids)
        ):
            continue
        yield item, occurred_at


def _history_items(
    spec: HistoricalFolder,
    *,
    since: datetime,
    state: HistoryImportState,
) -> Iterator[tuple[Any, datetime | None]]:
    if spec.folder is None:
        raise OutlookFolderUnavailable(f"Folder is not open: {spec.folder_path}")
    try:
        namespace = spec.folder.Session
        committed_ids = _cursor_group_ids(state)
        checkpoint_time = _cursor_utc(state.cursor_timestamp)
        resume_at = (
            checkpoint_time if committed_ids
            else _legacy_resume_at(namespace, spec, state)
        )
        # A committed undated checkpoint follows all dated windows. A
        # legacy null-date cursor has no reliable ordering: replay safely.
        undated_only = bool(committed_ids) and checkpoint_time is None
        since = since.astimezone(UTC)
        oldest = _minute_floor(since)
        upper = (
            _minute_floor(resume_at) + timedelta(minutes=1)
            if resume_at is not None else None
        )
        anchor = upper or (_minute_floor(datetime.now(UTC)) + timedelta(minutes=1))
        span = _INITIAL_WINDOW

        while not undated_only and anchor > oldest:
            lower = max(oldest, anchor - span)
            # Bounds are minute aligned for Outlook; exact cutoff and
            # checkpoint are checked again in Python.
            _table, rows = _read_history_window(spec, lower, upper)
            candidates = _history_candidates(rows, since=since, resume_at=resume_at)
            logger.info(
                "history_table_ready", folder=spec.folder_path,
                rows_scanned=len(rows), candidates=len(candidates),
                resumed=bool(state.cursor_entry_id),
            )
            yield from _yield_history_candidates(
                namespace, spec, candidates,
                checkpoint_time=checkpoint_time, committed_ids=committed_ids,
            )
            # No older query until this window is exhausted. Reconnects
            # start at the last committed date, not at the folder head.
            upper = anchor = lower
            if len(rows) < _TABLE_BATCH_SIZE:
                span = min(span * 2, _MAX_WINDOW)
            elif len(rows) > 4096:
                span = max(span / 2, timedelta(days=1))

        _table, rows = _read_history_window(spec, None, None)
        candidates = _history_candidates(rows, since=since, resume_at=resume_at)
        logger.info(
            "history_table_ready", folder=spec.folder_path,
            rows_scanned=len(rows), candidates=len(candidates),
            resumed=bool(state.cursor_entry_id),
        )
        yield from _yield_history_candidates(
            namespace, spec, candidates,
            checkpoint_time=checkpoint_time, committed_ids=committed_ids,
        )
    except Exception as exc:
        # A bad filter or unreadable row is a paused import, never an
        # apparently successful but incomplete folder.
        raise OutlookFolderUnavailable(
            f"Could not enumerate Outlook table for {spec.folder_path}: {exc}"
        ) from exc


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
        state.metadata_json = {
            key: value
            for key, value in (state.metadata_json or {}).items()
            if key not in {_TABLE_CURSOR_KEY, _TABLE_CURSOR_IDS_KEY}
        }
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
        logger.info(
            "history_index_batch", batch=batches, claimed=last_claimed,
            completed=int(result["completed"]),
        )
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

                    # SessionLocal uses expire_on_commit=False. Close the
                    # read transaction before any remote COM enumeration.
                    session.commit()
                    last_progress = time.monotonic()
                    folder_since = min(since, state.oldest_allowed_at or since)

                    for item, occurred_at in _resilient_history_items(
                        spec,
                        since=folder_since,
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

                        entry_id = _require_history_entry_id(item, spec)

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
                            _record_history_cursor(state, entry_id, occurred_at)
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

                        if (
                            totals["seen"] % 100 == 0
                            or time.monotonic() - last_progress >= 30
                        ):
                            last_progress = time.monotonic()
                            logger.info(
                                "history_message_progress",
                                folder=spec.folder_path,
                                seen=totals["seen"],
                                created=totals["created"],
                            )
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

                        if max_messages and totals["seen"] >= max_messages:
                            stopped_by_limit = True
                            reached_folder_end = False
                            break

                    if reached_folder_end:
                        state.status = "completed"
                        state.oldest_allowed_at = folder_since
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
        logger.info("history_indexing_started", enabled=index_after_import)
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
