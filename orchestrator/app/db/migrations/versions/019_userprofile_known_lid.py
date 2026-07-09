"""Add known_lid to user_profiles for shared-group LID resolution.

Revision ID: 019
Revises: 018

known_lid — a WhatsApp LID (opaque numeric ID) known to belong to this
person, learned once (e.g. via admin registration) and reused thereafter to
resolve identity/ACL checks in SHARED groups, where WhatsApp sends LIDs
instead of phone numbers and the existing private_group_jid-based
resolve_inbound strategies don't apply (those only cover personal 1:1
groups). Nullable/unique: at most one profile per LID, not every profile
has a known LID.
"""

revision = "019"
down_revision = "018"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column(
        "user_profiles",
        sa.Column("known_lid", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_user_profiles_known_lid", "user_profiles", ["known_lid"], unique=True,
    )


def downgrade():
    op.drop_index("ix_user_profiles_known_lid", table_name="user_profiles")
    op.drop_column("user_profiles", "known_lid")
