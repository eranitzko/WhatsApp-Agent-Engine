"""Add composite indexes on invoices(group_id, invoice_date) and (group_id, image_hash)

Revision ID: 002
Revises: 001
Create Date: 2026-04-19
"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_invoices_group_date", "invoices", ["group_id", "invoice_date"])
    op.create_index("ix_invoices_group_hash", "invoices", ["group_id", "image_hash"])


def downgrade() -> None:
    op.drop_index("ix_invoices_group_hash", "invoices")
    op.drop_index("ix_invoices_group_date", "invoices")
