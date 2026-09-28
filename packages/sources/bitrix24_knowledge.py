
from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from decimal import Decimal
import re
from typing import Any
from typing import Sequence
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.database.models import (
    B24Task,
    B24TaskObservation,
    KnowledgeEntity,
)
from packages.knowledge.service import normalize_text


B24_SOURCE_NAMESPACE = "b24_task"
B24_OBSERVATION_SOURCE_TYPE = "b24_observation"

KNOWN_TITLE_PREFIX = re.compile(
    r"^\s*\[\s*зни\s*\]\s*:?\s*",
    flags=re.IGNORECASE,
)


class B24KnowledgePlanningError(ValueError):
    pass


@dataclass(frozen=True)
class B24KnowledgeEntityPlan:
    task_external_id: str
    action: str
    existing_entity_id: str | None
    canonical_name: str
    source_title: str | None
    description: str | None
    desired_external_id: str
    desired_attributes: dict[str, Any]
    changed_fields: tuple[str, ...]
    observation_id: str
    evidence_source_type: str
    evidence_source_ref: str
    evidence_source_date: str | None
    parent_external_id: str | None
    parent_resolution: str | None
    parent_relation_candidate: bool
    collision_entity_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _json_value(
    value: Any,
) -> Any:
    if isinstance(
        value,
        datetime,
    ):
        return value.isoformat()

    if isinstance(
        value,
        date,
    ):
        return value.isoformat()

    if isinstance(
        value,
        Decimal,
    ):
        return str(value)

    if isinstance(
        value,
        uuid.UUID,
    ):
        return str(value)

    if isinstance(
        value,
        dict,
    ):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
        }

    if isinstance(
        value,
        (list, tuple),
    ):
        return [
            _json_value(item)
            for item in value
        ]

    return value


def clean_b24_title(
    title: str | None,
) -> str | None:
    if title is None:
        return None

    value = str(
        title
    ).strip()

    if not value:
        return None

    stripped = KNOWN_TITLE_PREFIX.sub(
        "",
        value,
        count=1,
    ).strip()

    return (
        stripped
        if stripped
        else value
    )


def canonical_b24_task_name(
    external_id: str,
    title: str | None,
) -> str:
    task_id = str(
        external_id
    ).strip()

    if not task_id:
        raise B24KnowledgePlanningError(
            "B24 Task ID must not be empty"
        )

    clean_title = clean_b24_title(
        title
    )

    if clean_title:
        return (
            f"Б24 №{task_id} "
            f"«{clean_title}»"
        )

    return (
        f"Б24 №{task_id}"
    )


def _task_value(
    task: Any,
    field: str,
) -> Any:
    return _json_value(
        getattr(
            task,
            field,
            None,
        )
    )


def build_b24_attributes(
    task: Any,
    observation: Any,
) -> dict[str, Any]:
    observation_id = str(
        observation.id
    )

    snapshot_id = str(
        observation.snapshot_id
    )

    fields = (
        "source_url",
        "project_external_id",
        "project_name",
        "status",
        "stage",
        "last_activity_at",
        "deadline_at",
        "created_at_source",
        "started_at",
        "updated_at_source",
        "closed_at",
        "creator_label",
        "assignee_label",
        "coassignee_labels",
        "observer_labels",
        "additional_employee_labels",
        "parent_external_id",
        "parent_title_snapshot",
        "parent_stage_snapshot",
        "planned_duration_minutes",
        "spent_minutes",
        "track_work_time",
        "tags",
        "business_direction",
        "request_type",
        "task_kind",
        "product",
        "planned_at",
        "planned_release_at",
        "actual_release_at",
        "deadline_change_in_progress",
        "deadline_change_count",
        "ai_used",
        "helpdesk_number",
        "progress_percent",
        "crm_context_raw",
        "flow_raw",
        "priority_raw",
        "deadline_change_request_raw",
        "deadline_change_reason",
        "approval_comment",
        "approval_result",
    )

    result: dict[str, Any] = {
        "source_namespace": (
            B24_SOURCE_NAMESPACE
        ),
        "source_type": "b24_task",
        "task_id": str(
            task.external_id
        ),
        "source_title": (
            _task_value(
                task,
                "title",
            )
        ),
    }

    for field in fields:
        result[field] = _task_value(
            task,
            field,
        )

    result.update(
        {
            "latest_observation_id": (
                observation_id
            ),
            "latest_snapshot_id": (
                snapshot_id
            ),
            "observation_source_type": (
                B24_OBSERVATION_SOURCE_TYPE
            ),
            "observation_source_ref": (
                "workmind://"
                + B24_OBSERVATION_SOURCE_TYPE
                + "/"
                + observation_id
            ),
            "observed_at": _json_value(
                observation.observed_at
            ),
            "raw_hash": str(
                observation.raw_hash
            ),
            "canonical_hash": str(
                observation.canonical_hash
            ),
            "extraction_input_hash": str(
                observation.extraction_input_hash
            ),
            "contract_version": str(
                observation.contract_version
            ),
            "normalizer_version": str(
                observation.normalizer_version
            ),
        }
    )

    return result


