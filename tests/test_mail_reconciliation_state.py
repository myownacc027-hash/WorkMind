from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from apps.sync_runner.reconciliation import (
    load_json,
    mark_reconciliation_completed,
    mark_reconciliation_failed,
    mark_reconciliation_started,
)


def test_state_lifecycle_preserves_last_success(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    started_at = datetime(
        2026,
        8,
        3,
        10,
        0,
        tzinfo=UTC,
    )

    mark_reconciliation_started(
        path,
        started_at=started_at,
        mode="reconciliation",
        lookback_days=14,
        max_per_folder=0,
    )
    assert load_json(path)["last_status"] == "running"

    completed = {
        "status": "completed",
        "mode": "reconciliation",
        "started_at": started_at.isoformat(),
        "finished_at": datetime(
            2026,
            8,
            3,
            10,
            5,
            tzinfo=UTC,
        ).isoformat(),
        "duration_seconds": 300,
        "lookback_days": 14,
        "max_per_folder": 0,
        "collector": {
            "seen": 472,
            "created": 145,
            "updated": 2,
        },
    }
    mark_reconciliation_completed(
        path,
        result=completed,
    )
    state = load_json(path)
    assert state["last_status"] == "completed"
    assert state["last_recovered_messages"] == 145

    mark_reconciliation_failed(
        path,
        result={
            "mode": "reconciliation",
            "started_at": started_at.isoformat(),
            "finished_at": datetime(
                2026,
                8,
                4,
                tzinfo=UTC,
            ).isoformat(),
            "lookback_days": 14,
            "max_per_folder": 0,
            "error_type": "TestError",
            "error": "boom",
        },
    )
    failed = load_json(path)
    assert failed["last_status"] == "failed"
    assert (
        failed["last_success_at"]
        == completed["finished_at"]
    )
