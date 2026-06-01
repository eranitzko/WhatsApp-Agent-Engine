"""One-time migration: copy invoices from old invoice_curator.db into new engine DB.

Skips duplicates by message_id. Copies GroupConfig too.

Usage:
    python tools/migrate_invoices.py <old_db_path> <new_db_path>
"""

import sqlite3
import sys
from datetime import datetime, timezone


def migrate(old_path: str, new_path: str) -> None:
    old = sqlite3.connect(old_path)
    old.row_factory = sqlite3.Row
    new = sqlite3.connect(new_path)

    old_cur = old.cursor()
    new_cur = new.cursor()

    # ── Invoices ──────────────────────────────────────────────────────────────
    old_cur.execute("SELECT * FROM invoices ORDER BY created_at")
    invoices = old_cur.fetchall()
    print(f"Found {len(invoices)} invoices in old DB")

    inserted = 0
    skipped = 0
    for inv in invoices:
        # Check for duplicate by message_id
        new_cur.execute("SELECT 1 FROM invoices WHERE message_id = ?", (inv["message_id"],))
        if new_cur.fetchone():
            skipped += 1
            continue

        new_cur.execute("""
            INSERT INTO invoices (
                id, group_id, message_id, image_hash, r2_key,
                received_at, invoice_date, invoice_number, vendor, description,
                amount_original, currency_original, amount_ils,
                exchange_rate, rate_source, rate_date,
                extraction_confidence, flagged, flag_reason,
                submitted_by, created_at
            ) VALUES (
                :id, :group_id, :message_id, :image_hash, :r2_key,
                :received_at, :invoice_date, :invoice_number, :vendor, :description,
                :amount_original, :currency_original, :amount_ils,
                :exchange_rate, :rate_source, :rate_date,
                :extraction_confidence, :flagged, :flag_reason,
                :submitted_by, :created_at
            )
        """, dict(inv))
        inserted += 1

    new.commit()
    print(f"Inserted: {inserted}  Skipped (already exist): {skipped}")

    # ── GroupConfig ───────────────────────────────────────────────────────────
    try:
        old_cur.execute("SELECT * FROM group_config")
        configs = old_cur.fetchall()
        for cfg in configs:
            new_cur.execute("SELECT 1 FROM group_config WHERE group_id = ?", (cfg["group_id"],))
            if new_cur.fetchone():
                # Update existing config
                new_cur.execute("""
                    UPDATE group_config SET
                        report_header = ?, report_author = ?,
                        feedback_language = ?, lead_currency = ?,
                        force_dual_currency = ?
                    WHERE group_id = ?
                """, (
                    cfg["report_header"], cfg["report_author"],
                    cfg["feedback_language"], cfg["lead_currency"],
                    cfg["force_dual_currency"], cfg["group_id"],
                ))
                print(f"Updated group_config for {cfg['group_id']}")
            else:
                new_cur.execute("""
                    INSERT INTO group_config (
                        group_id, report_header, report_author,
                        feedback_language, lead_currency, force_dual_currency, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    cfg["group_id"], cfg["report_header"], cfg["report_author"],
                    cfg["feedback_language"], cfg["lead_currency"],
                    cfg["force_dual_currency"],
                    datetime.now(timezone.utc).isoformat(),
                ))
                print(f"Inserted group_config for {cfg['group_id']}")
        new.commit()
    except Exception as e:
        print(f"GroupConfig migration skipped: {e}")

    old.close()
    new.close()
    print("Migration complete.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python migrate_invoices.py <old_db> <new_db>")
        sys.exit(1)
    migrate(sys.argv[1], sys.argv[2])
