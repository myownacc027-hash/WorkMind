from __future__ import annotations

import uuid

import pytest

import packages.knowledge.obsidian_export as export
from packages.knowledge.obsidian_export import (
    ObsidianExportError,
    _assert_complete_service_snapshot,
    build_projection_manifest,
    normalize_service_snapshot,
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
    entity_id: str = ENTITY_A,
    name: str = "Test System Alpha",
) -> dict:
    claim = {
        "claim_id": CLAIM_ID,
        "predicate": "status",
        "object_text": "active",
        "claim_text": (
            "Test System Alpha status is active."
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


def _counts(
    *,
    claims: int = 1,
    outgoing: int = 0,
    incoming: int = 0,
    events: int = 0,
    dated: int = 1,
) -> dict[str, int]:
    return {
        "ordinary_claim_count": claims,
        "outgoing_relation_count": outgoing,
        "incoming_relation_count": incoming,
        "event_count": events,
        "dated_claim_count": dated,
    }


def _install_single_entity(
    monkeypatch: pytest.MonkeyPatch,
    raw: dict,
    *,
    total_count: int = 1,
) -> None:
    monkeypatch.setattr(
        export,
        "_list_entities",
        lambda **_kwargs: {
            "total_count": total_count,
            "selected_count": 1,
            "entities": [
                {
                    "entity_id": raw["entity_id"],
                    "entity_type": raw["entity_type"],
                    "canonical_name": (
                        raw["canonical_name"]
                    ),
                    "status": raw["status"],
                }
            ],
        },
    )

    monkeypatch.setattr(
        export,
        "_load_entity_snapshot",
        lambda *_args, **_kwargs: raw,
    )

    monkeypatch.setattr(
        export,
        "_projection_counts",
        lambda *_args, **_kwargs: _counts(),
    )


def test_normalize_flattens_dated_claim_envelope() -> None:
    snapshot = normalize_service_snapshot(
        _raw()
    )

    assert (
        snapshot["entity"]["entity_id"]
        == ENTITY_A
    )

    assert len(
        snapshot["dated_claims"]
    ) == 1

    dated = snapshot[
        "dated_claims"
    ][0]

    assert dated["claim_id"] == CLAIM_ID
    assert dated["predicate"] == "status"
    assert (
        dated["timeline_at"]
        == "2026-09-22T09:00:00+00:00"
    )
    assert (
        dated["time_source"]
        == "observed_at"
    )


def test_complete_snapshot_accepts_exact_counts() -> None:
    _assert_complete_service_snapshot(
        _raw(),
        _counts(),
    )


def test_complete_snapshot_rejects_truncated_claims() -> None:
    with pytest.raises(
        ObsidianExportError,
        match="claims: service=1, database=2",
    ):
        _assert_complete_service_snapshot(
            _raw(),
            _counts(
                claims=2,
            ),
        )


def test_complete_snapshot_rejects_truncated_relations() -> None:
    with pytest.raises(
        ObsidianExportError,
        match=(
            "outgoing_relations: "
            "service=0, database=1"
        ),
    ):
        _assert_complete_service_snapshot(
            _raw(),
            _counts(
                outgoing=1,
            ),
        )


def test_manifest_is_deterministic_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    raw = _raw()

    _install_single_entity(
        monkeypatch,
        raw,
    )

    vault = (
        tmp_path
        / "vault-that-must-not-exist"
    )

    first = build_projection_manifest(
        limit=5,
        vault_root=vault,
    )

    second = build_projection_manifest(
        limit=5,
        vault_root=vault,
    )

    assert first == second
    assert first["dry_run"] is True
    assert (
        first["vault_writes_performed"]
        is False
    )
    assert first["selection_complete"] is True
    assert first["selected_entity_count"] == 1
    assert not vault.exists()

    entry = first["entries"][0]

    assert (
        entry["relative_path"]
        == (
            "Entities/system/"
            "Test System Alpha.md"
        )
    )

    assert len(
        entry["content_sha256"]
    ) == 64

    assert "content" not in entry
    assert entry["content_bytes"] > 0


def test_manifest_reports_bounded_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    raw = _raw()

    _install_single_entity(
        monkeypatch,
        raw,
        total_count=7,
    )

    result = build_projection_manifest(
        limit=1,
        vault_root=tmp_path / "vault",
    )

    assert result[
        "total_entity_count"
    ] == 7

    assert result[
        "selected_entity_count"
    ] == 1

    assert (
        result["selection_complete"]
        is False
    )


def test_manifest_entries_are_sorted_by_stable_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    raw_a = _raw(
        ENTITY_A,
        "Alpha",
    )

    raw_b = _raw(
        ENTITY_B,
        "Beta",
    )

    monkeypatch.setattr(
        export,
        "_list_entities",
        lambda **_kwargs: {
            "total_count": 2,
            "selected_count": 2,
            "entities": [
                {
                    "entity_id": ENTITY_B,
                    "entity_type": "system",
                    "canonical_name": "Beta",
                    "status": "active",
                },
                {
                    "entity_id": ENTITY_A,
                    "entity_type": "system",
                    "canonical_name": "Alpha",
                    "status": "active",
                },
            ],
        },
    )

    rows = {
        ENTITY_A: raw_a,
        ENTITY_B: raw_b,
    }

    monkeypatch.setattr(
        export,
        "_load_entity_snapshot",
        lambda entity_id, **_kwargs: (
            rows[entity_id]
        ),
    )

    monkeypatch.setattr(
        export,
        "_projection_counts",
        lambda *_args, **_kwargs: _counts(),
    )

    result = build_projection_manifest(
        limit=2,
        vault_root=tmp_path / "vault",
    )

    paths = [
        item["relative_path"]
        for item in result["entries"]
    ]

    assert paths == sorted(paths)


def test_manifest_includes_navigation_plan_without_writes(
    monkeypatch,
    tmp_path,
) -> None:
    task_id = (
        "11111111-1111-1111-1111-111111111111"
    )

    incident_id = (
        "22222222-2222-2222-2222-222222222222"
    )

    selection = {
        "total_count": 2,
        "selected_count": 2,
        "entities": [
            {
                "entity_id": task_id,
                "entity_type": "task",
                "canonical_name": "Task A",
                "status": "active",
            },
            {
                "entity_id": incident_id,
                "entity_type": "incident",
                "canonical_name": "Incident A",
                "status": "active",
            },
        ],
    }

    snapshots = {
        task_id: {
            "entity": {
                "entity_id": task_id,
                "entity_type": "task",
                "canonical_name": "Task A",
                "status": "active",
            },
            "claims": [],
            "outgoing_relations": [],
            "incoming_relations": [],
            "events": [],
            "dated_claims": [],
        },
        incident_id: {
            "entity": {
                "entity_id": incident_id,
                "entity_type": "incident",
                "canonical_name": "Incident A",
                "status": "active",
            },
            "claims": [],
            "outgoing_relations": [],
            "incoming_relations": [],
            "events": [],
            "dated_claims": [],
        },
    }

    zero_counts = {
        "ordinary_claim_count": 0,
        "outgoing_relation_count": 0,
        "incoming_relation_count": 0,
        "event_count": 0,
        "dated_claim_count": 0,
    }

    monkeypatch.setattr(
        export,
        "_list_entities",
        lambda **_kwargs: selection,
    )

    monkeypatch.setattr(
        export,
        "_load_entity_snapshot",
        lambda entity_id, **_kwargs: snapshots[
            entity_id
        ],
    )

    monkeypatch.setattr(
        export,
        "_projection_counts",
        lambda _entity_id: dict(
            zero_counts
        ),
    )

    monkeypatch.setattr(
        export,
        "normalize_service_snapshot",
        lambda raw: raw,
    )

    first = export.build_projection_manifest(
        limit=100,
        vault_root=tmp_path,
    )

    second = export.build_projection_manifest(
        limit=100,
        vault_root=tmp_path,
    )

    assert first == second

    assert first[
        "selected_entity_count"
    ] == 2

    assert first[
        "selection_complete"
    ] is True

    assert first[
        "navigation_entry_count"
    ] == 4

    assert first[
        "projected_file_count"
    ] == 6

    navigation = first[
        "navigation_entries"
    ]

    assert [
        item["relative_path"]
        for item in navigation
    ] == [
        "Home.md",
        "Index/Incidents.md",
        "Index/Organizations.md",
        "Index/Tasks.md",
    ]

    assert [
        item["navigation_role"]
        for item in navigation
    ] == [
        "home",
        "index",
        "index",
        "index",
    ]

    assert all(
        len(
            item["content_sha256"]
        )
        == 64
        for item in navigation
    )

    assert first[
        "navigation_content_bytes"
    ] == sum(
        item["content_bytes"]
        for item in navigation
    )

    assert first[
        "projected_content_bytes"
    ] == (
        first["total_content_bytes"]
        + first["navigation_content_bytes"]
    )

    assert len(
        first[
            "navigation_fingerprint"
        ]
    ) == 64

    assert first[
        "vault_writes_performed"
    ] is False

    assert list(
        tmp_path.iterdir()
    ) == []


def test_manifest_rejects_human_path_collision(
    monkeypatch,
    tmp_path,
) -> None:
    first_id = (
        "11111111-1111-4111-8111-111111111111"
    )

    second_id = (
        "22222222-2222-4222-8222-222222222222"
    )

    selection = {
        "total_count": 2,
        "selected_count": 2,
        "entities": [
            {
                "entity_id": first_id,
                "entity_type": "task",
                "canonical_name": "Plan: A",
                "status": "active",
            },
            {
                "entity_id": second_id,
                "entity_type": "task",
                "canonical_name": "Plan/A",
                "status": "active",
            },
        ],
    }

    snapshots = {
        first_id: {
            "entity": {
                "entity_id": first_id,
                "entity_type": "task",
                "canonical_name": "Plan: A",
                "status": "active",
            },
            "claims": [],
            "outgoing_relations": [],
            "incoming_relations": [],
            "events": [],
            "dated_claims": [],
        },
        second_id: {
            "entity": {
                "entity_id": second_id,
                "entity_type": "task",
                "canonical_name": "Plan/A",
                "status": "active",
            },
            "claims": [],
            "outgoing_relations": [],
            "incoming_relations": [],
            "events": [],
            "dated_claims": [],
        },
    }

    zero_counts = {
        "ordinary_claim_count": 0,
        "outgoing_relation_count": 0,
        "incoming_relation_count": 0,
        "event_count": 0,
        "dated_claim_count": 0,
    }

    monkeypatch.setattr(
        export,
        "_list_entities",
        lambda **_kwargs: selection,
    )

    monkeypatch.setattr(
        export,
        "_load_entity_snapshot",
        lambda entity_id, **_kwargs: (
            snapshots[entity_id]
        ),
    )

    monkeypatch.setattr(
        export,
        "_projection_counts",
        lambda _entity_id: dict(
            zero_counts
        ),
    )

    monkeypatch.setattr(
        export,
        "normalize_service_snapshot",
        lambda raw: raw,
    )

    with pytest.raises(
        ObsidianExportError,
        match="Projection path collision",
    ):
        export.build_projection_manifest(
            limit=100,
            vault_root=tmp_path,
        )

    assert list(
        tmp_path.iterdir()
    ) == []