def _b24_identity_from_entity(
    entity: Any,
) -> str | None:
    attributes = (
        entity.attributes_json
        if isinstance(
            entity.attributes_json,
            dict,
        )
        else {}
    )

    b24 = attributes.get(
        "b24"
    )

    if not isinstance(
        b24,
        dict,
    ):
        return None

    if (
        b24.get(
            "source_namespace"
        )
        != B24_SOURCE_NAMESPACE
    ):
        return None

    task_id = b24.get(
        "task_id"
    )

    if task_id in (
        None,
        "",
    ):
        return None

    return str(
        task_id
    )


def _identity_matches(
    task_external_id: str,
    entities: Sequence[Any],
) -> list[Any]:
    source_matches = [
        entity
        for entity in entities
        if (
            _b24_identity_from_entity(
                entity
            )
            == task_external_id
        )
    ]

    if source_matches:
        return source_matches

    return [
        entity
        for entity in entities
        if (
            entity.external_id
            is not None
            and str(
                entity.external_id
            )
            == task_external_id
        )
    ]


def plan_b24_task(
    task: Any,
    observation: Any,
    *,
    existing_task_entities: Sequence[Any],
    source_task_ids: set[str],
) -> B24KnowledgeEntityPlan:
    external_id = str(
        task.external_id
    )

    canonical_name = (
        canonical_b24_task_name(
            external_id,
            getattr(
                task,
                "title",
                None,
            ),
        )
    )

    normalized_key = normalize_text(
        canonical_name
    )

    identity_matches = (
        _identity_matches(
            external_id,
            existing_task_entities,
        )
    )

    if len(identity_matches) > 1:
        return B24KnowledgeEntityPlan(
            task_external_id=external_id,
            action="collision",
            existing_entity_id=None,
            canonical_name=canonical_name,
            source_title=getattr(
                task,
                "title",
                None,
            ),
            description=getattr(
                task,
                "description",
                None,
            ),
            desired_external_id=external_id,
            desired_attributes={},
            changed_fields=(),
            observation_id=str(
                observation.id
            ),
            evidence_source_type=(
                B24_OBSERVATION_SOURCE_TYPE
            ),
            evidence_source_ref=(
                "workmind://"
                + B24_OBSERVATION_SOURCE_TYPE
                + "/"
                + str(
                    observation.id
                )
            ),
            evidence_source_date=(
                _json_value(
                    observation.observed_at
                )
            ),
            parent_external_id=(
                str(
                    task.parent_external_id
                )
                if getattr(
                    task,
                    "parent_external_id",
                    None,
                )
                else None
            ),
            parent_resolution=None,
            parent_relation_candidate=False,
            collision_entity_ids=tuple(
                sorted(
                    str(
                        entity.id
                    )
                    for entity
                    in identity_matches
                )
            ),
        )

    existing = (
        identity_matches[0]
        if identity_matches
        else None
    )

    if existing is None:
        name_collisions = [
            entity
            for entity
            in existing_task_entities
            if (
                str(
                    entity.normalized_key
                    or ""
                )
                == normalized_key
            )
        ]

        if name_collisions:
            return B24KnowledgeEntityPlan(
                task_external_id=external_id,
                action="collision",
                existing_entity_id=None,
                canonical_name=canonical_name,
                source_title=getattr(
                    task,
                    "title",
                    None,
                ),
                description=getattr(
                    task,
                    "description",
                    None,
                ),
                desired_external_id=external_id,
                desired_attributes={},
                changed_fields=(),
                observation_id=str(
                    observation.id
                ),
                evidence_source_type=(
                    B24_OBSERVATION_SOURCE_TYPE
                ),
                evidence_source_ref=(
                    "workmind://"
                    + B24_OBSERVATION_SOURCE_TYPE
                    + "/"
                    + str(
                        observation.id
                    )
                ),
                evidence_source_date=(
                    _json_value(
                        observation.observed_at
                    )
                ),
                parent_external_id=(
                    str(
                        task.parent_external_id
                    )
                    if getattr(
                        task,
                        "parent_external_id",
                        None,
                    )
                    else None
                ),
                parent_resolution=None,
                parent_relation_candidate=False,
                collision_entity_ids=tuple(
                    sorted(
                        str(
                            entity.id
                        )
                        for entity
                        in name_collisions
                    )
                ),
            )

    b24_attributes = build_b24_attributes(
        task,
        observation,
    )

    existing_attributes = (
        dict(
            existing.attributes_json
            or {}
        )
        if existing is not None
        else {}
    )

    desired_attributes = dict(
        existing_attributes
    )

    desired_attributes[
        "b24"
    ] = b24_attributes

    description = getattr(
        task,
        "description",
        None,
    )

    changed_fields: list[str] = []

    if existing is None:
        action = "create"
    else:
        if (
            existing.canonical_name
            != canonical_name
        ):
            changed_fields.append(
                "canonical_name"
            )

        if (
            str(
                existing.external_id
                or ""
            )
            != external_id
        ):
            changed_fields.append(
                "external_id"
            )

        if (
            existing.description
            != description
        ):
            changed_fields.append(
                "description"
            )

        if (
            existing.attributes_json
            != desired_attributes
        ):
            changed_fields.append(
                "attributes_json"
            )

        action = (
            "update"
            if changed_fields
            else "noop"
        )

    parent_external_id = (
        str(
            task.parent_external_id
        )
        if getattr(
            task,
            "parent_external_id",
            None,
        )
        else None
    )

    if parent_external_id is None:
        parent_resolution = None
        relation_candidate = False
    elif (
        parent_external_id
        in source_task_ids
    ):
        parent_resolution = (
            "resolved_in_b24_source_layer"
        )
        relation_candidate = True
    else:
        parent_resolution = (
            "unresolved_external_reference"
        )
        relation_candidate = False

    return B24KnowledgeEntityPlan(
        task_external_id=external_id,
        action=action,
        existing_entity_id=(
            str(
                existing.id
            )
            if existing is not None
            else None
        ),
        canonical_name=canonical_name,
        source_title=getattr(
            task,
            "title",
            None,
        ),
        description=description,
        desired_external_id=external_id,
        desired_attributes=(
            desired_attributes
        ),
        changed_fields=tuple(
            changed_fields
        ),
        observation_id=str(
            observation.id
        ),
        evidence_source_type=(
            B24_OBSERVATION_SOURCE_TYPE
        ),
        evidence_source_ref=(
            "workmind://"
            + B24_OBSERVATION_SOURCE_TYPE
            + "/"
            + str(
                observation.id
            )
        ),
        evidence_source_date=(
            _json_value(
                observation.observed_at
            )
        ),
        parent_external_id=(
            parent_external_id
        ),
        parent_resolution=(
            parent_resolution
        ),
        parent_relation_candidate=(
            relation_candidate
        ),
        collision_entity_ids=(),
    )


