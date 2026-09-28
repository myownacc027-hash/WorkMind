
"""Bitrix24 source storage foundation.

Revision ID: 0012_bitrix24_sources
Revises: 0011_content_addressed_storage
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0012_bitrix24_sources"
down_revision: str | None = "0011_content_addressed_storage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_object_column(name: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text("'{}'::jsonb"),
        nullable=False,
    )


def _json_array_column(name: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text("'[]'::jsonb"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "b24_import_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=True),
        sa.Column("source_hash", sa.String(64), nullable=True),
        sa.Column("source_timezone", sa.String(128), nullable=False),
        _json_array_column("present_columns"),
        sa.Column(
            "row_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "task_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "skipped_row_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        _json_object_column("metadata_json"),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transport",
            "source_hash",
            name="uq_b24_import_snapshots_transport_hash",
        ),
    )

    op.create_index(
        "ix_b24_import_snapshots_imported_at",
        "b24_import_snapshots",
        ["imported_at"],
        unique=False,
    )

    op.create_table(
        "b24_tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column(
            "last_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("source_timezone", sa.String(128), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("project_external_id", sa.String(64), nullable=True),
        _json_array_column("present_columns"),
        _json_object_column("raw_row"),
        _json_object_column("raw_hyperlinks"),
        _json_object_column("canonical_json"),
        _json_object_column("extraction_input_json"),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("canonical_hash", sa.String(64), nullable=False),
        sa.Column("extraction_input_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_source", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at_source", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creator_label", sa.Text(), nullable=True),
        sa.Column("assignee_label", sa.Text(), nullable=True),
        _json_array_column("coassignee_labels"),
        _json_array_column("observer_labels"),
        _json_array_column("additional_employee_labels"),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("stage", sa.Text(), nullable=True),
        sa.Column("project_name", sa.Text(), nullable=True),
        sa.Column("parent_external_id", sa.String(64), nullable=True),
        sa.Column("parent_title_snapshot", sa.Text(), nullable=True),
        sa.Column("parent_stage_snapshot", sa.Text(), nullable=True),
        sa.Column("planned_duration_minutes", sa.Integer(), nullable=True),
        sa.Column("spent_minutes", sa.Integer(), nullable=True),
        sa.Column("track_work_time", sa.Boolean(), nullable=True),
        _json_array_column("tags"),
        sa.Column("business_direction", sa.Text(), nullable=True),
        sa.Column("request_type", sa.Text(), nullable=True),
        sa.Column("task_kind", sa.Text(), nullable=True),
        sa.Column("product", sa.Text(), nullable=True),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "planned_release_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "actual_release_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "deadline_change_in_progress",
            sa.Boolean(),
            nullable=True,
        ),
        sa.Column("deadline_change_count", sa.Integer(), nullable=True),
        sa.Column("ai_used", sa.Boolean(), nullable=True),
        sa.Column("helpdesk_number", sa.Text(), nullable=True),
        sa.Column("progress_percent", sa.Float(), nullable=True),
        sa.Column("crm_context_raw", sa.Text(), nullable=True),
        sa.Column("flow_raw", sa.Text(), nullable=True),
        sa.Column("priority_raw", sa.Text(), nullable=True),
        sa.Column("deadline_change_request_raw", sa.Text(), nullable=True),
        sa.Column("deadline_change_reason", sa.Text(), nullable=True),
        sa.Column("approval_comment", sa.Text(), nullable=True),
        sa.Column("approval_result", sa.Text(), nullable=True),
        sa.Column(
            "first_imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["last_snapshot_id"],
            ["b24_import_snapshots.id"],
            name="fk_b24_tasks_last_snapshot",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "external_id",
            name="uq_b24_tasks_external_id",
        ),
    )

    op.create_index(
        "ix_b24_tasks_status",
        "b24_tasks",
        ["status"],
        unique=False,
    )

    op.create_index(
        "ix_b24_tasks_parent_external_id",
        "b24_tasks",
        ["parent_external_id"],
        unique=False,
    )

    op.create_index(
        "ix_b24_tasks_project_external_id",
        "b24_tasks",
        ["project_external_id"],
        unique=False,
    )

    op.create_index(
        "ix_b24_tasks_extraction_input_hash",
        "b24_tasks",
        ["extraction_input_hash"],
        unique=False,
    )

    op.create_table(
        "b24_task_observations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("source_timezone", sa.String(128), nullable=False),
        _json_array_column("present_columns"),
        _json_object_column("raw_row"),
        _json_object_column("raw_hyperlinks"),
        _json_object_column("canonical_json"),
        _json_object_column("extraction_input_json"),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("canonical_hash", sa.String(64), nullable=False),
        sa.Column("extraction_input_hash", sa.String(64), nullable=False),
        sa.Column(
            "contract_version",
            sa.String(64),
            server_default=sa.text("'b24-source-v1'"),
            nullable=False,
        ),
        sa.Column(
            "normalizer_version",
            sa.String(64),
            server_default=sa.text("'b24-normalizer-v1'"),
            nullable=False,
        ),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["b24_tasks.id"],
            name="fk_b24_task_observations_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["b24_import_snapshots.id"],
            name="fk_b24_task_observations_snapshot",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "external_id",
            name="uq_b24_task_observations_snapshot_external",
        ),
    )

    op.create_index(
        "ix_b24_task_observations_task",
        "b24_task_observations",
        ["task_id", "observed_at"],
        unique=False,
    )

    op.create_index(
        "ix_b24_task_observations_extraction_hash",
        "b24_task_observations",
        ["extraction_input_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_b24_task_observations_extraction_hash",
        table_name="b24_task_observations",
    )
    op.drop_index(
        "ix_b24_task_observations_task",
        table_name="b24_task_observations",
    )
    op.drop_table("b24_task_observations")

    op.drop_index(
        "ix_b24_tasks_extraction_input_hash",
        table_name="b24_tasks",
    )
    op.drop_index(
        "ix_b24_tasks_project_external_id",
        table_name="b24_tasks",
    )
    op.drop_index(
        "ix_b24_tasks_parent_external_id",
        table_name="b24_tasks",
    )
    op.drop_index(
        "ix_b24_tasks_status",
        table_name="b24_tasks",
    )
    op.drop_table("b24_tasks")

    op.drop_index(
        "ix_b24_import_snapshots_imported_at",
        table_name="b24_import_snapshots",
    )
    op.drop_table("b24_import_snapshots")
