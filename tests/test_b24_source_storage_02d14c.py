
from pathlib import Path

from packages.database.models import (
    B24ImportSnapshot,
    B24Task,
    B24TaskObservation,
)


MIGRATION = Path(
    "migrations/versions/0012_bitrix24_sources.py"
).read_text(encoding="utf-8")


def test_b24_storage_tables_are_separate_source_models() -> None:
    assert B24ImportSnapshot.__tablename__ == "b24_import_snapshots"
    assert B24Task.__tablename__ == "b24_tasks"
    assert B24TaskObservation.__tablename__ == "b24_task_observations"


def test_b24_task_has_required_source_contract_fields() -> None:
    columns = set(B24Task.__table__.c.keys())

    required = {
        "id",
        "external_id",
        "last_snapshot_id",
        "transport",
        "source_timezone",
        "source_url",
        "project_external_id",
        "present_columns",
        "raw_row",
        "raw_hyperlinks",
        "raw_hash",
        "canonical_hash",
        "extraction_input_hash",
        "title",
        "description",
        "status",
        "stage",
        "parent_external_id",
    }

    assert required <= columns


def test_b24_observation_is_immutable_source_revision_shape() -> None:
    columns = set(B24TaskObservation.__table__.c.keys())

    required = {
        "id",
        "task_id",
        "snapshot_id",
        "external_id",
        "raw_row",
        "raw_hyperlinks",
        "canonical_json",
        "extraction_input_json",
        "raw_hash",
        "canonical_hash",
        "extraction_input_hash",
        "contract_version",
        "normalizer_version",
        "observed_at",
    }

    assert required <= columns


def test_b24_task_identity_is_external_id_unique() -> None:
    constraints = {
        constraint.name
        for constraint in B24Task.__table__.constraints
        if constraint.name
    }

    assert "uq_b24_tasks_external_id" in constraints


def test_b24_observation_is_unique_per_snapshot_and_task_external_id() -> None:
    constraints = {
        constraint.name
        for constraint in B24TaskObservation.__table__.constraints
        if constraint.name
    }

    assert "uq_b24_task_observations_snapshot_external" in constraints


def test_b24_migration_extends_current_alembic_head() -> None:
    assert 'revision: str = "0012_bitrix24_sources"' in MIGRATION
    assert (
        'down_revision: str | None = "0011_content_addressed_storage"'
        in MIGRATION
    )


def test_b24_storage_does_not_reuse_external_documents() -> None:
    assert "external_documents" not in B24Task.__table__.name
    assert "external_documents" not in B24TaskObservation.__table__.name


def test_b24_storage_keeps_three_fingerprints() -> None:
    task_columns = set(B24Task.__table__.c.keys())
    observation_columns = set(B24TaskObservation.__table__.c.keys())

    for value in (
        "raw_hash",
        "canonical_hash",
        "extraction_input_hash",
    ):
        assert value in task_columns
        assert value in observation_columns
