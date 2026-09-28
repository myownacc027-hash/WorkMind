
from __future__ import annotations

from datetime import UTC
from datetime import datetime
from types import SimpleNamespace
import uuid

from packages.knowledge.service import normalize_text
from packages.sources.bitrix24_knowledge import (
    build_b24_attributes,
    canonical_b24_task_name,
    clean_b24_title,
    plan_b24_task,
)


def _task(
    *,
    external_id: str = "189422",
    title: str | None = (
        "[ЗнИ]: Оптимизация Кластерного отбора"
    ),
    description: str | None = "Description",
    parent_external_id: str | None = None,
):
    return SimpleNamespace(
        external_id=external_id,
        title=title,
        description=description,
        source_url=(
            "https://b24/workgroups/group/13/"
            "tasks/task/view/"
            + external_id
            + "/"
        ),
        project_external_id="13",
        project_name=(
            "001 Автоматизация/Разработка"
        ),
        status="Ждёт выполнения",
        stage="Необходимо сделать",
        last_activity_at=datetime(
            2026,
            9,
            24,
            tzinfo=UTC,
        ),
        deadline_at=datetime(
            2026,
            10,
            1,
            tzinfo=UTC,
        ),
        created_at_source=datetime(
            2026,
            1,
            1,
            tzinfo=UTC,
        ),
        started_at=None,
        updated_at_source=datetime(
            2026,
            9,
            24,
            tzinfo=UTC,
        ),
        closed_at=None,
        creator_label="Creator",
        assignee_label="Assignee",
        coassignee_labels=[],
        observer_labels=[
            "Observer One",
            "Observer Two",
        ],
        additional_employee_labels=[],
        parent_external_id=(
            parent_external_id
        ),
        parent_title_snapshot=None,
        parent_stage_snapshot=None,
        planned_duration_minutes=None,
        spent_minutes=5,
        track_work_time=True,
        tags=["ЗнИ"],
        business_direction="ДСЛ",
        request_type="Запрос на Изменение",
        task_kind=None,
        product="1С: WMS",
        planned_at=None,
        planned_release_at=None,
        actual_release_at=None,
        deadline_change_in_progress=False,
        deadline_change_count=0,
        ai_used=None,
        helpdesk_number=None,
        progress_percent=None,
        crm_context_raw=None,
        flow_raw=None,
        priority_raw=None,
        deadline_change_request_raw=None,
        deadline_change_reason=None,
        approval_comment=None,
        approval_result=None,
    )


def _observation():
    return SimpleNamespace(
        id=uuid.uuid4(),
        snapshot_id=uuid.uuid4(),
        observed_at=datetime(
            2026,
            9,
            28,
            tzinfo=UTC,
        ),
        raw_hash="a" * 64,
        canonical_hash="b" * 64,
        extraction_input_hash="c" * 64,
        contract_version="b24-source-v1",
        normalizer_version="b24-normalizer-v1",
    )


def _entity(
    *,
    external_id: str | None,
    canonical_name: str,
    attributes: dict | None = None,
    description: str | None = None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        entity_type="task",
        external_id=external_id,
        canonical_name=canonical_name,
        normalized_key=normalize_text(
            canonical_name
        ),
        attributes_json=(
            attributes
            or {}
        ),
        description=description,
    )


def test_known_zni_prefix_is_removed_only_from_canonical_title() -> None:
    assert (
        clean_b24_title(
            "[ЗнИ]: Test title"
        )
        == "Test title"
    )

    assert (
        canonical_b24_task_name(
            "123",
            "[ЗнИ]: Test title",
        )
        == "Б24 №123 «Test title»"
    )


def test_unknown_prefix_is_preserved() -> None:
    assert (
        clean_b24_title(
            "[OTHER]: Test title"
        )
        == "[OTHER]: Test title"
    )


def test_source_title_and_participant_labels_are_preserved() -> None:
    task = _task()
    observation = _observation()

    attributes = build_b24_attributes(
        task,
        observation,
    )

    assert (
        attributes[
            "source_title"
        ]
        == task.title
    )

    assert (
        attributes[
            "observer_labels"
        ]
        == [
            "Observer One",
            "Observer Two",
        ]
    )

    assert (
        attributes[
            "source_namespace"
        ]
        == "b24_task"
    )

    assert (
        attributes[
            "observation_source_type"
        ]
        == "b24_observation"
    )


def test_exact_external_id_updates_existing_entity_without_duplicate() -> None:
    task = _task()
    observation = _observation()

    existing = _entity(
        external_id="189422",
        canonical_name=(
            "Б24 №189422 "
            "«Оптимизация Кластерного отбора»"
        ),
        attributes={
            "other_system": {
                "keep": True,
            }
        },
        description="Old description",
    )

    plan = plan_b24_task(
        task,
        observation,
        existing_task_entities=[
            existing
        ],
        source_task_ids={
            "189422"
        },
    )

    assert plan.action == "update"
    assert (
        plan.existing_entity_id
        == str(
            existing.id
        )
    )

    assert (
        plan.desired_attributes[
            "other_system"
        ][
            "keep"
        ]
        is True
    )

    assert (
        plan.desired_attributes[
            "b24"
        ][
            "task_id"
        ]
        == "189422"
    )


def test_name_collision_without_source_identity_is_blocked() -> None:
    task = _task(
        external_id="123",
        title="Collision",
    )

    observation = _observation()

    existing = _entity(
        external_id="999",
        canonical_name=(
            "Б24 №123 «Collision»"
        ),
    )

    plan = plan_b24_task(
        task,
        observation,
        existing_task_entities=[
            existing
        ],
        source_task_ids={
            "123"
        },
    )

    assert plan.action == "collision"
    assert plan.existing_entity_id is None
    assert plan.collision_entity_ids == (
        str(
            existing.id
        ),
    )


def test_parent_relation_is_only_candidate_when_parent_is_present() -> None:
    observation = _observation()

    resolved = plan_b24_task(
        _task(
            external_id="2",
            parent_external_id="1",
        ),
        observation,
        existing_task_entities=[],
        source_task_ids={
            "1",
            "2",
        },
    )

    unresolved = plan_b24_task(
        _task(
            external_id="2",
            parent_external_id="99",
        ),
        observation,
        existing_task_entities=[],
        source_task_ids={
            "2",
        },
    )

    assert (
        resolved.parent_resolution
        == "resolved_in_b24_source_layer"
    )

    assert (
        resolved.parent_relation_candidate
        is True
    )

    assert (
        unresolved.parent_resolution
        == "unresolved_external_reference"
    )

    assert (
        unresolved.parent_relation_candidate
        is False
    )
