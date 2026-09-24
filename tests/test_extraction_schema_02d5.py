from __future__ import annotations

import jsonschema
import pytest

from packages.knowledge.extraction_schema import (
    BUNDLE_SCHEMA,
)


def test_bundle_schema_is_valid_json_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(
        BUNDLE_SCHEMA
    )


def test_bundle_schema_requires_typed_entity_contract() -> None:
    entity_schema = (
        BUNDLE_SCHEMA[
            "properties"
        ][
            "entities"
        ][
            "items"
        ]
    )

    assert entity_schema[
        "additionalProperties"
    ] is False

    assert set(
        entity_schema[
            "required"
        ]
    ) == {
        "key",
        "entity_type",
        "canonical_name",
    }

    assert (
        entity_schema[
            "properties"
        ][
            "entity_id"
        ][
            "pattern"
        ]
    )


def test_bundle_schema_rejects_entity_without_key() -> None:
    bundle = {
        "entities": [
            {
                "entity_type": "incident",
                "canonical_name": "Synthetic incident",
            }
        ],
        "claims": [],
        "relations": [],
        "events": [],
    }

    with pytest.raises(
        jsonschema.ValidationError
    ):
        jsonschema.validate(
            bundle,
            BUNDLE_SCHEMA,
        )


def test_bundle_schema_accepts_minimal_workmind_bundle() -> None:
    bundle = {
        "entities": [
            {
                "key": "incident_alpha",
                "entity_type": "incident",
                "canonical_name": "Incident Alpha",
            },
            {
                "key": "person_ivan",
                "entity_type": "person",
                "canonical_name": "Ivan Petrov",
            },
        ],
        "claims": [
            {
                "subject_key": "incident_alpha",
                "predicate": "current_status",
                "object_text": "investigating",
                "evidence_type": "direct_statement",
                "evidence": [
                    {
                        "source_ref": "workmind://email/test",
                        "chunk_id": "chunk-test",
                    }
                ],
            }
        ],
        "relations": [
            {
                "from_key": "person_ivan",
                "relation_type": "owns",
                "to_key": "incident_alpha",
                "evidence_type": "direct_statement",
                "evidence": [
                    {
                        "source_ref": "workmind://email/test",
                        "chunk_id": "chunk-test",
                    }
                ],
            }
        ],
        "events": [
            {
                "entity_key": "incident_alpha",
                "event_type": "reported",
                "title": "Incident Alpha reported",
                "occurred_at": "2026-09-23T10:00:00+00:00",
                "evidence_type": "direct_statement",
                "evidence": [
                    {
                        "source_ref": "workmind://email/test",
                        "chunk_id": "chunk-test",
                    }
                ],
            }
        ],
    }

    jsonschema.validate(
        bundle,
        BUNDLE_SCHEMA,
    )


def test_bundle_schema_rejects_invented_entity_id_shape() -> None:
    bundle = {
        "entities": [
            {
                "key": "person_ivan",
                "entity_id": "person:ivan-petrov",
                "entity_type": "person",
                "canonical_name": "Ivan Petrov",
            }
        ],
        "claims": [],
        "relations": [],
        "events": [],
    }

    with pytest.raises(
        jsonschema.ValidationError
    ):
        jsonschema.validate(
            bundle,
            BUNDLE_SCHEMA,
        )


def test_fact_evidence_requires_exact_pair_shape() -> None:
    claim_schema = (
        BUNDLE_SCHEMA[
            "properties"
        ][
            "claims"
        ][
            "items"
        ]
    )

    evidence_schema = (
        claim_schema[
            "properties"
        ][
            "evidence"
        ][
            "items"
        ]
    )

    assert set(
        evidence_schema[
            "required"
        ]
    ) == {
        "source_ref",
        "chunk_id",
    }


def test_relations_and_events_have_real_workmind_keys() -> None:
    relation_schema = (
        BUNDLE_SCHEMA[
            "properties"
        ][
            "relations"
        ][
            "items"
        ]
    )

    event_schema = (
        BUNDLE_SCHEMA[
            "properties"
        ][
            "events"
        ][
            "items"
        ]
    )

    assert set(
        relation_schema[
            "required"
        ]
    ) == {
        "from_key",
        "relation_type",
        "to_key",
        "evidence_type",
        "evidence",
    }

    assert set(
        event_schema[
            "required"
        ]
    ) == {
        "entity_key",
        "event_type",
        "title",
        "occurred_at",
        "evidence_type",
        "evidence",
    }
