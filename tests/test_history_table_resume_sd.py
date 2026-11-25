from datetime import UTC, datetime, timedelta, timezone
from itertools import islice
from types import SimpleNamespace

import pytest

from apps.history_importer import main as history

NOW = datetime(2026, 11, 25, 12, 0, 42, tzinfo=UTC)
SINCE = NOW - timedelta(days=365)


def state(entry=None, timestamp=None, version=None, ids=None):
    metadata = {}
    if version is not None:
        metadata[history._TABLE_CURSOR_KEY] = version
    if ids is not None:
        metadata[history._TABLE_CURSOR_IDS_KEY] = ids
    return SimpleNamespace(
        cursor_entry_id=entry, cursor_timestamp=timestamp, metadata_json=metadata,
    )


def folder(live=None, date_attr="ReceivedTime"):
    return history.HistoricalFolder(
        store_id="store", store_display_name="archive", source_kind="archive",
        folder_id="folder", folder_path="archive/inbox", date_attr=date_attr,
        folder=live,
    )


def test_legacy_cursor_replays_equal_timestamp_group():
    rows = [
        ("new", NOW + timedelta(seconds=1)), ("B", NOW), ("A", NOW),
        ("C", NOW), ("older", NOW - timedelta(seconds=1)),
    ]
    candidates = history._history_candidates(
        rows, since=SINCE, resume_at=NOW
    )
    assert [entry for entry, _ in candidates] == ["C", "B", "A", "older"]


def test_date_cutoff_and_undated_items():
    rows = [
        ("missing", None), ("boundary", SINCE),
        ("outside", SINCE - timedelta(seconds=1)),
    ]
    assert history._history_candidates(rows, since=SINCE, resume_at=None) == [
        ("boundary", SINCE), ("missing", None)
    ]


def test_cursor_group_uses_persistent_ids_not_table_order():
    checkpoint = state("legacy", NOW, 1)
    history._record_history_cursor(checkpoint, "C", NOW)
    assert history._cursor_group_ids(checkpoint) == ("C",)
    history._record_history_cursor(checkpoint, "A", NOW)
    assert history._cursor_group_ids(checkpoint) == ("C", "A")
    assert checkpoint.cursor_entry_id == "A"
    older = NOW - timedelta(seconds=1)
    history._record_history_cursor(checkpoint, "B", older)
    assert history._cursor_group_ids(checkpoint) == ("B",)
    assert checkpoint.cursor_timestamp == older


def test_undated_group_continues_without_replaying_dated_mail():
    checkpoint = state()
    history._record_history_cursor(checkpoint, "A", None)
    history._record_history_cursor(checkpoint, "B", None)
    assert history._cursor_group_ids(checkpoint) == ("A", "B")


def test_filter_bounds_fallback_and_null(monkeypatch):
    monkeypatch.setattr(
        history, "_format_dasl_utc", lambda value: value.strftime("%Y-%m-%d %H:%M")
    )
    received = f'"{history._TABLE_RECEIVED}"'
    sent = f'"{history._TABLE_SENT}"'
    low = history._minute_floor(SINCE)
    high = history._minute_floor(NOW) + timedelta(minutes=1)
    query = history._history_filter("ReceivedTime", low, high)
    assert query == (
        f"@SQL=(({received} >= '2025-11-25 12:00' AND "
        f"{received} < '2026-11-25 12:01') OR ({received} IS NULL AND "
        f"({sent} >= '2025-11-25 12:00' AND {sent} < '2026-11-25 12:01')))"
    )
    assert f"{sent} IS NULL AND" in history._history_filter("SentOn", low, high)
    assert history._history_filter("ReceivedTime", None, None) == (
        f"@SQL=({received} IS NULL AND {sent} IS NULL)"
    )
    assert " < " not in history._history_filter("ReceivedTime", low, None)


