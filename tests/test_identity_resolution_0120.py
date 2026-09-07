from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from packages.database.models import (
    KnowledgeClaim,
    KnowledgeEntity,
    KnowledgeEvent,
    KnowledgeEvidence,
)
from packages.knowledge.extraction import (
    _allowed_evidence_from_chunks,
    _append_quality_event,
    _quality_metrics,
    _validation_state,
)
from packages.knowledge.identity_resolution import (
    duplicate_risk_diagnostics,
    extract_external_ids,
    lexical_identity_score,
    ontology_diagnostics,
    resolve_identity_candidates,
)


class _Scalars:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def __iter__(self):
        return iter(self.values)


class _Rows:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def all(self) -> list[Any]:
        return self.values


class FakeSession:
    def __init__(
        self,
        scalar_results: list[list[Any]],
        execute_results: list[list[Any]] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results)
        self.execute_results = list(execute_results or [])

    def scalars(self, _statement: Any) -> _Scalars:
        return _Scalars(self.scalar_results.pop(0))

    def execute(self, _statement: Any) -> _Rows:
        return _Rows(self.execute_results.pop(0))


SETTINGS = SimpleNamespace(
    knowledge_identity_max_candidates=8,
    knowledge_identity_exact_query_limit=60,
    knowledge_identity_exact_phrase_limit=600,
    knowledge_identity_semantic_source_limit=20,
    knowledge_identity_semantic_threshold=0.58,
    knowledge_identity_semantic_chunk_limit=60,
    knowledge_identity_semantic_evidence_limit=240,
    knowledge_identity_semantic_entity_limit=60,
    knowledge_identity_lexical_min_score=0.62,
    knowledge_identity_lexical_scan_limit=300,
    knowledge_identity_active_claims_per_entity=60,
    knowledge_identity_mandatory_claims_safety_cap=200,
    knowledge_identity_recent_claims_per_entity=60,
    knowledge_identity_recent_events_per_entity=30,
)


def _entity(
    name: str,
    *,
    entity_type: str = "system",
    external_id: str | None = None,
) -> KnowledgeEntity:
    return KnowledgeEntity(
        id=uuid.uuid4(),
        entity_type=entity_type,
        canonical_name=name,
        normalized_key=name.casefold(),
        external_id=external_id,
        status="active",
        description=None,
        attributes_json={},
    )


