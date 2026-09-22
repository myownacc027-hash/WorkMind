from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

import packages.knowledge.obsidian_apply as apply
from packages.knowledge.obsidian_export import (
    ObsidianExportError,
)
from packages.knowledge.obsidian_projection import (
    entity_relative_path,
    render_entity_markdown,
)


ENTITY_A = str(
    uuid.UUID(
        "11111111-1111-4111-8111-111111111111"
    )
)

ENTITY_B = str(
    uuid.UUID(
        "22222222-2222-4222-8222-222222222222"
    )
)

CLAIM_ID = str(
    uuid.UUID(
        "33333333-3333-4333-8333-333333333333"
    )
)


def _raw(
    *,
    entity_id: str = ENTITY_A,
    name: str = "Test System Alpha",
) -> dict:
    claim = {
        "claim_id": CLAIM_ID,
        "predicate": "status",
        "object_text": "active",
        "claim_text": (
            f"{name} status is active."
        ),
        "status": "active",
        "observed_at": (
            "2026-09-22T09:00:00+00:00"
        ),
        "evidence": [],
    }

    return {
        "entity_id": entity_id,
        "entity_ref": (
            f"workmind://entity/{entity_id}"
        ),
        "entity_type": "system",
        "canonical_name": name,
        "external_id": None,
        "status": "active",
        "description": "Synthetic test entity.",
        "attributes": {},
        "created_at": (
            "2026-09-22T08:00:00+00:00"
        ),
        "updated_at": (
            "2026-09-22T09:00:00+00:00"
        ),
        "identity_evidence": [],
        "claims": [
            claim,
        ],
        "outgoing_relations": [],
        "incoming_relations": [],
        "events": [],
        "dated_claims": [
            {
                "timeline_at": (
                    "2026-09-22T09:00:00+00:00"
                ),
                "time_source": "observed_at",
                "time_semantics": (
                    "Observation time, not event time."
                ),
                "claim": dict(claim),
            }
        ],
    }


def _counts() -> dict[str, int]:
    return {
        "ordinary_claim_count": 1,
        "outgoing_relation_count": 0,
        "incoming_relation_count": 0,
        "event_count": 0,
        "dated_claim_count": 1,
    }


def _manifest(
    raw: dict,
    vault_root: Path,
    *,
    total_count: int = 1,
) -> dict:
    snapshot = apply.normalize_service_snapshot(
        raw
    )

    relative_path = entity_relative_path(
        snapshot
    )

    markdown = render_entity_markdown(
        snapshot
    )

    payload = markdown.encode(
        "utf-8"
    )

    selected_count = 1

    return {
        "manifest_version": (
            "wm-obsidian-manifest-v1"
        ),
        "projection_version": (
            "wm-obsidian-v1"
        ),
        "source_of_truth": "postgresql",
        "dry_run": True,
        "vault_writes_performed": False,
        "include_excerpt": False,
        "status_filter": None,
        "vault_root": str(vault_root),
        "total_entity_count": total_count,
        "selected_entity_count": (
            selected_count
        ),
        "selection_complete": (
            selected_count == total_count
        ),
        "total_content_bytes": len(
            payload
        ),
        "manifest_fingerprint": (
            hashlib.sha256(
                (
                    relative_path.as_posix()
                    + " "
                    + hashlib.sha256(
                        payload
                    ).hexdigest()
                ).encode("utf-8")
            ).hexdigest()
        ),
        "entries": [
            {
                "entity_id": (
                    raw["entity_id"]
                ),
                "entity_type": (
                    raw["entity_type"]
                ),
                "canonical_name": (
                    raw["canonical_name"]
                ),
                "status": raw["status"],
                "relative_path": (
                    relative_path.as_posix()
                ),
                "target_path": str(
                    vault_root.joinpath(
                        *relative_path.parts
                    )
                ),
                "content_sha256": (
                    hashlib.sha256(
                        payload
                    ).hexdigest()
                ),
                "content_chars": len(
                    markdown
                ),
                "content_bytes": len(
                    payload
                ),
                "counts": _counts(),
            }
        ],
    }


def _install(
    monkeypatch: pytest.MonkeyPatch,
    raw: dict,
    vault: Path,
    *,
    total_count: int = 1,
) -> None:
    monkeypatch.setattr(
        apply,
        "build_projection_manifest",
        lambda **_kwargs: _manifest(
            raw,
            vault,
            total_count=total_count,
        ),
    )

    monkeypatch.setattr(
        apply,
        "_load_entity_snapshot",
        lambda *_args, **_kwargs: raw,
    )

    monkeypatch.setattr(
        apply,
        "_projection_counts",
        lambda *_args, **_kwargs: _counts(),
    )


def _target(
    vault: Path,
    entity_id: str = ENTITY_A,
) -> Path:
    return (
        vault
        / "Entities"
        / "system"
        / f"{entity_id}.md"
    )


def test_managed_marker_requires_yaml_frontmatter() -> None:
    assert apply._managed_frontmatter(
        (
            "---\n"
            'workmind_projection: "wm-obsidian-v1"\n'
            "workmind_managed: true\n"
            "---\n"
            "# Generated\n"
        )
    )

    assert not apply._managed_frontmatter(
        (
            "# User note\n"
            "workmind_managed: true\n"
        )
    )


