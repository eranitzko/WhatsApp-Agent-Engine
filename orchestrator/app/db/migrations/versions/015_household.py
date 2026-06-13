"""Add Household + HouseholdMember tables; add household_id to ledger and confirmations.

Revision ID: 015
Revises: 014

This migration is ADDITIVE ONLY — no existing columns are dropped or renamed.
LedgerEntry.group_jid and CrossGroupConfirmation.target_group_jid are kept
for backward-compat; household_id is nullable and will be populated in a
subsequent data migration once households are configured via the admin panel.
"""

revision = "015"
down_revision = "014"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.create_table(
        "households",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "household_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36),
                  sa.ForeignKey("households.id"), nullable=False),
        sa.Column("phone", sa.String, nullable=False),
        sa.Column("private_group_jid", sa.String,
                  sa.ForeignKey("group_registry.group_jid"), nullable=True),
        sa.Column("display_name", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("household_id", "phone",
                            name="uq_household_members_household_phone"),
        sa.UniqueConstraint("phone", name="uq_household_members_phone"),
    )
    op.create_index(
        "ix_household_members_phone", "household_members", ["phone"]
    )
    op.create_index(
        "ix_household_members_household_id", "household_members", ["household_id"]
    )

    # LedgerEntry — add household_id (nullable, query scope)
    op.add_column(
        "ledger_entries",
        sa.Column("household_id", sa.String(36),
                  sa.ForeignKey("households.id"), nullable=True),
    )
    op.create_index(
        "ix_ledger_entries_household_id", "ledger_entries", ["household_id"]
    )

    # CrossGroupConfirmation — add household_id for LID-safe matching
    op.add_column(
        "cross_group_confirmations",
        sa.Column("household_id", sa.String(36),
                  sa.ForeignKey("households.id"), nullable=True),
    )
    op.create_index(
        "ix_cross_group_confirmations_household_target",
        "cross_group_confirmations",
        ["household_id", "target_phone", "status"],
    )


def downgrade():
    op.drop_index("ix_cross_group_confirmations_household_target",
                  table_name="cross_group_confirmations")
    op.drop_column("cross_group_confirmations", "household_id")
    op.drop_index("ix_ledger_entries_household_id", table_name="ledger_entries")
    op.drop_column("ledger_entries", "household_id")
    op.drop_index("ix_household_members_household_id",
                  table_name="household_members")
    op.drop_index("ix_household_members_phone", table_name="household_members")
    op.drop_table("household_members")
    op.drop_table("households")
