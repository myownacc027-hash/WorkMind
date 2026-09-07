from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import typer
from sqlalchemy import func, select

from apps.attachment_processor.common import write_json_status
from apps.attachment_processor.worker import run_attachment_batch
from packages.common.config import get_settings
from packages.common.logging import configure_logging
from packages.database.models import ProcessingJob
from packages.database.session import SessionLocal

app = typer.Typer(
    add_completion=False,
    help="Extract and index queued WorkMind attachments.",
)


def _remaining() -> int:
    with SessionLocal() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(ProcessingJob)
                .where(
                    ProcessingJob.job_type == "index_attachment",
                    ProcessingJob.source_type == "attachment",
                    ProcessingJob.status.in_(("pending", "processing")),
                )
            )
            or 0
        )


@app.command()
def run(
    batch_size: int = typer.Option(10, min=1, max=50),
    until_empty: bool = typer.Option(
        False,
        "--until-empty",
        help="Continue until no queued attachment jobs remain.",
    ),
    max_batches: int = typer.Option(
        100_000,
        min=1,
        max=1_000_000,
    ),
) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    started = datetime.now(UTC)
    started_mono = time.monotonic()
    totals = {
        "claimed": 0,
        "completed": 0,
        "failed": 0,
        "indexed": 0,
        "terminal": 0,
        "chunks": 0,
        "batches": 0,
    }

    for _ in range(max_batches):
        result = run_attachment_batch(
            SessionLocal,
            settings=settings,
            batch_size=batch_size,
        )
        totals["batches"] += 1
        for key in (
            "claimed",
            "completed",
            "failed",
            "indexed",
            "terminal",
            "chunks",
        ):
            totals[key] += int(result[key])

        elapsed = max(
            time.monotonic() - started_mono,
            0.001,
        )
        remaining = _remaining()
        rate = totals["completed"] / elapsed
        eta_seconds = (
            round(remaining / rate)
            if rate > 0
            else None
        )
        status = {
            "status": (
                "completed"
                if result["claimed"] == 0
                else "running"
            ),
            "started_at": started.isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "elapsed_seconds": round(elapsed, 3),
            "remaining_jobs": remaining,
            "rate_attachments_per_minute": round(rate * 60, 2),
            "eta": (
                str(timedelta(seconds=eta_seconds))
                if eta_seconds is not None
                else None
            ),
            "totals": totals,
            "last_batch": result,
        }
        write_json_status(
            settings.attachment_worker_status_file,
            settings.attachment_worker_log_file,
            status,
        )
        typer.echo(
            json.dumps(
                status,
                ensure_ascii=False,
                indent=2,
            )
        )

        if result["claimed"] == 0:
            return
        if not until_empty:
            return

    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
