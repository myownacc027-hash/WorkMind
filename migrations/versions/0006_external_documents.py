"""External document ingestion.

Revision ID: 0006_external_documents
Revises: 0005_agent_action_log
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_external_documents"
down_revision: str | None = "0005_agent_action_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("archive_path", sa.Text(), nullable=False),
        sa.Column("processed_text_path", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "source_date",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "processing_status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "extraction_method",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("extracted_chars", sa.BigInteger(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("sheet_count", sa.Integer(), nullable=True),
        sa.Column("slide_count", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
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
            "content_hash",
            name="uq_external_documents_content_hash",
        ),
    )
    op.create_index(
        "ix_external_documents_processing_status",
        "external_documents",
        ["processing_status"],
        unique=False,
    )
    op.create_index(
        "ix_external_documents_source_date",
        "external_documents",
        ["source_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_external_documents_source_date",
        table_name="external_documents",
    )
    op.drop_index(
        "ix_external_documents_processing_status",
        table_name="external_documents",
    )
    op.drop_table("external_documents")
