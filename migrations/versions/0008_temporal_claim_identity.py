"""Stable claim identity and temporal supersession links.

Revision ID: 0008_temporal_claim_identity
Revises: 0007_structured_knowledge
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_temporal_claim_identity"
down_revision: str | None = "0007_structured_knowledge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_claims",
        sa.Column("claim_key", sa.String(255), nullable=True),
    )
    op.add_column(
        "knowledge_claims",
        sa.Column(
            "supersedes_claim_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "knowledge_claims",
        sa.Column(
            "superseded_by_claim_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    # Existing rows already have a unique immutable identifier. Preserve it
    # as the initial stable key so current claim IDs remain updateable.
    op.execute(
        "UPDATE knowledge_claims "
        "SET claim_key = claim_hash "
        "WHERE claim_key IS NULL"
    )
    op.alter_column(
        "knowledge_claims",
        "claim_key",
        existing_type=sa.String(255),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_knowledge_claim_key",
        "knowledge_claims",
        ["claim_key"],
    )
    op.create_foreign_key(
        "fk_knowledge_claim_supersedes",
        "knowledge_claims",
        "knowledge_claims",
        ["supersedes_claim_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_knowledge_claim_superseded_by",
        "knowledge_claims",
        "knowledge_claims",
        ["superseded_by_claim_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )
    op.create_index(
        "ix_knowledge_claim_supersedes",
        "knowledge_claims",
        ["supersedes_claim_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_claim_superseded_by",
        "knowledge_claims",
        ["superseded_by_claim_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_claim_superseded_by",
        table_name="knowledge_claims",
    )
    op.drop_index(
        "ix_knowledge_claim_supersedes",
        table_name="knowledge_claims",
    )
    op.drop_constraint(
        "fk_knowledge_claim_superseded_by",
        "knowledge_claims",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_knowledge_claim_supersedes",
        "knowledge_claims",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_knowledge_claim_key",
        "knowledge_claims",
        type_="unique",
    )
    op.drop_column("knowledge_claims", "superseded_by_claim_id")
    op.drop_column("knowledge_claims", "supersedes_claim_id")
    op.drop_column("knowledge_claims", "claim_key")
