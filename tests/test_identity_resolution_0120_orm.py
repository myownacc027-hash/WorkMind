from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from packages.common.config import get_settings
from packages.database.models import (
    Base,
    DocumentChunk,
    ExternalDocument,
    KnowledgeClaim,
    KnowledgeEntity,
    KnowledgeEvent,
    KnowledgeEvidence,
    KnowledgeExtractionJob,
)
from packages.knowledge import extraction, identity_resolution
from packages.knowledge.extraction import (
    _bundle_sha256,
    _check_evidence_scope,
    _quality_metrics,
    _record_quality_event,
    apply_validated_extraction_job,
    build_extraction_packet,
    claim_extraction_jobs,
    enqueue_indexed_source,
    reextract_extraction_job_as_current_version,
    requeue_extraction_job,
    validate_extraction_job,
)
from packages.knowledge.identity_resolution import resolve_identity_candidates


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type: JSONB, _compiler: Any, **_kwargs: Any) -> str:
    return "JSON"


TABLES = [
    ExternalDocument.__table__,
    DocumentChunk.__table__,
    KnowledgeEntity.__table__,
    KnowledgeClaim.__table__,
    KnowledgeEvent.__table__,
    KnowledgeEvidence.__table__,
    KnowledgeExtractionJob.__table__,
]


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=TABLES)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def settings():
    return get_settings().model_copy(
        update={
            "knowledge_extraction_enabled": True,
            "knowledge_extraction_version": "wm-ke-v2",
            "knowledge_extraction_min_chars": 1,
            "knowledge_extraction_max_chunks": 20,
            "knowledge_extraction_max_context_chars": 20000,
            "knowledge_extraction_thread_messages": 10,
            "knowledge_identity_max_candidates": 8,
            "knowledge_identity_exact_query_limit": 60,
            "knowledge_identity_exact_phrase_limit": 600,
            "knowledge_identity_semantic_source_limit": 20,
            "knowledge_identity_semantic_threshold": 0.58,
            "knowledge_identity_semantic_chunk_limit": 60,
            "knowledge_identity_semantic_evidence_limit": 240,
            "knowledge_identity_semantic_entity_limit": 60,
            "knowledge_identity_lexical_min_score": 0.62,
            "knowledge_identity_lexical_scan_limit": 300,
            "knowledge_identity_active_claims_per_entity": 10,
            "knowledge_identity_mandatory_claims_safety_cap": 200,
            "knowledge_identity_recent_claims_per_entity": 10,
            "knowledge_identity_recent_events_per_entity": 5,
            "knowledge_extraction_quality_history_limit": 50,
        }
    )


def _entity(name: str, entity_type: str = "system") -> KnowledgeEntity:
    return KnowledgeEntity(
        id=uuid.uuid4(),
        entity_type=entity_type,
        canonical_name=name,
        normalized_key=name.casefold(),
        status="active",
        attributes_json={},
    )


def _claim(
    entity: KnowledgeEntity,
    index: int,
    *,
    status: str = "superseded",
    predicate: str = "historical_note",
    object_entity: KnowledgeEntity | None = None,
) -> KnowledgeClaim:
    return KnowledgeClaim(
        id=uuid.uuid4(),
        subject_entity_id=entity.id,
        object_entity_id=object_entity.id if object_entity else None,
        predicate=predicate,
        object_text=None if object_entity else f"value-{index}",
        claim_text=f"claim-{index}",
        evidence_type="direct_statement",
        confidence=1.0,
        status=status,
        claim_key=f"claim-{uuid.uuid4()}",
        claim_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        attributes_json={},
        created_by="test",
    )


def _event(entity: KnowledgeEntity, index: int) -> KnowledgeEvent:
    return KnowledgeEvent(
        id=uuid.uuid4(),
        entity_id=entity.id,
        event_type="update",
        title=f"event-{index}",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        status="confirmed",
        event_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        attributes_json={},
        created_by="test",
    )


def _chunk(source_id: uuid.UUID, text: str, index: int = 0) -> DocumentChunk:
    return DocumentChunk(
        id=uuid.uuid4(),
        source_type="document",
        source_id=source_id,
        chunk_index=index,
        text=text,
        content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        embedding_status="indexed",
        metadata_json={},
    )


def _semantic(chunk_id: uuid.UUID, score: float = 0.88):
    def search(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "results": [
                {
                    "source_ref": "workmind://document/history",
                    "matched_chunks": [
                        {"chunk_id": str(chunk_id), "semantic_score": score}
                    ],
                }
            ],
            "warnings": [],
        }

    return search


