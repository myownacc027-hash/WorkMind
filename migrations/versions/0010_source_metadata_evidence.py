"""Source metadata evidence for non-text/binary artifacts.

Revision ID: 0010_source_metadata_evidence
Revises: 0009_knowledge_extraction_queue
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_source_metadata_evidence"
down_revision: str | None = "0009_knowledge_extraction_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_evidence",
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "knowledge_evidence",
        sa.Column(
            "evidence_kind",
            sa.String(32),
            server_default=sa.text("'chunk'"),
            nullable=False,
        ),
    )
    op.add_column(
        "knowledge_evidence",
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_knowledge_evidence_entity",
        "knowledge_evidence",
        "knowledge_entities",
        ["entity_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column(
        "knowledge_evidence",
        "chunk_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.drop_constraint(
        "ck_knowledge_evidence_one_parent",
        "knowledge_evidence",
        type_="check",
    )
    op.create_check_constraint(
        "ck_knowledge_evidence_one_parent",
        "knowledge_evidence",
        "(entity_id IS NOT NULL AND claim_id IS NULL AND event_id IS NULL) OR "
        "(entity_id IS NULL AND claim_id IS NOT NULL AND event_id IS NULL) OR "
        "(entity_id IS NULL AND claim_id IS NULL AND event_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_knowledge_evidence_kind_payload",
        "knowledge_evidence",
        "(evidence_kind = 'chunk' AND chunk_id IS NOT NULL) OR "
        "(evidence_kind = 'source_metadata' AND chunk_id IS NULL)",
    )
    op.create_index(
        "ix_knowledge_evidence_entity",
        "knowledge_evidence",
        ["entity_id"],
        unique=False,
    )


def downgrade() -> None:
    # Metadata-only evidence cannot be represented by the pre-0.12.1 schema.
    op.execute(
        "DELETE FROM knowledge_evidence WHERE evidence_kind = 'source_metadata'"
    )
    op.drop_index(
        "ix_knowledge_evidence_entity",
        table_name="knowledge_evidence",
    )
    op.drop_constraint(
        "ck_knowledge_evidence_kind_payload",
        "knowledge_evidence",
        type_="check",
    )
    op.drop_constraint(
        "ck_knowledge_evidence_one_parent",
        "knowledge_evidence",
        type_="check",
    )
    op.create_check_constraint(
        "ck_knowledge_evidence_one_parent",
        "knowledge_evidence",
        "(claim_id IS NOT NULL AND event_id IS NULL) OR "
        "(claim_id IS NULL AND event_id IS NOT NULL)",
    )
    op.alter_column(
        "knowledge_evidence",
        "chunk_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_constraint(
        "fk_knowledge_evidence_entity",
        "knowledge_evidence",
        type_="foreignkey",
    )
    op.drop_column("knowledge_evidence", "metadata_json")
    op.drop_column("knowledge_evidence", "evidence_kind")
    op.drop_column("knowledge_evidence", "entity_id")
