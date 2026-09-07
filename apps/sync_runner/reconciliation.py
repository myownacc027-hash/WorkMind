from __future__ import annotations

import json
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(
            value.strip().replace("Z", "+00:00")
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json_atomic(
    path: Path,
    value: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _collector_failed(result: dict[str, Any]) -> int:
    collector = result.get("collector")
    if not isinstance(collector, dict):
        return 0
    try:
        return int(collector.get("failed", 0))
    except (TypeError, ValueError):
        return 1


def qualifies_as_reconciliation(
    result: dict[str, Any],
    *,
    required_days: int,
) -> bool:
    if result.get("status") not in {
        "completed",
        "completed_with_errors",
    }:
        return False
    if result.get("mode") not in {
        "full",
        "reconciliation",
    }:
        return False
    try:
        lookback_days = int(
            result.get("lookback_days", 0)
        )
        max_per_folder = int(
            result.get("max_per_folder", -1)
        )
    except (TypeError, ValueError):
        return False
    return (
        lookback_days >= required_days
        and max_per_folder == 0
        and _collector_failed(result) == 0
        and parse_timestamp(result.get("finished_at"))
        is not None
    )


def _latest_qualifying_log_result(
    log_file: Path,
    *,
    required_days: int,
    max_lines: int = 5000,
) -> dict[str, Any]:
    if not log_file.exists():
        return {}
    try:
        with log_file.open(
            "r",
            encoding="utf-8",
            errors="replace",
        ) as handle:
            recent = deque(handle, maxlen=max_lines)
    except OSError:
        return {}

    for raw_line in reversed(recent):
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(value, dict)
            and qualifies_as_reconciliation(
                value,
                required_days=required_days,
            )
        ):
            return value
    return {}


def _state_success_run(
    state: dict[str, Any],
    *,
    required_days: int,
) -> dict[str, Any]:
    explicit = state.get("last_success_run")
    if (
        isinstance(explicit, dict)
        and qualifies_as_reconciliation(
            explicit,
            required_days=required_days,
        )
    ):
        return explicit

    # Backward compatibility with 0.10.3-0.10.6 state files.
    legacy = state.get("last_run")
    if (
        isinstance(legacy, dict)
        and qualifies_as_reconciliation(
            legacy,
            required_days=required_days,
        )
    ):
        return legacy
    return {}


def latest_reconciliation_success(
    *,
    state_file: Path,
    sync_status_file: Path,
    sync_log_file: Path,
    required_days: int,
) -> tuple[datetime | None, str, dict[str, Any]]:
    state = load_json(state_file)
    candidates: list[
        tuple[datetime, str, dict[str, Any]]
    ] = []

    state_success = _state_success_run(
        state,
        required_days=required_days,
    )
    if state_success:
        state_time = parse_timestamp(
            state_success.get("finished_at")
        )
        if state_time is not None:
            candidates.append(
                (state_time, "state_file", state_success)
            )

    latest_status = load_json(sync_status_file)
    if qualifies_as_reconciliation(
        latest_status,
        required_days=required_days,
    ):
        completed_at = parse_timestamp(
            latest_status.get("finished_at")
        )
        if completed_at is not None:
            candidates.append(
                (
                    completed_at,
                    "sync_status_file",
                    latest_status,
                )
            )

    latest_log = _latest_qualifying_log_result(
        sync_log_file,
        required_days=required_days,
    )
    if latest_log:
        completed_at = parse_timestamp(
            latest_log.get("finished_at")
        )
        if completed_at is not None:
            candidates.append(
                (
                    completed_at,
                    "sync_log_file",
                    latest_log,
                )
            )

    if not candidates:
        return None, "none", state

    completed_at, source, value = max(
        candidates,
        key=lambda item: item[0],
    )
    return completed_at, source, value


def reconciliation_health(
    *,
    state_file: Path,
    sync_status_file: Path,
    sync_log_file: Path,
    enabled: bool,
    required_days: int,
    interval_hours: int,
    stale_hours: int,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    current = (now or utc_now()).astimezone(UTC)
    state = load_json(state_file)
    last_success, source, source_value = (
        latest_reconciliation_success(
            state_file=state_file,
            sync_status_file=sync_status_file,
            sync_log_file=sync_log_file,
            required_days=required_days,
        )
    )

    age_hours: float | None = None
    next_due_at: datetime | None = None
    if last_success is not None:
        age_hours = max(
            0.0,
            (
                current - last_success
            ).total_seconds()
            / 3600.0,
        )
        next_due_at = (
            last_success
            + timedelta(hours=interval_hours)
        )

    due = bool(
        enabled
        and (
            force
            or last_success is None
            or current
            >= (
                last_success
                + timedelta(hours=interval_hours)
            )
        )
    )
    stale = bool(
        enabled
        and (
            last_success is None
            or current
            >= (
                last_success
                + timedelta(hours=stale_hours)
            )
        )
    )

    reason = "disabled"
    if enabled:
        if force:
            reason = "forced"
        elif last_success is None:
            reason = "never_completed"
        elif due:
            reason = "interval_elapsed"
        else:
            reason = "not_due"

    last_run = (
        state.get("last_run")
        if isinstance(state.get("last_run"), dict)
        else {}
    )
    if not last_run and source_value:
        last_run = {
            "status": source_value.get("status"),
            "mode": source_value.get("mode"),
            "started_at": source_value.get("started_at"),
            "finished_at": source_value.get("finished_at"),
            "lookback_days": source_value.get(
                "lookback_days"
            ),
            "max_per_folder": source_value.get(
                "max_per_folder"
            ),
            "collector": source_value.get("collector"),
        }

    return {
        "enabled": enabled,
        "due": due,
        "stale": stale,
        "reason": reason,
        "required_lookback_days": required_days,
        "interval_hours": interval_hours,
        "stale_hours": stale_hours,
        "last_success_at": (
            last_success.isoformat()
            if last_success
            else None
        ),
        "last_success_source": source,
        "age_hours": (
            round(age_hours, 3)
            if age_hours is not None
            else None
        ),
        "next_due_at": (
            next_due_at.isoformat()
            if next_due_at
            else None
        ),
        "last_status": state.get("last_status"),
        "last_error": state.get("last_error"),
        "last_run": last_run,
    }


def mark_reconciliation_started(
    path: Path,
    *,
    started_at: datetime,
    mode: str,
    lookback_days: int,
    max_per_folder: int,
) -> dict[str, Any]:
    state = load_json(path)
    state.update(
        {
            "last_status": "running",
            "last_error": None,
            "last_started_at": started_at.isoformat(),
            "last_run": {
                "status": "running",
                "mode": mode,
                "started_at": started_at.isoformat(),
                "finished_at": None,
                "lookback_days": lookback_days,
                "max_per_folder": max_per_folder,
            },
        }
    )
    write_json_atomic(path, state)
    return state


def mark_reconciliation_completed(
    path: Path,
    *,
    result: dict[str, Any],
) -> dict[str, Any]:
    state = load_json(path)
    finished_at = result.get("finished_at")
    collector = (
        result.get("collector")
        if isinstance(result.get("collector"), dict)
        else {}
    )
    success_run = {
        "status": result.get("status"),
        "mode": result.get("mode"),
        "started_at": result.get("started_at"),
        "finished_at": finished_at,
        "lookback_days": result.get("lookback_days"),
        "max_per_folder": result.get("max_per_folder"),
        "collector": collector,
        "duration_seconds": result.get(
            "duration_seconds"
        ),
    }
    state.update(
        {
            "last_status": "completed",
            "last_error": None,
            "last_success_at": finished_at,
            "last_success_run": success_run,
            "last_finished_at": finished_at,
            "last_recovered_messages": int(
                collector.get("created", 0)
            ),
            "last_updated_messages": int(
                collector.get("updated", 0)
            ),
            "last_seen_messages": int(
                collector.get("seen", 0)
            ),
            "last_run": success_run,
        }
    )
    write_json_atomic(path, state)
    return state


def mark_reconciliation_failed(
    path: Path,
    *,
    result: dict[str, Any],
) -> dict[str, Any]:
    state = load_json(path)
    state.update(
        {
            "last_status": "failed",
            "last_error": result.get("error"),
            "last_finished_at": result.get(
                "finished_at"
            ),
            "last_run": {
                "status": "failed",
                "mode": result.get("mode"),
                "started_at": result.get("started_at"),
                "finished_at": result.get(
                    "finished_at"
                ),
                "lookback_days": result.get(
                    "lookback_days"
                ),
                "max_per_folder": result.get(
                    "max_per_folder"
                ),
                "error_type": result.get(
                    "error_type"
                ),
                "error": result.get("error"),
            },
        }
    )
    write_json_atomic(path, state)
    return state



def recover_abandoned_reconciliation(
    path: Path,
    *,
    recovered_at: datetime,
    reason: str,
) -> dict[str, Any]:
    """Persist recovery of an orphaned reconciliation run."""
    state = load_json(path)
    if state.get("last_status") != "running":
        return state
    previous_run = (
        state.get("last_run")
        if isinstance(state.get("last_run"), dict)
        else {}
    )
    try:
        abandoned_count = int(state.get("abandoned_count", 0))
    except (TypeError, ValueError):
        abandoned_count = 0
    state.update({
        "last_status": "aborted",
        "last_error": reason,
        "last_finished_at": recovered_at.isoformat(),
        "last_run": {
            **previous_run,
            "status": "aborted",
            "finished_at": recovered_at.isoformat(),
            "error_type": "AbandonedRun",
            "error": reason,
        },
        "abandoned_count": abandoned_count + 1,
        "last_abandoned_at": recovered_at.isoformat(),
    })
    write_json_atomic(path, state)
    return state
