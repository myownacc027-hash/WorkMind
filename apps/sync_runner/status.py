from __future__ import annotations

import json

from apps.sync_runner.reconciliation import reconciliation_health
from packages.common.config import get_settings
from packages.common.locking import lock_is_held


def _append_unique(
    values: list[str],
    message: str,
) -> None:
    if message and message not in values:
        values.append(message)



def _runtime_reconciliation_state(
    *,
    value: dict,
    reconciliation: dict,
    operation_lock_held: bool | None,
) -> tuple[bool, bool]:
    persisted_running = bool(
        value.get("status") == "running"
        and reconciliation.get("coverage_run")
    )
    if not persisted_running:
        return False, False
    if operation_lock_held is True:
        return True, False
    if operation_lock_held is False:
        return False, True
    return True, False


def main() -> None:
    settings = get_settings()
    if not settings.sync_status_file.exists():
        value = {
            "status": "never_run",
            "status_file": str(
                settings.sync_status_file
            ),
        }
    else:
        try:
            value = json.loads(
                settings.sync_status_file.read_text(
                    encoding="utf-8"
                )
            )
        except (
            OSError,
            json.JSONDecodeError,
        ) as exc:
            print(
                json.dumps(
                    {
                        "status": "invalid_status_file",
                        "status_file": str(
                            settings.sync_status_file
                        ),
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            raise SystemExit(1) from exc

    health = reconciliation_health(
        state_file=settings.mail_reconciliation_status_file,
        sync_status_file=settings.sync_status_file,
        sync_log_file=settings.sync_log_file,
        enabled=settings.mail_reconciliation_enabled,
        required_days=settings.mail_reconciliation_days,
        interval_hours=(
            settings.mail_reconciliation_interval_hours
        ),
        stale_hours=settings.mail_reconciliation_stale_hours,
    )

    run_reconciliation = (
        value.get("reconciliation")
        if isinstance(value.get("reconciliation"), dict)
        else {}
    )
    operation_lock_held = lock_is_held(
        settings.outlook_operation_lock_file
    )
    current_reconciliation, abandoned_reconciliation = (
        _runtime_reconciliation_state(
            value=value,
            reconciliation=run_reconciliation,
            operation_lock_held=operation_lock_held,
        )
    )

    value["reconciliation"] = {
        **run_reconciliation,
        **health,
        "operation_lock_held": operation_lock_held,
        "abandoned": abandoned_reconciliation,
    }
    if abandoned_reconciliation:
        value["raw_status"] = value.get("status")
        value["status"] = "abandoned"
        value["reconciliation"]["raw_last_status"] = (
            value["reconciliation"].get("last_status")
        )
        value["reconciliation"]["last_status"] = "aborted"

    warnings = [
        str(item)
        for item in (
            value.get("warnings")
            if isinstance(value.get("warnings"), list)
            else []
        )
        if str(item).strip()
    ]

    if value.get("status") == "failed":
        _append_unique(
            warnings,
            "The latest mail synchronization failed.",
        )
    elif value.get("status") == "completed_with_errors":
        _append_unique(
            warnings,
            "The latest mail synchronization completed "
            "with errors.",
        )

    if abandoned_reconciliation:
        _append_unique(
            warnings,
            "The previous mail reconciliation terminated without a "
            "terminal state. The last successful reconciliation is "
            "preserved; the next sync will persist crash recovery.",
        )
    elif current_reconciliation:
        _append_unique(
            warnings,
            "Mail reconciliation is currently running.",
        )
    elif health["stale"]:
        _append_unique(
            warnings,
            "Mail reconciliation is stale or has never "
            "completed. Late Outlook messages may be "
            "missing from WorkMind.",
        )
    elif health["due"]:
        _append_unique(
            warnings,
            "Mail reconciliation is due and will run "
            "during the next successful scheduled sync.",
        )

    value["attention_required"] = bool(
        value.get("attention_required", False)
        or value.get("status")
        in {"failed", "completed_with_errors", "abandoned"}
        or (
            health["stale"]
            and not current_reconciliation
        )
    )
    value["warnings"] = warnings

    print(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
