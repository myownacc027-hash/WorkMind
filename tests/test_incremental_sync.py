from datetime import UTC, datetime, timedelta

def test_incremental_overlap_math() -> None:
    cursor = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    since = cursor - timedelta(minutes=30)
    assert since == datetime(
        2026, 7, 15, 11, 30, tzinfo=UTC
    )
