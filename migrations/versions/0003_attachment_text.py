"""Attachment text extraction metadata.

Revision ID: 0003_attachment_text
Revises: 0002_history_import
Create Date: 2026-07-20
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_attachment_text"
down_revision = "0002_history_import"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column("extracted_text_path", sa.Text(), nullable=True),
    )
    op.add_column(
        "attachments",
        sa.Column(
            "extraction_method",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "attachments",
        sa.Column("extracted_chars", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "attachments",
        sa.Column("page_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "attachments",
        sa.Column("sheet_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "attachments",
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "attachments",
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "attachments",
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_attachments_processing_status",
        "attachments",
        ["processing_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_attachments_processing_status",
        table_name="attachments",
    )
    op.drop_column("attachments", "processed_at")
    op.drop_column("attachments", "metadata_json")
    op.drop_column("attachments", "last_error")
    op.drop_column("attachments", "sheet_count")
    op.drop_column("attachments", "page_count")
    op.drop_column("attachments", "extracted_chars")
    op.drop_column("attachments", "extraction_method")
    op.drop_column("attachments", "extracted_text_path")
