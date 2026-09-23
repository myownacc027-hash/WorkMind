from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from packages.knowledge.obsidian_projection import (
    ObsidianProjectionError,
    PROJECTION_VERSION,
    entity_relative_path,
    render_entity_markdown,
)


ENTITY_ID = uuid.UUID(
    "11111111-1111-4111-8111-111111111111"
)

OLD_CLAIM_ID = uuid.UUID(
    "22222222-2222-4222-8222-222222222222"
)

NEW_CLAIM_ID = uuid.UUID(
    "33333333-3333-4333-8333-333333333333"
)

RELATION_ID = uuid.UUID(
    "44444444-4444-4444-8444-444444444444"
)

EVENT_ID = uuid.UUID(
    "55555555-5555-4555-8555-555555555555"
)

CHUNK_V1 = uuid.UUID(
    "66666666-6666-4666-8666-666666666666"
)

CHUNK_V2 = uuid.UUID(
    "77777777-7777-4777-8777-777777777777"
)


def _snapshot() -> dict:
    return {
        "entity": {
            "entity_id": str(ENTITY_ID),
            "entity_type": "system",
            "canonical_name": "Test System Alpha",
            "normalized_key": "test system alpha",
            "external_id": "SYS-ALPHA",
            "status": "active",
            "description": (
                "Synthetic entity used for projection tests."
            ),
        },
        "claims": [
            {
                "claim_id": str(NEW_CLAIM_ID),
                "predicate": "status",
                "object_text": "disabled",
                "claim_text": (
                    "Test System Alpha status is disabled."
                ),
                "status": "active",
                "observed_at": (
                    "2026-09-22T10:00:00+00:00"
                ),
                "supersedes_claim_id": str(
                    OLD_CLAIM_ID
                ),
                "evidence": [
                    {
                        "source_ref": (
                            "workmind://document/"
                            "bbbbbbbb-bbbb-4bbb-8bbb-"
                            "bbbbbbbbbbbb"
                        ),
                        "chunk_id": str(CHUNK_V2),
                        "evidence_role": (
                            "direct_statement"
                        ),
                        "excerpt": (
                            "Test System Alpha status "
                            "is disabled."
                        ),
                    }
                ],
            },
            {
                "claim_id": str(OLD_CLAIM_ID),
                "predicate": "status",
                "object_text": "active",
                "claim_text": (
                    "Test System Alpha status is active."
                ),
                "status": "superseded",
                "observed_at": (
                    "2026-09-22T09:00:00+00:00"
                ),
                "valid_to": (
                    "2026-09-22T10:00:00+00:00"
                ),
                "superseded_by_claim_id": str(
                    NEW_CLAIM_ID
                ),
                "evidence": [
                    {
                        "source_ref": (
                            "workmind://document/"
                            "aaaaaaaa-aaaa-4aaa-8aaa-"
                            "aaaaaaaaaaaa"
                        ),
                        "chunk_id": str(CHUNK_V1),
                        "evidence_role": (
                            "direct_statement"
                        ),
                        "excerpt": (
                            "Test System Alpha status "
                            "is active."
                        ),
                    }
                ],
            },
        ],
        "outgoing_relations": [
            {
                "relation_id": str(RELATION_ID),
                "relation_type": "depends_on",
                "to_entity_name": "Test Dependency",
                "to_entity_ref": (
                    "workmind://entity/"
                    "88888888-8888-4888-8888-"
                    "888888888888"
                ),
                "status": "active",
            }
        ],
        "incoming_relations": [],
        "events": [
            {
                "event_id": str(EVENT_ID),
                "event_type": "status_change",
                "title": "Status changed to disabled",
                "occurred_at": (
                    "2026-09-22T10:00:00+00:00"
                ),
                "status": "confirmed",
            }
        ],
        "dated_claims": [
            {
                "claim_id": str(NEW_CLAIM_ID),
                "predicate": "status",
                "object_text": "disabled",
                "status": "active",
                "observed_at": (
                    "2026-09-22T10:00:00+00:00"
                ),
            }
        ],
        "identity_evidence": [
            {
                "source_ref": (
                    "workmind://document/"
                    "bbbbbbbb-bbbb-4bbb-8bbb-"
                    "bbbbbbbbbbbb"
                ),
                "chunk_id": str(CHUNK_V2),
                "evidence_role": "identity",
                "excerpt": "Test System Alpha",
            }
        ],
    }


