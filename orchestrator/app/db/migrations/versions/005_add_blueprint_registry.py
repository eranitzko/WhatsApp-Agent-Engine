"""Add blueprints, group_registry, and admin_numbers tables

Revision ID: 005
Revises: 004
Create Date: 2026-05-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "blueprints",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(), nullable=False, server_default="claude-sonnet-4-6"),
        sa.Column("tools_enabled", sa.Text(), nullable=False),
        sa.Column("max_tool_turns", sa.Integer(), server_default="6"),
        sa.Column("context_window", sa.Integer(), server_default="8"),
        sa.Column("context_idle_reset_minutes", sa.Integer(), server_default="60"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "group_registry",
        sa.Column("group_jid", sa.String(), primary_key=True),
        sa.Column("blueprint_id", sa.String(), sa.ForeignKey("blueprints.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("trigger_type", sa.String(), nullable=False, server_default="always"),
        sa.Column("trigger_prefix", sa.String(), nullable=True),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "admin_numbers",
        sa.Column("phone_number", sa.String(), primary_key=True),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("group_registry")
    op.drop_table("blueprints")
    op.drop_table("admin_numbers")
