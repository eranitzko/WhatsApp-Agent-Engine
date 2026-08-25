"""Add ledger_mode to household_members — lets an admin mark specific
members as running a pooled "joint account" (their debts/payments are
fungible with each other for settlement purposes) versus the default
"independent" ledger (their own separate balance, not merged with anyone
else's). Replaces the older, per-group GroupParticipant.is_household /
set_household mechanism, which only affected report labeling and never
settlement — this is a genuine settlement-matching capability, not just
display grouping.

Revision ID: 022
Revises: 021
"""

revision = "022"
down_revision = "021"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column(
        "household_members",
        sa.Column("ledger_mode", sa.String(), nullable=False, server_default="independent"),
    )


def downgrade():
    op.drop_column("household_members", "ledger_mode")
