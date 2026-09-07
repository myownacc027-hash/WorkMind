from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apps.sync_runner.reconciliation import (
    mark_reconciliation_completed,
    qualifies_as_reconciliation,
    reconciliation_health,
)


def _completed_full(
    finished_at: datetime,
    *,
    days: int = 14,
    max_per_folder: int = 0,
) -> dict:
    return {
        "status": "completed",
        "mode": "full",
        "started_at": (
            finished_at - timedelta(minutes=5)
        ).isoformat(),
        "finished_at": finished_at.isoformat(),
        "lookback_days": days,
        "max_per_folder": max_per_folder,
        "collector": {
            "seen": 472,
            "created": 145,
            "updated": 2,
        },
    }


def test_qualifying_full_run_requires_unlimited_scan() -> None:
    now = datetime(2026, 8, 3, tzinfo=UTC)
    assert qualifies_as_reconciliation(
        _completed_full(now),
        required_days=14,
    )
    assert not qualifies_as_reconciliation(
        _completed_full(
            now,
            max_per_folder=500,
        ),
        required_days=14,
    )


def test_recent_full_run_from_log_bootstraps_health(
    tmp_path: Path,
) -> None:
    now = datetime(
        2026,
        8,
        3,
        14,
        0,
        tzinfo=UTC,
    )
    log_file = tmp_path / "mail-sync.jsonl"
    log_file.write_text(
        json.dumps(
            _completed_full(
                now - timedelta(hours=1)
            )
        )
        + "\n",
        encoding="utf-8",
    )

    health = reconciliation_health(
        state_file=tmp_path / "state.json",
        sync_status_file=tmp_path / "status.json",
        sync_log_file=log_file,
        enabled=True,
        required_days=14,
        interval_hours=24,
        stale_hours=36,
        now=now,
    )

    assert health["due"] is False
    assert health["stale"] is False
    assert (
        health["last_success_source"]
        == "sync_log_file"
    )


def test_reconciliation_becomes_due_after_interval(
    tmp_path: Path,
) -> None:
    now = datetime(
        2026,
        8,
        3,
        14,
        0,
        tzinfo=UTC,
    )
    result = _completed_full(
        now - timedelta(hours=25)
    )
    mark_reconciliation_completed(
        tmp_path / "state.json",
        result=result,
    )

    health = reconciliation_health(
        state_file=tmp_path / "state.json",
        sync_status_file=tmp_path / "status.json",
        sync_log_file=tmp_path / "log.jsonl",
        enabled=True,
        required_days=14,
        interval_hours=24,
        stale_hours=36,
        now=now,
    )

    assert health["due"] is True
    assert health["stale"] is False
    assert health["reason"] == "interval_elapsed"


def test_reconciliation_becomes_stale(
    tmp_path: Path,
) -> None:
    now = datetime(
        2026,
        8,
        3,
        14,
        0,
        tzinfo=UTC,
    )
    result = _completed_full(
        now - timedelta(hours=40)
    )
    mark_reconciliation_completed(
        tmp_path / "state.json",
        result=result,
    )

    health = reconciliation_health(
        state_file=tmp_path / "state.json",
        sync_status_file=tmp_path / "status.json",
        sync_log_file=tmp_path / "log.jsonl",
        enabled=True,
        required_days=14,
        interval_hours=24,
        stale_hours=36,
        now=now,
    )

    assert health["due"] is True
    assert health["stale"] is True


def test_force_reconciliation_is_due(
    tmp_path: Path,
) -> None:
    now = datetime(
        2026,
        8,
        3,
        14,
        0,
        tzinfo=UTC,
    )
    result = _completed_full(
        now - timedelta(hours=1)
    )
    mark_reconciliation_completed(
        tmp_path / "state.json",
        result=result,
    )

    health = reconciliation_health(
        state_file=tmp_path / "state.json",
        sync_status_file=tmp_path / "status.json",
        sync_log_file=tmp_path / "log.jsonl",
        enabled=True,
        required_days=14,
        interval_hours=24,
        stale_hours=36,
        now=now,
        force=True,
    )

    assert health["due"] is True
    assert health["reason"] == "forced"
