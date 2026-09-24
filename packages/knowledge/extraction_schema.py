from __future__ import annotations

from typing import Any


UUID_PATTERN = (
    "^[0-9a-fA-F]{8}-"
    "[0-9a-fA-F]{4}-"
    "[1-5][0-9a-fA-F]{3}-"
    "[89abAB][0-9a-fA-F]{3}-"
    "[0-9a-fA-F]{12}$"
)


CHUNK_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "description": (
        "Evidence for a newly asserted fact. source_ref and chunk_id "
        "must exactly match one entry from packet.allowed_evidence."
    ),
    "properties": {
        "source_ref": {
            "type": "string",
            "minLength": 1,
        },
        "chunk_id": {
            "type": "string",
            "minLength": 1,
        },
        "evidence_role": {
            "type": "string",
            "enum": ["direct_statement", "indirect_report", "inference", "notification"],
            "description": (
                "Optional evidence classification. If omitted, WorkMind "
                "defaults it to the enclosing claim/relation/event "
                "evidence_type."
            ),
        },
        "excerpt": {
            "type": "string",
        },
    },
    "required": [
        "source_ref",
        "chunk_id",
    ],
}


ENTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "description": (
        "A durable WorkMind entity. key is the local identifier used by "
        "claims, relations, and events inside this bundle. entity_id is "
        "optional and may only be copied exactly from an existing "
        "identity_candidate; never invent entity_id."
    ),
    "properties": {
        "key": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Required local bundle key. This is not entity_id."
            ),
        },
        "entity_id": {
            "type": "string",
            "pattern": UUID_PATTERN,
            "description": (
                "Exact existing WorkMind entity UUID copied from "
                "identity_candidates. Omit for a new entity."
            ),
        },
        "entity_type": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Use an entity type supported by packet.entity_ontology."
            ),
        },
        "canonical_name": {
            "type": "string",
            "minLength": 1,
        },
        "external_id": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Only an explicit stable external identifier from source."
            ),
        },
        "status": {
            "type": "string",
            "minLength": 1,
        },
        "description": {
            "type": "string",
        },
    },
    "required": [
        "key",
        "entity_type",
        "canonical_name",
    ],
}


CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "description": (
        "A factual assertion about one entity. At least one of "
        "object_key or object_text must be supplied; WorkMind performs "
        "the final semantic validation."
    ),
    "properties": {
        "subject_key": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Must match one entities[].key in this bundle."
            ),
        },
        "predicate": {
            "type": "string",
            "minLength": 1,
        },
        "object_key": {
            "type": "string",
            "minLength": 1,
            "description": (
                "When present, must match one entities[].key."
            ),
        },
        "object_text": {
            "type": "string",
            "minLength": 1,
        },
        "claim_text": {
            "type": "string",
            "minLength": 1,
        },
        "evidence_type": {
            "type": "string",
            "enum": ["direct_statement", "indirect_report", "inference", "notification"],
            "description": (
                "Evidence classification accepted by the WorkMind validator."
            ),
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "status": {
            "type": "string",
            "minLength": 1,
        },
        "valid_from": {
            "type": "string",
            "minLength": 1,
        },
        "valid_to": {
            "type": "string",
            "minLength": 1,
        },
        "observed_at": {
            "type": "string",
            "minLength": 1,
        },
        "supersedes_claim_id": {
            "type": "string",
            "pattern": UUID_PATTERN,
        },
        "evidence": {
            "type": "array",
            "minItems": 1,
            "items": CHUNK_EVIDENCE_SCHEMA,
        },
    },
    "required": [
        "subject_key",
        "predicate",
        "evidence_type",
        "evidence",
    ],
}


RELATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "description": (
        "A relationship between two entities from this bundle."
    ),
    "properties": {
        "from_key": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Must match one entities[].key."
            ),
        },
        "relation_type": {
            "type": "string",
            "minLength": 1,
        },
        "to_key": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Must match one entities[].key."
            ),
        },
        "claim_text": {
            "type": "string",
            "minLength": 1,
        },
        "evidence_type": {
            "type": "string",
            "enum": ["direct_statement", "indirect_report", "inference", "notification"],
            "description": (
                "Evidence classification accepted by the WorkMind validator."
            ),
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "status": {
            "type": "string",
            "minLength": 1,
        },
        "valid_from": {
            "type": "string",
            "minLength": 1,
        },
        "valid_to": {
            "type": "string",
            "minLength": 1,
        },
        "observed_at": {
            "type": "string",
            "minLength": 1,
        },
        "supersedes_claim_id": {
            "type": "string",
            "pattern": UUID_PATTERN,
        },
        "evidence": {
            "type": "array",
            "minItems": 1,
            "items": CHUNK_EVIDENCE_SCHEMA,
        },
    },
    "required": [
        "from_key",
        "relation_type",
        "to_key",
        "evidence_type",
        "evidence",
    ],
}


EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "description": (
        "A real-world event with an asserted occurrence time. "
        "Do not convert an observation timestamp into occurred_at "
        "unless the source explicitly supports that event time."
    ),
    "properties": {
        "event_id": {
            "type": "string",
            "pattern": UUID_PATTERN,
            "description": (
                "Exact existing WorkMind event UUID only. "
                "Omit for a new event."
            ),
        },
        "entity_key": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Must match one entities[].key."
            ),
        },
        "event_type": {
            "type": "string",
            "minLength": 1,
        },
        "title": {
            "type": "string",
            "minLength": 1,
        },
        "description": {
            "type": "string",
        },
        "occurred_at": {
            "type": "string",
            "minLength": 1,
        },
        "ended_at": {
            "type": "string",
            "minLength": 1,
        },
        "status": {
            "type": "string",
            "minLength": 1,
        },
        "evidence_type": {
            "type": "string",
            "enum": ["direct_statement", "indirect_report", "inference", "notification"],
            "description": (
                "Evidence classification accepted by the WorkMind validator."
            ),
        },
        "evidence": {
            "type": "array",
            "minItems": 1,
            "items": CHUNK_EVIDENCE_SCHEMA,
        },
    },
    "required": [
        "entity_key",
        "event_type",
        "title",
        "occurred_at",
        "evidence_type",
        "evidence",
    ],
}


BUNDLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "description": (
        "WorkMind wm-ke-v2 candidate bundle. The remote model only "
        "generates a candidate; WorkMind remains authoritative for "
        "identity, evidence, ontology, duplicate, temporal, and "
        "semantic validation."
    ),
    "properties": {
        "entities": {
            "type": "array",
            "items": ENTITY_SCHEMA,
        },
        "claims": {
            "type": "array",
            "items": CLAIM_SCHEMA,
        },
        "relations": {
            "type": "array",
            "items": RELATION_SCHEMA,
        },
        "events": {
            "type": "array",
            "items": EVENT_SCHEMA,
        },
    },
    "required": [
        "entities",
        "claims",
        "relations",
        "events",
    ],
}
def normalize_provider_bundle(
    bundle: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Apply only semantics-preserving compatibility normalization to a
    provider-generated candidate bundle.

    This is deliberately NOT a general repair layer:
    - missing top-level sections are equivalent to empty arrays;
    - event confidence is discarded because WorkMind events do not persist
      an event-level confidence field;
    - all other unexpected structure is left untouched so normal validation
      can reject it.
    """
    import copy

    if not isinstance(
        bundle,
        dict,
    ):
        raise TypeError(
            "Provider bundle must be a JSON object."
        )

    sections = (
        "entities",
        "claims",
        "relations",
        "events",
    )

    if not any(
        section in bundle
        for section in sections
    ):
        raise ValueError(
            "Provider bundle contains no recognized WorkMind sections."
        )

    normalized = copy.deepcopy(
        bundle
    )

    actions: list[str] = []

    for section in sections:
        if section not in normalized:
            normalized[
                section
            ] = []

            actions.append(
                f"add_missing_root_section:{section}"
            )

    events = normalized.get(
        "events"
    )

    if isinstance(
        events,
        list,
    ):
        for index, event in enumerate(
            events
        ):
            if not isinstance(
                event,
                dict,
            ):
                continue

            if "confidence" in event:
                event.pop(
                    "confidence",
                    None,
                )

                actions.append(
                    f"drop_unsupported_event_confidence:{index}"
                )

    return (
        normalized,
        actions,
    )
