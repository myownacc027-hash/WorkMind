from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from apps.sync_runner.reconciliation import (
    mark_reconciliation_completed,
    mark_reconciliation_failed,
    qualifies_as_reconciliation,
    reconciliation_health,
)


def _result(
    finished: datetime,
    *,
    days: int = 14,
    status: str = "completed",
    failed: int = 0,
) -> dict:
    return {
        "status": status,
        "mode": "reconciliation",
        "started_at": (
            finished - timedelta(minutes=5)
        ).isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": 300,
        "lookback_days": days,
        "max_per_folder": 0,
        "collector": {
            "seen": 500,
            "created": 10,
            "updated": 2,
            "failed": failed,
        },
    }


def test_completed_with_non_mail_errors_can_cover_mail() -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    assert qualifies_as_reconciliation(
        _result(
            now,
            status="completed_with_errors",
            failed=0,
        ),
        required_days=14,
    )


def test_mail_failure_never_qualifies() -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    assert not qualifies_as_reconciliation(
        _result(now, failed=1),
        required_days=14,
    )


def test_old_14_day_state_does_not_satisfy_30_day_policy(
    tmp_path: Path,
) -> None:
    now = datetime(
        2026,
        8,
        14,
        8,
        0,
        tzinfo=UTC,
    )
    state_file = tmp_path / "state.json"
    mark_reconciliation_completed(
        state_file,
        result=_result(now, days=14),
    )

    health = reconciliation_health(
        state_file=state_file,
        sync_status_file=tmp_path / "status.json",
        sync_log_file=tmp_path / "log.jsonl",
        enabled=True,
        required_days=30,
        interval_hours=24,
        stale_hours=36,
        now=now + timedelta(minutes=1),
    )

    assert health["last_success_at"] is None
    assert health["due"] is True
    assert health["stale"] is True


def test_failed_attempt_preserves_qualified_success(
    tmp_path: Path,
) -> None:
    success = datetime(
        2026,
        8,
        14,
        8,
        0,
        tzinfo=UTC,
    )
    path = tmp_path / "state.json"
    mark_reconciliation_completed(
        path,
        result=_result(success),
    )
    mark_reconciliation_failed(
        path,
        result={
            "status": "failed",
            "mode": "reconciliation",
            "started_at": (
                success + timedelta(hours=1)
            ).isoformat(),
            "finished_at": (
                success + timedelta(
                    hours=1,
                    minutes=5,
                )
            ).isoformat(),
            "lookback_days": 14,
            "max_per_folder": 0,
            "error_type": "TestError",
            "error": "boom",
        },
    )

    health = reconciliation_health(
        state_file=path,
        sync_status_file=tmp_path / "status.json",
        sync_log_file=tmp_path / "log.jsonl",
        enabled=True,
        required_days=14,
        interval_hours=24,
        stale_hours=36,
        now=success + timedelta(hours=2),
    )

    assert health["last_success_at"] == success.isoformat()
    assert health["due"] is False
