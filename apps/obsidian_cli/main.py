from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from packages.knowledge.obsidian_apply import apply_projection
from packages.knowledge.obsidian_apply import projection_status
from packages.knowledge.obsidian_export import (
    ObsidianExportError,
    build_projection_manifest,
)


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
)


def _echo_json(value: dict[str, Any]) -> None:
    typer.echo(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        )
    )


def _domain_error(exc: ObsidianExportError) -> None:
    _echo_json(
        {
            "ok": False,
            "error": "obsidian_export_error",
            "detail": str(exc),
        }
    )
    raise typer.Exit(2)


@app.command("dry-run")
def dry_run(
    limit: int = typer.Option(
        100,
        "--limit",
        min=1,
        max=1000,
    ),
    entity_status: str | None = typer.Option(
        None,
        "--status",
        help="Optional structured-knowledge entity status filter.",
    ),
    include_excerpt: bool = typer.Option(
        False,
        "--include-excerpt",
        help="Include evidence excerpts in generated Markdown.",
    ),
    vault_root: Path | None = typer.Option(
        None,
        "--vault-root",
        file_okay=False,
        dir_okay=True,
        help="Override the configured Obsidian vault root.",
    ),
) -> None:
    """Build and print the projection manifest without writing the vault."""

    try:
        result = build_projection_manifest(
            limit=limit,
            status=entity_status,
            include_excerpt=include_excerpt,
            vault_root=vault_root,
        )
    except ObsidianExportError as exc:
        _domain_error(exc)

    _echo_json(result)



@app.command("status")
def status_command(
    include_excerpt: bool = typer.Option(
        False,
        "--include-excerpt",
        help="Include evidence excerpts in generated Markdown comparison.",
    ),
    vault_root: Path | None = typer.Option(
        None,
        "--vault-root",
        file_okay=False,
        help="Override the configured Obsidian vault root.",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Exit with code 1 when the vault is not in sync.",
    ),
) -> None:
    """
    Compare the complete WorkMind projection with the vault without writing.
    """

    try:
        result = projection_status(
            include_excerpt=include_excerpt,
            vault_root=vault_root,
        )
    except ObsidianExportError as exc:
        _domain_error(exc)

    _echo_json(
        result
    )

    if (
        check
        and not result.get(
            "in_sync",
            False,
        )
    ):
        raise typer.Exit(
            code=1
        )


@app.command("apply")
def apply_command(
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        max=1000,
        hidden=True,
        help=(
            "Deprecated for apply. Full writable projection "
            "is always unbounded."
        ),
    ),
    entity_status: str | None = typer.Option(
        None,
        "--status",
        help="Optional structured-knowledge entity status filter.",
    ),
    include_excerpt: bool = typer.Option(
        False,
        "--include-excerpt",
        help="Include evidence excerpts in generated Markdown.",
    ),
    vault_root: Path | None = typer.Option(
        None,
        "--vault-root",
        file_okay=False,
        dir_okay=True,
        help="Override the configured Obsidian vault root.",
    ),
    prune: bool = typer.Option(
        True,
        "--prune/--no-prune",
        help="Remove stale WorkMind-managed Markdown notes.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Explicitly confirm vault mutation.",
    ),
) -> None:
    """Apply a complete projection to the vault after explicit confirmation."""

    if not yes:
        _echo_json(
            {
                "ok": False,
                "error": "explicit_confirmation_required",
                "detail": (
                    "The apply command writes the Obsidian vault. "
                    "Re-run with --yes after reviewing dry-run output."
                ),
            }
        )
        raise typer.Exit(2)

    try:
        result = apply_projection(
            limit=limit,
            status=entity_status,
            include_excerpt=include_excerpt,
            vault_root=vault_root,
            prune=prune,
        )
    except ObsidianExportError as exc:
        _domain_error(exc)

    _echo_json(result)


if __name__ == "__main__":
    app()