def test_evidence_enums_match_validator_02d7a() -> None:
    import ast
    import inspect

    from packages.knowledge import service

    validator_values = sorted(
        str(value)
        for value in service.EVIDENCE_TYPES
    )

    claim_schema = (
        BUNDLE_SCHEMA[
            "properties"
        ][
            "claims"
        ][
            "items"
        ]
    )

    relation_schema = (
        BUNDLE_SCHEMA[
            "properties"
        ][
            "relations"
        ][
            "items"
        ]
    )

    event_schema = (
        BUNDLE_SCHEMA[
            "properties"
        ][
            "events"
        ][
            "items"
        ]
    )

    for item_schema in (
        claim_schema,
        relation_schema,
        event_schema,
    ):
        assert sorted(
            item_schema[
                "properties"
            ][
                "evidence_type"
            ][
                "enum"
            ]
        ) == validator_values

    role_enum = sorted(
        claim_schema[
            "properties"
        ][
            "evidence"
        ][
            "items"
        ][
            "properties"
        ][
            "evidence_role"
        ][
            "enum"
        ]
    )

    assert role_enum == validator_values

    tree = ast.parse(
        inspect.getsource(
            service._validated_evidence
        )
    )

    role_check_found = False

    for node in ast.walk(
        tree
    ):
        if (
            isinstance(
                node,
                ast.Compare,
            )
            and isinstance(
                node.left,
                ast.Name,
            )
            and node.left.id
            == "role"
            and len(
                node.ops
            )
            == 1
            and isinstance(
                node.ops[0],
                ast.NotIn,
            )
            and len(
                node.comparators
            )
            == 1
            and isinstance(
                node.comparators[0],
                ast.Name,
            )
            and node.comparators[0].id
            == "EVIDENCE_TYPES"
        ):
            role_check_found = True
            break

    assert role_check_found is True


def test_schema_rejects_email_evidence_type_02d7a() -> None:
    bundle = {
        "entities": [
            {
                "key": "incident_alpha",
                "entity_type": "incident",
                "canonical_name": "Incident Alpha",
            }
        ],
        "claims": [
            {
                "subject_key": "incident_alpha",
                "predicate": "status",
                "object_text": "active",
                "evidence_type": "email",
                "evidence": [
                    {
                        "source_ref": "workmind://email/test",
                        "chunk_id": "chunk-test",
                    }
                ],
            }
        ],
        "relations": [],
        "events": [],
    }

    with pytest.raises(
        jsonschema.ValidationError
    ):
        jsonschema.validate(
            bundle,
            BUNDLE_SCHEMA,
        )


def test_schema_rejects_primary_evidence_role_02d7a() -> None:
    bundle = {
        "entities": [
            {
                "key": "incident_alpha",
                "entity_type": "incident",
                "canonical_name": "Incident Alpha",
            }
        ],
        "claims": [
            {
                "subject_key": "incident_alpha",
                "predicate": "status",
                "object_text": "active",
                "evidence_type": "direct_statement",
                "evidence": [
                    {
                        "source_ref": "workmind://email/test",
                        "chunk_id": "chunk-test",
                        "evidence_role": "primary",
                    }
                ],
            }
        ],
        "relations": [],
        "events": [],
    }

    with pytest.raises(
        jsonschema.ValidationError
    ):
        jsonschema.validate(
            bundle,
            BUNDLE_SCHEMA,
        )


def test_evidence_role_may_be_omitted_02d7a() -> None:
    bundle = {
        "entities": [
            {
                "key": "incident_alpha",
                "entity_type": "incident",
                "canonical_name": "Incident Alpha",
            }
        ],
        "claims": [
            {
                "subject_key": "incident_alpha",
                "predicate": "status",
                "object_text": "active",
                "evidence_type": "direct_statement",
                "evidence": [
                    {
                        "source_ref": "workmind://email/test",
                        "chunk_id": "chunk-test",
                    }
                ],
            }
        ],
        "relations": [],
        "events": [],
    }

    jsonschema.validate(
        bundle,
        BUNDLE_SCHEMA,
    )
