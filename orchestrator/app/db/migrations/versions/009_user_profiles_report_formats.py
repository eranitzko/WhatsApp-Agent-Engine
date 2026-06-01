"""Add user_profiles and report_formats tables

Revision ID: 009
Revises: 008
Create Date: 2026-05-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("phone"),
    )

    op.create_table(
        "report_formats",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("group_jid", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_jid", "name", name="uq_report_formats_group_name"),
    )


def downgrade() -> None:
    op.drop_table("report_formats")
    op.drop_table("user_profiles")
