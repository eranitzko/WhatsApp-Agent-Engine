"""Add custom_instructions to group_registry

Revision ID: 007
Revises: 006
Create Date: 2026-05-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("group_registry", sa.Column("custom_instructions", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("group_registry", "custom_instructions")
