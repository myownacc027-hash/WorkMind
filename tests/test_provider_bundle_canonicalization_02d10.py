from __future__ import annotations

import jsonschema
import pytest

from packages.knowledge.extraction_schema import (
    BUNDLE_SCHEMA,
    normalize_provider_bundle,
)


def test_missing_root_sections_become_empty_arrays() -> None:
    raw = {
        "entities": [
            {
                "key": "person_alpha",
                "entity_type": "person",
                "canonical_name": "Person Alpha",
            }
        ]
    }

    normalized, actions = normalize_provider_bundle(
        raw
    )

    assert normalized == {
        "entities": [
            {
                "key": "person_alpha",
                "entity_type": "person",
                "canonical_name": "Person Alpha",
            }
        ],
        "claims": [],
        "relations": [],
        "events": [],
    }

    assert actions == [
        "add_missing_root_section:claims",
        "add_missing_root_section:relations",
        "add_missing_root_section:events",
    ]

    jsonschema.validate(
        normalized,
        BUNDLE_SCHEMA,
    )


def test_event_confidence_is_removed_only_from_events() -> None:
    raw = {
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
                "confidence": 0.9,
                "evidence": [
                    {
                        "source_ref": "workmind://email/test",
                        "chunk_id": "chunk-test",
                    }
                ],
            }
        ],
        "relations": [],
        "events": [
            {
                "entity_key": "incident_alpha",
                "event_type": "reported",
                "title": "Incident reported",
                "occurred_at": "2026-09-23T10:00:00+00:00",
                "evidence_type": "direct_statement",
                "confidence": 0.85,
                "evidence": [
                    {
                        "source_ref": "workmind://email/test",
                        "chunk_id": "chunk-test",
                    }
                ],
            }
        ],
    }

    normalized, actions = normalize_provider_bundle(
        raw
    )

    assert (
        normalized[
            "claims"
        ][0][
            "confidence"
        ]
        == 0.9
    )

    assert (
        "confidence"
        not in normalized[
            "events"
        ][0]
    )

    assert actions == [
        "drop_unsupported_event_confidence:0"
    ]

    jsonschema.validate(
        normalized,
        BUNDLE_SCHEMA,
    )


def test_unknown_nested_properties_are_not_silently_removed() -> None:
    raw = {
        "entities": [
            {
                "key": "person_alpha",
                "entity_type": "person",
                "canonical_name": "Person Alpha",
                "invented_field": "must remain invalid",
            }
        ],
        "claims": [],
        "relations": [],
        "events": [],
    }

    normalized, actions = normalize_provider_bundle(
        raw
    )

    assert actions == []

    assert (
        normalized[
            "entities"
        ][0][
            "invented_field"
        ]
        == "must remain invalid"
    )

    with pytest.raises(
        jsonschema.ValidationError
    ):
        jsonschema.validate(
            normalized,
            BUNDLE_SCHEMA,
        )


def test_unrelated_object_is_not_converted_to_empty_bundle() -> None:
    with pytest.raises(
        ValueError,
        match="no recognized WorkMind sections",
    ):
        normalize_provider_bundle(
            {
                "answer": "hello",
            }
        )


def test_normalizer_does_not_mutate_original() -> None:
    raw = {
        "entities": [],
        "events": [
            {
                "confidence": 0.5,
            }
        ],
    }

    normalized, _ = normalize_provider_bundle(
        raw
    )

    assert (
        raw[
            "events"
        ][0][
            "confidence"
        ]
        == 0.5
    )

    assert (
        "confidence"
        not in normalized[
            "events"
        ][0]
    )
