"""Add PostgreSQL full-text index for hybrid search.

Revision ID: 0004_hybrid_search_fts
Revises: 0003_attachment_text
Create Date: 2026-07-21
"""

from alembic import op

revision = "0004_hybrid_search_fts"
down_revision = "0003_attachment_text"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_document_chunks_text_fts_simple"


def upgrade() -> None:
    # CONCURRENTLY avoids a long write lock while indexing the
    # already populated chunk table. Alembic must leave its normal
    # transaction for this PostgreSQL command.
    with op.get_context().autocommit_block():
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}
            ON document_chunks
            USING gin (
                to_tsvector(
                    'simple'::regconfig,
                    COALESCE(text, ''::text)
                )
            )
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"
        )
