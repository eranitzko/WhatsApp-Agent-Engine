"""Add conversation_history table for persistent agent context

Revision ID: 004
Revises: 003
Create Date: 2026-05-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_history",
        sa.Column("group_id", sa.String(255), primary_key=True),
        sa.Column("messages_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("last_active", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("conversation_history")
