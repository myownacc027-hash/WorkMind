from __future__ import annotations

import json
from pathlib import Path

import typer
from sqlalchemy import select

from packages.common.config import get_settings
from packages.database.models import (
    Attachment,
    DocumentChunk,
    ProcessingJob,
)
from packages.database.repositories import (
    enqueue_attachment_processing,
)
from packages.database.session import SessionLocal
from packages.storage import resolve_attachment_path

app = typer.Typer(
    add_completion=False,
    help="Queue existing archived attachments for extraction.",
)


@app.command()
def backfill(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Create processing jobs. Without it, report only.",
    ),
    reset_errors: bool = typer.Option(
        False,
        "--reset-errors",
        help="Retry attachments/jobs currently marked as error.",
    ),
) -> None:
    settings = get_settings()

    with SessionLocal() as session:
        attachments = list(
            session.scalars(
                select(Attachment).order_by(Attachment.created_at)
            )
        )
        indexed_attachment_ids = set(
            session.scalars(
                select(DocumentChunk.source_id)
                .where(DocumentChunk.source_type == "attachment")
                .distinct()
            )
        )
        active_job_ids = set(
            session.scalars(
                select(ProcessingJob.source_id)
                .where(
                    ProcessingJob.job_type == "index_attachment",
                    ProcessingJob.source_type == "attachment",
                    ProcessingJob.status.in_(("pending", "processing")),
                )
                .distinct()
            )
        )
        error_jobs_by_source: dict = {}
        if reset_errors:
            for job in session.scalars(
                select(ProcessingJob).where(
                    ProcessingJob.job_type == "index_attachment",
                    ProcessingJob.source_type == "attachment",
                    ProcessingJob.status == "error",
                )
            ):
                error_jobs_by_source.setdefault(
                    job.source_id,
                    [],
                ).append(job)

        report = {
            "mode": "apply" if apply else "dry_run",
            "total_attachments": len(attachments),
            "supported": 0,
            "unsupported": 0,
            "missing_file": 0,
            "already_indexed": 0,
            "already_queued": 0,
            "queued": 0,
            "errors_reset": 0,
        }

        for attachment in attachments:
            extension = Path(attachment.filename).suffix.lower()
            if extension not in settings.attachment_supported_extensions:
                report["unsupported"] += 1
                if apply and attachment.processing_status == "new":
                    attachment.processing_status = "unsupported"
                    attachment.last_error = (
                        f"Unsupported extension: "
                        f"{extension or '(none)'}"
                    )
                continue

            report["supported"] += 1
            attachment_path = resolve_attachment_path(
                attachment
            )
            if (
                attachment_path is None
                or not attachment_path.exists()
            ):
                report["missing_file"] += 1
                if apply:
                    attachment.processing_status = "missing"
                    attachment.last_error = (
                        "Archived attachment file is missing."
                    )
                continue

            if (
                attachment.processing_status == "indexed"
                and attachment.id in indexed_attachment_ids
            ):
                report["already_indexed"] += 1
                continue

            if attachment.id in active_job_ids:
                report["already_queued"] += 1
                continue

            if attachment.processing_status == "error":
                if not reset_errors:
                    continue
                report["errors_reset"] += 1
                for job in error_jobs_by_source.get(
                    attachment.id,
                    [],
                ):
                    job.status = "pending"
                    job.attempts = 0
                    job.last_error = None
                    job.locked_at = None
                attachment.last_error = None

            if apply:
                enqueue_attachment_processing(
                    session,
                    attachment.id,
                )
                attachment.processing_status = "queued"
            report["queued"] += 1

        if apply:
            session.commit()

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