def test_apply_creates_managed_note(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw = _raw()
    vault = tmp_path / "vault"

    _install(
        monkeypatch,
        raw,
        vault,
    )

    result = apply.apply_projection(
        vault_root=vault,
    )

    target = _target(vault)

    assert target.exists()

    text = target.read_text(
        encoding="utf-8"
    )

    assert (
        "workmind_managed: true"
        in text
    )

    assert (
        "# Test System Alpha"
        in text
    )

    assert result[
        "written_count"
    ] == 1

    assert result[
        "unchanged_count"
    ] == 0

    assert result[
        "pruned_count"
    ] == 0

    leftovers = list(
        tmp_path.glob(
            ".vault.workmind-stage-*"
        )
    )

    assert leftovers == []


def test_unmanaged_target_blocks_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw = _raw()
    vault = tmp_path / "vault"

    _install(
        monkeypatch,
        raw,
        vault,
    )

    target = _target(vault)

    target.parent.mkdir(
        parents=True
    )

    target.write_text(
        "# My user note\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ObsidianExportError,
        match=(
            "Refusing to overwrite "
            "unmanaged Markdown file"
        ),
    ):
        apply.apply_projection(
            vault_root=vault,
        )

    assert (
        target.read_text(
            encoding="utf-8"
        )
        == "# My user note\n"
    )


def test_managed_target_can_be_updated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw = _raw()
    vault = tmp_path / "vault"

    _install(
        monkeypatch,
        raw,
        vault,
    )

    first = apply.apply_projection(
        vault_root=vault,
    )

    assert first[
        "written_count"
    ] == 1

    raw["canonical_name"] = (
        "Test System Alpha Renamed"
    )

    raw["claims"][0][
        "claim_text"
    ] = (
        "Test System Alpha Renamed "
        "status is active."
    )

    raw["dated_claims"][0][
        "claim"
    ] = dict(
        raw["claims"][0]
    )

    second = apply.apply_projection(
        vault_root=vault,
    )

    assert second[
        "written_count"
    ] == 1

    text = _target(
        vault
    ).read_text(
        encoding="utf-8"
    )

    assert (
        "# Test System Alpha Renamed"
        in text
    )


def test_prune_deletes_only_stale_managed_notes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw = _raw()
    vault = tmp_path / "vault"

    _install(
        monkeypatch,
        raw,
        vault,
    )

    apply.apply_projection(
        vault_root=vault,
    )

    stale = (
        vault
        / "Entities"
        / "task"
        / f"{ENTITY_B}.md"
    )

    stale.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    stale.write_text(
        (
            "---\n"
            'workmind_projection: "wm-obsidian-v1"\n'
            "workmind_managed: true\n"
            "---\n"
            "# Stale generated note\n"
        ),
        encoding="utf-8",
    )

    user_note = (
        vault
        / "Entities"
        / "task"
        / "my-user-note.md"
    )

    user_note.write_text(
        "# Keep me\n",
        encoding="utf-8",
    )

    result = apply.apply_projection(
        vault_root=vault,
        prune=True,
    )

    assert not stale.exists()
    assert user_note.exists()

    assert result[
        "pruned_count"
    ] == 1

    assert result[
        "preserved_unmanaged_count"
    ] == 1


def test_incomplete_manifest_blocks_all_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw = _raw()
    vault = tmp_path / "vault"

    _install(
        monkeypatch,
        raw,
        vault,
        total_count=2,
    )

    with pytest.raises(
        ObsidianExportError,
        match=(
            "Refusing to apply "
            "incomplete projection"
        ),
    ):
        apply.apply_projection(
            vault_root=vault,
        )

    assert not vault.exists()


def test_hash_drift_blocks_before_vault_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = _raw(
        name="Version One"
    )

    second = _raw(
        name="Version Two"
    )

    vault = tmp_path / "vault"

    monkeypatch.setattr(
        apply,
        "build_projection_manifest",
        lambda **_kwargs: _manifest(
            first,
            vault,
        ),
    )

    monkeypatch.setattr(
        apply,
        "_load_entity_snapshot",
        lambda *_args, **_kwargs: second,
    )

    monkeypatch.setattr(
        apply,
        "_projection_counts",
        lambda *_args, **_kwargs: _counts(),
    )

    with pytest.raises(
        ObsidianExportError,
        match=(
            "changed between manifest and apply"
        ),
    ):
        apply.apply_projection(
            vault_root=vault,
        )

    assert not vault.exists()


def test_unchanged_projection_is_not_rewritten(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw = _raw()
    vault = tmp_path / "vault"

    _install(
        monkeypatch,
        raw,
        vault,
    )

    first = apply.apply_projection(
        vault_root=vault,
    )

    second = apply.apply_projection(
        vault_root=vault,
    )

    assert first[
        "written_count"
    ] == 1

    assert second[
        "written_count"
    ] == 0

    assert second[
        "unchanged_count"
    ] == 1

    assert second[
        "vault_writes_performed"
    ] is False