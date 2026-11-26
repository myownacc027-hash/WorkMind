import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from apps.history_importer import inventory, main as history


NOW = datetime(2026, 1, 1, tzinfo=UTC)
SINCE = NOW - timedelta(days=365)


def state(**values):
    defaults = dict(
        id="state", status="paused", oldest_allowed_at=SINCE,
        cursor_entry_id=None, cursor_timestamp=None, metadata_json={},
        seen_count=10, created_count=9, failed_count=0,
    )
    return SimpleNamespace(**(defaults | values))


def spec(namespace):
    return history.HistoricalFolder(
        store_id="store", store_display_name="archive", source_kind="archive",
        folder_id="folder", folder_path="archive/inbox", date_attr="ReceivedTime",
        folder=SimpleNamespace(Session=namespace),
    )


@pytest.mark.parametrize("verify_mail, expected, opened_expected", [
    (False, 3, ["row-committed", "row-new"]),
    (True, 2, ["row-committed", "row-new", "older", "other"]),
])
def test_inventory_counts_remaining_checkpoint_alias_and_mail_class(
    monkeypatch, verify_mail, expected, opened_expected
):
    checkpoint = state(
        cursor_entry_id="committed", cursor_timestamp=NOW,
        metadata_json={
            history._TABLE_CURSOR_KEY: history._TABLE_CURSOR_VERSION,
            history._TABLE_CURSOR_IDS_KEY: ["committed"],
        },
    )
    aliases = {"row-committed": "committed", "row-new": "new"}
    opened = []

    def open_item(entry_id, store):
        opened.append(entry_id)
        return SimpleNamespace(
            Class=26 if entry_id == "other" else history.MAIL_ITEM_CLASS,
            EntryID=aliases.get(entry_id, entry_id),
        )

    namespace = SimpleNamespace(
        GetItemFromID=open_item,
        CompareEntryIDs=lambda a, b: aliases.get(a, a) == aliases.get(b, b),
    )

    def windows(folder, *, since, resume_at, undated_only, resumed):
        assert since == SINCE
        assert resume_at == NOW
        assert resumed and not undated_only
        yield object(), [
            ("row-committed", NOW), ("row-new", NOW),
            ("older", NOW - timedelta(days=1)), ("other", None),
        ]

    monkeypatch.setattr(history, "_history_windows", windows)
    before = vars(checkpoint).copy()
    assert inventory.count_remaining(
        spec(namespace), checkpoint, since=SINCE, verify_mail=verify_mail
    ) == expected
    assert opened == opened_expected
    assert vars(checkpoint) == before


def test_inventory_undated_cursor_uses_same_window_rule(monkeypatch):
    checkpoint = state(
        cursor_entry_id="committed", cursor_timestamp=None,
        metadata_json={
            history._TABLE_CURSOR_KEY: history._TABLE_CURSOR_VERSION,
            history._TABLE_CURSOR_IDS_KEY: ["committed"],
        },
    )

    def windows(folder, **kwargs):
        assert kwargs["undated_only"]
        assert kwargs["resume_at"] is None
        yield object(), [("committed", None)]

    monkeypatch.setattr(history, "_history_windows", windows)
    assert inventory.count_remaining(
        spec(SimpleNamespace()), checkpoint, since=SINCE, verify_mail=False
    ) == 0


def test_inventory_unstarted_folder_baseline():
    checkpoint, cutoff, complete = inventory.folder_baseline(None, SINCE)
    assert checkpoint.id is None
    assert checkpoint.seen_count == checkpoint.created_count == 0
    assert cutoff == SINCE and not complete


def test_inventory_completed_folder_needs_no_rescan():
    checkpoint = state(status="completed", oldest_allowed_at=SINCE - timedelta(days=4))
    actual, cutoff, complete = inventory.folder_baseline(checkpoint, SINCE)
    assert actual is checkpoint
    assert cutoff == checkpoint.oldest_allowed_at and complete


def test_inventory_refuses_cutoff_that_would_reset_state():
    checkpoint = state(oldest_allowed_at=SINCE + timedelta(days=1))
    with pytest.raises(RuntimeError, match="reset"):
        inventory.folder_baseline(checkpoint, SINCE)
    assert checkpoint.seen_count == 10


def test_fixed_cutoff_guard_preserves_existing_state():
    checkpoint = state(oldest_allowed_at=SINCE + timedelta(days=1))
    session = SimpleNamespace(scalar=lambda query: checkpoint)
    with pytest.raises(history.OutlookFolderUnavailable, match="reset"):
        history._get_state(
            session, mailbox_id="mailbox", spec=spec(None),
            since=SINCE, reset=False, preserve_cursor=True,
        )
    assert checkpoint.seen_count == 10 and checkpoint.status == "paused"


def test_fixed_cutoff_requires_timezone_and_cannot_be_future():
    assert history._resolve_since(5, SINCE.isoformat()) == SINCE
    for invalid in ("invalid", "2020-01-01T12:00:00", "9999-01-01T00:00:00+00:00"):
        with pytest.raises(history.typer.BadParameter):
            history._resolve_since(5, invalid)


def test_inventory_publishes_complete_snapshot_atomically(tmp_path):
    path = tmp_path / "history-inventory.json"
    snapshot = {"version": 1, "remaining_at_snapshot": 42, "folders": []}
    inventory.atomic_snapshot(path, snapshot)
    assert json.loads(path.read_text(encoding="utf-8")) == snapshot
    assert not path.with_suffix(".tmp").exists()
