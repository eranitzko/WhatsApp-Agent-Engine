"""Add routing fields to user_profiles; add entry_type to ledger_entries.

Revision ID: 017
Revises: 016

Two schema gaps fixed together:

1. user_profiles gains private_group_jid and primary_accounting_group_jid so
   that LID-safe inbound resolution and primary-group overrides work for every
   person who has a UserProfile, regardless of whether they have a HouseholdMember
   row.  UserProfile is created for everyone; HouseholdMember requires explicit
   household enrollment.

2. ledger_entries gains entry_type ('debt' | 'payment') so that the FIFO
   diagnostic and reconciliation queries have an explicit discriminator instead
   of relying on the description-prefix heuristic.  Existing rows are populated
   by the heuristic once (on migration) so the column is immediately reliable
   for the diagnostic.
"""

revision = "017"
down_revision = "016"

import sqlalchemy as sa
from alembic import op


def upgrade():
    # -- user_profiles routing fields ----------------------------------------
    op.add_column(
        "user_profiles",
        sa.Column(
            "private_group_jid",
            sa.String,
            sa.ForeignKey("group_registry.group_jid"),
            nullable=True,
        ),
    )
    op.add_column(
        "user_profiles",
        sa.Column(
            "primary_accounting_group_jid",
            sa.String,
            sa.ForeignKey("group_registry.group_jid"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_user_profiles_private_group_jid",
        "user_profiles",
        ["private_group_jid"],
    )

    # -- ledger_entries entry_type -------------------------------------------
    op.add_column(
        "ledger_entries",
        sa.Column("entry_type", sa.String(16), nullable=False, server_default="debt"),
    )
    # Populate existing rows from description convention: payment legs always
    # start with "Payment on YYYY-MM-DD" (enforced by _apply_payment_fifo).
    op.execute(
        "UPDATE ledger_entries SET entry_type = 'payment' "
        "WHERE description LIKE 'Payment on %'"
    )


def downgrade():
    op.drop_column("ledger_entries", "entry_type")
    op.drop_index("ix_user_profiles_private_group_jid", table_name="user_profiles")
    op.drop_column("user_profiles", "primary_accounting_group_jid")
    op.drop_column("user_profiles", "private_group_jid")