def test_projection_path_tracks_canonical_name() -> None:
    first = _snapshot()
    renamed = _snapshot()

    renamed["entity"]["canonical_name"] = (
        "Test System Alpha Renamed"
    )

    expected = (
        Path("Entities")
        / "system"
        / f"{ENTITY_ID}.md"
    )

    first_path = entity_relative_path(first)
    renamed_path = entity_relative_path(renamed)

    assert first_path != renamed_path
    assert first_path.parent == renamed_path.parent
    assert first_path.suffix == ".md"
    assert renamed_path.suffix == ".md"


def test_projection_preserves_history_and_provenance() -> None:
    markdown = render_entity_markdown(
        _snapshot()
    )

    assert (
        f'workmind_projection: "{PROJECTION_VERSION}"'
        in markdown
    )

    assert (
        f'workmind_entity_id: "{ENTITY_ID}"'
        in markdown
    )

    assert "# Test System Alpha" in markdown
    assert "## Active claims" in markdown
    assert "## Historical claims" in markdown

    assert "**status**: disabled _[active]_" in markdown
    assert (
        "**status**: active _[superseded]_"
        in markdown
    )

    assert (
        f"Supersedes: `{OLD_CLAIM_ID}`"
        in markdown
    )

    assert (
        f"Superseded by: `{NEW_CLAIM_ID}`"
        in markdown
    )

    assert str(CHUNK_V1) in markdown
    assert str(CHUNK_V2) in markdown

    assert (
        "workmind://document/"
        "aaaaaaaa-aaaa-4aaa-8aaa-"
        "aaaaaaaaaaaa"
        in markdown
    )

    assert "**depends_on** → Test Dependency" in markdown
    assert (
        "**status_change**: Status changed to disabled"
        in markdown
    )

    assert "## Identity evidence" in markdown
    assert markdown.endswith("\n")


def test_projection_is_deterministic_for_input_order() -> None:
    first = _snapshot()
    second = _snapshot()

    second["claims"] = list(
        reversed(
            second["claims"]
        )
    )

    assert (
        render_entity_markdown(first)
        == render_entity_markdown(second)
    )


def test_projection_contains_no_generation_timestamp() -> None:
    markdown = render_entity_markdown(
        _snapshot()
    )

    assert "generated_at:" not in markdown
    assert "exported_at:" not in markdown


def test_invalid_entity_id_is_rejected() -> None:
    snapshot = _snapshot()

    snapshot["entity"]["entity_id"] = (
        "not-a-uuid"
    )

    with pytest.raises(
        ObsidianProjectionError,
        match="Invalid entity_id",
    ):
        entity_relative_path(snapshot)


def test_missing_entity_is_rejected() -> None:
    with pytest.raises(
        ObsidianProjectionError,
        match="does not contain an entity",
    ):
        render_entity_markdown(
            {
                "claims": [],
            }
        )


