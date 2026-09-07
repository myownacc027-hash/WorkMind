from __future__ import annotations

import json
import time

import typer

from apps.processing_worker.worker import run_batch
from packages.common.config import get_settings
from packages.common.logging import configure_logging
from packages.database.session import SessionLocal

app = typer.Typer(add_completion=False, help="Process WorkMind indexing jobs.")


@app.command()
def run(
    batch_size: int = typer.Option(10, min=1, max=500),
    once: bool = typer.Option(True, help="Process one batch and exit."),
    poll_seconds: float = typer.Option(5.0, min=0.5, max=300),
) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    while True:
        result = run_batch(SessionLocal, settings=settings, batch_size=batch_size)
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        if once:
            return
        if result["claimed"] == 0:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    app()
