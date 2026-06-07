"""Add personal accounting tables and group_type column

Revision ID: 011
Revises: 010
Create Date: 2026-06-04
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # group_type on existing group_registry
    op.add_column(
        "group_registry",
        sa.Column("group_type", sa.String(), nullable=True, server_default="personal"),
    )

    # display_name on existing user_profiles
    op.add_column(
        "user_profiles",
        sa.Column("display_name", sa.String(), nullable=True),
    )

    # user_accounts: maps phone → group_jid with role
    op.create_table(
        "user_accounts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("group_jid", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="owner"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["group_jid"], ["group_registry.group_jid"]),
        sa.UniqueConstraint("phone", "group_jid", name="uq_user_accounts_phone_group"),
    )
    op.create_index("ix_user_accounts_phone", "user_accounts", ["phone"])
    op.create_index("ix_user_accounts_group_jid", "user_accounts", ["group_jid"])

    # split_transactions: parent record for multi-party splits
    op.create_table(
        "split_transactions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("reporter_group_jid", sa.String(), nullable=False),
        sa.Column("reporter_phone", sa.String(), nullable=False),
        sa.Column("payer_phone", sa.String(), nullable=False),
        sa.Column("total_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    # cross_group_confirmations: persistent 2nd-party and split confirmations
    op.create_table(
        "cross_group_confirmations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("split_transaction_id", sa.String(36), nullable=True),
        sa.Column("initiator_phone", sa.String(), nullable=False),
        sa.Column("initiator_group_jid", sa.String(), nullable=False),
        sa.Column("target_phone", sa.String(), nullable=False),
        sa.Column("target_group_jid", sa.String(), nullable=False),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("action_payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["split_transaction_id"], ["split_transactions.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_cgc_target_phone_status",
        "cross_group_confirmations",
        ["target_phone", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_cgc_target_phone_status", table_name="cross_group_confirmations")
    op.drop_table("cross_group_confirmations")
    op.drop_table("split_transactions")
    op.drop_index("ix_user_accounts_group_jid", table_name="user_accounts")
    op.drop_index("ix_user_accounts_phone", table_name="user_accounts")
    op.drop_table("user_accounts")
    op.drop_column("user_profiles", "display_name")
    op.drop_column("group_registry", "group_type")
