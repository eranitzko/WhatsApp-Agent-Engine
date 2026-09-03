"""Store the exact (possibly AI-localized) prompt text sent for a
CrossGroupConfirmation, so a later free-form reply can be interpreted with
the original wording as context, without reconstructing it from
action_payload per action_type.

Revision ID: 024
Revises: 023
"""

revision = "024"
down_revision = "023"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column(
        "cross_group_confirmations",
        sa.Column("prompt_text", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("cross_group_confirmations", "prompt_text")
