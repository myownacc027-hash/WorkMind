from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import apps.obsidian_cli.main as cli


runner = CliRunner()


def test_obsidian_console_script_is_registered() -> None:
    pyproject = Path(
        "pyproject.toml"
    ).read_text(
        encoding="utf-8"
    )

    assert (
        'workmind-obsidian = "apps.obsidian_cli.main:app"'
        in pyproject
    )


def test_dry_run_calls_manifest_without_apply(
    monkeypatch,
) -> None:
    calls = []

    def fake_manifest(**kwargs):
        calls.append(kwargs)

        return {
            "dry_run": True,
            "vault_writes_performed": False,
            "selection_complete": True,
            "selected_entity_count": 5,
        }

    def forbidden_apply(**_kwargs):
        raise AssertionError(
            "dry-run must not call apply_projection"
        )

    monkeypatch.setattr(
        cli,
        "build_projection_manifest",
        fake_manifest,
    )

    monkeypatch.setattr(
        cli,
        "apply_projection",
        forbidden_apply,
    )

    result = runner.invoke(
        cli.app,
        [
            "dry-run",
            "--limit",
            "25",
            "--status",
            "active",
        ],
    )

    assert result.exit_code == 0

    payload = json.loads(
        result.stdout
    )

    assert payload[
        "dry_run"
    ] is True

    assert payload[
        "vault_writes_performed"
    ] is False

    assert calls == [
        {
            "limit": 25,
            "status": "active",
            "include_excerpt": False,
            "vault_root": None,
        }
    ]


def test_apply_requires_explicit_yes(
    monkeypatch,
) -> None:
    called = False

    def forbidden_apply(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError(
            "apply_projection must not run without --yes"
        )

    monkeypatch.setattr(
        cli,
        "apply_projection",
        forbidden_apply,
    )

    result = runner.invoke(
        cli.app,
        [
            "apply",
        ],
    )

    assert result.exit_code == 2
    assert called is False

    payload = json.loads(
        result.stdout
    )

    assert payload == {
        "ok": False,
        "error": "explicit_confirmation_required",
        "detail": (
            "The apply command writes the Obsidian vault. "
            "Re-run with --yes after reviewing dry-run output."
        ),
    }


def test_apply_with_yes_calls_safe_writer(
    monkeypatch,
    tmp_path,
) -> None:
    calls = []

    def fake_apply(**kwargs):
        calls.append(kwargs)

        return {
            "ok": True,
            "selected_entity_count": 5,
            "written_count": 0,
            "unchanged_count": 5,
            "vault_writes_performed": False,
        }

    monkeypatch.setattr(
        cli,
        "apply_projection",
        fake_apply,
    )

    vault = tmp_path / "vault"

    result = runner.invoke(
        cli.app,
        [
            "apply",
            "--yes",
            "--limit",
            "50",
            "--status",
            "active",
            "--include-excerpt",
            "--vault-root",
            str(vault),
            "--no-prune",
        ],
    )

    assert result.exit_code == 0

    payload = json.loads(
        result.stdout
    )

    assert payload[
        "ok"
    ] is True

    assert payload[
        "vault_writes_performed"
    ] is False

    assert len(calls) == 1

    call = calls[0]

    assert call[
        "limit"
    ] == 50

    assert call[
        "status"
    ] == "active"

    assert call[
        "include_excerpt"
    ] is True

    assert call[
        "vault_root"
    ] == vault

    assert call[
        "prune"
    ] is False
