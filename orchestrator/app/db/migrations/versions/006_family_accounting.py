"""Add family accounting tables

Revision ID: 006
Revises: 005
Create Date: 2026-05-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("transaction_id", sa.String(36), nullable=False, index=True),
        sa.Column("group_jid", sa.String(255), nullable=False, index=True),
        sa.Column("from_phone", sa.String(255), nullable=False),
        sa.Column("to_phone", sa.String(255), nullable=False),
        sa.Column("amount_ils", sa.Numeric(18, 4), nullable=False),
        sa.Column("amount_settled_ils", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("transaction_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "ledger_settlements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("payment_leg_id", sa.String(36), sa.ForeignKey("ledger_entries.id"), nullable=False),
        sa.Column("debt_leg_id", sa.String(36), sa.ForeignKey("ledger_entries.id"), nullable=False),
        sa.Column("amount_ils", sa.Numeric(18, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "scheduled_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_jid", sa.String(255), nullable=False),
        sa.Column("to_phone", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("send_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Indexes for ledger_settlements FK lookups
    op.create_index("ix_ledger_settlements_payment_leg_id", "ledger_settlements", ["payment_leg_id"])
    op.create_index("ix_ledger_settlements_debt_leg_id", "ledger_settlements", ["debt_leg_id"])
    # Index for scheduler poll: WHERE sent=0 AND send_at <= now()
    op.create_index("ix_scheduled_messages_sent_send_at", "scheduled_messages", ["sent", "send_at"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_messages_sent_send_at", table_name="scheduled_messages")
    op.drop_index("ix_ledger_settlements_debt_leg_id", table_name="ledger_settlements")
    op.drop_index("ix_ledger_settlements_payment_leg_id", table_name="ledger_settlements")
    op.drop_table("scheduled_messages")
    op.drop_table("ledger_settlements")
    op.drop_table("ledger_entries")
