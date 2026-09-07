from datetime import UTC, datetime

import pytest

from apps.mcp_server.services import (
    WorkMindQueryError,
    _like_pattern,
    _parse_datetime,
    _uuid,
)


def test_parse_date_start() -> None:
    assert _parse_datetime("2026-07-15") == datetime(
        2026, 7, 15, tzinfo=UTC
    )


def test_parse_date_end_is_next_midnight() -> None:
    assert _parse_datetime(
        "2026-07-15",
        end_of_day=True,
    ) == datetime(2026, 7, 16, tzinfo=UTC)


def test_like_pattern_escapes_wildcards() -> None:
    assert _like_pattern(r"a%b_c") == r"%a\%b\_c%"


def test_uuid_validation() -> None:
    with pytest.raises(WorkMindQueryError):
        _uuid("not-a-uuid")
