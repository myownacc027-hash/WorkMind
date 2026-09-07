"""Bounded entity resolution for knowledge extraction packets.

Identity candidates are context for entity reuse and deduplication. They are
deliberately resolved independently from the target packet's evidence scope.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from packages.common.config import Settings
from packages.database.models import (
    KnowledgeClaim,
    KnowledgeEntity,
    KnowledgeEvent,
    KnowledgeEvidence,
)
from packages.knowledge.service import normalize_text
from packages.search.hybrid import search_hybrid
from packages.search.ranking import anchor_tokens, informative_query_tokens

SemanticSearch = Callable[..., dict[str, Any]]

_MARKED_ID_RE = re.compile(r"(?<!\w)[#№]\s*(\d{4,12})(?![\w-])")
_TYPED_ID_RE = re.compile(
    r"(?<!\w)(task|задач[аиу]?|б24|incident|инцидент[ау]?|"
    r"project|проект[ау]?|ticket|тикет[ау]?|bug|дефект[ау]?)"
    r"\s*(?:id\s*)?[:=#№-]?\s*(\d{4,12})(?![\w-])",
    re.IGNORECASE,
)
_URL_ID_RE = re.compile(
    r"/(tasks?|incidents?|projects?)/(?:view/)?(\d{4,12})(?:/|\b)",
    re.IGNORECASE,
)
_STABLE_TECHNICAL_ID_RE = re.compile(
    r"(?<!\w)(?=[A-Za-zА-Яа-я0-9_.:/-]{4,64}(?!\w))"
    r"(?=[A-Za-zА-Яа-я0-9_.:/-]*[A-Za-zА-Яа-я])"
    r"(?=[A-Za-zА-Яа-я0-9_.:/-]*\d)"
    r"[A-Za-zА-Яа-я0-9_.:/-]+"
)
_TECHNICAL_TOKEN_RE = re.compile(
    r"(?<!\w)(?=[\w./:-]{4,64}(?!\w))"
    r"(?=[\w./:-]*[A-Za-zА-Яа-я])(?=[\w./:-]*[\d_./:-])"
    r"[\w./:-]+",
    re.UNICODE,
)
_ISO_DATE_RE = re.compile(
    r"(?<!\d)\d{4}-\d{2}-\d{2}"
    r"(?:[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?(?!\d)"
)
_GENERIC_ACRONYMS = {
    "dit",
    "it",
    "llc",
    "moscow",
    "spb",
    "дит",
    "ит",
    "мск",
    "ооо",
    "пао",
    "спб",
    "зао",
}
_GENERIC_TOKENS = {
    "application",
    "business",
    "company",
    "component",
    "process",
    "processing",
    "project",
    "service",
    "status",
    "system",
    "task",
    "бизнес",
    "задача",
    "задачи",
    "задачу",
    "изменение",
    "компания",
    "обработка",
    "обработки",
    "обработку",
    "процесс",
    "процесса",
    "процессы",
    "проект",
    "сервис",
    "система",
    "системы",
    "систему",
    "статус",
}
_EXCLUSIVE_TEMPORAL_PREDICATES = {
    "assignee",
    "current_status",
    "decision",
    "deadline",
    "owner",
    "status",
}
_CLOSE_ONTOLOGY_TYPES = {frozenset(("system", "process"))}


@dataclass(frozen=True, slots=True)
class ExternalIdSignal:
    value: str
    entity_types: tuple[str, ...]
    reason: str


_TYPED_ENTITY_TYPES = {
    "task": ("task",),
    "задача": ("task",),
    "задачи": ("task",),
    "задачу": ("task",),
    "б24": ("task",),
    "ticket": ("task", "incident"),
    "тикет": ("task", "incident"),
    "тикета": ("task", "incident"),
    "incident": ("incident",),
    "инцидент": ("incident",),
    "инцидента": ("incident",),
    "project": ("project",),
    "проект": ("project",),
    "проекта": ("project",),
    "bug": ("incident",),
    "дефект": ("incident",),
    "дефекта": ("incident",),
}


def extract_external_id_signals(
    texts: Iterable[str],
    *,
    limit: int = 30,
) -> list[ExternalIdSignal]:
    signals: list[ExternalIdSignal] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    def add(value: str, entity_types: tuple[str, ...], reason: str) -> None:
        key = (value.casefold(), entity_types)
        if key in seen or len(signals) >= limit:
            return
        seen.add(key)
        signals.append(ExternalIdSignal(value, entity_types, reason))

    for original in texts:
        text = _ISO_DATE_RE.sub(" ", original)
        for match in _MARKED_ID_RE.finditer(text):
            add(match.group(1), (), "explicit #/№ identifier")
        for match in _TYPED_ID_RE.finditer(text):
            prefix = normalize_text(match.group(1))
            add(
                match.group(2),
                _TYPED_ENTITY_TYPES.get(prefix, ()),
                f"typed identifier prefix {match.group(1)!r}",
            )
        for match in _URL_ID_RE.finditer(text):
            prefix = match.group(1).casefold().rstrip("s")
            add(
                match.group(2),
                _TYPED_ENTITY_TYPES.get(prefix, ()),
                f"typed URL path {match.group(1)!r}",
            )
        for match in _STABLE_TECHNICAL_ID_RE.finditer(text):
            token = match.group(0).strip("._:/-")
            if token and not token.isdigit():
                add(token, (), "stable mixed technical identifier")
    return signals


def extract_external_ids(texts: Iterable[str], *, limit: int = 30) -> list[str]:
    return [signal.value for signal in extract_external_id_signals(texts, limit=limit)]


def _technical_tokens(text: str) -> set[str]:
    values = {item.casefold() for item in _TECHNICAL_TOKEN_RE.findall(text)}
    values.update(
        token
        for token in anchor_tokens(text)
        if len(token) >= 4 and token not in _GENERIC_ACRONYMS
    )
    return {value for value in values if value not in _GENERIC_ACRONYMS}


def _significant_tokens(text: str, *, limit: int = 24) -> tuple[str, ...]:
    values = {
        token
        for token in informative_query_tokens(text)
        if len(token) >= 4 and token not in _GENERIC_TOKENS
    }
    return tuple(sorted(values, key=lambda item: (-len(item), item))[:limit])


def _token_roots(tokens: Iterable[str]) -> set[str]:
    return {token[:6] if len(token) >= 7 else token for token in tokens}


def _type_compatibility(
    entity: KnowledgeEntity | dict[str, Any],
    signals: Iterable[ExternalIdSignal],
) -> str | None:
    external_id = str(
        getattr(entity, "external_id", None)
        if isinstance(entity, KnowledgeEntity)
        else entity.get("external_id")
        or ""
    ).casefold()
    entity_type = str(
        getattr(entity, "entity_type", "")
        if isinstance(entity, KnowledgeEntity)
        else entity.get("entity_type")
        or ""
    ).casefold()
    matching = [
        signal
        for signal in signals
        if external_id and signal.value.casefold() == external_id
    ]
    typed = [signal for signal in matching if signal.entity_types]
    if typed:
        return (
            "typed_match"
            if any(entity_type in signal.entity_types for signal in typed)
            else "typed_conflict"
        )
    if matching:
        return "untyped_match"
    return None


def _set_type_context(
    candidates: dict[uuid.UUID, dict[str, Any]],
    entity: KnowledgeEntity,
    signals: Iterable[ExternalIdSignal],
) -> None:
    item = candidates.get(entity.id)
    if item is None:
        return
    compatibility = _type_compatibility(entity, signals)
    item["identity_match"]["type_compatibility"] = compatibility
    if compatibility == "typed_conflict":
        reason = "candidate entity_type conflicts with typed external-id context"
        if reason not in item["identity_match"]["reasons"]:
            item["identity_match"]["reasons"].append(reason)


def _exact_name_phrases(source_text: str, *, limit: int) -> set[str]:
    tokens = normalize_text(source_text).split()
    phrases: set[str] = set()
    if not tokens:
        return phrases
    max_width = min(12, len(tokens))
    per_width = max(2, limit // max_width)
    for width in range(1, max_width + 1):
        window_count = len(tokens) - width + 1
        if window_count <= per_width:
            indexes = range(window_count)
        else:
            indexes = {
                round(index * (window_count - 1) / (per_width - 1))
                for index in range(per_width)
            }
        for index in indexes:
            window = tokens[index : index + width]
            if width == 1 and (
                window[0].isdigit()
                or window[0] in _GENERIC_TOKENS
                or len(window[0]) < 2
            ):
                continue
            phrases.add(" ".join(window)[:255])
            if len(phrases) >= limit:
                return phrases
    return phrases


def _exact_candidates(
    session: Session,
    texts: list[str],
    *,
    settings: Settings,
) -> tuple[list[KnowledgeEntity], list[ExternalIdSignal], set[str]]:
    signals = extract_external_id_signals(texts)
    phrases = _exact_name_phrases(
        "\n".join(texts),
        limit=settings.knowledge_identity_exact_phrase_limit,
    )
    conditions = []
    for signal in signals:
        condition = KnowledgeEntity.external_id == signal.value
        if signal.entity_types:
            condition = condition & KnowledgeEntity.entity_type.in_(
                signal.entity_types
            )
        conditions.append(condition)
    if phrases:
        conditions.append(KnowledgeEntity.normalized_key.in_(phrases))
    if not conditions:
        return [], signals, phrases
    entities = list(
        session.scalars(
            select(KnowledgeEntity)
            .where(or_(*conditions))
            .order_by(
                (KnowledgeEntity.status == "active").desc(),
                KnowledgeEntity.updated_at.desc(),
                KnowledgeEntity.id,
            )
            .limit(settings.knowledge_identity_exact_query_limit)
        )
    )
    return entities, signals, phrases


def lexical_identity_score(
    source_text: str,
    entity: KnowledgeEntity,
    *,
    external_id_signals: Iterable[ExternalIdSignal] | None = None,
) -> tuple[float, list[str]]:
    """Score an entity name conservatively against extraction source text."""
    normalized_source = normalize_text(source_text)
    normalized_name = normalize_text(entity.canonical_name)
    normalized_key = normalize_text(entity.normalized_key or "")
    description = normalize_text(entity.description or "")
    external_id = normalize_text(entity.external_id or "")
    reasons: list[str] = []

    signals = list(
        external_id_signals
        if external_id_signals is not None
        else extract_external_id_signals([source_text])
    )
    matching_signals = [
        signal
        for signal in signals
        if external_id and signal.value.casefold() == external_id.casefold()
    ]
    compatible_signals = [
        signal
        for signal in matching_signals
        if not signal.entity_types or entity.entity_type in signal.entity_types
    ]
    if compatible_signals:
        typed = any(signal.entity_types for signal in compatible_signals)
        return 1.0, [
            "exact typed external_id in source"
            if typed
            else "exact untyped external_id in source"
        ]
    if matching_signals:
        reasons.append(
            "external_id occurs only in a conflicting typed context"
        )
        return 0.0, reasons
    if normalized_name and normalized_name in normalized_source:
        return 0.98, ["exact normalized canonical_name phrase in source"]
    if normalized_key and normalized_key in normalized_source:
        return 0.97, ["exact normalized entity key phrase in source"]

    name_tokens = _significant_tokens(normalized_name)
    matched = [token for token in name_tokens if token in normalized_source]
    technical = _technical_tokens(
        entity.canonical_name + " " + (entity.external_id or "")
    )
    matched_technical = [
        token for token in technical if token in source_text.casefold()
    ]
    if len(matched) < 2 and not matched_technical:
        return 0.0, []

    coverage = len(matched) / len(name_tokens) if name_tokens else 0.0
    informative_length = min(sum(len(token) for token in matched) / 24.0, 1.0)
    description_tokens = _significant_tokens(description, limit=12)
    description_hits = sum(
        token in normalized_source for token in description_tokens
    )
    description_score = (
        description_hits / len(description_tokens) if description_tokens else 0.0
    )
    score = (
        0.14
        + 0.58 * coverage
        + 0.18 * informative_length
        + 0.10 * description_score
    )
    if matched_technical:
        score = max(score, 0.86)
        reasons.append("stable technical identifier matched")
    reasons.append(
        f"{len(matched)}/{len(name_tokens)} informative name tokens matched"
    )
    return min(score, 0.95), reasons


def _claim_item(claim: KnowledgeClaim) -> dict[str, Any]:
    return {
        "claim_id": str(claim.id),
        "claim_key": claim.claim_key,
        "predicate": claim.predicate,
        "object_text": claim.object_text,
        "object_entity_id": (
            str(claim.object_entity_id) if claim.object_entity_id else None
        ),
        "status": claim.status,
        "valid_from": claim.valid_from.isoformat() if claim.valid_from else None,
        "valid_to": claim.valid_to.isoformat() if claim.valid_to else None,
        "observed_at": claim.observed_at.isoformat() if claim.observed_at else None,
        "supersedes_claim_id": (
            str(claim.supersedes_claim_id) if claim.supersedes_claim_id else None
        ),
        "superseded_by_claim_id": (
            str(claim.superseded_by_claim_id)
            if claim.superseded_by_claim_id
            else None
        ),
    }


def _candidate_shell(entity: KnowledgeEntity) -> dict[str, Any]:
    return {
        "entity_id": str(entity.id),
        "entity_ref": f"workmind://entity/{entity.id}",
        "entity_type": entity.entity_type,
        "canonical_name": entity.canonical_name,
        "external_id": entity.external_id,
        "status": entity.status,
        "description": entity.description,
        "identity_match": {"methods": [], "score": 0.0, "reasons": []},
        "mandatory_active_claims": [],
        "non_temporal_active_claims": [],
        "active_claims": [],
        "recent_claims": [],
        "recent_events": [],
    }


def _add_match(
    candidates: dict[uuid.UUID, dict[str, Any]],
    entity: KnowledgeEntity,
    *,
    method: str,
    score: float,
    reasons: Iterable[str],
) -> None:
    item = candidates.setdefault(entity.id, _candidate_shell(entity))
    match = item["identity_match"]
    if method not in match["methods"]:
        match["methods"].append(method)
    match["score"] = max(float(match["score"]), round(float(score), 6))
    for reason in reasons:
        if reason not in match["reasons"]:
            match["reasons"].append(reason)


def _lexical_pool(
    session: Session,
    source_text: str,
    *,
    scan_limit: int,
) -> list[KnowledgeEntity]:
    tokens = _significant_tokens(source_text)
    technical = sorted(
        _technical_tokens(source_text),
        key=lambda item: (-len(item), item),
    )
    search_tokens = tuple(dict.fromkeys([*technical[:12], *tokens[:16]]))
    if not search_tokens:
        return []
    conditions = []
    for token in search_tokens:
        pattern = f"%{token}%"
        conditions.extend(
            [
                KnowledgeEntity.canonical_name.ilike(pattern),
                KnowledgeEntity.normalized_key.ilike(pattern),
                KnowledgeEntity.external_id.ilike(pattern),
                KnowledgeEntity.description.ilike(pattern),
            ]
        )
    return list(
        session.scalars(
            select(KnowledgeEntity)
            .where(KnowledgeEntity.status == "active", or_(*conditions))
            .order_by(KnowledgeEntity.updated_at.desc(), KnowledgeEntity.id)
            .limit(scan_limit)
        )
    )


def _semantic_entities(
    session: Session,
    source_text: str,
    *,
    settings: Settings,
    semantic_search: SemanticSearch,
) -> tuple[list[tuple[KnowledgeEntity, float, str]], list[str], int]:
    try:
        result = semantic_search(
            source_text[:6000],
            mode="semantic",
            source_type="all",
            # search_hybrid limit is the number of diversified sources, not
            # the physical Qdrant point count.
            limit=settings.knowledge_identity_semantic_source_limit,
            min_score=settings.knowledge_identity_semantic_threshold,
            snippets_per_source=min(
                10,
                settings.knowledge_identity_semantic_chunk_limit,
            ),
        )
    except Exception as exc:
        return (
            [],
            [f"Semantic identity channel failed: {type(exc).__name__}: {exc}"],
            0,
        )

    warnings = [str(item) for item in result.get("warnings", [])]
    chunk_scores: dict[uuid.UUID, float] = {}
    source_rows = list(result.get("results", []))
    if len(source_rows) > settings.knowledge_identity_semantic_source_limit:
        warnings.append(
            "Semantic source results were truncated to the configured "
            "identity source limit."
        )
    snippet_limit_reached = False
    for source in source_rows[
        : settings.knowledge_identity_semantic_source_limit
    ]:
        matched_chunks = list(source.get("matched_chunks", []))
        if len(matched_chunks) > 10:
            snippet_limit_reached = True
        for chunk in matched_chunks[:10]:
            try:
                chunk_id = uuid.UUID(str(chunk.get("chunk_id")))
            except (TypeError, ValueError):
                continue
            score = float(
                chunk.get("semantic_score") or chunk.get("score") or 0.0
            )
            if score >= settings.knowledge_identity_semantic_threshold:
                chunk_scores[chunk_id] = max(
                    chunk_scores.get(chunk_id, 0.0),
                    score,
                )
    if snippet_limit_reached:
        warnings.append(
            "Semantic snippets per source exceeded the search contract and "
            "were truncated to 10."
        )
    ranked_chunks = sorted(
        chunk_scores,
        key=lambda chunk_id: (-chunk_scores[chunk_id], str(chunk_id)),
    )
    if len(ranked_chunks) > settings.knowledge_identity_semantic_chunk_limit:
        warnings.append(
            "Semantic matched chunks were truncated to the configured "
            "identity chunk limit after score ordering."
        )
        ranked_chunks = ranked_chunks[
            : settings.knowledge_identity_semantic_chunk_limit
        ]
        chunk_scores = {
            chunk_id: chunk_scores[chunk_id] for chunk_id in ranked_chunks
        }
    if not chunk_scores:
        return [], warnings, 0

    chunk_rank = {chunk_id: rank for rank, chunk_id in enumerate(ranked_chunks)}
    score_order = case(
        *(
            (KnowledgeEvidence.chunk_id == chunk_id, rank)
            for chunk_id, rank in chunk_rank.items()
        ),
        else_=len(chunk_rank),
    )
    rows = session.execute(
        select(KnowledgeEvidence, KnowledgeClaim, KnowledgeEvent)
        .outerjoin(
            KnowledgeClaim,
            KnowledgeEvidence.claim_id == KnowledgeClaim.id,
        )
        .outerjoin(
            KnowledgeEvent,
            KnowledgeEvidence.event_id == KnowledgeEvent.id,
        )
        .where(KnowledgeEvidence.chunk_id.in_(ranked_chunks))
        .order_by(score_order, KnowledgeEvidence.id)
        .limit(settings.knowledge_identity_semantic_evidence_limit + 1)
    ).all()
    if len(rows) > settings.knowledge_identity_semantic_evidence_limit:
        warnings.append(
            "KnowledgeEvidence fan-out was truncated to the configured "
            "identity evidence limit."
        )
        rows = rows[: settings.knowledge_identity_semantic_evidence_limit]
    entity_scores: dict[uuid.UUID, tuple[float, str]] = {}
    entity_limit_reached = False
    for evidence, claim, event in rows:
        entity_ids: list[uuid.UUID] = []
        if claim is not None:
            entity_ids.append(claim.subject_entity_id)
            if claim.object_entity_id is not None:
                entity_ids.append(claim.object_entity_id)
        if event is not None:
            entity_ids.append(event.entity_id)
        score = chunk_scores[evidence.chunk_id]
        for entity_id in entity_ids:
            current = entity_scores.get(entity_id)
            if current is None or score > current[0]:
                if (
                    current is None
                    and len(entity_scores)
                    >= settings.knowledge_identity_semantic_entity_limit
                ):
                    entity_limit_reached = True
                    continue
                entity_scores[entity_id] = (score, str(evidence.chunk_id))

    if entity_limit_reached:
        warnings.append(
            "Semantic claim/event entity fan-out was truncated to the "
            "configured identity entity limit."
        )

    if not entity_scores:
        return [], warnings, len(chunk_scores)

    entities = {
        entity.id: entity
        for entity in session.scalars(
            select(KnowledgeEntity).where(
                KnowledgeEntity.id.in_(list(entity_scores))
            ).limit(settings.knowledge_identity_semantic_entity_limit)
        )
    }
    return [
        (entities[entity_id], score, chunk_id)
        for entity_id, (score, chunk_id) in entity_scores.items()
        if entity_id in entities
    ], warnings, len(chunk_scores)


def _enrich_context(
    session: Session,
    candidates: list[dict[str, Any]],
    *,
    settings: Settings,
) -> list[str]:
    if not candidates:
        return []
    blocking_errors: list[str] = []
    for item in candidates:
        entity_id = uuid.UUID(item["entity_id"])
        context_warnings: list[str] = []
        mandatory_claims = list(
            session.scalars(
                select(KnowledgeClaim)
                .where(
                    KnowledgeClaim.subject_entity_id == entity_id,
                    KnowledgeClaim.status == "active",
                    KnowledgeClaim.predicate.in_(
                        _EXCLUSIVE_TEMPORAL_PREDICATES
                    ),
                )
                .order_by(
                    KnowledgeClaim.predicate,
                    KnowledgeClaim.updated_at.desc(),
                    KnowledgeClaim.id,
                )
                .limit(
                    settings.knowledge_identity_mandatory_claims_safety_cap
                    + 1
                )
            )
        )
        mandatory_overflow = (
            len(mandatory_claims)
            > settings.knowledge_identity_mandatory_claims_safety_cap
        )
        if mandatory_overflow:
            error = (
                f"Mandatory active temporal context exceeds safety cap for "
                f"entity {entity_id}: at least {len(mandatory_claims)} rows; "
                "extraction is blocked until the anomaly is resolved."
            )
            blocking_errors.append(error)
            context_warnings.append(error)
            mandatory_claims = mandatory_claims[
                : settings.knowledge_identity_mandatory_claims_safety_cap
            ]
        non_temporal_claims = list(
            session.scalars(
                select(KnowledgeClaim)
                .where(
                    KnowledgeClaim.subject_entity_id == entity_id,
                    KnowledgeClaim.status == "active",
                    ~KnowledgeClaim.predicate.in_(
                        _EXCLUSIVE_TEMPORAL_PREDICATES
                    ),
                )
                .order_by(KnowledgeClaim.updated_at.desc(), KnowledgeClaim.id)
                .limit(settings.knowledge_identity_active_claims_per_entity + 1)
            )
        )
        if len(non_temporal_claims) > settings.knowledge_identity_active_claims_per_entity:
            context_warnings.append(
                "Non-temporal active claims were truncated to the per-entity "
                "limit."
            )
            non_temporal_claims = non_temporal_claims[
                : settings.knowledge_identity_active_claims_per_entity
            ]
        historical_claims = list(
            session.scalars(
                select(KnowledgeClaim)
                .where(
                    KnowledgeClaim.subject_entity_id == entity_id,
                    KnowledgeClaim.status != "active",
                )
                .order_by(KnowledgeClaim.updated_at.desc(), KnowledgeClaim.id)
                .limit(settings.knowledge_identity_recent_claims_per_entity + 1)
            )
        )
        if len(historical_claims) > settings.knowledge_identity_recent_claims_per_entity:
            context_warnings.append(
                "Historical claims were truncated to the per-entity limit."
            )
            historical_claims = historical_claims[
                : settings.knowledge_identity_recent_claims_per_entity
            ]
        events = list(
            session.scalars(
                select(KnowledgeEvent)
                .where(KnowledgeEvent.entity_id == entity_id)
                .order_by(KnowledgeEvent.occurred_at.desc(), KnowledgeEvent.id)
                .limit(settings.knowledge_identity_recent_events_per_entity + 1)
            )
        )
        if len(events) > settings.knowledge_identity_recent_events_per_entity:
            context_warnings.append(
                "Events were truncated to the per-entity limit."
            )
            events = events[: settings.knowledge_identity_recent_events_per_entity]

        mandatory_items = [_claim_item(claim) for claim in mandatory_claims]
        non_temporal_items = [
            _claim_item(claim) for claim in non_temporal_claims
        ]
        item["mandatory_active_claims"] = mandatory_items
        item["non_temporal_active_claims"] = non_temporal_items
        item["active_claims"] = [*mandatory_items, *non_temporal_items]
        item["recent_claims"] = [
            _claim_item(claim) for claim in historical_claims
        ]
        item["recent_events"] = [
            {
                "event_id": str(event.id),
                "event_type": event.event_type,
                "title": event.title,
                "occurred_at": event.occurred_at.isoformat(),
                "ended_at": (
                    event.ended_at.isoformat() if event.ended_at else None
                ),
                "status": event.status,
            }
            for event in events
        ]
        item["context_diagnostics"] = {
            "mandatory_temporal_predicates": sorted(
                _EXCLUSIVE_TEMPORAL_PREDICATES
            ),
            "mandatory_claim_safety_cap": (
                settings.knowledge_identity_mandatory_claims_safety_cap
            ),
            "mandatory_claim_count": len(mandatory_items),
            "mandatory_context_complete": not mandatory_overflow,
            "non_temporal_active_claim_limit": (
                settings.knowledge_identity_active_claims_per_entity
            ),
            "historical_claim_limit": settings.knowledge_identity_recent_claims_per_entity,
            "event_limit": settings.knowledge_identity_recent_events_per_entity,
            "warnings": context_warnings,
        }
    return blocking_errors


def resolve_identity_candidates(
    session: Session,
    texts: list[str],
    *,
    settings: Settings,
    semantic_search: SemanticSearch | None = None,
) -> dict[str, Any]:
    """Resolve a bounded, ranked, evidence-backed entity identity context."""
    source_text = "\n".join(text for text in texts if text).strip()
    candidates: dict[uuid.UUID, dict[str, Any]] = {}
    diagnostics: dict[str, Any] = {
        "exact_candidate_hits": 0,
        "semantic_candidate_hits": 0,
        "semantic_chunk_hits": 0,
        "lexical_candidate_hits": 0,
        "merged_identity_candidate_count": 0,
        "typed_external_id_context": [],
        "untyped_external_id_ambiguities": [],
        "blocking_errors": [],
        "warnings": [],
    }
    if not source_text:
        return {"candidates": [], "diagnostics": diagnostics}
    normalized_source = normalize_text(source_text)

    exact_entities, external_signals, exact_phrases = _exact_candidates(
        session,
        texts,
        settings=settings,
    )
    diagnostics["typed_external_id_context"] = [
        {
            "external_id": signal.value,
            "entity_types": list(signal.entity_types),
            "reason": signal.reason,
        }
        for signal in external_signals
        if signal.entity_types
    ]
    signal_values = {signal.value.casefold() for signal in external_signals}
    for entity in exact_entities:
        compatibility = _type_compatibility(entity, external_signals)
        if (
            entity.external_id
            and entity.external_id.casefold() in signal_values
            and compatibility != "typed_conflict"
        ):
            _add_match(
                candidates,
                entity,
                method="exact_external_id",
                score=1.0,
                reasons=[f"external_id {entity.external_id!r} occurs in source"],
            )
        normalized_key = normalize_text(entity.normalized_key or "")
        normalized_name = normalize_text(entity.canonical_name)
        if (
            compatibility != "typed_conflict"
            and (
                normalized_key in exact_phrases
                or normalized_name in exact_phrases
            )
        ):
            _add_match(
                candidates,
                entity,
                method="exact_normalized_name",
                score=0.98,
                reasons=["exact normalized entity key occurs in source"],
            )
        _set_type_context(candidates, entity, external_signals)

    lexical_entities = _lexical_pool(
        session,
        source_text,
        scan_limit=settings.knowledge_identity_lexical_scan_limit,
    )
    for entity in lexical_entities:
        score, reasons = lexical_identity_score(
            source_text,
            entity,
            external_id_signals=external_signals,
        )
        if score < settings.knowledge_identity_lexical_min_score:
            continue
        _add_match(
            candidates,
            entity,
            method="lexical_name",
            score=score,
            reasons=reasons,
        )
        _set_type_context(candidates, entity, external_signals)
        normalized_name = normalize_text(entity.canonical_name)
        normalized_key = normalize_text(entity.normalized_key or "")
        if (
            normalized_name
            and normalized_name in normalized_source
            or normalized_key
            and normalized_key in normalized_source
        ):
            _add_match(
                candidates,
                entity,
                method="exact_normalized_name",
                score=max(score, 0.97),
                reasons=["exact normalized entity name or key occurs in source"],
            )

    semantic_rows, semantic_warnings, chunk_hits = _semantic_entities(
        session,
        source_text,
        settings=settings,
        semantic_search=semantic_search or search_hybrid,
    )
    for entity, score, chunk_id in semantic_rows:
        compatibility = _type_compatibility(entity, external_signals)
        semantic_score = min(score, 0.95) if compatibility == "typed_conflict" else score
        _add_match(
            candidates,
            entity,
            method="semantic_evidence",
            score=semantic_score,
            reasons=[
                f"historical evidence chunk {chunk_id} is semantically similar"
            ],
        )
        _set_type_context(candidates, entity, external_signals)

    diagnostics["exact_candidate_hits"] = sum(
        "exact_external_id" in item["identity_match"]["methods"]
        or "exact_normalized_name" in item["identity_match"]["methods"]
        for item in candidates.values()
    )
    diagnostics["lexical_candidate_hits"] = sum(
        "lexical_name" in item["identity_match"]["methods"]
        for item in candidates.values()
    )
    diagnostics["semantic_candidate_hits"] = len(semantic_rows)
    diagnostics["semantic_chunk_hits"] = chunk_hits
    diagnostics["warnings"] = semantic_warnings

    ranked = sorted(
        candidates.values(),
        key=lambda item: (
            -float(item["identity_match"]["score"]),
            item["canonical_name"].casefold(),
            item["entity_id"],
        ),
    )[: settings.knowledge_identity_max_candidates]
    for item in ranked:
        item["identity_match"]["methods"].sort()
    context_errors = _enrich_context(session, ranked, settings=settings)
    diagnostics["blocking_errors"] = context_errors
    untyped_values = {
        signal.value.casefold()
        for signal in external_signals
        if not signal.entity_types
    }
    for external_id in sorted(untyped_values):
        matches = [
            item
            for item in ranked
            if str(item.get("external_id") or "").casefold() == external_id
        ]
        entity_types = sorted({str(item["entity_type"]) for item in matches})
        if len(entity_types) > 1:
            diagnostics["untyped_external_id_ambiguities"].append(
                {
                    "external_id": external_id,
                    "entity_types": entity_types,
                    "candidate_entity_ids": [item["entity_id"] for item in matches],
                }
            )
    diagnostics["merged_identity_candidate_count"] = len(ranked)
    diagnostics["channel_limits"] = {
        "exact_entities": settings.knowledge_identity_exact_query_limit,
        "exact_phrases": settings.knowledge_identity_exact_phrase_limit,
        "semantic_sources": settings.knowledge_identity_semantic_source_limit,
        "semantic_chunks": settings.knowledge_identity_semantic_chunk_limit,
        "semantic_evidence_rows": settings.knowledge_identity_semantic_evidence_limit,
        "semantic_entities": settings.knowledge_identity_semantic_entity_limit,
        "lexical_entities": settings.knowledge_identity_lexical_scan_limit,
        "merged_candidates": settings.knowledge_identity_max_candidates,
    }
    return {"candidates": ranked, "diagnostics": diagnostics}


def duplicate_risk_diagnostics(
    bundle: dict[str, Any],
    identity_candidates: list[dict[str, Any]],
    *,
    threshold: float,
) -> dict[str, Any]:
    """Identify new proposed entities that overlap strong WorkMind candidates."""
    risks: list[dict[str, Any]] = []
    strong = [
        candidate
        for candidate in identity_candidates
        if float(
            candidate.get("identity_match", {}).get("score") or 0.0
        )
        >= threshold
        and candidate.get("identity_match", {}).get("type_compatibility")
        != "typed_conflict"
    ]
    for proposed in bundle.get("entities", []):
        if not isinstance(proposed, dict) or proposed.get("entity_id"):
            continue
        proposed_name = normalize_text(
            str(proposed.get("canonical_name") or "")
        )
        proposed_type = str(proposed.get("entity_type") or "").strip().lower()
        if not proposed_name:
            continue
        for candidate in strong:
            candidate_type = str(candidate.get("entity_type") or "").lower()
            if proposed_type and candidate_type and proposed_type != candidate_type:
                continue
            similarity, similarity_reasons = _identity_similarity(
                proposed,
                candidate,
            )
            methods = candidate.get("identity_match", {}).get("methods", [])
            if similarity < threshold:
                continue
            risks.append(
                {
                    "proposed_key": proposed.get("key"),
                    "proposed_canonical_name": proposed.get("canonical_name"),
                    "candidate_entity_id": candidate.get("entity_id"),
                    "candidate_canonical_name": candidate.get("canonical_name"),
                    "candidate_score": candidate.get("identity_match", {}).get(
                        "score"
                    ),
                    "identity_similarity": round(similarity, 6),
                    "methods": methods,
                    "similarity_reasons": similarity_reasons,
                    "reason": (
                        "A new entity overlaps a strong WorkMind identity "
                        "candidate."
                    ),
                }
            )
    candidate_ids = sorted(
        {str(item["candidate_entity_id"]) for item in risks}
    )
    return {
        "detected": bool(risks),
        "count": len(risks),
        "candidate_entity_ids": candidate_ids,
        "reason": (
            "One or more proposed new entities overlap strong identity candidates."
            if risks
            else (
                "No unsupported overlap with strong identity candidates was "
                "detected."
            )
        ),
        "items": risks,
    }


def _identity_similarity(
    proposed: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[float, list[str]]:
    proposed_name = normalize_text(str(proposed.get("canonical_name") or ""))
    candidate_name = normalize_text(str(candidate.get("canonical_name") or ""))
    proposed_external = normalize_text(str(proposed.get("external_id") or ""))
    candidate_external = normalize_text(str(candidate.get("external_id") or ""))
    reasons: list[str] = []
    score = 0.0

    if proposed_external and candidate_external:
        if proposed_external == candidate_external:
            return 1.0, ["matching external_id"]
        reasons.append("different external_id values")
    if proposed_name and proposed_name == candidate_name:
        return 1.0, ["exact normalized canonical_name"]

    proposed_technical = _technical_tokens(
        f"{proposed_name} {proposed_external}"
    )
    candidate_technical = _technical_tokens(
        f"{candidate_name} {candidate_external}"
    )
    technical_overlap = proposed_technical & candidate_technical
    if technical_overlap:
        score = max(score, 0.92)
        reasons.append(
            "matching stable technical identifier: "
            + ", ".join(sorted(technical_overlap))
        )

    proposed_tokens = set(_significant_tokens(proposed_name))
    candidate_tokens = set(_significant_tokens(candidate_name))
    matched_roots = _token_roots(proposed_tokens) & _token_roots(candidate_tokens)
    if len(matched_roots) >= 2:
        overlap = len(matched_roots) / max(
            1,
            min(
                len(_token_roots(proposed_tokens)),
                len(_token_roots(candidate_tokens)),
            ),
        )
        score = max(score, 0.72 + 0.20 * overlap)
        reasons.append(
            f"{len(matched_roots)} informative name token roots overlap"
        )
    phrase_overlap = (
        proposed_name
        and candidate_name
        and (
            proposed_name in candidate_name
            or candidate_name in proposed_name
        )
    )
    if phrase_overlap and (len(matched_roots) >= 2 or technical_overlap):
        score = max(score, 0.90)
        reasons.append("informative normalized name phrase overlap")

    if proposed_external and candidate_external and proposed_external != candidate_external:
        # Distinct stable IDs prevent a name-only exact channel hit from being
        # interpreted as duplicate identity unless the names themselves match.
        if proposed_name != candidate_name and not technical_overlap:
            score = min(score, 0.77)
    return score, reasons


def ontology_diagnostics(
    bundle: dict[str, Any],
    identity_candidates: list[dict[str, Any]],
    *,
    threshold: float,
) -> dict[str, Any]:
    """Return non-blocking warnings for unstable or cross-type identities."""
    warnings: list[dict[str, Any]] = []
    strong = [
        candidate
        for candidate in identity_candidates
        if float(candidate.get("identity_match", {}).get("score") or 0.0)
        >= threshold
    ]
    for proposed in bundle.get("entities", []):
        if not isinstance(proposed, dict) or proposed.get("entity_id"):
            continue
        proposed_name = normalize_text(str(proposed.get("canonical_name") or ""))
        proposed_type = str(proposed.get("entity_type") or "").strip().lower()
        if not proposed_name:
            continue
        has_stable_identifier = bool(proposed.get("external_id")) or bool(
            _technical_tokens(proposed_name)
        )
        generic_lead = proposed_name.split()[0] in _GENERIC_TOKENS
        strong_same_identity = False
        for candidate in strong:
            similarity, reasons = _identity_similarity(proposed, candidate)
            candidate_type = str(candidate.get("entity_type") or "").lower()
            if similarity >= threshold and candidate_type == proposed_type:
                strong_same_identity = True
            if (
                frozenset((proposed_type, candidate_type))
                in _CLOSE_ONTOLOGY_TYPES
                and similarity >= threshold
            ):
                warnings.append(
                    {
                        "warning": "cross_type_ambiguity",
                        "proposed_key": proposed.get("key"),
                        "proposed_entity_type": proposed_type,
                        "candidate_entity_id": candidate.get("entity_id"),
                        "candidate_entity_type": candidate_type,
                        "identity_similarity": round(similarity, 6),
                        "reasons": reasons,
                    }
                )
        if generic_lead and not has_stable_identifier and not strong_same_identity:
            warnings.append(
                {
                    "warning": "generic_identity",
                    "proposed_key": proposed.get("key"),
                    "canonical_name": proposed.get("canonical_name"),
                    "reason": (
                        "Generic entity name has no stable technical identifier "
                        "or strong same-type identity candidate."
                    ),
                }
            )
    return {
        "warning_count": len(warnings),
        "warnings": warnings,
    }
