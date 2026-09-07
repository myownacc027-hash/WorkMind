from __future__ import annotations

import json

import typer

from packages.search.hybrid import search_hybrid

app = typer.Typer(
    add_completion=False,
    help="Test WorkMind unified hybrid search.",
)


@app.command()
def search(
    query: str = typer.Argument(
        ...,
        help="Question, phrase, task number, code, or identifier.",
    ),
    limit: int = typer.Option(10, min=1, max=50),
    mode: str = typer.Option(
        "hybrid",
        help="hybrid, semantic, or lexical",
    ),
    source_type: str = typer.Option(
        "all",
        help="all, email, attachment, or document",
    ),
    sender: str | None = typer.Option(None),
    filename: str | None = typer.Option(None),
    date_from: str | None = typer.Option(None),
    date_to: str | None = typer.Option(None),
    snippets_per_source: int = typer.Option(
        3,
        min=1,
        max=10,
    ),
) -> None:
    result = search_hybrid(
        query,
        limit=limit,
        mode=mode,
        source_type=source_type,
        sender=sender,
        filename=filename,
        date_from=date_from,
        date_to=date_to,
        snippets_per_source=snippets_per_source,
    )
    typer.echo(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
