"""Add request_log table for agent turn monitoring

Revision ID: 012
Revises: 011
Create Date: 2026-06-07
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "request_logs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("group_jid", sa.String(), nullable=False),
        sa.Column("blueprint_id", sa.String(), nullable=False),
        sa.Column("sender_phone", sa.String(), nullable=True),
        sa.Column("history_pairs", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tool_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tool_names", sa.Text(), nullable=True),      # JSON array of tool names
        sa.Column("stop_reason", sa.String(), nullable=True),
        sa.Column("tool_calls_made", sa.Text(), nullable=True), # JSON array of {name, preview}
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_request_logs_group_jid", "request_logs", ["group_jid"])
    op.create_index("ix_request_logs_created_at", "request_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_request_logs_created_at", table_name="request_logs")
    op.drop_index("ix_request_logs_group_jid", table_name="request_logs")
    op.drop_table("request_logs")
