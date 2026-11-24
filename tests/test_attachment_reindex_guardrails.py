from __future__ import annotations

from typer.testing import CliRunner

from apps.attachment_processor.backfill import app


runner = CliRunner()


def test_reindex_requires_explicit_extension_filter() -> None:
    result = runner.invoke(app, ["--reindex-indexed", "--statuses", "indexed"])
    assert result.exit_code != 0
    assert "--extensions" in result.output


def test_reindex_requires_exactly_indexed_status() -> None:
    result = runner.invoke(
        app,
        ["--reindex-indexed", "--extensions", ".xlsx", "--statuses", "unsupported"],
    )
    assert result.exit_code != 0
    assert "--statuses indexed" in result.output


def test_truncated_only_needs_reindex_opt_in() -> None:
    result = runner.invoke(app, ["--truncated-only"])
    assert result.exit_code != 0
    assert "--reindex-indexed" in result.output