def test_relations_render_as_stable_obsidian_wikilinks() -> None:
    from packages.knowledge.obsidian_projection import (
        render_entity_markdown,
    )

    source_id = (
        "11111111-1111-4111-8111-111111111111"
    )
    incident_id = (
        "22222222-2222-4222-8222-222222222222"
    )
    organization_id = (
        "33333333-3333-4333-8333-333333333333"
    )

    snapshot = {
        "entity": {
            "entity_id": source_id,
            "entity_type": "task",
            "canonical_name": "Task Alpha",
            "status": "active",
        },
        "claims": [],
        "outgoing_relations": [
            {
                "relation_id": (
                    "44444444-4444-4444-8444-444444444444"
                ),
                "relation_type": "has_incident",
                "status": "active",
                "to_entity": {
                    "entity_id": incident_id,
                    "entity_type": "incident",
                    "canonical_name": "Incident Alpha",
                    "status": "active",
                },
                "to_entity_id": incident_id,
                "to_entity_name": "Incident Alpha",
                "evidence": [],
            }
        ],
        "incoming_relations": [
            {
                "relation_id": (
                    "55555555-5555-4555-8555-555555555555"
                ),
                "relation_type": "owned_by",
                "status": "active",
                "from_entity": {
                    "entity_id": organization_id,
                    "entity_type": "organization",
                    "canonical_name": "Organization Beta",
                    "status": "active",
                },
                "from_entity_id": organization_id,
                "from_entity_name": "Organization Beta",
                "evidence": [],
            }
        ],
        "events": [],
        "dated_claims": [],
        "identity_evidence": [],
    }

    markdown = render_entity_markdown(
        snapshot
    )

    assert (
        "[[Entities/incident/"
        "Incident Alpha|Incident Alpha]]"
        in markdown
    )

    assert (
        "[[Entities/organization/"
        "Organization Beta|Organization Beta]]"
        in markdown
    )

    assert (
        "Entities/incident/Incident Alpha.md"
        not in markdown
    )



def test_entity_path_uses_readable_cyrillic_name() -> None:
    name = (
        "\u0418\u043d\u0446\u0438\u0434\u0435\u043d\u0442 "
        "\u041124 \u2116203548"
    )

    snapshot = {
        "entity": {
            "entity_id": (
                "11111111-1111-4111-8111-111111111111"
            ),
            "entity_type": "incident",
            "canonical_name": name,
        }
    }

    assert (
        entity_relative_path(
            snapshot
        ).as_posix()
        == (
            "Entities/incident/"
            + name
            + ".md"
        )
    )



def test_entity_path_sanitizes_windows_forbidden_characters() -> None:
    name = (
        "\u041f\u043b\u0430\u043d: "
        "\u044d\u0442\u0430\u043f/"
        "\u043f\u043e\u0434\u044d\u0442\u0430\u043f*"
    )

    expected = (
        "\u041f\u043b\u0430\u043d - "
        "\u044d\u0442\u0430\u043f - "
        "\u043f\u043e\u0434\u044d\u0442\u0430\u043f -"
    )

    snapshot = {
        "entity": {
            "entity_id": (
                "11111111-1111-4111-8111-111111111111"
            ),
            "entity_type": "task",
            "canonical_name": name,
        }
    }

    assert (
        entity_relative_path(
            snapshot
        ).as_posix()
        == (
            "Entities/task/"
            + expected
            + ".md"
        )
    )


def test_entity_path_avoids_windows_reserved_basename() -> None:
    snapshot = {
        "entity": {
            "entity_id": (
                "11111111-1111-4111-8111-111111111111"
            ),
            "entity_type": "system",
            "canonical_name": "CON",
        }
    }

    assert (
        entity_relative_path(
            snapshot
        ).as_posix()
        == "Entities/system/_CON.md"
    )



def test_long_entity_path_is_bounded_and_deterministic() -> None:
    first = {
        "entity": {
            "entity_id": (
                "11111111-1111-4111-8111-111111111111"
            ),
            "entity_type": "task",
            "canonical_name": (
                "Very long task name "
                + ("segment " * 40)
            ),
        }
    }

    second = {
        "entity": {
            **first["entity"],
            "entity_id": (
                "22222222-2222-4222-8222-222222222222"
            ),
        }
    }

    different = {
        "entity": {
            **first["entity"],
            "canonical_name": (
                first["entity"]["canonical_name"]
                + "different"
            ),
        }
    }

    first_path = entity_relative_path(
        first
    )

    second_path = entity_relative_path(
        second
    )

    different_path = entity_relative_path(
        different
    )

    # Same visible name intentionally produces the same candidate path.
    # Manifest-level collision handling is added in OBSIDIAN-01G2.
    assert first_path == second_path

    assert (
        first_path
        != different_path
    )

    assert (
        len(first_path.stem)
        <= 120
    )

    assert (
        first_path.suffix
        == ".md"
    )
