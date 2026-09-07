from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil
import structlog
import typer

from apps.attachment_processor.worker import run_attachment_batch
from apps.outlook_collector.outlook import (
    OutlookCollectorError,
    collect_mail,
)
from apps.processing_worker.worker import run_batch
from apps.sync_runner.reconciliation import (
    mark_reconciliation_completed,
    mark_reconciliation_failed,
    mark_reconciliation_started,
    reconciliation_health,
    recover_abandoned_reconciliation,
)
from packages.common.config import get_settings
from packages.common.locking import single_instance_lock
from packages.common.logging import configure_logging
from packages.database.session import SessionLocal
from packages.processing.embeddings import embedding_service_status

app = typer.Typer(
    add_completion=False,
    help=(
        "Synchronize Outlook, reconcile late messages, "
        "and index queued WorkMind sources."
    ),
)


def _now() -> datetime:
    return datetime.now(UTC)


def _is_outlook_running() -> bool:
    for process in psutil.process_iter(["name"]):
        try:
            if (
                process.info.get("name") or ""
            ).casefold() == "outlook.exe":
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def _write_result(
    status_file: Path,
    log_file: Path,
    result: dict[str, Any],
) -> None:
    status_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    encoded = json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
    )
    temporary = status_file.with_suffix(".tmp")
    temporary.write_text(
        encoded + "\n",
        encoding="utf-8",
    )
    temporary.replace(status_file)

    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                result,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )


def _echo_result(result: dict[str, Any]) -> None:
    typer.echo(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )


