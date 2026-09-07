
"""Content-addressed attachment storage.

Revision ID: 0011_content_addressed_storage
Revises: 0010_source_metadata_evidence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0011_content_addressed_storage"
down_revision: str | None = "0010_source_metadata_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stored_objects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "size_bytes",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "storage_path",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sha256",
            name="uq_stored_objects_sha256",
        ),
        sa.UniqueConstraint(
            "storage_path",
            name="uq_stored_objects_storage_path",
        ),
    )

    op.add_column(
        "attachments",
        sa.Column(
            "stored_object_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_attachments_stored_object",
        "attachments",
        "stored_objects",
        ["stored_object_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_attachments_stored_object_id",
        "attachments",
        ["stored_object_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_attachments_stored_object_id",
        table_name="attachments",
    )
    op.drop_constraint(
        "fk_attachments_stored_object",
        "attachments",
        type_="foreignkey",
    )
    op.drop_column(
        "attachments",
        "stored_object_id",
    )
    op.drop_table("stored_objects")
