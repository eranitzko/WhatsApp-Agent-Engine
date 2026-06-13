"""Add routing fields to user_profiles for LID-safe inbound resolution.

Revision ID: 017a
Revises: 016

Adds private_group_jid and primary_accounting_group_jid to user_profiles so
that LID-safe inbound resolution and primary-group overrides work for every
person who has a UserProfile, regardless of household enrollment status.
Pure DDL — no existing row data is transformed.

Rollback: downgrade() drops both columns and the index.
"""

revision = "017a"
down_revision = "016"

import sqlalchemy as sa
from alembic import op


def upgrade():
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


def downgrade():
    op.drop_index("ix_user_profiles_private_group_jid", table_name="user_profiles")
    op.drop_column("user_profiles", "primary_accounting_group_jid")
    op.drop_column("user_profiles", "private_group_jid")
