"""Create group_participants table

Revision ID: 008
Revises: 007
Create Date: 2026-05-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "group_participants",
        sa.Column("group_jid", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("push_name", sa.String(), nullable=True),
        sa.Column("admin_name", sa.String(), nullable=True),
        sa.Column("is_household", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["group_jid"], ["group_registry.group_jid"]),
        sa.PrimaryKeyConstraint("group_jid", "phone"),
    )


def downgrade() -> None:
    op.drop_table("group_participants")
