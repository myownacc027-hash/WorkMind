
from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.database.models import (
    B24ImportSnapshot,
    B24Task,
    B24TaskObservation,
)
from packages.sources.bitrix24 import (
    CONTRACT_VERSION,
    NORMALIZER_VERSION,
    B24WorkbookSnapshot,
    build_extraction_input,
    stable_payload_hash,
)


CANONICAL_MODEL_FIELDS = {
    "title",
    "description",
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
    "status",
    "stage",
    "project_name",
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
    "source_url",
    "project_external_id",
}


def _merge_present_columns(
    previous: list[str] | None,
    incoming: tuple[str, ...],
) -> list[str]:
    return list(
        dict.fromkeys(
            [
                *(previous or []),
                *incoming,
            ]
        )
    )


def _apply_canonical_columns(
    target: B24Task,
    incoming: dict[str, Any],
) -> None:
    unsupported = sorted(
        set(incoming)
        - CANONICAL_MODEL_FIELDS
        - {"external_id"}
    )

    if unsupported:
        raise ValueError(
            "Unsupported B24 canonical fields: "
            + ", ".join(unsupported)
        )

    for field in CANONICAL_MODEL_FIELDS:
        if field in incoming:
            setattr(
                target,
                field,
                incoming[field],
            )


def import_b24_snapshot(
    session: Session,
    snapshot: B24WorkbookSnapshot,
    *,
    transport: str = "xlsx",
    source_name: str | None = None,
) -> dict[str, Any]:
    transport_value = transport.strip().lower()

    if not transport_value:
        raise ValueError(
            "transport must not be empty"
        )

    existing_snapshot = session.scalar(
        select(B24ImportSnapshot).where(
            B24ImportSnapshot.transport
            == transport_value,
            B24ImportSnapshot.source_hash
            == snapshot.source_hash,
        )
    )

    if existing_snapshot is not None:
        return {
            "ok": True,
            "already_imported": True,
            "snapshot_id": str(
                existing_snapshot.id
            ),
            "source_hash": snapshot.source_hash,
            "task_count": snapshot.task_count,
            "created_tasks": 0,
            "updated_tasks": 0,
            "observations_created": 0,
            "canonical_changed": 0,
            "extraction_input_changed": 0,
        }

    imported_at = datetime.now(UTC)

    snapshot_row = B24ImportSnapshot(
        id=uuid.uuid4(),
        transport=transport_value,
        source_name=(
            source_name
            or snapshot.source_path.name
        ),
        source_hash=snapshot.source_hash,
        source_timezone=snapshot.source_timezone,
        present_columns=list(
            snapshot.present_columns
        ),
        row_count=snapshot.row_count,
        task_count=snapshot.task_count,
        skipped_row_count=(
            snapshot.skipped_row_count
        ),
        metadata_json={
            "sheet_name": snapshot.sheet_name,
            "contract_version": CONTRACT_VERSION,
            "normalizer_version": NORMALIZER_VERSION,
            "hyperlink_count": snapshot.hyperlink_count,
            "formula_count": snapshot.formula_count,
        },
        imported_at=imported_at,
    )

    session.add(
        snapshot_row
    )

    created_tasks = 0
    updated_tasks = 0
    observations_created = 0
    canonical_changed = 0
    extraction_input_changed = 0

    for record in snapshot.tasks:
        task = session.scalar(
            select(B24Task).where(
                B24Task.external_id
                == record.external_id
            )
        )

        if task is None:
            effective_canonical = dict(
                record.canonical_json
            )

            effective_extraction = (
                build_extraction_input(
                    effective_canonical
                )
            )

            effective_canonical_hash = (
                stable_payload_hash(
                    effective_canonical
                )
            )

            effective_extraction_hash = (
                stable_payload_hash(
                    effective_extraction
                )
            )

            task = B24Task(
                id=uuid.uuid4(),
                external_id=record.external_id,
                last_snapshot_id=snapshot_row.id,
                transport=transport_value,
                source_timezone=(
                    snapshot.source_timezone
                ),
                present_columns=list(
                    record.present_columns
                ),
                raw_row=dict(
                    record.raw_row
                ),
                raw_hyperlinks=dict(
                    record.raw_hyperlinks
                ),
                canonical_json=(
                    effective_canonical
                ),
                extraction_input_json=(
                    effective_extraction
                ),
                raw_hash=record.raw_hash,
                canonical_hash=(
                    effective_canonical_hash
                ),
                extraction_input_hash=(
                    effective_extraction_hash
                ),
                first_imported_at=imported_at,
                last_seen_at=imported_at,
            )

            _apply_canonical_columns(
                task,
                record.canonical,
            )

            session.add(
                task
            )

            created_tasks += 1
            canonical_changed += 1
            extraction_input_changed += 1
        else:
            previous_canonical_hash = (
                task.canonical_hash
            )

            previous_extraction_hash = (
                task.extraction_input_hash
            )

            effective_canonical = dict(
                task.canonical_json
                or {}
            )

            effective_canonical.update(
                record.canonical_json
            )

            effective_extraction = (
                build_extraction_input(
                    effective_canonical
                )
            )

            effective_canonical_hash = (
                stable_payload_hash(
                    effective_canonical
                )
            )

            effective_extraction_hash = (
                stable_payload_hash(
                    effective_extraction
                )
            )

            _apply_canonical_columns(
                task,
                record.canonical,
            )

            task.last_snapshot_id = (
                snapshot_row.id
            )

            task.transport = (
                transport_value
            )

            task.source_timezone = (
                snapshot.source_timezone
            )

            task.present_columns = (
                _merge_present_columns(
                    task.present_columns,
                    record.present_columns,
                )
            )

            task.raw_row = dict(
                record.raw_row
            )

            task.raw_hyperlinks = dict(
                record.raw_hyperlinks
            )

            task.canonical_json = (
                effective_canonical
            )

            task.extraction_input_json = (
                effective_extraction
            )

            task.raw_hash = (
                record.raw_hash
            )

            task.canonical_hash = (
                effective_canonical_hash
            )

            task.extraction_input_hash = (
                effective_extraction_hash
            )

            task.last_seen_at = (
                imported_at
            )

            updated_tasks += 1

            if (
                effective_canonical_hash
                != previous_canonical_hash
            ):
                canonical_changed += 1

            if (
                effective_extraction_hash
                != previous_extraction_hash
            ):
                extraction_input_changed += 1

        observation = B24TaskObservation(
            id=uuid.uuid4(),
            task_id=task.id,
            snapshot_id=snapshot_row.id,
            external_id=record.external_id,
            transport=transport_value,
            source_timezone=(
                snapshot.source_timezone
            ),
            present_columns=list(
                record.present_columns
            ),
            raw_row=dict(
                record.raw_row
            ),
            raw_hyperlinks=dict(
                record.raw_hyperlinks
            ),
            canonical_json=dict(
                record.canonical_json
            ),
            extraction_input_json=dict(
                record.extraction_input
            ),
            raw_hash=record.raw_hash,
            canonical_hash=(
                record.canonical_hash
            ),
            extraction_input_hash=(
                record.extraction_input_hash
            ),
            contract_version=(
                CONTRACT_VERSION
            ),
            normalizer_version=(
                NORMALIZER_VERSION
            ),
            observed_at=imported_at,
        )

        session.add(
            observation
        )

        observations_created += 1

    session.flush()

    return {
        "ok": True,
        "already_imported": False,
        "snapshot_id": str(
            snapshot_row.id
        ),
        "source_hash": snapshot.source_hash,
        "task_count": snapshot.task_count,
        "created_tasks": created_tasks,
        "updated_tasks": updated_tasks,
        "observations_created": (
            observations_created
        ),
        "canonical_changed": (
            canonical_changed
        ),
        "extraction_input_changed": (
            extraction_input_changed
        ),
    }
