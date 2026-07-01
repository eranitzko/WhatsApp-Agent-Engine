"""Add resend tracking columns to cross_group_confirmations.

Revision ID: 018
Revises: 017b

resend_count  — how many times the initiator has re-sent this confirmation.
last_resent_at — timestamp of the last re-send (UTC).

Rate limits enforced by the resend_confirmation tool:
  - max 2 re-sends per 24 hours
  - at least 2 hours between re-sends
"""

revision = "018"
down_revision = "017b"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column(
        "cross_group_confirmations",
        sa.Column("resend_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "cross_group_confirmations",
        sa.Column("last_resent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("cross_group_confirmations", "last_resent_at")
    op.drop_column("cross_group_confirmations", "resend_count")
