from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.database.models import (
    Attachment,
    DocumentChunk,
    EmailMessage,
    ExternalDocument,
    KnowledgeClaim,
    KnowledgeEntity,
    KnowledgeEvent,
    KnowledgeEvidence,
)
from packages.database.session import SessionLocal
from packages.knowledge.temporal import (
    interval_contains,
    intervals_overlap,
    overlap_bounds,
)

ENTITY_TYPES = {
    "project",
    "task",
    "incident",
    "person",
    "organization",
    "decision",
    "process",
    "system",
    "document",
    "other",
}
EVIDENCE_TYPES = {
    "direct_statement",
    "indirect_report",
    "notification",
    "inference",
}

SOURCE_METADATA_FIELDS: dict[str, frozenset[str]] = {
    "attachment": frozenset(
        {"filename", "content_hash", "size_bytes", "mime_type", "parent_source_ref"}
    ),
    "document": frozenset(
        {"filename", "content_hash", "size_bytes", "mime_type", "source_date"}
    ),
}
EXCLUSIVE_PREDICATES = {
    "status",
    "current_status",
    "owner",
    "assignee",
    "deadline",
    "decision",
}


class KnowledgeValidationError(ValueError):
    """Invalid structured-knowledge payload."""


def normalize_text(value: str) -> str:
    folded = value.strip().casefold().replace("ё", "е")
    folded = re.sub(r"[^0-9a-zа-я]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def normalize_predicate(value: str) -> str:
    normalized = normalize_text(value).replace(" ", "_")
    if not normalized:
        raise KnowledgeValidationError("predicate must not be empty")
    return normalized[:128]


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise KnowledgeValidationError("datetime value must be a string")
    raw = value.strip()
    try:
        if len(raw) == 10:
            return datetime.fromisoformat(raw).replace(tzinfo=UTC)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except ValueError as exc:
        raise KnowledgeValidationError(
            f"Invalid ISO date/datetime: {value!r}"
        ) from exc


def _uuid(value: Any, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise KnowledgeValidationError(f"{field_name} must be a UUID") from exc


def _hash(*parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _parse_source_ref(source_ref: str) -> tuple[str, uuid.UUID]:
    match = re.fullmatch(
        r"workmind://(email|attachment|document)/([0-9a-fA-F-]{36})",
        source_ref.strip(),
    )
    if not match:
        raise KnowledgeValidationError(f"Invalid source_ref: {source_ref!r}")
    return match.group(1), _uuid(match.group(2), "source_id")


def _source_exists(
    session: Session,
    source_type: str,
    source_id: uuid.UUID,
) -> bool:
    model = {
        "email": EmailMessage,
        "attachment": Attachment,
        "document": ExternalDocument,
    }[source_type]
    return session.get(model, source_id) is not None


def _source_date(
    session: Session,
    source_type: str,
    source_id: uuid.UUID,
) -> datetime | None:
    if source_type == "email":
        message = session.get(EmailMessage, source_id)
        return (message.received_at or message.sent_at) if message else None
    if source_type == "attachment":
        attachment = session.get(Attachment, source_id)
        if attachment is None:
            return None
        message = session.get(EmailMessage, attachment.message_id)
        return (message.received_at or message.sent_at) if message else None
    document = session.get(ExternalDocument, source_id)
    return document.source_date if document else None


def _validated_evidence(
    session: Session,
    raw: Any,
    default_role: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise KnowledgeValidationError("evidence entry must be an object")
    source_ref = str(raw.get("source_ref") or "").strip()
    source_type, source_id = _parse_source_ref(source_ref)
    if not _source_exists(session, source_type, source_id):
        raise KnowledgeValidationError(f"Source does not exist: {source_ref}")
    chunk_id = _uuid(raw.get("chunk_id"), "chunk_id")
    chunk = session.get(DocumentChunk, chunk_id)
    if chunk is None:
        raise KnowledgeValidationError(f"Chunk does not exist: {chunk_id}")
    if chunk.source_type != source_type or chunk.source_id != source_id:
        raise KnowledgeValidationError(
            f"chunk_id {chunk_id} does not belong to {source_ref}"
        )
    role = str(raw.get("evidence_role") or default_role).strip()
    if role not in EVIDENCE_TYPES:
        raise KnowledgeValidationError(f"Unsupported evidence_role: {role!r}")
    proposed = str(raw.get("excerpt") or "").strip()
    if proposed and normalize_text(proposed) in normalize_text(chunk.text):
        excerpt = proposed[:3000]
        excerpt_replaced = False
    else:
        excerpt = chunk.text[:3000]
        excerpt_replaced = bool(proposed)
    return {
        "evidence_kind": "chunk",
        "source_type": source_type,
        "source_id": source_id,
        "source_ref": source_ref,
        "chunk_id": chunk_id,
        "metadata_json": {},
        "excerpt": excerpt,
        "excerpt_replaced": excerpt_replaced,
        "evidence_role": role,
        "source_date": _source_date(session, source_type, source_id),
    }


def _source_metadata_values(
    session: Session,
    source_type: str,
    source_id: uuid.UUID,
) -> dict[str, Any]:
    if source_type == "attachment":
        attachment = session.get(Attachment, source_id)
        if attachment is None:
            raise KnowledgeValidationError(f"Attachment source does not exist: {source_id}")
        return {
            "filename": attachment.filename,
            "content_hash": attachment.content_hash,
            "size_bytes": attachment.size_bytes,
            "mime_type": attachment.mime_type,
            "parent_source_ref": f"workmind://email/{attachment.message_id}",
        }
    if source_type == "document":
        document = session.get(ExternalDocument, source_id)
        if document is None:
            raise KnowledgeValidationError(f"Document source does not exist: {source_id}")
        return {
            "filename": document.original_filename,
            "content_hash": document.content_hash,
            "size_bytes": document.size_bytes,
            "mime_type": document.mime_type,
            "source_date": (
                document.source_date.isoformat() if document.source_date else None
            ),
        }
    raise KnowledgeValidationError(
        "source_metadata evidence is allowed only for attachment/document sources"
    )


def _validated_metadata_evidence(
    session: Session,
    raw: Any,
    default_role: str = "direct_statement",
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise KnowledgeValidationError("identity_evidence entry must be an object")
    kind = str(raw.get("evidence_kind") or "source_metadata").strip()
    if kind != "source_metadata":
        raise KnowledgeValidationError(
            "entity identity_evidence must use evidence_kind='source_metadata'"
        )
    source_ref = str(raw.get("source_ref") or "").strip()
    source_type, source_id = _parse_source_ref(source_ref)
    if source_type not in SOURCE_METADATA_FIELDS:
        raise KnowledgeValidationError(
            "source_metadata evidence is allowed only for attachment/document sources"
        )
    if not _source_exists(session, source_type, source_id):
        raise KnowledgeValidationError(f"Source does not exist: {source_ref}")
    proposed = raw.get("metadata")
    if not isinstance(proposed, dict) or not proposed:
        raise KnowledgeValidationError(
            "source_metadata evidence metadata must be a non-empty object"
        )
    unsupported = sorted(set(proposed) - set(SOURCE_METADATA_FIELDS[source_type]))
    if unsupported:
        raise KnowledgeValidationError(
            "Unsupported source_metadata fields: " + ", ".join(unsupported)
        )
    stored = _source_metadata_values(session, source_type, source_id)
    verified: dict[str, Any] = {}
    for field, value in proposed.items():
        actual = stored.get(field)
        if value != actual:
            raise KnowledgeValidationError(
                f"source_metadata mismatch for {field}: proposed={value!r} stored={actual!r}"
            )
        verified[field] = actual
    if "filename" not in verified:
        raise KnowledgeValidationError(
            "source_metadata identity evidence must include verified filename"
        )
    role = str(raw.get("evidence_role") or default_role).strip()
    if role not in EVIDENCE_TYPES:
        raise KnowledgeValidationError(f"Unsupported evidence_role: {role!r}")
    excerpt = "; ".join(f"{key}={verified[key]}" for key in sorted(verified))[:3000]
    return {
        "evidence_kind": "source_metadata",
        "source_type": source_type,
        "source_id": source_id,
        "source_ref": source_ref,
        "chunk_id": None,
        "metadata_json": verified,
        "excerpt": excerpt,
        "excerpt_replaced": bool(raw.get("excerpt")),
        "evidence_role": role,
        "source_date": _source_date(session, source_type, source_id),
    }


def _entity_plan(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise KnowledgeValidationError(f"entities[{index}] must be an object")
    key = normalize_text(str(raw.get("key") or ""))
    if not key:
        raise KnowledgeValidationError(f"entities[{index}].key is required")
    entity_type = str(raw.get("entity_type") or "").strip().lower()
    if entity_type not in ENTITY_TYPES:
        raise KnowledgeValidationError(
            f"entities[{index}] unsupported entity_type: {entity_type!r}"
        )
    canonical_name = str(raw.get("canonical_name") or "").strip()
    if not canonical_name:
        raise KnowledgeValidationError(
            f"entities[{index}].canonical_name is required"
        )
    entity_id = (
        _uuid(raw.get("entity_id"), "entity_id")
        if raw.get("entity_id") not in (None, "")
        else None
    )
    external_id = raw.get("external_id")
    external_id = (
        str(external_id).strip() if external_id not in (None, "") else None
    )
    normalized_key = normalize_text(
        str(raw.get("normalized_key") or external_id or canonical_name)
    )[:255]
    if not normalized_key:
        raise KnowledgeValidationError(
            f"entities[{index}].normalized_key is empty"
        )
    attributes = raw.get("attributes")
    return {
        "key": key,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "canonical_name": canonical_name,
        "normalized_key": normalized_key,
        "external_id": external_id,
        "status": str(raw.get("status") or "active")[:32],
        "description": (
            str(raw.get("description")).strip()
            if raw.get("description") not in (None, "")
            else None
        ),
        "attributes": attributes if isinstance(attributes, dict) else {},
        "identity_evidence_raw": raw.get("identity_evidence", []),
        "identity_evidence": [],
    }


def _identity_evidence_items(
    session: Session,
    raw: Any,
) -> list[dict[str, Any]]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise KnowledgeValidationError("identity_evidence must be an array")
    if not raw:
        return []
    return [_validated_metadata_evidence(session, item) for item in raw]


def _evidence_items(
    session: Session,
    raw: dict[str, Any],
    default_role: str,
) -> list[dict[str, Any]]:
    evidence = raw.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise KnowledgeValidationError("evidence must be a non-empty array")
    return [
        _validated_evidence(session, item, default_role)
        for item in evidence
    ]


def _resolve_existing_entity(
    session: Session,
    plan: dict[str, Any],
) -> KnowledgeEntity | None:
    entity_id = plan.get("entity_id")
    if entity_id is not None:
        existing = session.get(KnowledgeEntity, entity_id)
        if existing is None:
            raise KnowledgeValidationError(
                f"entity_id does not exist: {entity_id}"
            )
        if existing.entity_type != plan["entity_type"]:
            raise KnowledgeValidationError(
                "entity_id refers to a different entity_type"
            )
        if (
            plan["external_id"]
            and existing.external_id
            and existing.external_id != plan["external_id"]
        ):
            raise KnowledgeValidationError(
                "entity_id and external_id refer to different entities"
            )
        return existing

    if plan["external_id"]:
        existing = session.scalar(
            select(KnowledgeEntity).where(
                KnowledgeEntity.entity_type == plan["entity_type"],
                KnowledgeEntity.external_id == plan["external_id"],
            )
        )
        if existing is not None:
            return existing
    return session.scalar(
        select(KnowledgeEntity).where(
            KnowledgeEntity.entity_type == plan["entity_type"],
            KnowledgeEntity.normalized_key == plan["normalized_key"],
        )
    )


def _upsert_entity(
    session: Session,
    plan: dict[str, Any],
) -> tuple[KnowledgeEntity, bool]:
    entity = _resolve_existing_entity(session, plan)
    created = entity is None
    if entity is None:
        entity = KnowledgeEntity(
            entity_type=plan["entity_type"],
            canonical_name=plan["canonical_name"],
            normalized_key=plan["normalized_key"],
            external_id=plan["external_id"],
            status=plan["status"],
            description=plan["description"],
            attributes_json=plan["attributes"],
        )
        session.add(entity)
        session.flush()
    else:
        entity.canonical_name = plan["canonical_name"]
        entity.status = plan["status"]
        entity.description = plan["description"] or entity.description
        if plan["external_id"]:
            entity.external_id = plan["external_id"]
        entity.attributes_json = {
            **(entity.attributes_json or {}),
            **plan["attributes"],
        }
    return entity, created


def _claim_hash(
    subject_id: uuid.UUID,
    predicate: str,
    object_entity_id: uuid.UUID | None,
    object_text: str | None,
    valid_from: datetime | None,
    valid_to: datetime | None,
) -> str:
    return _hash(
        str(subject_id),
        predicate,
        str(object_entity_id or ""),
        normalize_text(object_text or ""),
        valid_from.isoformat() if valid_from else "",
        valid_to.isoformat() if valid_to else "",
    )


def _claim_identity_key(
    subject_id: uuid.UUID,
    predicate: str,
    object_entity_id: uuid.UUID | None,
    object_text: str | None,
) -> str:
    """Generate a stable key that does not depend on mutable dates."""
    return _hash(
        "claim-v2",
        str(subject_id),
        predicate,
        str(object_entity_id or ""),
        normalize_text(object_text or ""),
    )


def _validate_claim_key(value: Any) -> str | None:
    if value in (None, ""):
        return None
    key = str(value).strip()
    if not key:
        return None
    if len(key) > 255:
        raise KnowledgeValidationError(
            "claim_key must be at most 255 characters"
        )
    if not re.fullmatch(r"[0-9A-Za-z._:-]+", key):
        raise KnowledgeValidationError(
            "claim_key may contain only letters, digits, '.', '_', ':', '-'"
        )
    return key


def _event_hash(
    entity_id: uuid.UUID,
    event_type: str,
    title: str,
    occurred_at: datetime,
) -> str:
    return _hash(
        str(entity_id),
        event_type,
        normalize_text(title),
        occurred_at.isoformat(),
    )


def _save_evidence(
    session: Session,
    *,
    parent_kind: str,
    parent_id: uuid.UUID,
    evidence: list[dict[str, Any]],
) -> tuple[int, int]:
    if parent_kind not in {"entity", "claim", "event"}:
        raise KnowledgeValidationError(f"Unsupported evidence parent_kind: {parent_kind}")
    created = 0
    replacements = 0
    for item in evidence:
        metadata_fingerprint = json.dumps(
            item.get("metadata_json") or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence_hash = _hash(
            parent_kind,
            str(parent_id),
            item.get("evidence_kind") or "chunk",
            item["source_ref"],
            str(item.get("chunk_id") or ""),
            metadata_fingerprint,
            item["evidence_role"],
        )
        existing = session.scalar(
            select(KnowledgeEvidence.id).where(
                KnowledgeEvidence.evidence_hash == evidence_hash
            )
        )
        replacements += int(item["excerpt_replaced"])
        if existing is not None:
            continue
        session.add(
            KnowledgeEvidence(
                entity_id=parent_id if parent_kind == "entity" else None,
                claim_id=parent_id if parent_kind == "claim" else None,
                event_id=parent_id if parent_kind == "event" else None,
                evidence_kind=item.get("evidence_kind") or "chunk",
                source_type=item["source_type"],
                source_id=item["source_id"],
                source_ref=item["source_ref"],
                chunk_id=item.get("chunk_id"),
                metadata_json=item.get("metadata_json") or {},
                excerpt=item["excerpt"],
                evidence_role=item["evidence_role"],
                source_date=item["source_date"],
                evidence_hash=evidence_hash,
            )
        )
        created += 1
    return created, replacements


def import_knowledge_bundle(
    bundle_json: str,
    *,
    apply: bool = False,
    created_by: str = "mcp",
) -> dict[str, Any]:
    try:
        raw = json.loads(bundle_json)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "apply": apply,
            "errors": [
                f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
            ],
        }
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "apply": apply,
            "errors": ["Knowledge bundle must be a JSON object"],
        }
    errors: list[str] = []
    sections: dict[str, list[dict[str, Any]]] = {}
    for name in ("entities", "claims", "relations", "events"):
        value = raw.get(name, [])
        if not isinstance(value, list):
            errors.append(f"{name} must be an array")
            value = []
        sections[name] = value
    plans: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(sections["entities"]):
        try:
            plan = _entity_plan(item, index)
            if plan["key"] in plans:
                raise KnowledgeValidationError(
                    f"duplicate entity key: {plan['key']!r}"
                )
            plans[plan["key"]] = plan
        except KnowledgeValidationError as exc:
            errors.append(str(exc))
    counts = {name: len(value) for name, value in sections.items()}
    if errors:
        return {"ok": False, "apply": apply, "counts": counts, "errors": errors}

    with SessionLocal() as session:
        for plan in plans.values():
            try:
                plan["identity_evidence"] = _identity_evidence_items(
                    session,
                    plan.get("identity_evidence_raw"),
                )
            except KnowledgeValidationError as exc:
                errors.append(f"entities[{plan['key']}].identity_evidence: {exc}")
        claims: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        for section_name in ("claims", "relations"):
            for index, item in enumerate(sections[section_name]):
                try:
                    if not isinstance(item, dict):
                        raise KnowledgeValidationError("entry must be an object")
                    if section_name == "claims":
                        subject_key = normalize_text(str(item.get("subject_key") or ""))
                        object_key_raw = item.get("object_key")
                        predicate_raw = item.get("predicate")
                    else:
                        subject_key = normalize_text(str(item.get("from_key") or ""))
                        object_key_raw = item.get("to_key")
                        predicate_raw = item.get("relation_type")
                    if subject_key not in plans:
                        raise KnowledgeValidationError(
                            f"unknown subject/from key: {subject_key!r}"
                        )
                    object_key = (
                        normalize_text(str(object_key_raw))
                        if object_key_raw not in (None, "")
                        else None
                    )
                    object_text = (
                        str(item.get("object_text")).strip()
                        if item.get("object_text") not in (None, "")
                        else None
                    )
                    if section_name == "relations" and not object_key:
                        raise KnowledgeValidationError("relation requires to_key")
                    if object_key and object_key not in plans:
                        raise KnowledgeValidationError(
                            f"unknown object/to key: {object_key!r}"
                        )
                    if not object_key and not object_text:
                        raise KnowledgeValidationError(
                            "claim requires object_key or object_text"
                        )
                    if (
                        not object_key
                        and object_text
                        and not normalize_text(object_text)
                    ):
                        raise KnowledgeValidationError(
                            "claim object_text contains no semantic "
                            "letters or digits; possible encoding loss"
                        )
                    predicate = normalize_predicate(str(predicate_raw or ""))
                    evidence_type = str(
                        item.get("evidence_type") or "direct_statement"
                    ).strip()
                    if evidence_type not in EVIDENCE_TYPES:
                        raise KnowledgeValidationError(
                            f"unsupported evidence_type: {evidence_type!r}"
                        )
                    claim_text = str(item.get("claim_text") or "").strip()
                    if not claim_text:
                        claim_text = (
                            f"{plans[subject_key]['canonical_name']} "
                            f"{predicate.replace('_', ' ')} "
                            f"{plans[object_key]['canonical_name'] if object_key else object_text}"
                        )
                    confidence = float(item.get("confidence", 1.0))
                    if not 0 <= confidence <= 1:
                        raise KnowledgeValidationError(
                            "confidence must be between 0 and 1"
                        )
                    attributes = item.get("attributes")
                    valid_from = parse_datetime(item.get("valid_from"))
                    valid_to = parse_datetime(item.get("valid_to"))
                    if (
                        valid_from is not None
                        and valid_to is not None
                        and valid_from >= valid_to
                    ):
                        raise KnowledgeValidationError(
                            "valid_from must be earlier than valid_to"
                        )
                    claim_id = (
                        _uuid(item.get("claim_id"), "claim_id")
                        if item.get("claim_id") not in (None, "")
                        else None
                    )
                    claim_key = _validate_claim_key(
                        item.get("claim_key")
                    )
                    existing_claim = (
                        session.get(KnowledgeClaim, claim_id)
                        if claim_id is not None
                        else None
                    )
                    if claim_id is not None and existing_claim is None:
                        raise KnowledgeValidationError(
                            f"claim_id does not exist: {claim_id}"
                        )
                    if (
                        existing_claim is not None
                        and claim_key is not None
                        and existing_claim.claim_key != claim_key
                    ):
                        raise KnowledgeValidationError(
                            "claim_id and claim_key refer to different claims"
                        )
                    supersedes_claim_id = (
                        _uuid(
                            item.get("supersedes_claim_id"),
                            "supersedes_claim_id",
                        )
                        if item.get("supersedes_claim_id") not in (None, "")
                        else None
                    )
                    superseded_by_claim_id = (
                        _uuid(
                            item.get("superseded_by_claim_id"),
                            "superseded_by_claim_id",
                        )
                        if item.get("superseded_by_claim_id") not in (None, "")
                        else None
                    )
                    for field_name, related_id in (
                        ("supersedes_claim_id", supersedes_claim_id),
                        ("superseded_by_claim_id", superseded_by_claim_id),
                    ):
                        if related_id is not None and related_id == claim_id:
                            raise KnowledgeValidationError(
                                f"{field_name} cannot reference the same claim"
                            )
                        if (
                            related_id is not None
                            and session.get(KnowledgeClaim, related_id) is None
                        ):
                            raise KnowledgeValidationError(
                                f"{field_name} does not exist: {related_id}"
                            )
                    claims.append(
                        {
                            "claim_id": claim_id,
                            "claim_key": claim_key,
                            "subject_key": subject_key,
                            "predicate": predicate,
                            "object_key": object_key,
                            "object_text": object_text,
                            "claim_text": claim_text,
                            "evidence_type": evidence_type,
                            "confidence": confidence,
                            "status": str(item.get("status") or "active")[:32],
                            "valid_from": valid_from,
                            "valid_to": valid_to,
                            "observed_at": parse_datetime(item.get("observed_at")),
                            "supersedes_claim_id": supersedes_claim_id,
                            "superseded_by_claim_id": superseded_by_claim_id,
                            "attributes": attributes if isinstance(attributes, dict) else {},
                            "evidence": _evidence_items(
                                session,
                                item,
                                evidence_type,
                            ),
                        }
                    )
                except (KnowledgeValidationError, TypeError, ValueError) as exc:
                    errors.append(f"{section_name}[{index}]: {exc}")
        for index, item in enumerate(sections["events"]):
            try:
                if not isinstance(item, dict):
                    raise KnowledgeValidationError("entry must be an object")
                entity_key = normalize_text(str(item.get("entity_key") or ""))
                if entity_key not in plans:
                    raise KnowledgeValidationError(
                        f"unknown entity_key: {entity_key!r}"
                    )
                event_type = normalize_predicate(str(item.get("event_type") or ""))[:64]
                title = str(item.get("title") or "").strip()
                if not title:
                    raise KnowledgeValidationError("title is required")
                occurred_at = parse_datetime(item.get("occurred_at"))
                if occurred_at is None:
                    raise KnowledgeValidationError("occurred_at is required")
                evidence_type = str(
                    item.get("evidence_type") or "direct_statement"
                ).strip()
                if evidence_type not in EVIDENCE_TYPES:
                    raise KnowledgeValidationError(
                        f"unsupported evidence_type: {evidence_type!r}"
                    )
                event_id = (
                    _uuid(item.get("event_id"), "event_id")
                    if item.get("event_id") not in (None, "")
                    else None
                )
                existing_event = (
                    session.get(KnowledgeEvent, event_id)
                    if event_id is not None
                    else None
                )
                if event_id is not None and existing_event is None:
                    raise KnowledgeValidationError(
                        f"event_id does not exist: {event_id}"
                    )
                attributes = item.get("attributes")
                events.append(
                    {
                        "event_id": event_id,
                        "entity_key": entity_key,
                        "event_type": event_type,
                        "title": title,
                        "description": (
                            str(item.get("description")).strip()
                            if item.get("description") not in (None, "")
                            else None
                        ),
                        "occurred_at": occurred_at,
                        "ended_at": parse_datetime(item.get("ended_at")),
                        "status": str(item.get("status") or "confirmed")[:32],
                        "attributes": attributes if isinstance(attributes, dict) else {},
                        "evidence": _evidence_items(session, item, evidence_type),
                    }
                )
            except (KnowledgeValidationError, TypeError, ValueError) as exc:
                errors.append(f"events[{index}]: {exc}")
        if errors:
            session.rollback()
            return {"ok": False, "apply": apply, "counts": counts, "errors": errors}

        try:
            existing_entities = sum(
                int(_resolve_existing_entity(session, plan) is not None)
                for plan in plans.values()
            )

            entity_map: dict[str, KnowledgeEntity] = {}
            created_entities = 0
            updated_entities = 0
            for key, plan in plans.items():
                entity, created = _upsert_entity(session, plan)
                entity_map[key] = entity
                created_entities += int(created)
                updated_entities += int(not created)
            session.flush()

            created_claims = 0
            updated_claims = 0
            created_events = 0
            updated_events = 0
            created_evidence = 0
            excerpt_replacements = 0
            for key, plan in plans.items():
                new_evidence, replacements = _save_evidence(
                    session,
                    parent_kind="entity",
                    parent_id=entity_map[key].id,
                    evidence=plan.get("identity_evidence") or [],
                )
                created_evidence += new_evidence
                excerpt_replacements += replacements
            claim_results: list[dict[str, Any]] = []


            for item in claims:
                subject = entity_map[item["subject_key"]]
                object_entity = (
                    entity_map[item["object_key"]] if item["object_key"] else None
                )
                object_entity_id = object_entity.id if object_entity else None
                claim_hash = _claim_hash(
                    subject.id,
                    item["predicate"],
                    object_entity_id,
                    item["object_text"],
                    item["valid_from"],
                    item["valid_to"],
                )
                generated_key = _claim_identity_key(
                    subject.id,
                    item["predicate"],
                    object_entity_id,
                    item["object_text"],
                )

                claim: KnowledgeClaim | None = None
                identity_source = "new"
                if item["claim_id"] is not None:
                    claim = session.get(KnowledgeClaim, item["claim_id"])
                    if claim is None:
                        raise KnowledgeValidationError(
                            f"claim_id does not exist: {item['claim_id']}"
                        )
                    identity_source = "claim_id"
                elif item["claim_key"] is not None:
                    claim = session.scalar(
                        select(KnowledgeClaim).where(
                            KnowledgeClaim.claim_key == item["claim_key"]
                        )
                    )
                    identity_source = "claim_key"
                if claim is None:
                    claim = session.scalar(
                        select(KnowledgeClaim).where(
                            KnowledgeClaim.claim_hash == claim_hash
                        )
                    )
                    if claim is not None:
                        identity_source = "claim_hash"
                if claim is None and item["claim_key"] is None:
                    claim = session.scalar(
                        select(KnowledgeClaim).where(
                            KnowledgeClaim.claim_key == generated_key
                        )
                    )
                    if claim is not None:
                        identity_source = "generated_claim_key"

                created = claim is None
                if claim is None:
                    chosen_key = item["claim_key"] or generated_key
                    claim = KnowledgeClaim(
                        subject_entity_id=subject.id,
                        predicate=item["predicate"],
                        object_entity_id=object_entity_id,
                        object_text=item["object_text"],
                        claim_text=item["claim_text"],
                        evidence_type=item["evidence_type"],
                        confidence=item["confidence"],
                        status=item["status"],
                        valid_from=item["valid_from"],
                        valid_to=item["valid_to"],
                        observed_at=item["observed_at"],
                        claim_key=chosen_key,
                        claim_hash=claim_hash,
                        supersedes_claim_id=item["supersedes_claim_id"],
                        superseded_by_claim_id=item["superseded_by_claim_id"],
                        attributes_json=item["attributes"],
                        created_by=created_by[:64],
                    )
                    session.add(claim)
                    session.flush()
                else:
                    if (
                        claim.subject_entity_id != subject.id
                        or claim.predicate != item["predicate"]
                        or claim.object_entity_id != object_entity_id
                        or normalize_text(claim.object_text or "")
                        != normalize_text(item["object_text"] or "")
                    ):
                        raise KnowledgeValidationError(
                            "claim_id/claim_key refers to a different immutable "
                            "subject-predicate-object identity"
                        )
                    if (
                        item["claim_key"] is not None
                        and item["claim_key"] != claim.claim_key
                    ):
                        raise KnowledgeValidationError(
                            "claim_key is immutable for an existing claim"
                        )
                    hash_owner = session.scalar(
                        select(KnowledgeClaim.id).where(
                            KnowledgeClaim.claim_hash == claim_hash,
                            KnowledgeClaim.id != claim.id,
                        )
                    )
                    if hash_owner is not None:
                        raise KnowledgeValidationError(
                            "updated claim content would duplicate another claim"
                        )
                    claim.claim_text = item["claim_text"]
                    claim.evidence_type = item["evidence_type"]
                    claim.confidence = item["confidence"]
                    claim.status = item["status"]
                    claim.valid_from = item["valid_from"]
                    claim.valid_to = item["valid_to"]
                    claim.observed_at = item["observed_at"]
                    claim.claim_hash = claim_hash
                    claim.supersedes_claim_id = item["supersedes_claim_id"]
                    claim.superseded_by_claim_id = item["superseded_by_claim_id"]
                    claim.attributes_json = {
                        **(claim.attributes_json or {}),
                        **item["attributes"],
                    }

                if claim.supersedes_claim_id is not None:
                    previous = session.get(
                        KnowledgeClaim,
                        claim.supersedes_claim_id,
                    )
                    if previous is not None:
                        if previous.id == claim.id:
                            raise KnowledgeValidationError(
                                "A claim cannot supersede itself"
                            )
                        if (
                            previous.subject_entity_id
                            != claim.subject_entity_id
                            or previous.predicate != claim.predicate
                        ):
                            raise KnowledgeValidationError(
                                "supersedes_claim_id must refer to the same "
                                "subject and predicate"
                            )
                        previous.superseded_by_claim_id = claim.id
                        previous.status = "superseded"

                        boundary = (
                            claim.valid_from
                            or claim.observed_at
                        )
                        if boundary is not None:
                            if (
                                previous.valid_from is None
                                or boundary > previous.valid_from
                            ):
                                if (
                                    previous.valid_to is None
                                    or boundary < previous.valid_to
                                ):
                                    previous.valid_to = boundary
                if claim.superseded_by_claim_id is not None:
                    following = session.get(
                        KnowledgeClaim,
                        claim.superseded_by_claim_id,
                    )
                    if following is not None:
                        following.supersedes_claim_id = claim.id

                new_evidence, replacements = _save_evidence(
                    session,
                    parent_kind="claim",
                    parent_id=claim.id,
                    evidence=item["evidence"],
                )
                created_evidence += new_evidence
                excerpt_replacements += replacements
                created_claims += int(created)
                updated_claims += int(not created)
                claim_results.append(
                    {
                        "claim_id": str(claim.id),
                        "claim_key": claim.claim_key,
                        "predicate": claim.predicate,
                        "object_text": claim.object_text,
                        "object_entity_id": (
                            str(claim.object_entity_id)
                            if claim.object_entity_id
                            else None
                        ),
                        "status": claim.status,
                        "valid_from": (
                            claim.valid_from.isoformat()
                            if claim.valid_from
                            else None
                        ),
                        "valid_to": (
                            claim.valid_to.isoformat()
                            if claim.valid_to
                            else None
                        ),
                        "identity_source": identity_source,
                        "created": created,
                    }
                )

            for item in events:
                entity = entity_map[item["entity_key"]]
                event_hash = _event_hash(
                    entity.id,
                    item["event_type"],
                    item["title"],
                    item["occurred_at"],
                )
                event = (
                    session.get(
                        KnowledgeEvent,
                        item["event_id"],
                    )
                    if item["event_id"] is not None
                    else None
                )
                if event is not None:
                    if (
                        event.entity_id != entity.id
                        or event.event_type != item["event_type"]
                        or normalize_text(event.title)
                        != normalize_text(item["title"])
                        or event.occurred_at != item["occurred_at"]
                    ):
                        raise KnowledgeValidationError(
                            "event_id refers to a different immutable "
                            "entity-event_type-title-occurred_at identity"
                        )
                if event is None:
                    event = session.scalar(
                        select(KnowledgeEvent).where(
                            KnowledgeEvent.event_hash == event_hash
                        )
                    )
                created = event is None
                if event is None:
                    event = KnowledgeEvent(
                        entity_id=entity.id,
                        event_type=item["event_type"],
                        title=item["title"],
                        description=item["description"],
                        occurred_at=item["occurred_at"],
                        ended_at=item["ended_at"],
                        status=item["status"],
                        event_hash=event_hash,
                        attributes_json=item["attributes"],
                        created_by=created_by[:64],
                    )
                    session.add(event)
                    session.flush()
                else:
                    event.description = item["description"] or event.description
                    event.ended_at = item["ended_at"] or event.ended_at
                    event.status = item["status"]
                    event.attributes_json = {
                        **(event.attributes_json or {}),
                        **item["attributes"],
                    }
                new_evidence, replacements = _save_evidence(
                    session,
                    parent_kind="event",
                    parent_id=event.id,
                    evidence=item["evidence"],
                )
                created_evidence += new_evidence
                excerpt_replacements += replacements
                created_events += int(created)
                updated_events += int(not created)

            result = {
                "ok": True,
                "apply": apply,
                "counts": counts,
                "existing_entities": existing_entities,
                "new_entities": len(plans) - existing_entities,
                "entity_keys": sorted(plans),
                "created": {
                    "entities": created_entities,
                    "claims": created_claims,
                    "events": created_events,
                    "evidence": created_evidence,
                },
                "updated": {
                    "entities": updated_entities,
                    "claims": updated_claims,
                    "events": updated_events,
                },
                "excerpt_replacements": excerpt_replacements,
                "claims": claim_results,
                "entities": [
                    {
                        "key": key,
                        "entity_id": str(entity.id),
                        "entity_ref": f"workmind://entity/{entity.id}",
                        "entity_type": entity.entity_type,
                        "canonical_name": entity.canonical_name,
                        "external_id": entity.external_id,
                    }
                    for key, entity in entity_map.items()
                ],
            }
            if apply:
                session.commit()
                return result

            session.rollback()
            result["note"] = (
                "Full semantic dry-run passed and was rolled back. "
                "The same subject/object identity, claim/event IDs, "
                "supersession rules, evidence, and database constraints "
                "will be checked again before apply."
            )
            return result
        except (KnowledgeValidationError, IntegrityError) as exc:
            session.rollback()
            return {
                "ok": False,
                "apply": apply,
                "counts": counts,
                "errors": [
                    (
                        str(exc.orig)
                        if isinstance(exc, IntegrityError)
                        else str(exc)
                    )
                ],
            }


def _resolve_entity(
    session: Session,
    entity_ref_or_id: str,
    entity_type: str | None = None,
) -> KnowledgeEntity:
    raw = entity_ref_or_id.strip()
    if raw.startswith("workmind://entity/"):
        raw = raw.removeprefix("workmind://entity/")
    try:
        entity_id = uuid.UUID(raw)
    except ValueError:
        entity_id = None
    if entity_id is not None:
        entity = session.get(KnowledgeEntity, entity_id)
        if entity is None:
            raise KnowledgeValidationError(f"Entity not found: {entity_id}")
        return entity
    normalized = normalize_text(raw)
    statement = select(KnowledgeEntity).where(
        or_(
            KnowledgeEntity.external_id == raw,
            func.lower(KnowledgeEntity.canonical_name) == raw.casefold(),
            KnowledgeEntity.normalized_key == normalized,
        )
    )
    if entity_type:
        statement = statement.where(
            KnowledgeEntity.entity_type == entity_type.strip().lower()
        )
    matches = list(session.scalars(statement.limit(3)))
    if not matches:
        raise KnowledgeValidationError(f"Entity not found: {entity_ref_or_id!r}")
    if len(matches) > 1:
        raise KnowledgeValidationError("Entity reference is ambiguous; use entity_ref")
    return matches[0]


def _entity_summary(entity: KnowledgeEntity | None) -> dict[str, Any]:
    if entity is None:
        return {
            "entity_id": None,
            "entity_ref": None,
            "entity_type": None,
            "canonical_name": None,
            "external_id": None,
            "status": None,
        }
    return {
        "entity_id": str(entity.id),
        "entity_ref": f"workmind://entity/{entity.id}",
        "entity_type": entity.entity_type,
        "canonical_name": entity.canonical_name,
        "external_id": entity.external_id,
        "status": entity.status,
    }


def _evidence_dicts(
    session: Session,
    *,
    entity_id: uuid.UUID | None = None,
    claim_id: uuid.UUID | None = None,
    event_id: uuid.UUID | None = None,
    include_excerpt: bool = False,
) -> list[dict[str, Any]]:
    statement = select(KnowledgeEvidence)
    if entity_id is not None:
        statement = statement.where(KnowledgeEvidence.entity_id == entity_id)
    elif claim_id is not None:
        statement = statement.where(
            KnowledgeEvidence.claim_id == claim_id
        )
    elif event_id is not None:
        statement = statement.where(
            KnowledgeEvidence.event_id == event_id
        )
    else:
        raise ValueError("entity_id, claim_id, or event_id is required")

    rows = list(
        session.scalars(
            statement.order_by(
                KnowledgeEvidence.source_date,
                KnowledgeEvidence.id,
            )
        )
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {
            "evidence_id": str(row.id),
            "evidence_kind": row.evidence_kind,
            "source_type": row.source_type,
            "source_ref": row.source_ref,
            "chunk_id": str(row.chunk_id) if row.chunk_id else None,
            "metadata": dict(row.metadata_json or {}),
            "evidence_role": row.evidence_role,
            "source_date": (
                row.source_date.isoformat()
                if row.source_date
                else None
            ),
            "excerpt_chars": len(row.excerpt or ""),
        }
        if include_excerpt:
            item["excerpt"] = row.excerpt
        result.append(item)
    return result


def _claim_dict(
    session: Session,
    claim: KnowledgeClaim,
    *,
    include_evidence: bool = True,
    include_excerpt: bool = False,
) -> dict[str, Any]:
    result = {
        "claim_id": str(claim.id),
        "claim_key": claim.claim_key,
        "claim_ref": f"workmind://claim/{claim.id}",
        "subject_entity_id": str(claim.subject_entity_id),
        "predicate": claim.predicate,
        "claim_text": claim.claim_text,
        "object_type": "text",
        "object_text": claim.object_text,
        "evidence_type": claim.evidence_type,
        "confidence": claim.confidence,
        "status": claim.status,
        "valid_from": (
            claim.valid_from.isoformat()
            if claim.valid_from
            else None
        ),
        "valid_to": (
            claim.valid_to.isoformat()
            if claim.valid_to
            else None
        ),
        "observed_at": (
            claim.observed_at.isoformat()
            if claim.observed_at
            else None
        ),
        "attributes": claim.attributes_json,
        "supersedes_claim_id": (
            str(claim.supersedes_claim_id)
            if claim.supersedes_claim_id
            else None
        ),
        "superseded_by_claim_id": (
            str(claim.superseded_by_claim_id)
            if claim.superseded_by_claim_id
            else None
        ),
        "conflicting": False,
        "conflicting_claim_ids": [],
        "conflict_intervals": [],
    }
    if include_evidence:
        result["evidence"] = _evidence_dicts(
            session,
            claim_id=claim.id,
            include_excerpt=include_excerpt,
        )
    return result


def _relation_dict(
    session: Session,
    relation: KnowledgeClaim,
    *,
    include_evidence: bool = True,
    include_excerpt: bool = False,
) -> dict[str, Any]:
    from_entity = session.get(
        KnowledgeEntity,
        relation.subject_entity_id,
    )
    to_entity = (
        session.get(
            KnowledgeEntity,
            relation.object_entity_id,
        )
        if relation.object_entity_id
        else None
    )
    result: dict[str, Any] = {
        "relation_id": str(relation.id),
        "claim_key": relation.claim_key,
        "relation_type": relation.predicate,
        "from_entity": _entity_summary(from_entity),
        "from_entity_id": (
            str(from_entity.id) if from_entity else None
        ),
        "from_entity_ref": (
            f"workmind://entity/{from_entity.id}"
            if from_entity
            else None
        ),
        "from_entity_name": (
            from_entity.canonical_name
            if from_entity
            else None
        ),
        "to_entity": _entity_summary(to_entity),
        "to_entity_id": (
            str(to_entity.id) if to_entity else None
        ),
        "to_entity_ref": (
            f"workmind://entity/{to_entity.id}"
            if to_entity
            else None
        ),
        "to_entity_name": (
            to_entity.canonical_name
            if to_entity
            else None
        ),
        "claim_text": relation.claim_text,
        "evidence_type": relation.evidence_type,
        "confidence": relation.confidence,
        "status": relation.status,
        "valid_from": (
            relation.valid_from.isoformat()
            if relation.valid_from
            else None
        ),
        "valid_to": (
            relation.valid_to.isoformat()
            if relation.valid_to
            else None
        ),
        "observed_at": (
            relation.observed_at.isoformat()
            if relation.observed_at
            else None
        ),
        "attributes": relation.attributes_json,
    }
    if include_evidence:
        result["evidence"] = _evidence_dicts(
            session,
            claim_id=relation.id,
            include_excerpt=include_excerpt,
        )
    return result


def _event_dict(
    session: Session,
    event: KnowledgeEvent,
    *,
    include_evidence: bool = True,
    include_excerpt: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "event_id": str(event.id),
        "event_type": event.event_type,
        "title": event.title,
        "description": event.description,
        "occurred_at": event.occurred_at.isoformat(),
        "ended_at": (
            event.ended_at.isoformat()
            if event.ended_at
            else None
        ),
        "status": event.status,
        "attributes": event.attributes_json,
    }
    if include_evidence:
        result["evidence"] = _evidence_dicts(
            session,
            event_id=event.id,
            include_excerpt=include_excerpt,
        )
    return result


def _object_signature(claim: dict[str, Any]) -> str:
    return normalize_text(claim.get("object_text") or "")


def _mark_conflicts(claims: list[dict[str, Any]]) -> None:
    """Mark conflicting active exclusive claims with overlapping periods."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        exclusive = (
            claim["predicate"] in EXCLUSIVE_PREDICATES
            or bool(
                (claim.get("attributes") or {}).get("exclusive")
            )
        )
        if exclusive and claim.get("status") == "active":
            groups[claim["predicate"]].append(claim)

    for values in groups.values():
        for index, first in enumerate(values):
            for second in values[index + 1:]:
                if _object_signature(first) == _object_signature(second):
                    continue
                first_from = parse_datetime(first.get("valid_from"))
                first_to = parse_datetime(first.get("valid_to"))
                second_from = parse_datetime(second.get("valid_from"))
                second_to = parse_datetime(second.get("valid_to"))
                if not intervals_overlap(
                    first_from,
                    first_to,
                    second_from,
                    second_to,
                ):
                    continue
                first["conflicting"] = True
                second["conflicting"] = True
                if second["claim_id"] not in first["conflicting_claim_ids"]:
                    first["conflicting_claim_ids"].append(second["claim_id"])
                if first["claim_id"] not in second["conflicting_claim_ids"]:
                    second["conflicting_claim_ids"].append(first["claim_id"])
                interval = {
                    "with_claim_id": second["claim_id"],
                    **overlap_bounds(
                        first_from,
                        first_to,
                        second_from,
                        second_to,
                    ),
                }
                reverse_interval = {
                    "with_claim_id": first["claim_id"],
                    **overlap_bounds(
                        first_from,
                        first_to,
                        second_from,
                        second_to,
                    ),
                }
                first["conflict_intervals"].append(interval)
                second["conflict_intervals"].append(reverse_interval)


def _entity_counts(
    session: Session,
    entity_id: uuid.UUID,
) -> dict[str, int]:
    claim_count = int(
        session.scalar(
            select(func.count())
            .select_from(KnowledgeClaim)
            .where(
                KnowledgeClaim.subject_entity_id == entity_id,
                KnowledgeClaim.object_entity_id.is_(None),
            )
        )
        or 0
    )
    outgoing_count = int(
        session.scalar(
            select(func.count())
            .select_from(KnowledgeClaim)
            .where(
                KnowledgeClaim.subject_entity_id == entity_id,
                KnowledgeClaim.object_entity_id.is_not(None),
            )
        )
        or 0
    )
    incoming_count = int(
        session.scalar(
            select(func.count())
            .select_from(KnowledgeClaim)
            .where(
                KnowledgeClaim.object_entity_id == entity_id,
            )
        )
        or 0
    )
    event_count = int(
        session.scalar(
            select(func.count())
            .select_from(KnowledgeEvent)
            .where(KnowledgeEvent.entity_id == entity_id)
        )
        or 0
    )
    identity_evidence_count = int(
        session.scalar(
            select(func.count())
            .select_from(KnowledgeEvidence)
            .where(KnowledgeEvidence.entity_id == entity_id)
        )
        or 0
    )
    return {
        "claim_count": claim_count,
        "outgoing_relation_count": outgoing_count,
        "incoming_relation_count": incoming_count,
        "stored_claim_row_count": claim_count + outgoing_count,
        "event_count": event_count,
        "identity_evidence_count": identity_evidence_count,
    }


def search_entities(
    query: str,
    *,
    entity_type: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    clean = query.strip()
    if not clean:
        raise KnowledgeValidationError(
            "query must not be empty"
        )
    safe_limit = max(1, min(int(limit), 100))
    normalized = normalize_text(clean)
    pattern = f"%{clean}%"

    with SessionLocal() as session:
        statement = select(KnowledgeEntity).where(
            or_(
                KnowledgeEntity.canonical_name.ilike(pattern),
                KnowledgeEntity.external_id.ilike(pattern),
                KnowledgeEntity.normalized_key.ilike(
                    f"%{normalized}%"
                ),
            )
        )
        if entity_type:
            statement = statement.where(
                KnowledgeEntity.entity_type
                == entity_type.strip().lower()
            )
        entities = list(
            session.scalars(
                statement.order_by(
                    KnowledgeEntity.updated_at.desc()
                ).limit(safe_limit)
            )
        )
        results = []
        for entity in entities:
            item = {
                **_entity_summary(entity),
                "description": entity.description,
                "attributes": entity.attributes_json,
                **_entity_counts(session, entity.id),
            }
            results.append(item)
        return {
            "query": clean,
            "entity_type": entity_type,
            "count": len(results),
            "results": results,
        }


def get_claims(
    entity_ref_or_id: str,
    *,
    entity_type: str | None = None,
    predicate: str | None = None,
    evidence_type: str | None = None,
    status: str | None = None,
    include_conflicts: bool = True,
    include_relations: bool = False,
    include_evidence: bool = True,
    include_excerpt: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit), 1000))
    with SessionLocal() as session:
        entity = _resolve_entity(
            session,
            entity_ref_or_id,
            entity_type,
        )
        statement = select(KnowledgeClaim).where(
            KnowledgeClaim.subject_entity_id == entity.id
        )
        if not include_relations:
            statement = statement.where(
                KnowledgeClaim.object_entity_id.is_(None)
            )
        if predicate:
            statement = statement.where(
                KnowledgeClaim.predicate
                == normalize_predicate(predicate)
            )
        if evidence_type:
            statement = statement.where(
                KnowledgeClaim.evidence_type
                == evidence_type
            )
        if status:
            statement = statement.where(
                KnowledgeClaim.status == status
            )

        rows = list(
            session.scalars(
                statement.order_by(
                    KnowledgeClaim.observed_at,
                    KnowledgeClaim.created_at,
                ).limit(safe_limit)
            )
        )
        claims = [
            _claim_dict(
                session,
                claim,
                include_evidence=include_evidence,
                include_excerpt=include_excerpt,
            )
            if claim.object_entity_id is None
            else _relation_dict(
                session,
                claim,
                include_evidence=include_evidence,
                include_excerpt=include_excerpt,
            )
            for claim in rows
        ]
        ordinary_claims = [
            claim
            for claim in claims
            if "claim_id" in claim
        ]
        if include_conflicts:
            _mark_conflicts(ordinary_claims)
        return {
            **_entity_summary(entity),
            "count": len(claims),
            "claim_count": len(ordinary_claims),
            "relation_count": (
                len(claims) - len(ordinary_claims)
            ),
            "include_relations": include_relations,
            "claims": claims,
        }


def get_relations(
    entity_ref_or_id: str,
    *,
    entity_type: str | None = None,
    direction: str = "both",
    relation_type: str | None = None,
    status: str | None = None,
    include_evidence: bool = True,
    include_excerpt: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    normalized_direction = direction.strip().lower()
    if normalized_direction not in {
        "outgoing",
        "incoming",
        "both",
    }:
        raise KnowledgeValidationError(
            "direction must be outgoing, incoming, or both"
        )
    safe_limit = max(1, min(int(limit), 1000))
    normalized_relation_type = (
        normalize_predicate(relation_type)
        if relation_type
        else None
    )

    with SessionLocal() as session:
        entity = _resolve_entity(
            session,
            entity_ref_or_id,
            entity_type,
        )
        outgoing: list[dict[str, Any]] = []
        incoming: list[dict[str, Any]] = []

        if normalized_direction in {"outgoing", "both"}:
            statement = select(KnowledgeClaim).where(
                KnowledgeClaim.subject_entity_id == entity.id,
                KnowledgeClaim.object_entity_id.is_not(None),
            )
            if normalized_relation_type:
                statement = statement.where(
                    KnowledgeClaim.predicate
                    == normalized_relation_type
                )
            if status:
                statement = statement.where(
                    KnowledgeClaim.status == status
                )
            outgoing = [
                _relation_dict(
                    session,
                    relation,
                    include_evidence=include_evidence,
                    include_excerpt=include_excerpt,
                )
                for relation in session.scalars(
                    statement.order_by(
                        KnowledgeClaim.created_at,
                        KnowledgeClaim.id,
                    ).limit(safe_limit)
                )
            ]

        if normalized_direction in {"incoming", "both"}:
            statement = select(KnowledgeClaim).where(
                KnowledgeClaim.object_entity_id == entity.id
            )
            if normalized_relation_type:
                statement = statement.where(
                    KnowledgeClaim.predicate
                    == normalized_relation_type
                )
            if status:
                statement = statement.where(
                    KnowledgeClaim.status == status
                )
            incoming = [
                _relation_dict(
                    session,
                    relation,
                    include_evidence=include_evidence,
                    include_excerpt=include_excerpt,
                )
                for relation in session.scalars(
                    statement.order_by(
                        KnowledgeClaim.created_at,
                        KnowledgeClaim.id,
                    ).limit(safe_limit)
                )
            ]

        return {
            **_entity_summary(entity),
            "direction": normalized_direction,
            "relation_type": normalized_relation_type,
            "outgoing_count": len(outgoing),
            "incoming_count": len(incoming),
            "count": len(outgoing) + len(incoming),
            "outgoing": outgoing,
            "incoming": incoming,
        }


def get_timeline(
    entity_ref_or_id: str,
    *,
    entity_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_evidence: bool = True,
    include_excerpt: bool = False,
    limit: int = 500,
) -> dict[str, Any]:
    start = parse_datetime(date_from)
    end = parse_datetime(date_to)
    safe_limit = max(1, min(int(limit), 2000))

    with SessionLocal() as session:
        entity = _resolve_entity(
            session,
            entity_ref_or_id,
            entity_type,
        )

        event_statement = select(KnowledgeEvent).where(
            KnowledgeEvent.entity_id == entity.id
        )
        if start:
            event_statement = event_statement.where(
                KnowledgeEvent.occurred_at >= start
            )
        if end:
            event_statement = event_statement.where(
                KnowledgeEvent.occurred_at <= end
            )
        event_rows = list(
            session.scalars(
                event_statement.order_by(
                    KnowledgeEvent.occurred_at
                ).limit(safe_limit)
            )
        )
        events = [
            _event_dict(
                session,
                event,
                include_evidence=include_evidence,
                include_excerpt=include_excerpt,
            )
            for event in event_rows
        ]

        claim_statement = select(KnowledgeClaim).where(
            KnowledgeClaim.subject_entity_id == entity.id,
            KnowledgeClaim.object_entity_id.is_(None),
            or_(
                KnowledgeClaim.observed_at.is_not(None),
                KnowledgeClaim.valid_from.is_not(None),
            ),
        )
        claim_rows = list(
            session.scalars(
                claim_statement.order_by(
                    KnowledgeClaim.observed_at,
                    KnowledgeClaim.valid_from,
                    KnowledgeClaim.created_at,
                ).limit(safe_limit)
            )
        )
        dated_claims: list[dict[str, Any]] = []
        for claim in claim_rows:
            timeline_at = (
                claim.observed_at or claim.valid_from
            )
            if timeline_at is None:
                continue
            if start and timeline_at < start:
                continue
            if end and timeline_at > end:
                continue
            claim_value = _claim_dict(
                session,
                claim,
                include_evidence=include_evidence,
                include_excerpt=include_excerpt,
            )
            dated_claims.append(
                {
                    "timeline_at": timeline_at.isoformat(),
                    "time_source": (
                        "observed_at"
                        if claim.observed_at
                        else "valid_from"
                    ),
                    "time_semantics": (
                        "This is when the claim was observed or "
                        "became valid; it is not necessarily the "
                        "occurrence time of a real-world event."
                    ),
                    "claim": claim_value,
                }
            )

        timeline: list[dict[str, Any]] = [
            {
                "timeline_type": "event",
                "timeline_at": event["occurred_at"],
                "time_semantics": (
                    "occurred_at is the asserted occurrence "
                    "time of this event."
                ),
                "event": event,
            }
            for event in events
        ]
        timeline.extend(
            {
                "timeline_type": "dated_claim",
                **dated_claim,
            }
            for dated_claim in dated_claims
        )
        timeline.sort(
            key=lambda item: (
                item["timeline_at"],
                item["timeline_type"],
            )
        )
        timeline = timeline[:safe_limit]

        return {
            **_entity_summary(entity),
            "event_count": len(events),
            "dated_claim_count": len(dated_claims),
            "count": len(timeline),
            "events": events,
            "dated_claims": dated_claims,
            "timeline": timeline,
            "timeline_note": (
                "Events and dated claims are separate. "
                "A claim observed_at/valid_from must not be "
                "presented as the occurrence date of an event."
            ),
        }


def get_entity(
    entity_ref_or_id: str,
    *,
    entity_type: str | None = None,
    include_claims: bool = True,
    include_relations: bool = True,
    include_timeline: bool = True,
    include_evidence: bool = True,
    include_excerpt: bool = False,
) -> dict[str, Any]:
    with SessionLocal() as session:
        entity = _resolve_entity(
            session,
            entity_ref_or_id,
            entity_type,
        )
        result: dict[str, Any] = {
            **_entity_summary(entity),
            "description": entity.description,
            "attributes": entity.attributes_json,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
            **_entity_counts(session, entity.id),
        }
        if include_evidence:
            result["identity_evidence"] = _evidence_dicts(
                session,
                entity_id=entity.id,
                include_excerpt=include_excerpt,
            )

    if include_claims:
        claim_result = get_claims(
            entity_ref_or_id,
            entity_type=entity_type,
            include_relations=False,
            include_evidence=include_evidence,
            include_excerpt=include_excerpt,
        )
        result["claims"] = claim_result["claims"]

    if include_relations:
        relation_result = get_relations(
            entity_ref_or_id,
            entity_type=entity_type,
            direction="both",
            include_evidence=include_evidence,
            include_excerpt=include_excerpt,
        )
        result["outgoing_relations"] = (
            relation_result["outgoing"]
        )
        result["incoming_relations"] = (
            relation_result["incoming"]
        )
        # Backward-compatible alias: relations from this entity.
        result["relations"] = relation_result["outgoing"]

    if include_timeline:
        timeline_result = get_timeline(
            entity_ref_or_id,
            entity_type=entity_type,
            include_evidence=include_evidence,
            include_excerpt=include_excerpt,
        )
        result["events"] = timeline_result["events"]
        result["dated_claims"] = (
            timeline_result["dated_claims"]
        )
        result["timeline"] = timeline_result["timeline"]
        result["timeline_note"] = (
            timeline_result["timeline_note"]
        )

    return result


def _evidence_type_counts(
    session: Session,
    *,
    relations: bool,
) -> list[dict[str, Any]]:
    condition = (
        KnowledgeClaim.object_entity_id.is_not(None)
        if relations
        else KnowledgeClaim.object_entity_id.is_(None)
    )
    rows = session.execute(
        select(
            KnowledgeClaim.evidence_type,
            func.count(),
        )
        .where(condition)
        .group_by(KnowledgeClaim.evidence_type)
        .order_by(KnowledgeClaim.evidence_type)
    ).all()
    return [
        {
            "evidence_type": evidence_type,
            "count": int(count),
        }
        for evidence_type, count in rows
    ]


def get_status_history(
    entity_ref_or_id: str,
    *,
    entity_type: str | None = None,
    predicate: str = "current_status",
    as_of: str | None = None,
    include_evidence: bool = True,
    include_excerpt: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    """Return temporally ordered exclusive claims and current value(s)."""
    normalized_predicate = normalize_predicate(predicate)
    moment = parse_datetime(as_of) if as_of else datetime.now(UTC)
    if moment is None:
        moment = datetime.now(UTC)
    safe_limit = max(1, min(int(limit), 1000))

    with SessionLocal() as session:
        entity = _resolve_entity(
            session,
            entity_ref_or_id,
            entity_type,
        )
        rows = list(
            session.scalars(
                select(KnowledgeClaim)
                .where(
                    KnowledgeClaim.subject_entity_id == entity.id,
                    KnowledgeClaim.object_entity_id.is_(None),
                    KnowledgeClaim.predicate == normalized_predicate,
                )
                .order_by(
                    KnowledgeClaim.valid_from,
                    KnowledgeClaim.observed_at,
                    KnowledgeClaim.created_at,
                )
                .limit(safe_limit)
            )
        )
        history = [
            _claim_dict(
                session,
                claim,
                include_evidence=include_evidence,
                include_excerpt=include_excerpt,
            )
            for claim in rows
        ]
        _mark_conflicts(history)
        current: list[dict[str, Any]] = []
        for claim in history:
            valid_from = parse_datetime(claim.get("valid_from"))
            valid_to = parse_datetime(claim.get("valid_to"))
            effective = (
                claim.get("status") not in {"disputed", "rejected"}
                and interval_contains(valid_from, valid_to, moment)
            )
            claim["effective_at_as_of"] = effective
            if effective:
                current.append(claim)

        temporal_conflicts: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for claim in history:
            for other_id in claim["conflicting_claim_ids"]:
                pair = tuple(sorted((claim["claim_id"], other_id)))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                matching = next(
                    (
                        item
                        for item in claim["conflict_intervals"]
                        if item["with_claim_id"] == other_id
                    ),
                    {},
                )
                temporal_conflicts.append(
                    {
                        "claim_ids": list(pair),
                        "predicate": normalized_predicate,
                        **{
                            key: matching.get(key)
                            for key in (
                                "overlap_from",
                                "overlap_to",
                                "interval_semantics",
                            )
                        },
                    }
                )

        return {
            **_entity_summary(entity),
            "predicate": normalized_predicate,
            "as_of": moment.isoformat(),
            "interval_semantics": "[valid_from, valid_to)",
            "history_count": len(history),
            "current_count": len(current),
            "current": current,
            "history": history,
            "temporal_conflict_count": len(temporal_conflicts),
            "temporal_conflicts": temporal_conflicts,
            "note": (
                "Record status and temporal validity are separate. "
                "A superseded claim can remain historically valid for "
                "its closed interval. Conflicts require overlapping "
                "active exclusive claims with different values."
            ),
        }


def knowledge_status() -> dict[str, Any]:
    with SessionLocal() as session:
        by_type = session.execute(
            select(
                KnowledgeEntity.entity_type,
                func.count(),
            )
            .group_by(KnowledgeEntity.entity_type)
            .order_by(KnowledgeEntity.entity_type)
        ).all()

        ordinary_claim_rows = list(
            session.scalars(
                select(KnowledgeClaim).where(
                    KnowledgeClaim.object_entity_id.is_(None),
                    KnowledgeClaim.status == "active",
                )
            )
        )
        active_claims = [
            _claim_dict(
                session,
                claim,
                include_evidence=False,
            )
            for claim in ordinary_claim_rows
        ]
        grouped: dict[
            str,
            list[dict[str, Any]],
        ] = defaultdict(list)
        for claim in active_claims:
            grouped[
                str(claim["subject_entity_id"])
            ].append(claim)

        conflict_ids: set[str] = set()
        for claims in grouped.values():
            _mark_conflicts(claims)
            conflict_ids.update(
                claim["claim_id"]
                for claim in claims
                if claim["conflicting"]
            )

        entity_total = int(
            session.scalar(
                select(func.count())
                .select_from(KnowledgeEntity)
            )
            or 0
        )
        claim_total = int(
            session.scalar(
                select(func.count())
                .select_from(KnowledgeClaim)
                .where(
                    KnowledgeClaim.object_entity_id.is_(None)
                )
            )
            or 0
        )
        relation_total = int(
            session.scalar(
                select(func.count())
                .select_from(KnowledgeClaim)
                .where(
                    KnowledgeClaim.object_entity_id.is_not(None)
                )
            )
            or 0
        )
        event_total = int(
            session.scalar(
                select(func.count())
                .select_from(KnowledgeEvent)
            )
            or 0
        )
        evidence_total = int(
            session.scalar(
                select(func.count())
                .select_from(KnowledgeEvidence)
            )
            or 0
        )

        return {
            "ok": True,
            "entities": {
                "total": entity_total,
                "by_type": [
                    {
                        "entity_type": entity_type,
                        "count": int(count),
                    }
                    for entity_type, count in by_type
                ],
            },
            "claims": {
                "total": claim_total,
                "by_evidence_type": (
                    _evidence_type_counts(
                        session,
                        relations=False,
                    )
                ),
                "conflicting": len(conflict_ids),
            },
            "relations": {
                "total": relation_total,
                "by_evidence_type": (
                    _evidence_type_counts(
                        session,
                        relations=True,
                    )
                ),
            },
            "stored_claim_rows": {
                "total": claim_total + relation_total,
                "implementation_note": (
                    "Relations are stored as KnowledgeClaim rows "
                    "whose object_entity_id is not null."
                ),
            },
            "events": event_total,
            "evidence": evidence_total,
        }
