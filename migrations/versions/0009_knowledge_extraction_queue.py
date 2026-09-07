"""Knowledge extraction queue and leases.

Revision ID: 0009_knowledge_extraction_queue
Revises: 0008_temporal_claim_identity
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_knowledge_extraction_queue"
down_revision: str | None = "0008_temporal_claim_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_extraction_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_content_hash", sa.String(64), nullable=False),
        sa.Column("extractor_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "bundle_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "validation_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "apply_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "source_content_hash",
            "extractor_version",
            name="uq_knowledge_extraction_source_version",
        ),
    )
    op.create_index(
        "ix_knowledge_extraction_status_available",
        "knowledge_extraction_jobs",
        ["status", "available_at", "priority"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_extraction_source",
        "knowledge_extraction_jobs",
        ["source_type", "source_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_extraction_lease",
        "knowledge_extraction_jobs",
        ["status", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_extraction_lease",
        table_name="knowledge_extraction_jobs",
    )
    op.drop_index(
        "ix_knowledge_extraction_source",
        table_name="knowledge_extraction_jobs",
    )
    op.drop_index(
        "ix_knowledge_extraction_status_available",
        table_name="knowledge_extraction_jobs",
    )
    op.drop_table("knowledge_extraction_jobs")
