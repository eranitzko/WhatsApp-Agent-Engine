"""Add perceptual_hash to invoices for near-duplicate photo detection.

Revision ID: 020
Revises: 019

The existing image_hash (SHA-256) only catches byte-identical resends —
a user re-sending the same physical receipt as a fresh photo message
almost always produces different bytes (re-compression, a new photo taken
of the same paper, etc.), so it slips past that check entirely. Nullable:
only image-based invoices get one; manually-entered invoices (save_invoice)
have no image at all.
"""

revision = "020"
down_revision = "019"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column(
        "invoices",
        sa.Column("perceptual_hash", sa.String(), nullable=True),
    )


def downgrade():
    op.drop_column("invoices", "perceptual_hash")
