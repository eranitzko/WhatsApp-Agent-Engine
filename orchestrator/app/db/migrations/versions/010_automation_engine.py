"""Add automation_rules table

Revision ID: 010
Revises: 009
Create Date: 2026-06-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automation_rules",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("group_jid", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("rule_type", sa.String(), nullable=False),
        sa.Column("schedule_cron", sa.String(), nullable=True),
        sa.Column("inactivity_hours", sa.Integer(), nullable=True),
        sa.Column("threshold_config", sa.Text(), nullable=True),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("action_config", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending_confirm"),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["group_jid"], ["group_registry.group_jid"]),
    )


def downgrade() -> None:
    op.drop_table("automation_rules")
