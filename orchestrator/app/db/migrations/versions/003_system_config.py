"""Add system_config table for global settings

Revision ID: 003
Revises: 002
Create Date: 2026-04-26
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_config",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("value", sa.Text, nullable=False, server_default=""),
    )
    op.execute("INSERT INTO system_config (key, value) VALUES ('extra_date_formats', '')")


def downgrade() -> None:
    op.drop_table("system_config")
