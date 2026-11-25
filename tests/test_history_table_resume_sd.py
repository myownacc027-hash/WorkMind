from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from apps.history_importer import main as history

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)
SINCE = NOW - timedelta(days=365)


def state(entry=None, timestamp=None, version=None):
    return SimpleNamespace(
        cursor_entry_id=entry,
        cursor_timestamp=timestamp,
        metadata_json=(
            {} if version is None else {history._TABLE_CURSOR_KEY: version}
        ),
    )


def test_legacy_cursor_replays_equal_timestamp_group():
    rows = [
        ("new", NOW + timedelta(seconds=1)),
        ("B", NOW),
        ("A", NOW),
        ("C", NOW),
        ("older", NOW - timedelta(seconds=1)),
    ]
    candidates = history._history_candidates(
        rows, since=SINCE, state=state("B", NOW)
    )
    assert [entry for entry, _ in candidates] == ["C", "B", "A", "older"]


def test_stable_cursor_resumes_same_timestamp_even_if_deleted():
    rows = [("C", NOW), ("A", NOW), ("older", NOW - timedelta(seconds=1))]
    candidates = history._history_candidates(
        rows, since=SINCE, state=state("B", NOW, history._TABLE_CURSOR_VERSION)
    )
    assert [entry for entry, _ in candidates] == ["A", "older"]


def test_date_cutoff_and_undated_items():
    rows = [
        ("missing", None),
        ("boundary", SINCE),
        ("outside", SINCE - timedelta(seconds=1)),
    ]
    assert history._history_candidates(rows, since=SINCE, state=state()) == [
        ("boundary", SINCE), ("missing", None)
    ]


def test_table_opens_only_candidates():
    opened = []
    columns = []

    class Columns:
        def RemoveAll(self):
            columns.clear()

        def Add(self, name):
            columns.append(name)

    class Table:
        Columns = None
        EndOfTable = False

        def GetArray(self, count):
            assert count == history._TABLE_BATCH_SIZE
            self.EndOfTable = True
            return [
                ("new", NOW + timedelta(seconds=1), None),
                ("B", NOW, None),
                ("A", NOW, None),
            ]

    table = Table()
    table.Columns = Columns()

    def open_item(entry, store):
        assert store == "store"
        opened.append(entry)
        return SimpleNamespace(Class=history.MAIL_ITEM_CLASS, EntryID=entry)

    spec = history.HistoricalFolder(
        store_id="store",
        store_display_name="archive",
        source_kind="archive",
        folder_id="folder",
        folder_path="archive/inbox",
        date_attr="ReceivedTime",
        folder=SimpleNamespace(
            GetTable=lambda: table,
            Session=SimpleNamespace(GetItemFromID=open_item),
        ),
    )
    items = list(history._history_items(
        spec, since=SINCE, state=state("B", NOW, history._TABLE_CURSOR_VERSION)
    ))
    assert columns == ["EntryID", history._TABLE_RECEIVED, history._TABLE_SENT]
    assert opened == ["A"]
    assert [(item.EntryID, dt) for item, dt in items] == [("A", NOW)]


def test_required_store_can_be_found_after_unavailable_unrelated_store():
    target = SimpleNamespace(DisplayName="required")

    def get_store(index):
        if index == 1:
            raise RuntimeError("Exchange unavailable")
        return target

    namespace = SimpleNamespace(
        Stores=SimpleNamespace(Count=2, Item=get_store)
    )
    assert history._find_store(namespace, "required") is target
    with pytest.raises(history.OutlookFolderUnavailable):
        history._find_store(namespace, "missing")


def test_table_failure_is_retryable_not_successful_empty_folder():
    spec = history.HistoricalFolder(
        store_id="store", store_display_name="archive", source_kind="archive",
        folder_id="folder", folder_path="archive/inbox",
        date_attr="ReceivedTime",
        folder=SimpleNamespace(GetTable=lambda: None),
    )
    with pytest.raises(history.OutlookFolderUnavailable) as error:
        list(history._history_items(spec, since=SINCE, state=state()))
    assert history._retryable_outlook_error(error.value)


@pytest.mark.parametrize("value", [None, "", "  "])
def test_empty_entry_id_stops_history_instead_of_advancing_cursor(value):
    spec = history.HistoricalFolder(
        store_id="store", store_display_name="archive", source_kind="archive",
        folder_id="folder", folder_path="archive/inbox",
        date_attr="ReceivedTime",
    )
    with pytest.raises(history.OutlookFolderUnavailable):
        history._require_history_entry_id(SimpleNamespace(EntryID=value), spec)


def test_entry_id_com_failure_remains_retryable():
    class UnavailableItem:
        @property
        def EntryID(self):
            raise RuntimeError("Exchange unavailable")

    spec = history.HistoricalFolder(
        store_id="store", store_display_name="archive", source_kind="archive",
        folder_id="folder", folder_path="archive/inbox",
        date_attr="ReceivedTime",
    )
    with pytest.raises(history.OutlookFolderUnavailable) as error:
        history._require_history_entry_id(UnavailableItem(), spec)
    assert history._retryable_outlook_error(error.value)
