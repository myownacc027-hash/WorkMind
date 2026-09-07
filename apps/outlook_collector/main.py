from __future__ import annotations

import json

import structlog
import typer

from apps.outlook_collector.outlook import OutlookCollectorError, collect_mail
from packages.common.config import get_settings
from packages.common.logging import configure_logging
from packages.database.session import SessionLocal

app = typer.Typer(add_completion=False, help="Synchronize Outlook mail into WorkMind.")


@app.command()
def sync(
    lookback_days: int | None = typer.Option(None, min=1, help="Override lookback period."),
    max_per_folder: int | None = typer.Option(None, min=0, help="Limit messages per folder; 0 means all."),
) -> None:
    settings = get_settings()
    if lookback_days is not None:
        settings.outlook_lookback_days = lookback_days
    if max_per_folder is not None:
        settings.outlook_max_messages_per_folder = max_per_folder

    configure_logging(settings.log_level)
    logger = structlog.get_logger(__name__)
    logger.info(
        "outlook_sync_starting",
        folders=settings.outlook_folders,
        lookback_days=settings.outlook_lookback_days,
    )

    try:
        with SessionLocal() as session:
            counters = collect_mail(session, settings)
    except OutlookCollectorError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(json.dumps(counters, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
