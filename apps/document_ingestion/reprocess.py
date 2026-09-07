from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import typer
from sqlalchemy import select

from packages.common.config import get_settings
from packages.common.locking import single_instance_lock
from packages.database.models import ExternalDocument
from packages.database.session import SessionLocal
from packages.processing.documents import process_external_document

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Re-extract and re-index archived external documents.",
)


@app.callback()
def run(
    document_id: str | None = typer.Option(None, "--document-id"),
    all_errors: bool = typer.Option(False, "--all-errors"),
    stuck: bool = typer.Option(False, "--stuck"),
    stuck_minutes: int = typer.Option(
        60,
        "--stuck-minutes",
        min=5,
        max=1440,
    ),
    apply: bool = typer.Option(False, "--apply"),
) -> None:
    selectors = sum(
        int(value)
        for value in (bool(document_id), all_errors, stuck)
    )
    if selectors != 1:
        raise typer.BadParameter(
            "Specify exactly one of --document-id, --all-errors, or --stuck."
        )
    settings = get_settings()
    with SessionLocal() as session:
        if document_id:
            try:
                parsed_id = uuid.UUID(document_id)
            except ValueError as exc:
                raise typer.BadParameter("Invalid document UUID.") from exc
            documents = [session.get(ExternalDocument, parsed_id)]
            documents = [item for item in documents if item is not None]
        elif all_errors:
            documents = list(
                session.scalars(
                    select(ExternalDocument).where(
                        ExternalDocument.processing_status.in_((
                            "error",
                            "corrupt",
                            "empty_no_text",
                            "protected",
                            "missing",
                            "unsupported",
                            "too_large",
                        ))
                    )
                )
            )
        else:
            cutoff = datetime.now(UTC) - timedelta(
                minutes=stuck_minutes
            )
            documents = list(
                session.scalars(
                    select(ExternalDocument).where(
                        ExternalDocument.processing_status == "processing",
                        ExternalDocument.updated_at < cutoff,
                    )
                )
            )

    plan = [{
        "document_id": str(item.id),
        "source_ref": f"workmind://document/{item.id}",
        "filename": item.original_filename,
        "status": item.processing_status,
        "archive_path": item.archive_path,
    } for item in documents]
    if not apply:
        typer.echo(json.dumps({
            "apply": False,
            "count": len(plan),
            "documents": plan,
            "note": "Dry run only. Repeat with --apply to reprocess.",
        }, ensure_ascii=False, indent=2))
        return

    results: list[dict[str, Any]] = []
    with single_instance_lock(settings.document_import_lock_file) as acquired:
        if not acquired:
            raise typer.BadParameter("Another document import is already running.")
        with SessionLocal() as session:
            for item in documents:
                document = session.get(ExternalDocument, item.id)
                if document is None:
                    continue
                try:
                    result = process_external_document(
                        session,
                        document=document,
                        settings=settings,
                    )
                except Exception as exc:
                    session.rollback()
                    result = {
                        "status": "error",
                        "document_id": str(item.id),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                results.append(result)

    typer.echo(json.dumps({
        "apply": True,
        "count": len(results),
        "results": results,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