def _no_semantic(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {"results": [], "warnings": []}


def test_per_entity_claim_and_event_limits_do_not_starve_candidate(
    session_factory: sessionmaker[Session], settings: Any
) -> None:
    noisy = _entity("NoisyWarehouseSystem")
    stable = _entity("StableWarehouseSystem")
    with session_factory() as session:
        session.add_all([noisy, stable])
        session.add_all([_claim(noisy, index) for index in range(500)])
        active = _claim(
            stable,
            999,
            status="active",
            predicate="current_status",
        )
        session.add(active)
        session.add_all([_event(noisy, index) for index in range(100)])
        stable_event = _event(stable, 999)
        session.add(stable_event)
        session.commit()

        result = resolve_identity_candidates(
            session,
            ["NoisyWarehouseSystem StableWarehouseSystem"],
            settings=settings,
            semantic_search=_no_semantic,
        )

    by_name = {item["canonical_name"]: item for item in result["candidates"]}
    assert by_name["StableWarehouseSystem"]["active_claims"][0][
        "claim_id"
    ] == str(active.id)
    assert len(by_name["NoisyWarehouseSystem"]["recent_claims"]) == 10
    assert by_name["StableWarehouseSystem"]["recent_events"][0][
        "event_id"
    ] == str(stable_event.id)
    assert len(by_name["NoisyWarehouseSystem"]["recent_events"]) == 5


def test_mandatory_status_survives_more_than_sixty_active_claims(
    session_factory: sessionmaker[Session], settings: Any
) -> None:
    entity = _entity("TemporalSafetySystem")
    current_status = _claim(
        entity,
        999,
        status="active",
        predicate="current_status",
    )
    other_active = [
        _claim(
            entity,
            index,
            status="active",
            predicate=f"non_temporal_{index}",
        )
        for index in range(75)
    ]
    with session_factory() as session:
        session.add_all([entity, current_status, *other_active])
        session.commit()
        result = resolve_identity_candidates(
            session,
            ["TemporalSafetySystem"],
            settings=settings,
            semantic_search=_no_semantic,
        )

    candidate = result["candidates"][0]
    assert [item["claim_id"] for item in candidate["mandatory_active_claims"]] == [
        str(current_status.id)
    ]
    assert len(candidate["non_temporal_active_claims"]) == 10
    assert len(candidate["recent_claims"]) == 0
    assert candidate["context_diagnostics"]["mandatory_context_complete"] is True


def test_multiple_active_current_status_claims_are_all_exposed(
    session_factory: sessionmaker[Session], settings: Any
) -> None:
    entity = _entity("ConflictedStatusSystem")
    statuses = [
        _claim(
            entity,
            index,
            status="active",
            predicate="current_status",
        )
        for index in range(3)
    ]
    with session_factory() as session:
        session.add_all([entity, *statuses])
        session.commit()
        result = resolve_identity_candidates(
            session,
            ["ConflictedStatusSystem"],
            settings=settings,
            semantic_search=_no_semantic,
        )

    candidate = result["candidates"][0]
    assert {item["claim_id"] for item in candidate["mandatory_active_claims"]} == {
        str(claim.id) for claim in statuses
    }
    assert result["diagnostics"]["blocking_errors"] == []


def test_evidence_maps_claim_subject_object_and_event_entities(
    session_factory: sessionmaker[Session], settings: Any
) -> None:
    source_id = uuid.uuid4()
    subject = _entity("OrderRouter")
    object_entity = _entity("WarehouseCore")
    event_entity = _entity("DispatchService")
    chunk = _chunk(source_id, "historical identity evidence")
    claim = _claim(subject, 1, status="active", object_entity=object_entity)
    event = _event(event_entity, 1)
    with session_factory() as session:
        session.add_all([subject, object_entity, event_entity, chunk, claim, event])
        session.flush()
        session.add_all(
            [
                KnowledgeEvidence(
                    id=uuid.uuid4(),
                    claim_id=claim.id,
                    source_type="document",
                    source_id=source_id,
                    source_ref=f"workmind://document/{source_id}",
                    chunk_id=chunk.id,
                    excerpt="claim evidence",
                    evidence_role="direct_statement",
                    evidence_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                ),
                KnowledgeEvidence(
                    id=uuid.uuid4(),
                    event_id=event.id,
                    source_type="document",
                    source_id=source_id,
                    source_ref=f"workmind://document/{source_id}",
                    chunk_id=chunk.id,
                    excerpt="event evidence",
                    evidence_role="direct_statement",
                    evidence_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                ),
            ]
        )
        session.commit()
        result = resolve_identity_candidates(
            session,
            ["unrelated current wording"],
            settings=settings,
            semantic_search=_semantic(chunk.id),
        )

    assert {item["entity_id"] for item in result["candidates"]} == {
        str(subject.id),
        str(object_entity.id),
        str(event_entity.id),
    }


def test_exact_semantic_and_lexical_channels_merge_one_orm_entity(
    session_factory: sessionmaker[Session], settings: Any
) -> None:
    source_id = uuid.uuid4()
    entity = _entity("WMS Packaging Router")
    entity.external_id = "189422"
    chunk = _chunk(source_id, "WMS Packaging Router #189422")
    claim = _claim(entity, 1, status="active")
    with session_factory() as session:
        session.add_all([entity, chunk, claim])
        session.flush()
        session.add(
            KnowledgeEvidence(
                id=uuid.uuid4(),
                claim_id=claim.id,
                source_type="document",
                source_id=source_id,
                source_ref=f"workmind://document/{source_id}",
                chunk_id=chunk.id,
                excerpt="WMS Packaging Router #189422",
                evidence_role="direct_statement",
                evidence_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            )
        )
        session.commit()
        result = resolve_identity_candidates(
            session,
            ["WMS Packaging Router #189422"],
            settings=settings,
            semantic_search=_semantic(chunk.id),
        )

    assert len(result["candidates"]) == 1
    assert set(result["candidates"][0]["identity_match"]["methods"]) == {
        "exact_external_id",
        "exact_normalized_name",
        "lexical_name",
        "semantic_evidence",
    }


@pytest.mark.parametrize(
    ("source", "expected_type"),
    [
        ("task 189422", "task"),
        ("incident 203823", "incident"),
    ],
)
def test_typed_external_id_constraint_survives_exact_and_lexical_channels(
    source: str,
    expected_type: str,
    session_factory: sessionmaker[Session],
    settings: Any,
) -> None:
    external_id = source.split()[-1]
    task = _entity(f"Task {external_id}", "task")
    task.external_id = external_id
    incident = _entity(f"Incident {external_id}", "incident")
    incident.external_id = external_id
    with session_factory() as session:
        session.add_all([task, incident])
        session.commit()
        result = resolve_identity_candidates(
            session,
            [source],
            settings=settings,
            semantic_search=_no_semantic,
        )

    assert [item["entity_type"] for item in result["candidates"]] == [
        expected_type
    ]
    candidate = result["candidates"][0]
    assert candidate["identity_match"]["type_compatibility"] == "typed_match"
    assert set(candidate["identity_match"]["methods"]) == {
        "exact_external_id",
        "exact_normalized_name",
        "lexical_name",
    }


def test_untyped_external_id_keeps_cross_type_candidates_and_diagnostic(
    session_factory: sessionmaker[Session], settings: Any
) -> None:
    task = _entity("Task 189422", "task")
    task.external_id = "189422"
    incident = _entity("Incident 189422", "incident")
    incident.external_id = "189422"
    with session_factory() as session:
        session.add_all([task, incident])
        session.commit()
        result = resolve_identity_candidates(
            session,
            ["Проверить #189422"],
            settings=settings,
            semantic_search=_no_semantic,
        )

    assert {item["entity_type"] for item in result["candidates"]} == {
        "task",
        "incident",
    }
    assert all(
        item["identity_match"]["type_compatibility"] == "untyped_match"
        for item in result["candidates"]
    )
    assert result["diagnostics"]["untyped_external_id_ambiguities"] == [
        {
            "external_id": "189422",
            "entity_types": ["incident", "task"],
            "candidate_entity_ids": [
                item["entity_id"] for item in result["candidates"]
            ],
        }
    ]


def test_semantic_cross_type_candidate_cannot_replace_typed_exact_candidate(
    session_factory: sessionmaker[Session], settings: Any
) -> None:
    source_id = uuid.uuid4()
    task = _entity("Task 189422", "task")
    task.external_id = "189422"
    incident = _entity("Incident 189422", "incident")
    incident.external_id = "189422"
    chunk = _chunk(source_id, "historical incident evidence")
    claim = _claim(incident, 1, status="active")
    with session_factory() as session:
        session.add_all([task, incident, chunk, claim])
        session.flush()
        session.add(
            KnowledgeEvidence(
                id=uuid.uuid4(),
                claim_id=claim.id,
                source_type="document",
                source_id=source_id,
                source_ref=f"workmind://document/{source_id}",
                chunk_id=chunk.id,
                excerpt="historical incident evidence",
                evidence_role="direct_statement",
                evidence_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            )
        )
        session.commit()
        result = resolve_identity_candidates(
            session,
            ["task 189422"],
            settings=settings,
            semantic_search=_semantic(chunk.id, 0.88),
        )

    assert [item["entity_type"] for item in result["candidates"]] == [
        "task",
        "incident",
    ]
    exact, semantic = result["candidates"]
    assert exact["identity_match"]["score"] == 1.0
    assert semantic["identity_match"]["score"] == 0.88
    assert semantic["identity_match"]["methods"] == ["semantic_evidence"]
    assert semantic["identity_match"]["type_compatibility"] == "typed_conflict"


def test_semantic_threshold_admits_strong_and_rejects_weak_match(
    session_factory: sessionmaker[Session], settings: Any
) -> None:
    source_id = uuid.uuid4()
    entity = _entity("SemanticOnlyIdentity")
    chunk = _chunk(source_id, "historical semantic identity")
    claim = _claim(entity, 1, status="active")
    with session_factory() as session:
        session.add_all([entity, chunk, claim])
        session.flush()
        session.add(
            KnowledgeEvidence(
                id=uuid.uuid4(),
                claim_id=claim.id,
                source_type="document",
                source_id=source_id,
                source_ref=f"workmind://document/{source_id}",
                chunk_id=chunk.id,
                excerpt="historical semantic identity",
                evidence_role="direct_statement",
                evidence_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            )
        )
        session.commit()
        strong = resolve_identity_candidates(
            session,
            ["unrelated current source"],
            settings=settings,
            semantic_search=_semantic(chunk.id, 0.88),
        )
        weak = resolve_identity_candidates(
            session,
            ["unrelated current source"],
            settings=settings,
            semantic_search=_semantic(chunk.id, 0.57),
        )

    assert strong["candidates"][0]["entity_id"] == str(entity.id)
    assert weak["candidates"] == []


def test_stage_a_finds_old_exact_name_beyond_lexical_scan_limit(
    session_factory: sessionmaker[Session], settings: Any
) -> None:
    exact = _entity("Кластерная обработка специальная")
    with session_factory() as session:
        session.add(exact)
        session.add_all(
            [_entity(f"Кластерная обработка {index}") for index in range(1000)]
        )
        session.commit()
        result = resolve_identity_candidates(
            session,
            ["Кластерная обработка специальная"],
            settings=settings,
            semantic_search=_no_semantic,
        )

    candidate = next(
        item for item in result["candidates"] if item["entity_id"] == str(exact.id)
    )
    assert "exact_normalized_name" in candidate["identity_match"]["methods"]


def test_stage_a_exact_name_is_not_lost_after_phrase_generation_cap(
    session_factory: sessionmaker[Session], settings: Any
) -> None:
    exact = _entity("Late Stable Canonical Name")
    long_prefix = " ".join(f"prefixtoken{index}" for index in range(700))
    with session_factory() as session:
        session.add(exact)
        session.commit()
        result = resolve_identity_candidates(
            session,
            [f"{long_prefix} Late Stable Canonical Name"],
            settings=settings,
            semantic_search=_no_semantic,
        )
    assert result["candidates"][0]["entity_id"] == str(exact.id)
    assert "exact_normalized_name" in result["candidates"][0][
        "identity_match"
    ]["methods"]


@pytest.mark.parametrize("query", ["WMS", "wms", "ДСЛ", "дсл"])
def test_known_short_names_are_exact_case_insensitive(
    query: str,
    session_factory: sessionmaker[Session],
    settings: Any,
) -> None:
    expected = query.upper()
    with session_factory() as session:
        session.add_all([_entity("WMS"), _entity("ДСЛ")])
        session.commit()
        result = resolve_identity_candidates(
            session,
            [query],
            settings=settings,
            semantic_search=_no_semantic,
        )
    assert result["candidates"][0]["canonical_name"] == expected
    assert "exact_normalized_name" in result["candidates"][0][
        "identity_match"
    ]["methods"]


def test_semantic_evidence_fanout_is_bounded(
    session_factory: sessionmaker[Session], settings: Any
) -> None:
    bounded = settings.model_copy(
        update={
            "knowledge_identity_semantic_evidence_limit": 2,
            "knowledge_identity_semantic_entity_limit": 2,
        }
    )
    source_id = uuid.uuid4()
    chunk = _chunk(source_id, "historical evidence")
    entities = [_entity(f"MappedEntity{index}") for index in range(4)]
    claims = [_claim(entity, index, status="active") for index, entity in enumerate(entities)]
    with session_factory() as session:
        session.add_all([chunk, *entities, *claims])
        session.flush()
        session.add_all(
            [
                KnowledgeEvidence(
                    id=uuid.uuid4(),
                    claim_id=claim.id,
                    source_type="document",
                    source_id=source_id,
                    source_ref=f"workmind://document/{source_id}",
                    chunk_id=chunk.id,
                    excerpt=f"evidence-{index}",
                    evidence_role="direct_statement",
                    evidence_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                )
                for index, claim in enumerate(claims)
            ]
        )
        session.commit()
        result = resolve_identity_candidates(
            session,
            ["unrelated source wording"],
            settings=bounded,
            semantic_search=_semantic(chunk.id),
        )

    assert len(result["candidates"]) == 2
    assert any(
        "KnowledgeEvidence fan-out" in warning
        for warning in result["diagnostics"]["warnings"]
    )


def test_semantic_evidence_cap_prioritizes_highest_scoring_late_chunk(
    session_factory: sessionmaker[Session], settings: Any
) -> None:
    bounded = settings.model_copy(
        update={
            "knowledge_identity_semantic_source_limit": 30,
            "knowledge_identity_semantic_chunk_limit": 300,
            "knowledge_identity_semantic_evidence_limit": 20,
            "knowledge_identity_semantic_entity_limit": 20,
        }
    )
    source_id = uuid.uuid4()
    chunks: list[DocumentChunk] = []
    entities: list[KnowledgeEntity] = []
    claims: list[KnowledgeClaim] = []
    evidence_rows: list[KnowledgeEvidence] = []
    for index in range(300):
        chunk = _chunk(source_id, f"historical semantic row {index}", index)
        chunk.id = (
            uuid.UUID(int=(1 << 128) - 1)
            if index == 299
            else uuid.UUID(hex=f"a{index:031x}")
        )
        entity = _entity(f"SemanticMappedEntity{index}")
        claim = _claim(entity, index, status="active")
        evidence = KnowledgeEvidence(
            id=uuid.uuid4(),
            claim_id=claim.id,
            source_type="document",
            source_id=source_id,
            source_ref=f"workmind://document/{source_id}",
            chunk_id=chunk.id,
            excerpt=f"evidence-{index}",
            evidence_role="direct_statement",
            evidence_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        )
        chunks.append(chunk)
        entities.append(entity)
        claims.append(claim)
        evidence_rows.append(evidence)
    strongest = entities[-1]
    semantic_results = [
        {
            "source_ref": f"workmind://document/semantic-{source_index}",
            "matched_chunks": [
                {
                    "chunk_id": str(chunks[index].id),
                    "semantic_score": 0.99 if index == 299 else 0.60,
                }
                for index in range(source_index * 10, source_index * 10 + 10)
            ],
        }
        for source_index in range(30)
    ]

    def semantic_search(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"results": semantic_results, "warnings": []}

    assert len({chunk.id for chunk in chunks}) == 300
    with session_factory() as session:
        session.execute(
            DocumentChunk.__table__.insert(),
            [
                {
                    "id": chunk.id,
                    "source_type": chunk.source_type,
                    "source_id": chunk.source_id,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                    "content_hash": chunk.content_hash,
                    "embedding_status": chunk.embedding_status,
                    "metadata_json": {},
                }
                for chunk in chunks
            ],
        )
        session.add_all(entities)
        session.flush()
        session.add_all(claims)
        session.flush()
        session.add_all(evidence_rows)
        session.commit()
        result = resolve_identity_candidates(
            session,
            ["unrelated current message"],
            settings=bounded,
            semantic_search=semantic_search,
        )

    candidate = next(
        item
        for item in result["candidates"]
        if item["entity_id"] == str(strongest.id)
    )
    assert candidate["identity_match"]["score"] == 0.99
    assert any(
        "KnowledgeEvidence fan-out" in warning
        for warning in result["diagnostics"]["warnings"]
    )


def test_packet_identity_history_is_not_evidence(
    session_factory: sessionmaker[Session], settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = ExternalDocument(
        id=uuid.uuid4(),
        original_filename="target.txt",
        archive_path="target.txt",
        content_hash="a" * 64,
        size_bytes=100,
        processing_status="completed",
        metadata_json={},
    )
    history = ExternalDocument(
        id=uuid.uuid4(),
        original_filename="history.txt",
        archive_path="history.txt",
        content_hash="b" * 64,
        size_bytes=100,
        processing_status="completed",
        metadata_json={},
    )
    target_chunk = _chunk(target.id, "Target describes a packaging change")
    historical_chunk = _chunk(history.id, "Historical packaging identity")
    entity = _entity("PackagingIdentity")
    claim = _claim(entity, 1, status="active")
    job = KnowledgeExtractionJob(
        id=uuid.uuid4(),
        source_type="document",
        source_id=target.id,
        source_content_hash=target.content_hash,
        extractor_version="wm-ke-v1",
        status="leased",
        attempts=1,
        lease_token=uuid.uuid4(),
        bundle_json={},
        validation_json={},
        apply_json={},
    )
    with session_factory() as session:
        session.add_all(
            [target, history, target_chunk, historical_chunk, entity, claim, job]
        )
        session.flush()
        session.add(
            KnowledgeEvidence(
                id=uuid.uuid4(),
                claim_id=claim.id,
                source_type="document",
                source_id=history.id,
                source_ref=f"workmind://document/{history.id}",
                chunk_id=historical_chunk.id,
                excerpt="historical identity",
                evidence_role="direct_statement",
                evidence_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            )
        )
        session.commit()

    monkeypatch.setattr(extraction, "SessionLocal", session_factory)
    monkeypatch.setattr(extraction, "get_settings", lambda: settings)
    monkeypatch.setattr(identity_resolution, "search_hybrid", _semantic(historical_chunk.id))
    packet = build_extraction_packet(job.id)

    packet_chunk_ids = {item["chunk_id"] for item in packet["chunks"]}
    allowed_chunk_ids = {item["chunk_id"] for item in packet["allowed_evidence"]}
    assert str(historical_chunk.id) not in packet_chunk_ids
    assert str(historical_chunk.id) not in allowed_chunk_ids
    assert packet["job"]["extractor_version"] == "wm-ke-v1"
    assert packet["job"]["runtime_packet_contract_version"] == "wm-ke-v2"
    assert packet["identity_candidates"][0]["entity_id"] == str(entity.id)

    errors = _check_evidence_scope(
        packet,
        {
            "claims": [
                {
                    "evidence": [
                        {
                            "source_ref": f"workmind://document/{history.id}",
                            "chunk_id": str(historical_chunk.id),
                        }
                    ]
                }
            ]
        },
    )
    assert errors and "outside extraction packet" in errors[0]


def test_mandatory_temporal_overflow_blocks_packet_validation(
    session_factory: sessionmaker[Session], settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    bounded = settings.model_copy(
        update={"knowledge_identity_mandatory_claims_safety_cap": 10}
    )
    document = ExternalDocument(
        id=uuid.uuid4(),
        original_filename="overflow.txt",
        archive_path="overflow.txt",
        content_hash="9" * 64,
        size_bytes=100,
        processing_status="completed",
        metadata_json={},
    )
    chunk = _chunk(document.id, "OverflowTemporalSystem")
    entity = _entity("OverflowTemporalSystem")
    statuses = [
        _claim(
            entity,
            index,
            status="active",
            predicate="current_status",
        )
        for index in range(11)
    ]
    job = KnowledgeExtractionJob(
        id=uuid.uuid4(),
        source_type="document",
        source_id=document.id,
        source_content_hash=document.content_hash,
        extractor_version="wm-ke-v2",
        status="leased",
        attempts=1,
        lease_token=uuid.uuid4(),
        bundle_json={},
        validation_json={},
        apply_json={},
    )
    with session_factory() as session:
        session.add_all([document, chunk, entity, *statuses, job])
        session.commit()

    monkeypatch.setattr(extraction, "SessionLocal", session_factory)
    monkeypatch.setattr(extraction, "get_settings", lambda: bounded)
    monkeypatch.setattr(identity_resolution, "search_hybrid", _no_semantic)
    packet = build_extraction_packet(job.id)
    assert packet["job"]["extraction_blocked"] is True
    assert packet["blocking_diagnostics"]
    assert packet["identity_candidates"][0]["context_diagnostics"][
        "mandatory_context_complete"
    ] is False

    monkeypatch.setattr(
        extraction,
        "import_knowledge_bundle",
        lambda *_args, **_kwargs: {"ok": True, "apply": False},
    )
    result = validate_extraction_job(
        job.id,
        job.lease_token,
        json.dumps({"entities": [], "claims": [], "relations": [], "events": []}),
    )
    assert result["status"] == "pending"
    assert result["validation"]["ok"] is False
    assert result["validation"]["identity_context_errors"]


def test_quality_history_counts_two_attempts_without_double_count(
    session_factory: sessionmaker[Session],
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = KnowledgeExtractionJob(
        id=uuid.uuid4(),
        source_type="document",
        source_id=uuid.uuid4(),
        source_content_hash="c" * 64,
        extractor_version="wm-ke-v2",
        status="pending",
        attempts=1,
        bundle_json={},
        validation_json={},
        apply_json={},
    )
    with session_factory() as session:
        session.add(job)
        session.flush()
        _record_quality_event(
            session,
            job,
            event_type="preflight_failure",
            details="first",
            settings=settings,
        )
        _record_quality_event(
            session,
            job,
            event_type="preflight_failure",
            details="duplicate call",
            settings=settings,
        )
        job.attempts = 2
        _record_quality_event(
            session,
            job,
            event_type="fingerprint_mismatch",
            details="second",
            settings=settings,
        )
        session.commit()
        stored = session.get(KnowledgeExtractionJob, job.id)

    assert stored is not None
    assert len(stored.validation_json["quality_history"]) == 2
    metrics = _quality_metrics(
        [(stored.status, stored.validation_json)],
        {"pending": 1},
    )
    assert metrics["validation_failures"] == 2

    monkeypatch.setattr(extraction, "SessionLocal", session_factory)
    monkeypatch.setattr(extraction, "get_settings", lambda: settings)
    requeue_extraction_job(job.id, reason="new diagnostic cycle", reset_attempts=True)
    with session_factory() as session:
        reset_job = session.get(KnowledgeExtractionJob, job.id)
        assert reset_job is not None
        reset_job.attempts = 1
        _record_quality_event(
            session,
            reset_job,
            event_type="preflight_failure",
            details="same event type in a new cycle",
            settings=settings,
        )
        session.commit()
        final_state = dict(reset_job.validation_json)
    assert _quality_metrics(
        [("pending", final_state)],
        {"pending": 1},
    )["validation_failures"] == 3


def test_v1_enqueue_and_controlled_v2_reextract_policy(
    session_factory: sessionmaker[Session], settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = ExternalDocument(
        id=uuid.uuid4(),
        original_filename="upgrade.txt",
        archive_path="upgrade.txt",
        content_hash="d" * 64,
        size_bytes=100,
        processing_status="completed",
        metadata_json={},
    )
    chunk = _chunk(document.id, "Durable extraction source")
    old_job = KnowledgeExtractionJob(
        id=uuid.uuid4(),
        source_type="document",
        source_id=document.id,
        source_content_hash=document.content_hash,
        extractor_version="wm-ke-v1",
        status="completed",
        attempts=1,
        bundle_json={},
        validation_json={},
        apply_json={"ok": True},
    )
    with session_factory() as session:
        session.add_all([document, chunk, old_job])
        session.commit()
        auto = enqueue_indexed_source(
            session,
            source_type="document",
            source_id=document.id,
            settings=settings,
        )
    assert auto["queued"] is False
    assert auto["reason"] == "already_queued"

    monkeypatch.setattr(extraction, "SessionLocal", session_factory)
    monkeypatch.setattr(extraction, "get_settings", lambda: settings)
    preview = reextract_extraction_job_as_current_version(
        old_job.id,
        reason="controlled 0.12 verification",
        apply=False,
    )
    assert preview["action"] == "create_v2_job"
    applied = reextract_extraction_job_as_current_version(
        old_job.id,
        reason="controlled 0.12 verification",
        apply=True,
    )
    assert applied["status"] == "pending"
    with session_factory() as session:
        jobs = list(
            session.scalars(
                select(KnowledgeExtractionJob).where(
                    KnowledgeExtractionJob.source_id == document.id
                )
            )
        )
    assert len(jobs) == 2
    assert sum(item.status in extraction.ACTIVE_JOB_STATUSES for item in jobs) == 1
    assert {item.extractor_version for item in jobs} == {"wm-ke-v1", "wm-ke-v2"}


def test_controlled_reextract_resets_exhausted_attempts_and_is_leaseable(
    session_factory: sessionmaker[Session], settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = ExternalDocument(
        id=uuid.uuid4(),
        original_filename="exhausted.txt",
        archive_path="exhausted.txt",
        content_hash="8" * 64,
        size_bytes=100,
        processing_status="completed",
        metadata_json={},
    )
    chunk = _chunk(document.id, "Durable exhausted source")
    old_history = [
        {
            "event_key": "1:semantic_validation_failure",
            "attempt": 1,
            "event_type": "semantic_validation_failure",
            "occurred_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "details": "old cycle",
        }
    ]
    job = KnowledgeExtractionJob(
        id=uuid.uuid4(),
        source_type="document",
        source_id=document.id,
        source_content_hash=document.content_hash,
        extractor_version="wm-ke-v2",
        status="error",
        attempts=settings.knowledge_extraction_max_attempts,
        bundle_json={},
        validation_json={"quality_history": old_history},
        apply_json={},
    )
    with session_factory() as session:
        session.add_all([document, chunk, job])
        session.commit()

    monkeypatch.setattr(extraction, "SessionLocal", session_factory)
    monkeypatch.setattr(extraction, "get_settings", lambda: settings)
    result = reextract_extraction_job_as_current_version(
        job.id,
        reason="explicit new extraction cycle",
        apply=True,
    )
    assert result["status"] == "pending"
    with session_factory() as session:
        reset = session.get(KnowledgeExtractionJob, job.id)
        assert reset is not None
        assert reset.attempts == 0
        assert reset.validation_json["quality_history"][0] == old_history[0]
        assert reset.validation_json["quality_attempt_offset"] == (
            settings.knowledge_extraction_max_attempts
        )

    leased = claim_extraction_jobs(limit=1)
    assert len(leased) == 1
    assert leased[0].id == job.id
    assert leased[0].status == "leased"
    assert leased[0].attempts == 1


def test_validation_exception_is_persisted_as_attempt_failure(
    session_factory: sessionmaker[Session], settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = ExternalDocument(
        id=uuid.uuid4(),
        original_filename="validation.txt",
        archive_path="validation.txt",
        content_hash="e" * 64,
        size_bytes=100,
        processing_status="completed",
        metadata_json={},
    )
    chunk = _chunk(document.id, "Durable validation source")
    job = KnowledgeExtractionJob(
        id=uuid.uuid4(),
        source_type="document",
        source_id=document.id,
        source_content_hash=document.content_hash,
        extractor_version="wm-ke-v2",
        status="leased",
        attempts=1,
        lease_token=uuid.uuid4(),
        bundle_json={},
        validation_json={},
        apply_json={},
    )
    with session_factory() as session:
        session.add_all([document, chunk, job])
        session.commit()

    monkeypatch.setattr(extraction, "SessionLocal", session_factory)
    monkeypatch.setattr(extraction, "get_settings", lambda: settings)
    monkeypatch.setattr(identity_resolution, "search_hybrid", _no_semantic)

    def failed_import(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("validator unavailable")

    monkeypatch.setattr(extraction, "import_knowledge_bundle", failed_import)
    result = validate_extraction_job(
        job.id,
        job.lease_token,
        json.dumps({"entities": [], "claims": [], "relations": [], "events": []}),
    )
    assert result["status"] == "pending"
    with session_factory() as session:
        stored = session.get(KnowledgeExtractionJob, job.id)
        assert stored is not None
        assert stored.status == "pending"
        assert stored.validation_json["quality"]["validation_failures"] == 1
        assert stored.validation_json["quality_history"][0][
            "event_type"
        ] == "semantic_validation_failure"


@pytest.mark.parametrize(
    ("attempts", "expected_status"),
    [
        (1, "pending"),
        (5, "error"),
    ],
)
def test_failed_validation_returns_persisted_job_status(
    attempts: int,
    expected_status: str,
    session_factory: sessionmaker[Session],
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bounded = settings.model_copy(
        update={"knowledge_extraction_max_attempts": 5}
    )
    document = ExternalDocument(
        id=uuid.uuid4(),
        original_filename=f"validation-{expected_status}.txt",
        archive_path=f"validation-{expected_status}.txt",
        content_hash=("a" if expected_status == "pending" else "b") * 64,
        size_bytes=100,
        processing_status="completed",
        metadata_json={},
    )
    chunk = _chunk(document.id, "Durable failed validation source")
    job = KnowledgeExtractionJob(
        id=uuid.uuid4(),
        source_type="document",
        source_id=document.id,
        source_content_hash=document.content_hash,
        extractor_version="wm-ke-v2",
        status="leased",
        attempts=attempts,
        lease_token=uuid.uuid4(),
        bundle_json={},
        validation_json={},
        apply_json={},
    )
    with session_factory() as session:
        session.add_all([document, chunk, job])
        session.commit()

    monkeypatch.setattr(extraction, "SessionLocal", session_factory)
    monkeypatch.setattr(extraction, "get_settings", lambda: bounded)
    monkeypatch.setattr(identity_resolution, "search_hybrid", _no_semantic)
    monkeypatch.setattr(
        extraction,
        "import_knowledge_bundle",
        lambda *_args, **_kwargs: {
            "ok": False,
            "apply": False,
            "errors": ["controlled validation failure"],
        },
    )

    result = validate_extraction_job(
        job.id,
        job.lease_token,
        json.dumps({"entities": [], "claims": [], "relations": [], "events": []}),
    )
    with session_factory() as session:
        persisted = session.get(KnowledgeExtractionJob, job.id)

    assert persisted is not None
    assert persisted.status == expected_status
    assert result["status"] == persisted.status


def test_apply_exception_preserves_validation_diagnostics(
    session_factory: sessionmaker[Session], settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = ExternalDocument(
        id=uuid.uuid4(),
        original_filename="apply.txt",
        archive_path="apply.txt",
        content_hash="f" * 64,
        size_bytes=100,
        processing_status="completed",
        metadata_json={},
    )
    chunk = _chunk(document.id, "Durable apply source")
    bundle = {"entities": [], "claims": [], "relations": [], "events": []}
    job = KnowledgeExtractionJob(
        id=uuid.uuid4(),
        source_type="document",
        source_id=document.id,
        source_content_hash=document.content_hash,
        extractor_version="wm-ke-v2",
        status="validated",
        attempts=1,
        bundle_json=bundle,
        validation_json={
            "ok": True,
            "bundle_sha256": _bundle_sha256(bundle),
            "candidate_resolution": {"exact_candidate_hits": 1},
            "quality": {"duplicate_risk_count": 1},
            "quality_history": [],
        },
        apply_json={},
    )
    with session_factory() as session:
        session.add_all([document, chunk, job])
        session.commit()

    monkeypatch.setattr(extraction, "SessionLocal", session_factory)
    monkeypatch.setattr(extraction, "get_settings", lambda: settings)
    calls = 0

    def staged_import(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"ok": True, "apply": False}
        raise RuntimeError("apply unavailable")

    monkeypatch.setattr(extraction, "import_knowledge_bundle", staged_import)
    result = apply_validated_extraction_job(job.id)
    assert result["status"] == "pending"
    with session_factory() as session:
        stored = session.get(KnowledgeExtractionJob, job.id)
        assert stored is not None
        assert stored.validation_json["candidate_resolution"] == {
            "exact_candidate_hits": 1
        }
        assert stored.validation_json["quality"]["duplicate_risk_count"] == 1
        assert stored.validation_json["quality"]["validation_failures"] == 1
        assert stored.validation_json["quality_history"][0][
            "event_type"
        ] == "apply_failure"


# PRODUCT-03B1 E2E CLAIM SUPERSESSION


def test_product_03b1_claim_supersession_through_real_extraction_flow(
    monkeypatch,
    session_factory,
    settings,
) -> None:
    """
    Prove the complete durable-knowledge supersession path:

        indexed source
          -> enqueue
          -> claim lease
          -> extraction packet
          -> validate bundle
          -> apply bundle
          -> historical claim superseded by new claim

    The test uses the module's SQLite in-memory database only.
    No production PostgreSQL or Qdrant access is allowed.
    """
    import json

    from sqlalchemy import select

    from packages.database.models import (
        DocumentChunk,
        ExternalDocument,
        KnowledgeClaim,
        KnowledgeEntity,
        KnowledgeEvidence,
        KnowledgeExtractionJob,
    )
    import packages.knowledge.extraction as extraction
    import packages.knowledge.identity_resolution as identity_resolution
    import packages.knowledge.service as knowledge_service

    monkeypatch.setattr(
        extraction,
        "SessionLocal",
        session_factory,
    )
    monkeypatch.setattr(
        extraction,
        "get_settings",
        lambda: settings,
    )
    # SQLite drops timezone information from DateTime values.
    # Keep extraction clock naive UTC inside this SQLite-only E2E.
    sqlite_now = lambda: datetime.now(UTC).replace(tzinfo=None)

    monkeypatch.setattr(
        extraction,
        "_now",
        sqlite_now,
    )
    monkeypatch.setattr(
        knowledge_service,
        "SessionLocal",
        session_factory,
    )

    def no_semantic_results(*_args, **_kwargs):
        return {
            "results": [],
            "warnings": [],
        }

    monkeypatch.setattr(
        identity_resolution,
        "search_hybrid",
        no_semantic_results,
    )

    entity_name = "Test System Alpha"

    v1_text = (
        "Test System Alpha status is active."
    )
    v2_text = (
        "Test System Alpha status is disabled."
    )

    # ------------------------------------------------------------------
    # VERSION 1
    # ------------------------------------------------------------------

    document_v1 = ExternalDocument(
        id=uuid.uuid4(),
        original_filename="product-03b1-v1.txt",
        archive_path="test://product-03b1/v1",
        mime_type="text/plain",
        content_hash="a" * 64,
        size_bytes=len(v1_text.encode("utf-8")),
        processing_status="indexed",
        extraction_method="test",
        extracted_chars=len(v1_text),
        metadata_json={
            "test_case": "PRODUCT-03B1",
            "version": 1,
        },
    )

    chunk_v1 = DocumentChunk(
        source_type="document",
        source_id=document_v1.id,
        chunk_index=0,
        text=v1_text,
        content_hash="1" * 64,
        embedding_status="indexed",
        metadata_json={
            "test_case": "PRODUCT-03B1",
            "version": 1,
        },
    )

    with session_factory() as session:
        session.add_all(
            [
                document_v1,
                chunk_v1,
            ]
        )
        session.flush()

        queued_v1 = extraction.enqueue_indexed_source(
            session,
            source_type="document",
            source_id=document_v1.id,
            settings=settings,
            reason="product_03b1_v1",
        )

        assert queued_v1["queued"] is True

        job_v1_id = queued_v1["job_id"]

        session.commit()

    leased_v1 = extraction.claim_extraction_jobs(
        limit=1,
    )

    assert len(leased_v1) == 1
    assert str(leased_v1[0].id) == job_v1_id
    assert leased_v1[0].status == "leased"
    assert leased_v1[0].lease_token is not None

    lease_v1 = leased_v1[0].lease_token

    packet_v1 = extraction.build_extraction_packet(
        job_v1_id,
    )

    assert (
        packet_v1["job"]["status"]
        == "leased"
    )
    assert (
        packet_v1["job"]["extraction_blocked"]
        is False
    )

    evidence_v1 = next(
        item
        for item in packet_v1["allowed_evidence"]
        if item["chunk_id"] == str(chunk_v1.id)
    )

    bundle_v1 = {
        "entities": [
            {
                "key": "test_system_alpha",
                "entity_type": "system",
                "canonical_name": entity_name,
            }
        ],
        "claims": [
            {
                "subject_key": "test_system_alpha",
                "predicate": "status",
                "object_text": "active",
                "claim_text": (
                    "Test System Alpha status is active."
                ),
                "evidence_type": "direct_statement",
                "confidence": 1.0,
                "status": "active",
                "observed_at": (
                    "2026-09-22T09:00:00+00:00"
                ),
                "evidence": [
                    {
                        "source_ref": (
                            evidence_v1["source_ref"]
                        ),
                        "chunk_id": (
                            evidence_v1["chunk_id"]
                        ),
                        "excerpt": v1_text,
                        "evidence_role": (
                            "direct_statement"
                        ),
                    }
                ],
            }
        ],
        "relations": [],
        "events": [],
    }

    validation_v1 = (
        extraction.validate_extraction_job(
            job_v1_id,
            lease_v1,
            json.dumps(
                bundle_v1,
                ensure_ascii=False,
            ),
        )
    )

    assert validation_v1["status"] == "validated"
    assert (
        validation_v1["validation"]["ok"]
        is True
    )

    applied_v1 = (
        extraction.apply_validated_extraction_job(
            job_v1_id
        )
    )

    assert applied_v1["status"] == "completed"
    assert (
        applied_v1["apply_result"]["ok"]
        is True
    )

    with session_factory() as session:
        entities_after_v1 = list(
            session.scalars(
                select(KnowledgeEntity).where(
                    KnowledgeEntity.entity_type
                    == "system",
                    KnowledgeEntity.canonical_name
                    == entity_name,
                )
            )
        )

        assert len(entities_after_v1) == 1

        entity_id = entities_after_v1[0].id

        claims_after_v1 = list(
            session.scalars(
                select(KnowledgeClaim).where(
                    KnowledgeClaim.subject_entity_id
                    == entity_id,
                    KnowledgeClaim.predicate
                    == "status",
                )
            )
        )

        assert len(claims_after_v1) == 1

        claim_v1 = claims_after_v1[0]

        assert claim_v1.object_text == "active"
        assert claim_v1.status == "active"
        assert claim_v1.supersedes_claim_id is None
        assert (
            claim_v1.superseded_by_claim_id
            is None
        )

        claim_v1_id = claim_v1.id

        evidence_after_v1 = list(
            session.scalars(
                select(KnowledgeEvidence).where(
                    KnowledgeEvidence.claim_id
                    == claim_v1_id
                )
            )
        )

        assert len(evidence_after_v1) == 1
        assert (
            evidence_after_v1[0].chunk_id
            == chunk_v1.id
        )

    # ------------------------------------------------------------------
    # VERSION 2
    # ------------------------------------------------------------------

    document_v2 = ExternalDocument(
        id=uuid.uuid4(),
        original_filename="product-03b1-v2.txt",
        archive_path="test://product-03b1/v2",
        mime_type="text/plain",
        content_hash="b" * 64,
        size_bytes=len(v2_text.encode("utf-8")),
        processing_status="indexed",
        extraction_method="test",
        extracted_chars=len(v2_text),
        metadata_json={
            "test_case": "PRODUCT-03B1",
            "version": 2,
        },
    )

    chunk_v2 = DocumentChunk(
        source_type="document",
        source_id=document_v2.id,
        chunk_index=0,
        text=v2_text,
        content_hash="2" * 64,
        embedding_status="indexed",
        metadata_json={
            "test_case": "PRODUCT-03B1",
            "version": 2,
        },
    )

    with session_factory() as session:
        session.add_all(
            [
                document_v2,
                chunk_v2,
            ]
        )
        session.flush()

        queued_v2 = extraction.enqueue_indexed_source(
            session,
            source_type="document",
            source_id=document_v2.id,
            settings=settings,
            reason="product_03b1_v2",
        )

        assert queued_v2["queued"] is True

        job_v2_id = queued_v2["job_id"]

        session.commit()

    def historical_semantic_result(
        *_args,
        **_kwargs,
    ):
        return {
            "results": [
                {
                    "source_ref": (
                        "workmind://document/"
                        f"{document_v1.id}"
                    ),
                    "matched_chunks": [
                        {
                            "chunk_id": (
                                str(chunk_v1.id)
                            ),
                            "semantic_score": 0.99,
                        }
                    ],
                }
            ],
            "warnings": [],
        }

    monkeypatch.setattr(
        identity_resolution,
        "search_hybrid",
        historical_semantic_result,
    )

    leased_v2 = extraction.claim_extraction_jobs(
        limit=1,
    )

    assert len(leased_v2) == 1
    assert str(leased_v2[0].id) == job_v2_id
    assert leased_v2[0].status == "leased"
    assert leased_v2[0].lease_token is not None

    lease_v2 = leased_v2[0].lease_token

    packet_v2 = extraction.build_extraction_packet(
        job_v2_id,
    )

    assert (
        packet_v2["job"]["extraction_blocked"]
        is False
    )

    candidate = next(
        (
            item
            for item
            in packet_v2["identity_candidates"]
            if item.get("entity_id")
            == str(entity_id)
        ),
        None,
    )

    assert candidate is not None

    candidate_old_claim = next(
        (
            item
            for item
            in candidate.get(
                "active_claims",
                [],
            )
            if (
                item.get("predicate")
                == "status"
                and item.get("object_text")
                == "active"
            )
        ),
        None,
    )

    assert candidate_old_claim is not None
    assert (
        candidate_old_claim["claim_id"]
        == str(claim_v1_id)
    )

    evidence_v2 = next(
        item
        for item in packet_v2["allowed_evidence"]
        if item["chunk_id"] == str(chunk_v2.id)
    )

    bundle_v2 = {
        "entities": [
            {
                "key": "test_system_alpha",
                "entity_id": candidate["entity_id"],
                "entity_type": "system",
                "canonical_name": entity_name,
            }
        ],
        "claims": [
            {
                "subject_key": "test_system_alpha",
                "predicate": "status",
                "object_text": "disabled",
                "claim_text": (
                    "Test System Alpha status "
                    "is disabled."
                ),
                "evidence_type": "direct_statement",
                "confidence": 1.0,
                "status": "active",
                "observed_at": (
                    "2026-09-22T10:00:00+00:00"
                ),
                "supersedes_claim_id": (
                    candidate_old_claim[
                        "claim_id"
                    ]
                ),
                "evidence": [
                    {
                        "source_ref": (
                            evidence_v2["source_ref"]
                        ),
                        "chunk_id": (
                            evidence_v2["chunk_id"]
                        ),
                        "excerpt": v2_text,
                        "evidence_role": (
                            "direct_statement"
                        ),
                    }
                ],
            }
        ],
        "relations": [],
        "events": [],
    }

    validation_v2 = (
        extraction.validate_extraction_job(
            job_v2_id,
            lease_v2,
            json.dumps(
                bundle_v2,
                ensure_ascii=False,
            ),
        )
    )

    assert validation_v2["status"] == "validated"
    assert (
        validation_v2["validation"]["ok"]
        is True
    )

    applied_v2 = (
        extraction.apply_validated_extraction_job(
            job_v2_id
        )
    )

    assert applied_v2["status"] == "completed"
    assert (
        applied_v2["apply_result"]["ok"]
        is True
    )

    # ------------------------------------------------------------------
    # FINAL DURABLE STATE
    # ------------------------------------------------------------------

    with session_factory() as session:
        entities = list(
            session.scalars(
                select(KnowledgeEntity).where(
                    KnowledgeEntity.entity_type
                    == "system",
                    KnowledgeEntity.canonical_name
                    == entity_name,
                )
            )
        )

        assert len(entities) == 1
        assert entities[0].id == entity_id

        claims = list(
            session.scalars(
                select(KnowledgeClaim)
                .where(
                    KnowledgeClaim.subject_entity_id
                    == entity_id,
                    KnowledgeClaim.predicate
                    == "status",
                )
                .order_by(
                    KnowledgeClaim.created_at,
                    KnowledgeClaim.id,
                )
            )
        )

        assert len(claims) == 2

        active_claims = [
            item
            for item in claims
            if item.status == "active"
        ]

        superseded_claims = [
            item
            for item in claims
            if item.status == "superseded"
        ]

        assert len(active_claims) == 1
        assert len(superseded_claims) == 1

        new_claim = active_claims[0]
        old_claim = superseded_claims[0]

        assert old_claim.id == claim_v1_id
        assert old_claim.object_text == "active"

        assert new_claim.object_text == "disabled"

        assert (
            new_claim.supersedes_claim_id
            == old_claim.id
        )

        assert (
            old_claim.superseded_by_claim_id
            == new_claim.id
        )

        assert old_claim.valid_to is not None

        evidence_rows = list(
            session.scalars(
                select(KnowledgeEvidence).where(
                    KnowledgeEvidence.claim_id.in_(
                        [
                            old_claim.id,
                            new_claim.id,
                        ]
                    )
                )
            )
        )

        assert len(evidence_rows) == 2

        evidence_chunk_ids = {
            item.chunk_id
            for item in evidence_rows
        }

        assert evidence_chunk_ids == {
            chunk_v1.id,
            chunk_v2.id,
        }

        jobs = list(
            session.scalars(
                select(KnowledgeExtractionJob)
            )
        )

        assert len(jobs) == 2

        assert {
            item.status
            for item in jobs
        } == {
            "completed",
        }

        assert {
            str(item.id)
            for item in jobs
        } == {
            job_v1_id,
            job_v2_id,
        }
