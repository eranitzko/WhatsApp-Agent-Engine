"""Initial schema: invoices, group_config, exchange_rate_cache

Revision ID: 001
Revises:
Create Date: 2026-04-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(255), nullable=False),
        sa.Column("message_id", sa.String(255), nullable=False, unique=True),
        sa.Column("image_hash", sa.String(64), nullable=False),
        sa.Column("r2_key", sa.String(512), nullable=True),
        sa.Column("received_at", sa.DateTime, nullable=False),
        sa.Column("invoice_date", sa.Date, nullable=True),
        sa.Column("invoice_number", sa.String(255), nullable=True),
        sa.Column("vendor", sa.String(512), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("amount_original", sa.Numeric(18, 4), nullable=True),
        sa.Column("currency_original", sa.String(3), nullable=True),
        sa.Column("amount_ils", sa.Numeric(18, 4), nullable=True),
        sa.Column("exchange_rate", sa.Numeric(18, 6), nullable=True),
        sa.Column("rate_source", sa.String(10), nullable=True),
        sa.Column("rate_date", sa.Date, nullable=True),
        sa.Column("extraction_confidence", sa.Float, nullable=True),
        sa.Column("flagged", sa.Boolean, nullable=False, default=False),
        sa.Column("flag_reason", sa.Text, nullable=True),
        sa.Column("submitted_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_invoices_group_id", "invoices", ["group_id"])
    op.create_index("ix_invoices_image_hash", "invoices", ["image_hash"])

    op.create_table(
        "group_config",
        sa.Column("group_id", sa.String(255), primary_key=True),
        sa.Column("report_header", sa.String(512), nullable=False, default="Monthly Invoice Report"),
        sa.Column("report_author", sa.String(255), nullable=False, default=""),
        sa.Column("feedback_language", sa.String(2), nullable=False, default="en"),
        sa.Column("lead_currency", sa.String(3), nullable=False, default="ILS"),
        sa.Column("force_dual_currency", sa.Boolean, nullable=False, default=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "exchange_rate_cache",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("currency_from", sa.String(3), nullable=False),
        sa.Column("currency_to", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(18, 6), nullable=False),
        sa.Column("rate_date", sa.Date, nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("fetched_at", sa.DateTime, nullable=False),
    )
    op.create_index(
        "ix_rate_cache_lookup",
        "exchange_rate_cache",
        ["currency_from", "currency_to", "rate_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_rate_cache_lookup", "exchange_rate_cache")
    op.drop_table("exchange_rate_cache")
    op.drop_table("group_config")
    op.drop_index("ix_invoices_image_hash", "invoices")
    op.drop_index("ix_invoices_group_id", "invoices")
    op.drop_table("invoices")
