from __future__ import annotations

import json
from pathlib import Path

import typer

from packages.knowledge.service import import_knowledge_bundle

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def import_bundle(
    bundle_path: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    apply: bool = typer.Option(False, "--apply"),
) -> None:
    result = import_knowledge_bundle(
        bundle_path.read_text(encoding="utf-8"),
        apply=apply,
        created_by="cli",
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("ok") is False:
        raise typer.Exit(2)


if __name__ == "__main__":
    app()
