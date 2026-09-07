from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from packages.common.config import get_settings
from packages.database.models import DocumentChunk, ExternalDocument
from packages.database.session import SessionLocal
from packages.processing.documents import discover_files

_ERROR_STATUSES = (
    "error",
    "corrupt",
    "empty_no_text",
    "protected",
    "missing",
    "unsupported",
    "too_large",
)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def main() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    stale_cutoff = now - timedelta(
        minutes=settings.document_import_stale_minutes
    )

    with SessionLocal() as session:
        status_rows = session.execute(
            select(
                ExternalDocument.processing_status,
                func.count(),
            ).group_by(ExternalDocument.processing_status)
        ).all()
        chunks = int(
            session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.source_type == "document")
            )
            or 0
        )
        extracted_chars = int(
            session.scalar(
                select(
                    func.coalesce(
                        func.sum(ExternalDocument.extracted_chars),
                        0,
                    )
                )
            )
            or 0
        )
        error_count = int(
            session.scalar(
                select(func.count())
                .select_from(ExternalDocument)
                .where(
                    ExternalDocument.processing_status.in_(
                        _ERROR_STATUSES
                    )
                )
            )
            or 0
        )
        stuck_count = int(
            session.scalar(
                select(func.count())
                .select_from(ExternalDocument)
                .where(
                    ExternalDocument.processing_status == "processing",
                    ExternalDocument.updated_at < stale_cutoff,
                )
            )
            or 0
        )
        recent_errors = list(
            session.execute(
                select(
                    ExternalDocument.id,
                    ExternalDocument.original_filename,
                    ExternalDocument.processing_status,
                    ExternalDocument.last_error,
                    ExternalDocument.processed_at,
                    ExternalDocument.updated_at,
                )
                .where(
                    ExternalDocument.processing_status.in_(
                        _ERROR_STATUSES
                    )
                )
                .order_by(
                    ExternalDocument.updated_at.desc(),
                    ExternalDocument.id,
                )
                .limit(settings.document_status_recent_error_limit)
            ).mappings()
        )
        stuck_documents = list(
            session.execute(
                select(
                    ExternalDocument.id,
                    ExternalDocument.original_filename,
                    ExternalDocument.updated_at,
                    ExternalDocument.archive_path,
                )
                .where(
                    ExternalDocument.processing_status == "processing",
                    ExternalDocument.updated_at < stale_cutoff,
                )
                .order_by(ExternalDocument.updated_at)
                .limit(settings.document_status_recent_error_limit)
            ).mappings()
        )

    last_run: dict[str, Any] = {"status": "never_run"}
    if settings.document_import_status_file.exists():
        try:
            last_run = json.loads(
                settings.document_import_status_file.read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, json.JSONDecodeError) as exc:
            last_run = {"status": "invalid", "error": str(exc)}

    last_run_started = _parse_datetime(last_run.get("started_at"))
    last_run_age_minutes = (
        round((now - last_run_started).total_seconds() / 60, 2)
        if last_run_started
        else None
    )
    run_looks_stale = bool(
        last_run.get("status") == "running"
        and last_run_started
        and last_run_started < stale_cutoff
    )

    failed_files = _count_files(settings.document_failed_dir)
    attention_required = bool(
        error_count
        or stuck_count
        or run_looks_stale
        or last_run.get("status")
        in {"failed", "completed_with_errors", "invalid"}
    )
    if stuck_count or run_looks_stale:
        health = "error"
    elif attention_required:
        health = "warning"
    else:
        health = "ok"

    print(
        json.dumps(
            {
                "health": health,
                "attention_required": attention_required,
                "documents": {
                    str(status): int(count)
                    for status, count in status_rows
                },
                "document_chunks": chunks,
                "extracted_chars": extracted_chars,
                "pending_import_files": len(
                    discover_files(settings.document_import_dir)
                ),
                "failed_files_on_disk": failed_files,
                "error_documents": error_count,
                "stuck_processing_documents": stuck_count,
                "stale_after_minutes": (
                    settings.document_import_stale_minutes
                ),
                "last_run_age_minutes": last_run_age_minutes,
                "last_run_looks_stale": run_looks_stale,
                "recent_errors": [
                    {
                        **{
                            key: (
                                value.isoformat()
                                if isinstance(value, datetime)
                                else str(value)
                                if key == "id"
                                else value
                            )
                            for key, value in row.items()
                        },
                        "source_ref": (
                            f"workmind://document/{row['id']}"
                        ),
                    }
                    for row in recent_errors
                ],
                "stuck_documents": [
                    {
                        "document_id": str(row["id"]),
                        "source_ref": (
                            f"workmind://document/{row['id']}"
                        ),
                        "filename": row["original_filename"],
                        "updated_at": (
                            row["updated_at"].isoformat()
                            if row["updated_at"]
                            else None
                        ),
                        "archive_path": row["archive_path"],
                    }
                    for row in stuck_documents
                ],
                "import_dir": str(settings.document_import_dir),
                "archive_dir": str(settings.document_archive_dir),
                "failed_dir": str(settings.document_failed_dir),
                "last_run": last_run,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
