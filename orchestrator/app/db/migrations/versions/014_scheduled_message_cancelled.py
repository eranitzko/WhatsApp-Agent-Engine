"""Add cancelled column to scheduled_messages.

Revision ID: 014
Revises: 013
"""
revision = "014"
down_revision = "013"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column(
        "scheduled_messages",
        sa.Column("cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("scheduled_messages", "cancelled")