def _no_semantic(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {"results": [], "warnings": []}


def _semantic(chunk_id: uuid.UUID, score: float = 0.88):
    def search(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "results": [
                {
                    "matched_chunks": [
                        {
                            "chunk_id": str(chunk_id),
                            "semantic_score": score,
                        }
                    ]
                }
            ],
            "warnings": [],
        }

    return search


def test_exact_external_id_candidate_is_ranked_first() -> None:
    task = _entity(
        "Б24 №189422 Оптимизация кластерного отбора",
        entity_type="task",
        external_id="189422",
    )
    session = FakeSession([[task], [task], [], [], [], []])
    result = resolve_identity_candidates(
        session,
        ["Вернули задачу #189422 на доработку"],
        settings=SETTINGS,
        semantic_search=_no_semantic,
    )
    assert result["candidates"][0]["entity_id"] == str(task.id)
    assert "exact_external_id" in result["candidates"][0]["identity_match"][
        "methods"
    ]


def test_exact_canonical_name_is_found_without_external_id() -> None:
    entity = _entity("МИ_СоздатьУпаковкуДляНоменклатуры")
    session = FakeSession([[entity], [entity], [], [], [], []])
    result = resolve_identity_candidates(
        session,
        ["Изменена МИ_СоздатьУпаковкуДляНоменклатуры."],
        settings=SETTINGS,
        semantic_search=_no_semantic,
    )
    assert result["candidates"][0]["entity_id"] == str(entity.id)
    assert "exact_normalized_name" in result["candidates"][0][
        "identity_match"
    ]["methods"]


def test_semantic_evidence_maps_chunk_to_existing_entity() -> None:
    entity = _entity("МИ_СоздатьУпаковкуДляНоменклатуры")
    chunk_id = uuid.uuid4()
    claim = KnowledgeClaim(
        id=uuid.uuid4(),
        subject_entity_id=entity.id,
        predicate="date_relevance_rule",
        object_text="Дата актуальности равна дате создания упаковки",
        claim_text="Правило даты актуальности",
        evidence_type="direct_statement",
        confidence=1.0,
        status="active",
        claim_key="claim-key",
        claim_hash="a" * 64,
        attributes_json={},
        created_by="test",
    )
    evidence = KnowledgeEvidence(
        id=uuid.uuid4(),
        claim_id=claim.id,
        event_id=None,
        source_type="email",
        source_id=uuid.uuid4(),
        source_ref=f"workmind://email/{uuid.uuid4()}",
        chunk_id=chunk_id,
        excerpt="Дата актуальности автоматически ставится датой создания.",
        evidence_role="direct_statement",
        evidence_hash="b" * 64,
    )
    session = FakeSession(
        [[], [], [entity], [], [], [], []],
        execute_results=[[(evidence, claim, None)]],
    )
    result = resolve_identity_candidates(
        session,
        ["Теперь дата упаковки заполняется автоматически."],
        settings=SETTINGS,
        semantic_search=_semantic(chunk_id),
    )
    candidate = result["candidates"][0]
    assert candidate["entity_id"] == str(entity.id)
    assert candidate["identity_match"]["methods"] == ["semantic_evidence"]


def test_exact_semantic_and_lexical_methods_are_merged_once() -> None:
    entity = _entity(
        "WMS Создание упаковок 189422",
        external_id="189422",
    )
    chunk_id = uuid.uuid4()
    claim = KnowledgeClaim(
        id=uuid.uuid4(),
        subject_entity_id=entity.id,
        predicate="status",
        object_text="active",
        claim_text="Active",
        evidence_type="direct_statement",
        confidence=1.0,
        status="active",
        claim_key="merged-claim",
        claim_hash="c" * 64,
        attributes_json={},
        created_by="test",
    )
    evidence = KnowledgeEvidence(
        id=uuid.uuid4(),
        claim_id=claim.id,
        event_id=None,
        source_type="email",
        source_id=uuid.uuid4(),
        source_ref=f"workmind://email/{uuid.uuid4()}",
        chunk_id=chunk_id,
        excerpt="WMS Создание упаковок 189422",
        evidence_role="direct_statement",
        evidence_hash="d" * 64,
    )
    session = FakeSession(
        [[entity], [entity], [entity], [], [], [], []],
        execute_results=[[(evidence, claim, None)]],
    )
    result = resolve_identity_candidates(
        session,
        ["WMS Создание упаковок #189422"],
        settings=SETTINGS,
        semantic_search=_semantic(chunk_id),
    )
    assert len(result["candidates"]) == 1
    assert set(result["candidates"][0]["identity_match"]["methods"]) == {
        "exact_external_id",
        "exact_normalized_name",
        "lexical_name",
        "semantic_evidence",
    }


def test_semantic_failure_keeps_lexical_candidate_and_warning() -> None:
    entity = _entity("МИ_СоздатьУпаковкуДляНоменклатуры")
    session = FakeSession([[entity], [entity], [], [], [], []])

    def failed(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("qdrant unavailable")

    result = resolve_identity_candidates(
        session,
        ["МИ_СоздатьУпаковкуДляНоменклатуры изменена"],
        settings=SETTINGS,
        semantic_search=failed,
    )
    assert result["candidates"][0]["entity_id"] == str(entity.id)
    assert "qdrant unavailable" in result["diagnostics"]["warnings"][0]


def test_historical_identity_chunk_never_expands_allowed_evidence() -> None:
    target_chunk = uuid.uuid4()
    historical_chunk = uuid.uuid4()
    allowed = _allowed_evidence_from_chunks(
        [
            {
                "source_ref": f"workmind://email/{uuid.uuid4()}",
                "chunk_id": str(target_chunk),
            }
        ]
    )
    identity_context = {
        "identity_match": {
            "reasons": [f"historical evidence chunk {historical_chunk} is similar"]
        }
    }
    assert allowed == [
        {
            "source_ref": allowed[0]["source_ref"],
            "chunk_id": str(target_chunk),
        }
    ]
    assert str(historical_chunk) not in {item["chunk_id"] for item in allowed}
    assert identity_context


def test_candidate_contains_active_recent_claims_and_events() -> None:
    entity = _entity("WMS")
    claim = KnowledgeClaim(
        id=uuid.uuid4(),
        subject_entity_id=entity.id,
        predicate="current_status",
        object_text="Запущена",
        claim_text="WMS запущена",
        evidence_type="direct_statement",
        confidence=1.0,
        status="active",
        claim_key="active-claim",
        claim_hash="e" * 64,
        attributes_json={},
        created_by="test",
    )
    event = KnowledgeEvent(
        id=uuid.uuid4(),
        entity_id=entity.id,
        event_type="launch",
        title="Запуск WMS",
        occurred_at=datetime(2021, 11, 1, tzinfo=UTC),
        status="confirmed",
        event_hash="f" * 64,
        attributes_json={},
        created_by="test",
    )
    session = FakeSession([[entity], [claim], [], [], [event]])
    result = resolve_identity_candidates(
        session,
        ["WMS"],
        settings=SETTINGS,
        semantic_search=_no_semantic,
    )
    candidate = result["candidates"][0]
    assert candidate["active_claims"][0]["claim_id"] == str(claim.id)
    assert candidate["recent_claims"] == []
    assert candidate["recent_events"][0]["event_id"] == str(event.id)


def test_one_generic_word_does_not_create_lexical_system_match() -> None:
    entity = _entity("Обработка возвратов")
    score, reasons = lexical_identity_score(
        "Изменена обработка создания упаковок",
        entity,
    )
    assert score == 0.0
    assert reasons == []


@pytest.mark.parametrize(
    ("source", "name", "minimum", "maximum"),
    [
        ("Warehouse Routing Core", "Warehouse Routing Core", 0.98, 1.0),
        ("Supplier acceptance", "Warehouse Routing Core", 0.0, 0.0),
        ("Изменена обработка", "Обработка возвратов", 0.0, 0.0),
        (
            "Изменены Warehouse Routing правила",
            "Warehouse Routing Core",
            0.62,
            0.95,
        ),
        (
            "Сбой затронул OrderRouter-v2",
            "Модуль маршрутизации OrderRouter-v2",
            0.86,
            0.95,
        ),
        ("Запрос направлен в ДИТ", "ДИТ Платформа интеграции", 0.0, 0.61),
    ],
)
def test_lexical_scoring_matrix(
    source: str,
    name: str,
    minimum: float,
    maximum: float,
) -> None:
    score, _reasons = lexical_identity_score(source, _entity(name))
    assert minimum <= score <= maximum


def test_strong_candidate_marks_similar_new_entity_as_duplicate_risk() -> None:
    candidate_id = uuid.uuid4()
    result = duplicate_risk_diagnostics(
        {
            "entities": [
                {
                    "key": "packaging",
                    "entity_type": "system",
                    "canonical_name": "Создание упаковок WMS",
                }
            ]
        },
        [
            {
                "entity_id": str(candidate_id),
                "entity_type": "system",
                "canonical_name": "WMS Создание упаковок",
                "identity_match": {
                    "score": 0.91,
                    "methods": ["semantic_evidence", "lexical_name"],
                },
            }
        ],
        threshold=0.78,
    )
    assert result["detected"] is True
    assert result["candidate_entity_ids"] == [str(candidate_id)]


def test_new_entity_without_strong_candidate_has_no_duplicate_risk() -> None:
    result = duplicate_risk_diagnostics(
        {
            "entities": [
                {
                    "key": "new-process",
                    "entity_type": "process",
                    "canonical_name": "Приемка новых поставщиков",
                }
            ]
        },
        [],
        threshold=0.78,
    )
    assert result["detected"] is False
    assert result["count"] == 0


def test_external_id_extraction_rejects_date_but_keeps_stable_signals() -> None:
    assert extract_external_ids(["Дата 2026-08-13, время 11:03"]) == []
    assert extract_external_ids(["2026-08-13T11:03:21+00:00"]) == []
    assert extract_external_ids(["Проверить #189422"]) == ["189422"]
    assert extract_external_ids(["задача 189422"]) == ["189422"]
    assert "INC-203823" in extract_external_ids(["Инцидент INC-203823"])


def test_reused_candidate_does_not_hide_second_new_duplicate() -> None:
    candidate_id = uuid.uuid4()
    result = duplicate_risk_diagnostics(
        {
            "entities": [
                {"key": "reuse", "entity_id": str(candidate_id)},
                {
                    "key": "duplicate",
                    "entity_type": "system",
                    "canonical_name": "WMS создание упаковок",
                },
            ]
        },
        [
            {
                "entity_id": str(candidate_id),
                "entity_type": "system",
                "canonical_name": "Создание упаковок WMS",
                "identity_match": {"score": 0.91, "methods": ["lexical_name"]},
            }
        ],
        threshold=0.78,
    )
    assert result["detected"] is True
    assert result["items"][0]["proposed_key"] == "duplicate"


def test_distinct_external_ids_and_unrelated_names_are_not_duplicates() -> None:
    result = duplicate_risk_diagnostics(
        {
            "entities": [
                {
                    "key": "other",
                    "entity_type": "task",
                    "canonical_name": "Задача маршрутизации",
                    "external_id": "777777",
                }
            ]
        },
        [
            {
                "entity_id": str(uuid.uuid4()),
                "entity_type": "task",
                "canonical_name": "Задача кластерного отбора",
                "external_id": "189422",
                "identity_match": {"score": 1.0, "methods": ["exact_external_id"]},
            }
        ],
        threshold=0.78,
    )
    assert result["detected"] is False


def test_typed_conflict_candidate_does_not_drive_duplicate_risk() -> None:
    result = duplicate_risk_diagnostics(
        {
            "entities": [
                {
                    "key": "incident",
                    "entity_type": "incident",
                    "canonical_name": "Incident 189422",
                    "external_id": "189422",
                }
            ]
        },
        [
            {
                "entity_id": str(uuid.uuid4()),
                "entity_type": "incident",
                "canonical_name": "Incident 189422",
                "external_id": "189422",
                "identity_match": {
                    "score": 0.95,
                    "methods": ["semantic_evidence"],
                    "type_compatibility": "typed_conflict",
                },
            }
        ],
        threshold=0.78,
    )
    assert result["detected"] is False


def test_generic_acronym_alone_is_not_a_strong_identity_signal() -> None:
    entity = _entity("ДИТ Платформа интеграции")
    score, _reasons = lexical_identity_score("Запрос направлен в ДИТ", entity)
    assert score < 0.62


def test_ontology_reports_generic_and_cross_type_ambiguity() -> None:
    diagnostics = ontology_diagnostics(
        {
            "entities": [
                {
                    "key": "packaging",
                    "entity_type": "process",
                    "canonical_name": "Обработка по созданию упаковок",
                }
            ]
        },
        [
            {
                "entity_id": str(uuid.uuid4()),
                "entity_type": "system",
                "canonical_name": "Обработка создания упаковок",
                "identity_match": {"score": 0.9, "methods": ["semantic_evidence"]},
            }
        ],
        threshold=0.78,
    )
    warning_types = {item["warning"] for item in diagnostics["warnings"]}
    assert warning_types == {"cross_type_ambiguity", "generic_identity"}


def test_quality_metrics_extend_status_without_replacing_counts() -> None:
    counts = {"validated": 2, "skipped": 3, "completed": 4}
    metrics = _quality_metrics(
        [
            (
                "validated",
                {
                    "candidate_resolution": {
                        "exact_candidate_hits": 1,
                        "semantic_candidate_hits": 2,
                        "lexical_candidate_hits": 1,
                        "merged_identity_candidate_count": 2,
                    },
                    "quality": {
                        "existing_entities_reused": 1,
                        "new_entities_proposed": 1,
                        "duplicate_risk_count": 1,
                        "validation_failures": 0,
                    },
                },
            )
        ],
        counts,
    )
    assert metrics["exact_candidate_hits"] == 1
    assert metrics["existing_entities_reused"] == 1
    assert metrics["duplicate_risk_count"] == 1
    assert metrics["skipped_jobs"] == 3
    assert metrics["completed_jobs"] == 4
    assert metrics["validated_jobs"] == 2


def test_legacy_quality_failure_count_becomes_history_baseline() -> None:
    previous = {
        "candidate_resolution": {"exact_candidate_hits": 1},
        "quality": {"validation_failures": 2},
    }
    history = _append_quality_event(
        [],
        attempt=3,
        event_type="preflight_failure",
        details="third attempt",
        limit=50,
    )
    state = _validation_state(previous, {"ok": False}, history=history)
    assert state["quality_history_base_failures"] == 2
    assert state["quality"]["validation_failures"] == 3
    assert state["candidate_resolution"] == {"exact_candidate_hits": 1}
    metrics = _quality_metrics([("pending", state)], {"pending": 1})
    assert metrics["validation_failures"] == 3
