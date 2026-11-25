"""Read-only validation of the Outlook provider's UTC table filter and EntryIDs."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select

from apps.history_importer import main as history
from packages.common.config import get_settings
from packages.common.locking import single_instance_lock
from packages.common.logging import configure_logging
from packages.database.models import HistoryImportState
from packages.database.session import SessionLocal


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    with single_instance_lock(settings.outlook_operation_lock_file) as acquired:
        if not acquired:
            raise RuntimeError("Another Outlook operation is active.")
        with SessionLocal() as session:
            state = session.scalar(
                select(HistoryImportState)
                .where(
                    HistoryImportState.source_kind == "archive",
                    HistoryImportState.status.in_(("running", "paused", "pending")),
                    HistoryImportState.cursor_entry_id.is_not(None),
                )
                .order_by(HistoryImportState.updated_at.desc())
                .limit(1)
            )
            if state is None:
                raise RuntimeError("No unfinished archive folder with a checkpoint.")
            spec = history.HistoricalFolder(
                store_id=state.outlook_store_id,
                store_display_name=state.store_display_name,
                source_kind=state.source_kind,
                folder_id=state.folder_entry_id,
                folder_path=state.folder_path,
                date_attr=state.date_attr,
            )
            saved_cursor = state.cursor_entry_id
        # No open SQL transaction during the COM probe.
        application, namespace, live_folder = history._open_live_folder(spec)
        source = live_folder.GetTable()
        source.Columns.RemoveAll()
        for column in ("EntryID", history._TABLE_RECEIVED, history._TABLE_SENT):
            source.Columns.Add(column)
        sample = source.GetArray(16) if not source.EndOfTable else ()
        row = next(
            (
                value for value in sample
                if history._effective_table_time(value[1], value[2], spec.date_attr)
                is not None
            ),
            None,
        )
        if row is None:
            raise RuntimeError("No dated row in the independent table sample.")
        row_id = str(row[0])
        item = namespace.GetItemFromID(row_id, spec.store_id)
        live_id = history._require_history_entry_id(item, spec)
        if not history._same_outlook_entry(namespace, row_id, live_id):
            raise RuntimeError("Independent sample EntryID comparison failed.")
        occurred_at = history._effective_table_time(row[1], row[2], spec.date_attr)
        lower = history._minute_floor(occurred_at)
        upper = lower + timedelta(minutes=1)
        # Keep both tables alive while comparing their row IDs.
        filtered_table, filtered_rows = history._read_history_window(spec, lower, upper)
        found = any(
            candidate.casefold() == live_id.casefold()
            for candidate, _ in filtered_rows
        ) or any(
            history._same_outlook_entry(namespace, candidate, live_id)
            for candidate, _ in filtered_rows
        )
        if not found:
            raise RuntimeError("UTC filtered table omitted a known matching row.")
        checkpoint_found = None
        try:
            saved = namespace.GetItemFromID(saved_cursor, spec.store_id)
        except Exception as exc:
            if not history._is_mapi_not_found(exc):
                raise
        else:
            values = saved.PropertyAccessor.GetProperties(
                (history._TABLE_RECEIVED, history._TABLE_SENT)
            )
            saved_at = history._effective_table_time(values[0], values[1], spec.date_attr)
            if saved_at is not None and history._same_outlook_entry(
                namespace, str(saved.Parent.EntryID), spec.folder_id
            ):
                checkpoint_lower = history._minute_floor(saved_at)
                checkpoint_table, checkpoint_rows = history._read_history_window(
                    spec, checkpoint_lower, checkpoint_lower + timedelta(minutes=1)
                )
                checkpoint_found = any(
                    history._same_outlook_entry(namespace, candidate, saved_cursor)
                    for candidate, _ in checkpoint_rows
                )
                if not checkpoint_found:
                    raise RuntimeError("UTC filter omitted the existing checkpoint.")
        print(json.dumps({
            "ok": True,
            "folder": spec.folder_path,
            "filter": history._history_filter(spec.date_attr, lower, upper),
            "sample_found": found,
            "sample_alias": row_id.casefold() != live_id.casefold(),
            "filtered_rows": len(filtered_rows),
            "checkpoint_found": checkpoint_found,
        }, ensure_ascii=False, indent=2))
        # Explicitly retain the wrappers through the last identity comparison.
        _ = application, source, filtered_table


if __name__ == "__main__":
    main()
