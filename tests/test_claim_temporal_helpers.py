from datetime import UTC, datetime

from packages.knowledge.temporal import (
    interval_contains,
    intervals_overlap,
)


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def test_adjacent_half_open_intervals_do_not_overlap() -> None:
    boundary = dt("2026-07-02T11:03:21")
    assert not intervals_overlap(
        dt("2026-04-23T14:25:00"),
        boundary,
        boundary,
        None,
    )


def test_open_intervals_overlap() -> None:
    assert intervals_overlap(None, None, None, None)
    assert intervals_overlap(
        dt("2026-04-23T14:25:00"),
        None,
        dt("2026-07-02T11:03:21"),
        None,
    )


def test_interval_contains_uses_exclusive_end() -> None:
    start = dt("2026-04-23T14:25:00")
    end = dt("2026-07-02T11:03:21")
    assert interval_contains(start, end, start)
    assert not interval_contains(start, end, end)
