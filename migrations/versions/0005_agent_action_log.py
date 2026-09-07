"""Agent action audit log.

Revision ID: 0005_agent_action_log
Revises: 0004_hybrid_search_fts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_agent_action_log"
down_revision: str | None = "0004_hybrid_search_fts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_action_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=255), nullable=True),
        sa.Column(
            "request_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "response_summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_action_logs_created_at",
        "agent_action_logs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_agent_action_logs_tool_status",
        "agent_action_logs",
        ["tool_name", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_action_logs_tool_status",
        table_name="agent_action_logs",
    )
    op.drop_index(
        "ix_agent_action_logs_created_at",
        table_name="agent_action_logs",
    )
    op.drop_table("agent_action_logs")
