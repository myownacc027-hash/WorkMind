from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from apps.obsidian_cli import main as cli
from packages.knowledge import (
    obsidian_apply as apply,
)


MANAGED_CURRENT = (
    "---\n"
    'workmind_projection: "wm-obsidian-v1"\n'
    "workmind_managed: true\n"
    "workmind_kind: entity\n"
    "---\n"
    "# Current\n"
).encode("utf-8")


MANAGED_CHANGED = (
    "---\n"
    'workmind_projection: "wm-obsidian-v1"\n'
    "workmind_managed: true\n"
    "workmind_kind: entity\n"
    "---\n"
    "# Changed on disk\n"
).encode("utf-8")


MANAGED_STALE = (
    "---\n"
    'workmind_projection: "wm-obsidian-v1"\n'
    "workmind_managed: true\n"
    "workmind_kind: entity\n"
    "---\n"
    "# Stale\n"
).encode("utf-8")


def _manifest(
    vault: Path,
) -> dict:
    return {
        "manifest_version": (
            "wm-obsidian-manifest-v2"
        ),
        "projection_version": (
            "wm-obsidian-v1"
        ),
        "projection_fingerprint": (
            hashlib.sha256(
                b"projection"
            ).hexdigest()
        ),
        "manifest_fingerprint": (
            hashlib.sha256(
                b"projection"
            ).hexdigest()
        ),
        "vault_root": str(
            vault
        ),
        "selected_entity_count": 1,
        "selection_complete": True,
        "navigation_entries": [],
    }


def _install(
    monkeypatch: pytest.MonkeyPatch,
    vault: Path,
    payloads: dict[str, bytes],
) -> None:
    monkeypatch.setattr(
        apply,
        "build_projection_manifest",
        lambda **_kwargs: _manifest(
            vault
        ),
    )

    monkeypatch.setattr(
        apply,
        "_render_manifest_payloads",
        lambda *_args, **_kwargs: dict(
            payloads
        ),
    )


def test_status_reports_clean_vault_without_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    target = (
        vault
        / "Entities"
        / "task"
        / "Current.md"
    )

    target.parent.mkdir(
        parents=True
    )

    target.write_bytes(
        MANAGED_CURRENT
    )

    before = target.read_bytes()
    before_mtime = (
        target.stat().st_mtime_ns
    )

    _install(
        monkeypatch,
        vault,
        {
            "Entities/task/Current.md": (
                MANAGED_CURRENT
            )
        },
    )

    result = apply.projection_status(
        vault_root=vault
    )

    assert result[
        "in_sync"
    ] is True

    assert result[
        "unchanged_count"
    ] == 1

    assert result[
        "missing_count"
    ] == 0

    assert result[
        "changed_count"
    ] == 0

    assert result[
        "stale_managed_count"
    ] == 0

    assert result[
        "conflict_count"
    ] == 0

    assert result[
        "vault_writes_performed"
    ] is False

    assert target.read_bytes() == before

    assert (
        target.stat().st_mtime_ns
        == before_mtime
    )


def test_status_reports_missing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"

    _install(
        monkeypatch,
        vault,
        {
            "Entities/task/Missing.md": (
                MANAGED_CURRENT
            )
        },
    )

    result = apply.projection_status(
        vault_root=vault
    )

    assert result[
        "in_sync"
    ] is False

    assert result[
        "missing_paths"
    ] == [
        "Entities/task/Missing.md"
    ]

    assert not vault.exists()


def test_status_reports_changed_managed_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    target = (
        vault
        / "Entities"
        / "task"
        / "Current.md"
    )

    target.parent.mkdir(
        parents=True
    )

    target.write_bytes(
        MANAGED_CHANGED
    )

    _install(
        monkeypatch,
        vault,
        {
            "Entities/task/Current.md": (
                MANAGED_CURRENT
            )
        },
    )

    result = apply.projection_status(
        vault_root=vault
    )

    assert result[
        "changed_paths"
    ] == [
        "Entities/task/Current.md"
    ]

    assert result[
        "in_sync"
    ] is False

    assert (
        target.read_bytes()
        == MANAGED_CHANGED
    )


def test_status_reports_stale_managed_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"

    current = (
        vault
        / "Entities"
        / "task"
        / "Current.md"
    )

    stale = (
        vault
        / "Entities"
        / "task"
        / "Stale.md"
    )

    current.parent.mkdir(
        parents=True
    )

    current.write_bytes(
        MANAGED_CURRENT
    )

    stale.write_bytes(
        MANAGED_STALE
    )

    _install(
        monkeypatch,
        vault,
        {
            "Entities/task/Current.md": (
                MANAGED_CURRENT
            )
        },
    )

    result = apply.projection_status(
        vault_root=vault
    )

    assert result[
        "stale_managed_paths"
    ] == [
        "Entities/task/Stale.md"
    ]

    assert result[
        "in_sync"
    ] is False

    assert stale.exists()