def _health(
    settings: Any,
    *,
    enabled: bool,
    required_days: int,
    interval_hours: int,
    stale_hours: int,
    force: bool = False,
) -> dict[str, Any]:
    return reconciliation_health(
        state_file=settings.mail_reconciliation_status_file,
        sync_status_file=settings.sync_status_file,
        sync_log_file=settings.sync_log_file,
        enabled=enabled,
        required_days=required_days,
        interval_hours=interval_hours,
        stale_hours=stale_hours,
        force=force,
    )


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@app.command()
def sync(
    lookback_days: int = typer.Option(
        7,
        min=1,
        max=3650,
        help=(
            "Incremental fallback lookback when a folder "
            "has no saved cursor."
        ),
    ),
    max_per_folder: int = typer.Option(
        500,
        min=0,
        help=(
            "Incremental maximum messages per folder; "
            "0 means unlimited."
        ),
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help=(
            "Ignore saved cursors and rescan the requested "
            "lookback window."
        ),
    ),
    overlap_minutes: int = typer.Option(
        30,
        min=5,
        max=1440,
        help=(
            "Safety overlap behind the latest cursor for "
            "normal incremental mode."
        ),
    ),
    disable_reconciliation: bool = typer.Option(
        False,
        "--disable-reconciliation",
        help="Disable the automatic periodic full rescan.",
    ),
    force_reconciliation: bool = typer.Option(
        False,
        "--force-reconciliation",
        help=(
            "Run the reconciliation window now, regardless "
            "of its last successful time."
        ),
    ),
    reconcile_days: int | None = typer.Option(
        None,
        min=1,
        max=3650,
        help="Override the reconciliation lookback window.",
    ),
    reconcile_interval_hours: int | None = typer.Option(
        None,
        min=1,
        max=720,
        help="Override how often reconciliation is due.",
    ),
    reconcile_stale_hours: int | None = typer.Option(
        None,
        min=1,
        max=1440,
        help=(
            "Override when reconciliation health becomes "
            "stale."
        ),
    ),
    reconcile_max_per_folder: int | None = typer.Option(
        None,
        min=0,
        help=(
            "Override reconciliation message limit; "
            "0 means unlimited and is recommended."
        ),
    ),
    batch_size: int = typer.Option(
        100,
        min=1,
        max=500,
        help="Indexing jobs processed in one batch.",
    ),
    max_batches: int = typer.Option(
        50,
        min=1,
        max=1000,
        help="Safety limit for worker batches.",
    ),
    require_outlook_running: bool = typer.Option(
        True,
        help=(
            "Skip safely instead of starting Outlook "
            "automatically."
        ),
    ),
) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = structlog.get_logger(__name__)

    if full and force_reconciliation:
        raise typer.BadParameter(
            "--full and --force-reconciliation are mutually "
            "exclusive."
        )
    if disable_reconciliation and force_reconciliation:
        raise typer.BadParameter(
            "--disable-reconciliation and "
            "--force-reconciliation are mutually exclusive."
        )

    reconciliation_enabled = bool(
        settings.mail_reconciliation_enabled
        and not disable_reconciliation
    )
    resolved_reconcile_days = (
        reconcile_days
        if reconcile_days is not None
        else settings.mail_reconciliation_days
    )
    resolved_interval_hours = (
        reconcile_interval_hours
        if reconcile_interval_hours is not None
        else settings.mail_reconciliation_interval_hours
    )
    resolved_stale_hours = (
        reconcile_stale_hours
        if reconcile_stale_hours is not None
        else settings.mail_reconciliation_stale_hours
    )
    resolved_reconcile_max = (
        reconcile_max_per_folder
        if reconcile_max_per_folder is not None
        else settings.mail_reconciliation_max_per_folder
    )

    if resolved_stale_hours < resolved_interval_hours:
        raise typer.BadParameter(
            "Reconciliation stale threshold must be greater "
            "than or equal to its interval."
        )

    started = _now()
    started_monotonic = time.monotonic()
    initial_health = _health(
        settings,
        enabled=reconciliation_enabled,
        required_days=resolved_reconcile_days,
        interval_hours=resolved_interval_hours,
        stale_hours=resolved_stale_hours,
        force=force_reconciliation,
    )

    run_reconciliation = bool(
        not full
        and reconciliation_enabled
        and initial_health["due"]
    )
    effective_mode = (
        "full"
        if full
        else (
            "reconciliation"
            if run_reconciliation
            else "incremental"
        )
    )
    effective_lookback_days = (
        resolved_reconcile_days
        if run_reconciliation
        else lookback_days
    )
    effective_max_per_folder = (
        resolved_reconcile_max
        if run_reconciliation
        else max_per_folder
    )
    coverage_run = bool(
        effective_mode in {"full", "reconciliation"}
        and effective_lookback_days >= resolved_reconcile_days
        and effective_max_per_folder == 0
    )
    requested_mode = (
        "full"
        if full
        else (
            "reconciliation"
            if force_reconciliation
            else "incremental"
        )
    )

    base_result: dict[str, Any] = {
        "started_at": started.isoformat(),
        "finished_at": None,
        "duration_seconds": None,
        "status": "running",
        "outlook_running": _is_outlook_running(),
        "requested_mode": requested_mode,
        "mode": effective_mode,
        "lookback_days": effective_lookback_days,
        "overlap_minutes": overlap_minutes,
        "max_per_folder": effective_max_per_folder,
        "reconciliation": {
            **initial_health,
            "run_this_time": run_reconciliation,
            "coverage_run": coverage_run,
            "configured_max_per_folder": resolved_reconcile_max,
        },
        "embedding_service": embedding_service_status(
            timeout_seconds=1.5
        ),
    }

    with single_instance_lock(
        settings.outlook_operation_lock_file
    ) as acquired:
        if not acquired:
            result = {
                **base_result,
                "status": "skipped",
                "reason": "another_sync_is_running",
                "finished_at": _now().isoformat(),
                "duration_seconds": round(
                    time.monotonic() - started_monotonic,
                    3,
                ),
            }
            # The process owning the lock owns the shared running
            # status file. A contender only reports to its caller.
            _echo_result(result)
            return

        # We own the operation lock. A previously persisted reconciliation
        # still marked running is therefore orphaned and must be repaired.
        recover_abandoned_reconciliation(
            settings.mail_reconciliation_status_file,
            recovered_at=started,
            reason=(
                "Previous reconciliation ended without a terminal state; "
                "a later sync acquired the operation lock."
            ),
        )

        if (
            require_outlook_running
            and not base_result["outlook_running"]
        ):
            result = {
                **base_result,
                "status": "skipped",
                "reason": "outlook_is_not_running",
                "finished_at": _now().isoformat(),
                "duration_seconds": round(
                    time.monotonic() - started_monotonic,
                    3,
                ),
            }
            _write_result(
                settings.sync_status_file,
                settings.sync_log_file,
                result,
            )
            _echo_result(result)
            return

        settings.outlook_lookback_days = effective_lookback_days
        settings.outlook_max_messages_per_folder = (
            effective_max_per_folder
        )

        if coverage_run:
            mark_reconciliation_started(
                settings.mail_reconciliation_status_file,
                started_at=started,
                mode=effective_mode,
                lookback_days=effective_lookback_days,
                max_per_folder=effective_max_per_folder,
            )

        _write_result(
            settings.sync_status_file,
            settings.sync_log_file,
            base_result,
        )

        try:
            with SessionLocal() as session:
                collector = collect_mail(
                    session,
                    settings,
                    incremental=(
                        effective_mode == "incremental"
                    ),
                    overlap_minutes=overlap_minutes,
                )

            batches: list[dict[str, int]] = []
            totals = {
                "claimed": 0,
                "completed": 0,
                "failed": 0,
                "chunks": 0,
            }

            for _ in range(max_batches):
                batch = run_batch(
                    SessionLocal,
                    settings=settings,
                    batch_size=batch_size,
                )
                batches.append(batch)
                for key in totals:
                    totals[key] += _integer(batch.get(key))
                if batch["claimed"] == 0:
                    break
                if (
                    batch["claimed"] > 0
                    and batch["completed"] == 0
                ):
                    break

            queue_drained = bool(
                batches
                and batches[-1]["claimed"] == 0
            )

            attachment_batches: list[dict[str, int]] = []
            attachment_totals = {
                "claimed": 0,
                "completed": 0,
                "failed": 0,
                "indexed": 0,
                "terminal": 0,
                "chunks": 0,
            }
            for _ in range(min(max_batches, 10)):
                attachment_batch = run_attachment_batch(
                    SessionLocal,
                    settings=settings,
                    batch_size=min(batch_size, 25),
                )
                attachment_batches.append(attachment_batch)
                for key in attachment_totals:
                    attachment_totals[key] += _integer(
                        attachment_batch.get(key)
                    )
                if attachment_batch["claimed"] == 0:
                    break
                if (
                    attachment_batch["claimed"] > 0
                    and attachment_batch["completed"] == 0
                ):
                    break

            attachment_queue_drained = bool(
                attachment_batches
                and attachment_batches[-1]["claimed"] == 0
            )

            collector_failed = _integer(
                collector.get("failed")
            )
            attachment_save_failed = _integer(
                collector.get("attachment_failed")
            )
            worker_failed = totals["failed"]
            attachment_worker_failed = (
                attachment_totals["failed"]
            )

            warnings: list[str] = []
            if collector_failed:
                warnings.append(
                    f"{collector_failed} Outlook mail item(s) "
                    "failed or were skipped during collection."
                )
            if attachment_save_failed:
                warnings.append(
                    f"{attachment_save_failed} attachment(s) "
                    "could not be saved from Outlook."
                )
            if worker_failed:
                warnings.append(
                    f"{worker_failed} mail indexing job(s) "
                    "failed during this run."
                )
            if attachment_worker_failed:
                warnings.append(
                    f"{attachment_worker_failed} attachment "
                    "indexing job(s) failed during this run."
                )
            if not queue_drained:
                warnings.append(
                    "Mail indexing queue still contains work."
                )
            if not attachment_queue_drained:
                warnings.append(
                    "Attachment indexing queue still contains work."
                )

            has_errors = any(
                (
                    collector_failed,
                    attachment_save_failed,
                    worker_failed,
                    attachment_worker_failed,
                )
            )

            finished = _now()
            result = {
                **base_result,
                "status": (
                    "completed_with_errors"
                    if has_errors
                    else "completed"
                ),
                "collector": collector,
                "indexing": {
                    **totals,
                    "batches": len(batches),
                    "queue_drained": queue_drained,
                },
                "attachment_indexing": {
                    **attachment_totals,
                    "batches": len(attachment_batches),
                    "queue_drained": attachment_queue_drained,
                },
                "attention_required": has_errors,
                "warnings": warnings,
                "finished_at": finished.isoformat(),
                "duration_seconds": round(
                    time.monotonic() - started_monotonic,
                    3,
                ),
            }

            if coverage_run:
                if collector_failed == 0:
                    mark_reconciliation_completed(
                        settings.mail_reconciliation_status_file,
                        result=result,
                    )
                else:
                    mark_reconciliation_failed(
                        settings.mail_reconciliation_status_file,
                        result={
                            **result,
                            "status": "failed",
                            "error_type": "ReconciliationIncomplete",
                            "error": (
                                f"{collector_failed} Outlook "
                                "mail item(s) failed or were "
                                "skipped."
                            ),
                        },
                    )

            final_health = _health(
                settings,
                enabled=reconciliation_enabled,
                required_days=resolved_reconcile_days,
                interval_hours=resolved_interval_hours,
                stale_hours=resolved_stale_hours,
            )
            result["reconciliation"] = {
                **result["reconciliation"],
                **final_health,
                "run_this_time": run_reconciliation,
                "coverage_run": coverage_run,
            }

            _write_result(
                settings.sync_status_file,
                settings.sync_log_file,
                result,
            )
            logger.info(
                "workmind_sync_completed",
                status=result["status"],
                mode=effective_mode,
                collector=collector,
                indexing=totals,
                reconciliation=result["reconciliation"],
            )
        except OutlookCollectorError as exc:
            result = {
                **base_result,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "attention_required": True,
                "warnings": [str(exc)],
                "finished_at": _now().isoformat(),
                "duration_seconds": round(
                    time.monotonic() - started_monotonic,
                    3,
                ),
            }
            if coverage_run:
                mark_reconciliation_failed(
                    settings.mail_reconciliation_status_file,
                    result=result,
                )
            _write_result(
                settings.sync_status_file,
                settings.sync_log_file,
                result,
            )
            _echo_result(result)
            raise typer.Exit(code=2)
        except Exception as exc:
            result = {
                **base_result,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "attention_required": True,
                "warnings": [
                    f"{type(exc).__name__}: {exc}"
                ],
                "finished_at": _now().isoformat(),
                "duration_seconds": round(
                    time.monotonic() - started_monotonic,
                    3,
                ),
            }
            if coverage_run:
                mark_reconciliation_failed(
                    settings.mail_reconciliation_status_file,
                    result=result,
                )
            _write_result(
                settings.sync_status_file,
                settings.sync_log_file,
                result,
            )
            logger.exception(
                "workmind_sync_failed",
                mode=effective_mode,
                error=str(exc),
            )
            _echo_result(result)
            raise typer.Exit(code=1)

        # Normal control flow is intentionally outside the try block.
        _echo_result(result)
        if result["status"] == "completed_with_errors":
            raise typer.Exit(code=2)
        return


if __name__ == "__main__":
    app()
