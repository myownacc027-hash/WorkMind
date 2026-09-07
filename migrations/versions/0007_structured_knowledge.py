"""Structured knowledge pilot.

Revision ID: 0007_structured_knowledge
Revises: 0006_external_documents
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_structured_knowledge"
down_revision: str | None = "0006_external_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("normalized_key", sa.String(255), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "attributes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_type",
            "normalized_key",
            name="uq_knowledge_entity_type_key",
        ),
    )
    op.create_index(
        "ix_knowledge_entity_external_id",
        "knowledge_entities",
        ["external_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_entity_type_status",
        "knowledge_entities",
        ["entity_type", "status"],
        unique=False,
    )

    op.create_table(
        "knowledge_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "subject_entity_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("predicate", sa.String(128), nullable=False),
        sa.Column(
            "object_entity_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("object_text", sa.Text(), nullable=True),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("evidence_type", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_hash", sa.String(64), nullable=False),
        sa.Column(
            "attributes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
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
        sa.CheckConstraint(
            "object_entity_id IS NOT NULL OR object_text IS NOT NULL",
            name="ck_knowledge_claim_has_object",
        ),
        sa.ForeignKeyConstraint(
            ["subject_entity_id"],
            ["knowledge_entities.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["object_entity_id"],
            ["knowledge_entities.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_hash", name="uq_knowledge_claim_hash"),
    )
    op.create_index(
        "ix_knowledge_claim_subject_predicate",
        "knowledge_claims",
        ["subject_entity_id", "predicate"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_claim_object_entity",
        "knowledge_claims",
        ["object_entity_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_claim_status",
        "knowledge_claims",
        ["status"],
        unique=False,
    )

    op.create_table(
        "knowledge_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column(
            "attributes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
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
            ["entity_id"],
            ["knowledge_entities.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_hash", name="uq_knowledge_event_hash"),
    )
    op.create_index(
        "ix_knowledge_event_entity_date",
        "knowledge_events",
        ["entity_id", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "knowledge_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("evidence_role", sa.String(32), nullable=False),
        sa.Column("source_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(claim_id IS NOT NULL AND event_id IS NULL) OR "
            "(claim_id IS NULL AND event_id IS NOT NULL)",
            name="ck_knowledge_evidence_one_parent",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["knowledge_claims.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["knowledge_events.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evidence_hash",
            name="uq_knowledge_evidence_hash",
        ),
    )
    op.create_index(
        "ix_knowledge_evidence_source",
        "knowledge_evidence",
        ["source_type", "source_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_evidence_chunk",
        "knowledge_evidence",
        ["chunk_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_evidence_chunk", table_name="knowledge_evidence")
    op.drop_index("ix_knowledge_evidence_source", table_name="knowledge_evidence")
    op.drop_table("knowledge_evidence")
    op.drop_index("ix_knowledge_event_entity_date", table_name="knowledge_events")
    op.drop_table("knowledge_events")
    op.drop_index("ix_knowledge_claim_status", table_name="knowledge_claims")
    op.drop_index("ix_knowledge_claim_object_entity", table_name="knowledge_claims")
    op.drop_index("ix_knowledge_claim_subject_predicate", table_name="knowledge_claims")
    op.drop_table("knowledge_claims")
    op.drop_index("ix_knowledge_entity_type_status", table_name="knowledge_entities")
    op.drop_index("ix_knowledge_entity_external_id", table_name="knowledge_entities")
    op.drop_table("knowledge_entities")
