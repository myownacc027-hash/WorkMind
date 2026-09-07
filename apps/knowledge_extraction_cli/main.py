from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from packages.database.session import SessionLocal
from packages.knowledge.extraction import (
    apply_validated_extraction_job,
    backfill_extraction_queue,
    reextract_extraction_job_as_current_version,
    release_expired_leases,
    requeue_extraction_job,
    validate_extraction_job,
)

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@app.command("backfill")
def backfill(
    source_type: Annotated[
        list[str] | None,
        typer.Option(
            "--source-type",
            help="Repeat for email, attachment, or document. Default: all.",
        ),
    ] = None,
    date_from: str | None = typer.Option(None, "--date-from"),
    date_to: str | None = typer.Option(None, "--date-to"),
    limit: int = typer.Option(1000, min=1, max=100000),
    apply: bool = typer.Option(False, "--apply"),
) -> None:
    result = backfill_extraction_queue(
        source_types=source_type or None,
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to),
        limit=limit,
        apply=apply,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("release-expired")
def release_expired() -> None:
    with SessionLocal() as session:
        count = release_expired_leases(session)
        session.commit()
    typer.echo(json.dumps({"released": count}, indent=2))


@app.command("validate-file")
def validate_file(
    job_id: str = typer.Argument(...),
    lease_token: str = typer.Argument(...),
    bundle_path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
        ),
    ] = ...,
) -> None:
    """Validate a leased extraction bundle from UTF-8/UTF-8-BOM JSON."""
    bundle_json = bundle_path.read_text(encoding="utf-8-sig")
    result = validate_extraction_job(
        job_id,
        lease_token,
        bundle_json,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") != "validated":
        raise typer.Exit(2)


@app.command("apply-job")
def apply_job(
    job_id: str = typer.Argument(...),
) -> None:
    """Apply a previously validated extraction job."""
    result = apply_validated_extraction_job(job_id)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") != "completed":
        raise typer.Exit(2)


@app.command("requeue-job")
def requeue_job(
    job_id: str = typer.Argument(...),
    reason: str = typer.Option(
        "administratively requeued",
        "--reason",
    ),
    reset_attempts: bool = typer.Option(
        False,
        "--reset-attempts",
    ),
) -> None:
    """Clear a bad bundle and put a non-completed job back in pending."""
    result = requeue_extraction_job(
        job_id,
        reason=reason,
        reset_attempts=reset_attempts,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("reextract-job")
def reextract_job(
    job_id: str = typer.Argument(...),
    reason: str = typer.Option(..., "--reason"),
    apply: bool = typer.Option(False, "--apply"),
) -> None:
    """Preview or explicitly schedule a terminal job under the current version."""
    result = reextract_extraction_job_as_current_version(
        job_id,
        reason=reason,
        apply=apply,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        raise typer.Exit(2)


if __name__ == "__main__":
    app()
