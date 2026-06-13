"""Add entry_type discriminator to ledger_entries.

Revision ID: 017b
Revises: 017a

Adds entry_type ('debt' | 'payment') to ledger_entries so that FIFO
diagnostic and reconciliation queries have an explicit discriminator
instead of the description-prefix heuristic.

Existing rows are backfilled once from the description convention:
payment legs always start with "Payment on YYYY-MM-DD" (enforced by
_apply_payment_fifo). All other rows default to 'debt'.

Rollback: downgrade() drops the column (backfill is discarded, safe —
entry_type can be re-derived from description at any time).

VERIFY AFTER APPLY:
  SELECT entry_type, COUNT(*) FROM ledger_entries GROUP BY entry_type;
  -- Spot-check: rows with description LIKE 'Payment on %' but entry_type='debt' → expect 0
  SELECT COUNT(*) FROM ledger_entries
    WHERE description LIKE 'Payment on %' AND entry_type = 'debt';
"""

revision = "017b"
down_revision = "017a"

import sqlalchemy as sa
from alembic import op


def upgrade():
    op.add_column(
        "ledger_entries",
        sa.Column("entry_type", sa.String(16), nullable=False, server_default="debt"),
    )
    op.execute(
        "UPDATE ledger_entries SET entry_type = 'payment' "
        "WHERE description LIKE 'Payment on %'"
    )


def downgrade():
    op.drop_column("ledger_entries", "entry_type")
