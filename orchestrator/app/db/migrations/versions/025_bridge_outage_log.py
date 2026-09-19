"""Add bridge_outage_log — persistent history of WhatsApp-bridge outages,
for the admin panel's outage log review, separate from the current-outage
state in SystemConfig (which is overwritten in place and cleared on
recovery).

Revision ID: 025
Revises: 024
"""

revision = "025"
down_revision = "024"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.create_table(
        "bridge_outage_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("down_since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_table("bridge_outage_log")
