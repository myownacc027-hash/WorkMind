from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from apps.obsidian_cli import main as cli
from packages.knowledge import obsidian_apply as apply
from packages.knowledge import obsidian_export as export
from packages.knowledge.obsidian_export import (
    ObsidianExportError,
)


def _rows(count: int) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=uuid.UUID(
                int=index + 1
            ),
            entity_type="task",
            canonical_name=(
                f"Entity {index:04d}"
            ),
            status="active",
        )
        for index in range(count)
    ]


class _FakeSession:
    def __init__(
        self,
        rows: list[SimpleNamespace],
    ) -> None:
        self.rows = rows
        self.last_statement = None

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        tb,
    ) -> bool:
        return False

    def scalars(
        self,
        statement,
    ):
        self.last_statement = statement

        limit_clause = getattr(
            statement,
            "_limit_clause",
            None,
        )

        if limit_clause is None:
            return list(
                self.rows
            )

        value = int(
            limit_clause.value
        )

        return list(
            self.rows[:value]
        )


def _install_fake_entity_session(
    monkeypatch: pytest.MonkeyPatch,
    count: int,
) -> _FakeSession:
    rows = _rows(
        count
    )

    session = _FakeSession(
        rows
    )

    monkeypatch.setattr(
        export.service,
        "SessionLocal",
        lambda: session,
    )

    monkeypatch.setattr(
        export,
        "_scalar_count",
        lambda *_args, **_kwargs: len(
            rows
        ),
    )

    return session


def test_preview_selection_remains_capped_at_1000(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = (
        _install_fake_entity_session(
            monkeypatch,
            1001,
        )
    )

    result = export._list_entities(
        limit=5000,
    )

    assert result[
        "total_count"
    ] == 1001

    assert result[
        "selected_count"
    ] == 1000

    assert len(
        result["entities"]
    ) == 1000

    assert (
        getattr(
            session.last_statement,
            "_limit_clause",
            None,
        )
        is not None
    )


def test_full_selection_is_not_capped_at_1000(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = (
        _install_fake_entity_session(
            monkeypatch,
            1001,
        )
    )

    result = export._list_entities(
        limit=None,
    )

    assert result[
        "total_count"
    ] == 1001

    assert result[
        "selected_count"
    ] == 1001

    assert len(
        result["entities"]
    ) == 1001

    assert (
        getattr(
            session.last_statement,
            "_limit_clause",
            None,
        )
        is None
    )


def test_full_manifest_marks_1001_entities_complete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    summaries = [
        {
            "entity_id": str(
                uuid.UUID(
                    int=index + 1
                )
            ),
            "entity_type": "task",
            "canonical_name": (
                f"Entity {index:04d}"
            ),
            "status": "active",
        }
        for index in range(1001)
    ]

    by_id = {
        item["entity_id"]: item
        for item in summaries
    }

    monkeypatch.setattr(
        export,
        "_list_entities",
        lambda **_kwargs: {
            "total_count": 1001,
            "selected_count": 1001,
            "entities": summaries,
        },
    )

    monkeypatch.setattr(
        export,
        "_load_entity_snapshot",
        lambda entity_id, **_kwargs: {
            "entity": dict(
                by_id[entity_id]
            ),
            "claims": [],
            "outgoing_relations": [],
            "incoming_relations": [],
            "events": [],
            "dated_claims": [],
        },
    )

    monkeypatch.setattr(
        export,
        "_projection_counts",
        lambda _entity_id: {
            "ordinary_claim_count": 0,
            "outgoing_relation_count": 0,
            "incoming_relation_count": 0,
            "event_count": 0,
            "dated_claim_count": 0,
        },
    )

    monkeypatch.setattr(
        export,
        "_assert_complete_service_snapshot",
        lambda *_args, **_kwargs: None,
    )

    monkeypatch.setattr(
        export,
        "normalize_service_snapshot",
        lambda raw: raw,
    )

    monkeypatch.setattr(
        export,
        "render_entity_markdown",
        lambda snapshot: (
            "# "
            + snapshot["entity"][
                "canonical_name"
            ]
            + "\n"
        ),
    )

    monkeypatch.setattr(
        export,
        "render_navigation_pages",
        lambda entries: {
            "Home.md": (
                "# Home\n\n"
                + str(
                    len(entries)
                )
                + "\n"
            )
        },
    )

    manifest = (
        export.build_projection_manifest(
            limit=None,
            vault_root=tmp_path,
        )
    )

    assert manifest[
        "total_entity_count"
    ] == 1001

    assert manifest[
        "selected_entity_count"
    ] == 1001

    assert manifest[
        "selection_complete"
    ] is True

    assert len(
        manifest["entries"]
    ) == 1001


def test_limited_writable_projection_is_rejected_before_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_manifest(
        **_kwargs,
    ):
        raise AssertionError(
            "Manifest must not be built."
        )

    monkeypatch.setattr(
        apply,
        "build_projection_manifest",
        unexpected_manifest,
    )

    with pytest.raises(
        ObsidianExportError,
        match="limited projection",
    ):
        apply.apply_projection(
            limit=100,
        )


def test_default_apply_requests_unbounded_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_manifest(
        **kwargs,
    ):
        captured.update(
            kwargs
        )

        return {
            "selection_complete": False,
        }

    monkeypatch.setattr(
        apply,
        "build_projection_manifest",
        fake_manifest,
    )

    with pytest.raises(
        ObsidianExportError,
        match="incomplete projection",
    ):
        apply.apply_projection()

    assert captured[
        "limit"
    ] is None

    assert captured[
        "status"
    ] is None


def test_filtered_apply_still_fails_before_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_manifest(
        **_kwargs,
    ):
        raise AssertionError(
            "Manifest must not be built."
        )

    monkeypatch.setattr(
        apply,
        "build_projection_manifest",
        unexpected_manifest,
    )

    with pytest.raises(
        ObsidianExportError,
        match="filtered projection",
    ):
        apply.apply_projection(
            status="active",
        )


def test_cli_dry_run_exposes_limit_but_apply_hides_it() -> None:
    runner = CliRunner()

    dry_help = runner.invoke(
        cli.app,
        [
            "dry-run",
            "--help",
        ],
    )

    assert (
        dry_help.exit_code
        == 0
    )

    assert (
        "--limit"
        in dry_help.stdout
    )

    apply_help = runner.invoke(
        cli.app,
        [
            "apply",
            "--help",
        ],
    )

    assert (
        apply_help.exit_code
        == 0
    )

    assert (
        "--limit"
        not in apply_help.stdout
    )


def test_cli_legacy_apply_limit_is_rejected_without_db_access() -> None:
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        [
            "apply",
            "--yes",
            "--limit",
            "1",
        ],
    )

    assert (
        result.exit_code
        != 0
    )

    assert (
        "limited projection"
        in result.stdout
    )
