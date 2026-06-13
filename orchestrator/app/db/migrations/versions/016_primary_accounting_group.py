"""Add primary_accounting_group_jid to household_members.

Revision ID: 016
Revises: 015

When a person belongs to multiple family_accounting groups, this field marks
which one should receive bot-initiated outbound messages (notifications and
confirmation requests).  Null means "use the first-registered accounting
group" — the deterministic default that covers everyone who never needs to
override.
"""

revision = "016"
down_revision = "015"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column(
        "household_members",
        sa.Column(
            "primary_accounting_group_jid",
            sa.String,
            sa.ForeignKey("group_registry.group_jid"),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column("household_members", "primary_accounting_group_jid")
