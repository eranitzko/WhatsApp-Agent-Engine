"""Add notes to group_registry — a free-text admin-facing field for
labeling what a group is for, purely for the admin's own reference. Not
fed into the agent's system prompt (that's what custom_instructions is
for) — this is bookkeeping only.

Revision ID: 021
Revises: 020
"""

revision = "021"
down_revision = "020"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column("group_registry", sa.Column("notes", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("group_registry", "notes")