def test_mapi_utc_and_fallback_dates():
    # The wall-clock values of MAPI columns are UTC.
    value = NOW.replace(tzinfo=timezone(timedelta(hours=3)))
    assert history._table_utc(value) == NOW
    assert history._effective_table_time(None, NOW, "ReceivedTime") == NOW
    assert history._effective_table_time(NOW, None, "SentOn") == NOW
    assert history._effective_table_time(datetime(4501, 1, 1), NOW, "ReceivedTime") == NOW
    assert history._effective_table_time(None, None, "ReceivedTime") is None


def test_window_reads_only_necessary_interval(monkeypatch):
    opened = []
    windows = []

    def read_window(spec, lower, upper):
        windows.append((lower, upper))
        assert lower is not None
        assert upper == history._minute_floor(NOW) + timedelta(minutes=1)
        return object(), [
            ("new", NOW + timedelta(seconds=1)), ("B", NOW),
            ("A", NOW - timedelta(seconds=1)),
        ]

    def open_item(entry, store):
        assert store == "store"
        opened.append(entry)
        return SimpleNamespace(Class=history.MAIL_ITEM_CLASS, EntryID=entry)

    monkeypatch.setattr(history, "_read_history_window", read_window)
    checkpoint = state("B", NOW, history._TABLE_CURSOR_VERSION, ["B"])
    spec = folder(SimpleNamespace(Session=SimpleNamespace(GetItemFromID=open_item)))
    iterator = history._history_items(spec, since=SINCE, state=checkpoint)
    try:
        items = list(islice(iterator, 1))
    finally:
        iterator.close()
    assert opened == ["A"]
    assert [(item.EntryID, dt) for item, dt in items] == [
        ("A", NOW - timedelta(seconds=1))
    ]
    assert len(windows) == 1


def test_short_term_alias_and_equal_date_group():
    aliases = {"row-A": "A", "row-B": "B", "row-C": "C"}
    opened = []
    compared = []

    def open_item(entry, store):
        opened.append(entry)
        return SimpleNamespace(Class=history.MAIL_ITEM_CLASS, EntryID=aliases[entry])

    def compare(left, right):
        compared.append((left, right))
        return aliases.get(left, left) == aliases.get(right, right)

    namespace = SimpleNamespace(GetItemFromID=open_item, CompareEntryIDs=compare)
    items = list(history._yield_history_candidates(
        namespace, folder(), [(row, NOW) for row in aliases],
        checkpoint_time=NOW, committed_ids=("B", "C"),
    ))
    assert opened == ["row-A", "row-B", "row-C"]
    assert ("row-A", "A") in compared
    assert [item.EntryID for item, _ in items] == ["A"]


def test_true_identity_mismatch_still_stops():
    namespace = SimpleNamespace(
        GetItemFromID=lambda entry, store: SimpleNamespace(
            Class=history.MAIL_ITEM_CLASS, EntryID="different"
        ),
        CompareEntryIDs=lambda left, right: False,
    )
    with pytest.raises(history.OutlookFolderUnavailable, match="identity changed"):
        list(history._yield_history_candidates(
            namespace, folder(), [("row", NOW)],
            checkpoint_time=None, committed_ids=(),
        ))


def test_legacy_checkpoint_date_reconciled():
    later = NOW + timedelta(days=1)
    item = SimpleNamespace(PropertyAccessor=SimpleNamespace(
        GetProperties=lambda keys: (later, None),
    ))
    namespace = SimpleNamespace(GetItemFromID=lambda entry, store: item)
    assert history._legacy_resume_at(namespace, folder(), state("old", NOW)) == later


def test_legacy_deleted_checkpoint_uses_saved_date():
    def missing(entry, store):
        raise RuntimeError(-2147221233)

    namespace = SimpleNamespace(GetItemFromID=missing)
    assert history._legacy_resume_at(namespace, folder(), state("old", NOW)) == NOW


