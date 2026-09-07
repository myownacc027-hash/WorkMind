"""Initial mail archive schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-14
"""

from alembic import op

from packages.database.base import Base
from packages.database import models  # noqa: F401

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_subject_trgm "
        "ON email_messages USING gin (subject gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_body_trgm "
        "ON email_messages USING gin (body_text gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_email_body_fts "
        "ON email_messages USING gin (to_tsvector('simple', coalesce(subject,'') || ' ' || coalesce(body_text,'')))"
    )


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