def test_status_reports_unmanaged_target_conflict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"

    target = (
        vault
        / "Entities"
        / "task"
        / "Current.md"
    )

    target.parent.mkdir(
        parents=True
    )

    target.write_text(
        "# User note\n",
        encoding="utf-8",
    )

    _install(
        monkeypatch,
        vault,
        {
            "Entities/task/Current.md": (
                MANAGED_CURRENT
            )
        },
    )

    result = apply.projection_status(
        vault_root=vault
    )

    assert result[
        "conflict_paths"
    ] == [
        "Entities/task/Current.md"
    ]

    assert result[
        "in_sync"
    ] is False

    assert (
        target.read_text(
            encoding="utf-8"
        )
        == "# User note\n"
    )


def test_status_never_manages_obsidian_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"

    current = (
        vault
        / "Entities"
        / "task"
        / "Current.md"
    )

    config = (
        vault
        / ".obsidian"
        / "workspace.json"
    )

    current.parent.mkdir(
        parents=True
    )

    config.parent.mkdir(
        parents=True
    )

    current.write_bytes(
        MANAGED_CURRENT
    )

    config.write_text(
        '{"user": true}\n',
        encoding="utf-8",
    )

    _install(
        monkeypatch,
        vault,
        {
            "Entities/task/Current.md": (
                MANAGED_CURRENT
            )
        },
    )

    result = apply.projection_status(
        vault_root=vault
    )

    assert result[
        "in_sync"
    ] is True

    assert (
        config.read_text(
            encoding="utf-8"
        )
        == '{"user": true}\n'
    )


def test_cli_status_is_read_only_and_needs_no_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()

    monkeypatch.setattr(
        cli,
        "projection_status",
        lambda **_kwargs: {
            "in_sync": True,
            "missing_count": 0,
            "changed_count": 0,
            "stale_managed_count": 0,
            "conflict_count": 0,
            "vault_writes_performed": False,
        },
    )

    result = runner.invoke(
        cli.app,
        [
            "status",
        ],
    )

    assert result.exit_code == 0

    assert '"in_sync": true' in (
        result.stdout.lower()
    )

    assert (
        '"vault_writes_performed": false'
        in result.stdout.lower()
    )


def test_cli_status_help_has_no_mutating_options() -> None:
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        [
            "status",
            "--help",
        ],
    )

    assert result.exit_code == 0

    for option in (
        "--yes",
        "--prune",
        "--no-prune",
        "--limit",
        "--status",
    ):
        assert option not in result.stdout


def test_cli_status_check_sync_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()

    monkeypatch.setattr(
        cli,
        "projection_status",
        lambda **_kwargs: {
            "in_sync": True,
            "missing_count": 0,
            "changed_count": 0,
            "stale_managed_count": 0,
            "conflict_count": 0,
            "vault_writes_performed": False,
        },
    )

    result = runner.invoke(
        cli.app,
        [
            "status",
            "--check",
        ],
    )

    assert result.exit_code == 0

    assert (
        '"in_sync": true'
        in result.stdout.lower()
    )


def test_cli_status_check_drift_exits_one_after_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()

    monkeypatch.setattr(
        cli,
        "projection_status",
        lambda **_kwargs: {
            "in_sync": False,
            "missing_count": 1,
            "changed_count": 0,
            "stale_managed_count": 0,
            "conflict_count": 0,
            "missing_paths": [
                "Entities/task/Missing.md"
            ],
            "vault_writes_performed": False,
        },
    )

    result = runner.invoke(
        cli.app,
        [
            "status",
            "--check",
        ],
    )

    assert result.exit_code == 1

    assert (
        '"in_sync": false'
        in result.stdout.lower()
    )

    assert (
        "Entities/task/Missing.md"
        in result.stdout
    )


def test_cli_plain_status_drift_remains_informational(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()

    monkeypatch.setattr(
        cli,
        "projection_status",
        lambda **_kwargs: {
            "in_sync": False,
            "missing_count": 1,
            "changed_count": 0,
            "stale_managed_count": 0,
            "conflict_count": 0,
            "vault_writes_performed": False,
        },
    )

    result = runner.invoke(
        cli.app,
        [
            "status",
        ],
    )

    assert result.exit_code == 0

    assert (
        '"in_sync": false'
        in result.stdout.lower()
    )


def test_cli_status_help_exposes_check_option() -> None:
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        [
            "status",
            "--help",
        ],
    )

    assert result.exit_code == 0

    assert (
        "--check"
        in result.stdout
    )