def test_windows_are_adjacent_and_undated_last(monkeypatch):
    windows = []

    def read_window(spec, lower, upper):
        windows.append((lower, upper))
        return object(), []

    monkeypatch.setattr(history, "_read_history_window", read_window)
    checkpoint = state("B", NOW, history._TABLE_CURSOR_VERSION, ["B"])
    spec = folder(SimpleNamespace(Session=SimpleNamespace()))
    assert list(history._history_items(spec, since=SINCE, state=checkpoint)) == []
    assert windows[-1] == (None, None)
    dated = windows[:-1]
    assert dated[0][1] == history._minute_floor(NOW) + timedelta(minutes=1)
    assert dated[-1][0] == history._minute_floor(SINCE)
    assert all(left[0] == right[1] for left, right in zip(dated, dated[1:]))


def test_undated_checkpoint_queries_only_undated(monkeypatch):
    windows = []

    def read_window(spec, lower, upper):
        windows.append((lower, upper))
        return object(), []

    monkeypatch.setattr(history, "_read_history_window", read_window)
    checkpoint = state("B", None, history._TABLE_CURSOR_VERSION, ["B"])
    spec = folder(SimpleNamespace(Session=SimpleNamespace()))
    assert list(history._history_items(spec, since=SINCE, state=checkpoint)) == []
    assert windows == [(None, None)]


@pytest.mark.parametrize("outside", [True, False])
def test_read_window_validates_provider_filter(monkeypatch, outside):
    columns = []
    filters = []

    class Columns:
        def RemoveAll(self):
            columns.clear()

        def Add(self, name):
            columns.append(name)

    class Table:
        EndOfTable = False

        def GetArray(self, count):
            assert count == history._TABLE_BATCH_SIZE
            self.EndOfTable = True
            return [("entry", NOW + timedelta(days=1) if outside else NOW, None)]

    def get_table(filter_string):
        filters.append(filter_string)
        table = Table()
        table.Columns = Columns()
        return table

    monkeypatch.setattr(history, "_format_dasl_utc", lambda value: value.isoformat())
    spec = folder(SimpleNamespace(GetTable=get_table))
    if outside:
        with pytest.raises(history.OutlookFolderUnavailable, match="out-of-range"):
            history._read_history_window(spec, SINCE, NOW + timedelta(minutes=1))
    else:
        _, rows = history._read_history_window(spec, SINCE, NOW + timedelta(minutes=1))
        assert rows == [("entry", NOW)]
    assert columns == ["EntryID", history._TABLE_RECEIVED, history._TABLE_SENT]
    assert filters[0].startswith("@SQL=")


def test_required_store_can_be_found_after_unavailable_unrelated_store():
    target = SimpleNamespace(DisplayName="required")

    def get_store(index):
        if index == 1:
            raise RuntimeError("Exchange unavailable")
        return target

    namespace = SimpleNamespace(Stores=SimpleNamespace(Count=2, Item=get_store))
    assert history._find_store(namespace, "required") is target
    with pytest.raises(history.OutlookFolderUnavailable):
        history._find_store(namespace, "missing")


def test_table_failure_is_retryable_not_successful_empty_folder(monkeypatch):
    monkeypatch.setattr(history, "_format_dasl_utc", lambda value: value.isoformat())
    spec = folder(SimpleNamespace(GetTable=lambda criteria: None, Session=None))
    with pytest.raises(history.OutlookFolderUnavailable) as error:
        list(history._history_items(spec, since=SINCE, state=state()))
    assert history._retryable_outlook_error(error.value)


@pytest.mark.parametrize("value", [None, "", "  "])
def test_empty_entry_id_stops_history_instead_of_advancing_cursor(value):
    with pytest.raises(history.OutlookFolderUnavailable):
        history._require_history_entry_id(SimpleNamespace(EntryID=value), folder())


def test_entry_id_com_failure_remains_retryable():
    class UnavailableItem:
        @property
        def EntryID(self):
            raise RuntimeError("Exchange unavailable")

    with pytest.raises(history.OutlookFolderUnavailable) as error:
        history._require_history_entry_id(UnavailableItem(), folder())
    assert history._retryable_outlook_error(error.value)
