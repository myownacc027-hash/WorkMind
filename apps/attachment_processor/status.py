from __future__ import annotations

import json

from sqlalchemy import func, select

from packages.common.config import get_settings
from packages.database.models import (
    Attachment,
    DocumentChunk,
    ProcessingJob,
)
from packages.database.session import SessionLocal


def main() -> None:
    settings = get_settings()

    with SessionLocal() as session:
        attachment_rows = session.execute(
            select(
                Attachment.processing_status,
                func.count(),
            ).group_by(Attachment.processing_status)
        ).all()
        job_rows = session.execute(
            select(
                ProcessingJob.status,
                func.count(),
            )
            .where(
                ProcessingJob.job_type == "index_attachment",
                ProcessingJob.source_type == "attachment",
            )
            .group_by(ProcessingJob.status)
        ).all()
        chunks = int(
            session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.source_type == "attachment")
            )
            or 0
        )
        extracted_chars = int(
            session.scalar(
                select(func.coalesce(func.sum(Attachment.extracted_chars), 0))
            )
            or 0
        )

    worker = {"status": "never_run"}
    if settings.attachment_worker_status_file.exists():
        try:
            worker = json.loads(
                settings.attachment_worker_status_file.read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, json.JSONDecodeError) as exc:
            worker = {
                "status": "invalid",
                "error": str(exc),
            }

    print(
        json.dumps(
            {
                "attachments": {
                    str(status): int(count)
                    for status, count in attachment_rows
                },
                "jobs": {
                    str(status): int(count)
                    for status, count in job_rows
                },
                "attachment_chunks": chunks,
                "extracted_chars": extracted_chars,
                "worker": worker,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
