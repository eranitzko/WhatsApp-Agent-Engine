"""Revert the household-wide ledger_mode approach (022) in favor of a
group-scoped one: two people share a pooled ledger for settlement purposes
when they're both active participants of the same shared family_accounting
group with shared_ledger=True, not because of a flag on their household
membership. household_id remains the mechanism for cross-group ledger
VISIBILITY/reporting scope — this is specifically about who a payment can
settle against.

Revision ID: 023
Revises: 022
"""

revision = "023"
down_revision = "022"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column(
        "group_registry",
        sa.Column("shared_ledger", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.drop_column("household_members", "ledger_mode")


def downgrade():
    op.add_column(
        "household_members",
        sa.Column("ledger_mode", sa.String(), nullable=False, server_default="independent"),
    )
    op.drop_column("group_registry", "shared_ledger")