def _latest_observation(
    session: Session,
    task: B24Task,
) -> B24TaskObservation:
    if task.last_snapshot_id is None:
        raise B24KnowledgePlanningError(
            "B24 task has no last_snapshot_id: "
            + str(
                task.external_id
            )
        )

    rows = list(
        session.scalars(
            select(
                B24TaskObservation
            ).where(
                B24TaskObservation.task_id
                == task.id,
                B24TaskObservation.snapshot_id
                == task.last_snapshot_id,
            )
        )
    )

    if len(rows) != 1:
        raise B24KnowledgePlanningError(
            "Expected exactly one current observation "
            "for B24 task "
            + str(
                task.external_id
            )
            + ", found "
            + str(
                len(rows)
            )
        )

    return rows[0]


def build_b24_knowledge_plan(
    session: Session,
) -> dict[str, Any]:
    tasks = list(
        session.scalars(
            select(
                B24Task
            ).order_by(
                B24Task.external_id
            )
        )
    )

    existing_task_entities = list(
        session.scalars(
            select(
                KnowledgeEntity
            ).where(
                KnowledgeEntity.entity_type
                == "task"
            ).order_by(
                KnowledgeEntity.canonical_name
            )
        )
    )

    source_task_ids = {
        str(
            task.external_id
        )
        for task in tasks
    }

    plans: list[
        B24KnowledgeEntityPlan
    ] = []

    for task in tasks:
        observation = (
            _latest_observation(
                session,
                task,
            )
        )

        plans.append(
            plan_b24_task(
                task,
                observation,
                existing_task_entities=(
                    existing_task_entities
                ),
                source_task_ids=(
                    source_task_ids
                ),
            )
        )

    summary = {
        "total_tasks": len(
            plans
        ),
        "create_entities": sum(
            item.action == "create"
            for item in plans
        ),
        "update_entities": sum(
            item.action == "update"
            for item in plans
        ),
        "noop_entities": sum(
            item.action == "noop"
            for item in plans
        ),
        "collision_entities": sum(
            item.action == "collision"
            for item in plans
        ),
        "identity_evidence_candidates": sum(
            item.action
            != "collision"
            for item in plans
        ),
        "people_to_create": 0,
        "parent_relation_candidates": sum(
            item.parent_relation_candidate
            for item in plans
        ),
        "unresolved_parent_reference_count": sum(
            item.parent_resolution
            == "unresolved_external_reference"
            for item in plans
        ),
        "resolved_parent_ids": sorted(
            {
                item.parent_external_id
                for item in plans
                if (
                    item.parent_resolution
                    == "resolved_in_b24_source_layer"
                    and item.parent_external_id
                )
            }
        ),
        "unresolved_parent_ids": sorted(
            {
                item.parent_external_id
                for item in plans
                if (
                    item.parent_resolution
                    == "unresolved_external_reference"
                    and item.parent_external_id
                )
            }
        ),
    }

    return {
        "summary": summary,
        "entities": [
            item.to_dict()
            for item in plans
        ],
    }
