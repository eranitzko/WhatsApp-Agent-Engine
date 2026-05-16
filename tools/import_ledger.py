#!/usr/bin/env python3
"""One-time family ledger import from an existing XLSX.

Usage:
    python tools/import_ledger.py --file ledger.xlsx --group-jid "123456789@g.us"
    python tools/import_ledger.py --file ledger.xlsx --group-jid "123456789@g.us" --dry-run

Before running: fill in COLUMN_MAP below to match your spreadsheet.
Column letters are 0-indexed from 'A'. Each value is a field name in LedgerEntry.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "orchestrator"))

from app.db.models import Base, LedgerEntry
from app.db.session import SessionLocal

# ── Fill this in before running ───────────────────────────────────────────────
# Map Excel column letter → LedgerEntry field name.
# Supported fields: transaction_date, from_phone, to_phone, amount_ils,
#                   amount_settled_ils (optional), description (optional)
#
# Example (uncomment and adjust to your spreadsheet):
# COLUMN_MAP = {
#     "A": "transaction_date",   # e.g. 2025-01-15 or a date cell
#     "B": "from_phone",         # e.g. 972501234567
#     "C": "to_phone",           # e.g. 972509876543
#     "D": "amount_ils",         # numeric
#     "E": "description",        # free text
# }
COLUMN_MAP: dict[str, str] = {}
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_FIELDS = {"transaction_date", "from_phone", "to_phone", "amount_ils"}


def _col_idx(letter: str) -> int:
    return ord(letter.upper()) - ord("A")


def _parse_date(val) -> date:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val.strip())
    raise ValueError(f"Cannot parse date from {val!r}")


def _parse_decimal(val) -> Decimal:
    try:
        return Decimal(str(val)).quantize(Decimal("0.0001"))
    except InvalidOperation as exc:
        raise ValueError(f"Cannot parse amount from {val!r}") from exc


def import_xlsx(filepath: str, group_jid: str, *, header_rows: int = 1, dry_run: bool = False) -> None:
    """Read the XLSX and insert rows into ledger_entries.

    Args:
        filepath: Path to the XLSX file.
        group_jid: WhatsApp group JID to associate all rows with.
        header_rows: Number of header rows to skip (default 1).
        dry_run: If True, parse and validate without writing to DB.
    """
    if not COLUMN_MAP:
        print("ERROR: COLUMN_MAP is empty. Fill it in before running.")
        sys.exit(1)

    missing = REQUIRED_FIELDS - set(COLUMN_MAP.values())
    if missing:
        print(f"ERROR: COLUMN_MAP is missing required fields: {missing}")
        sys.exit(1)

    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active

    imported = skipped = errors = 0

    with SessionLocal() as db:
        for row_num, row in enumerate(ws.iter_rows(min_row=header_rows + 1, values_only=True), start=header_rows + 1):
            if all(v is None for v in row):
                continue  # blank row

            try:
                mapped: dict = {}
                for col_letter, field in COLUMN_MAP.items():
                    mapped[field] = row[_col_idx(col_letter)]

                tx_date = _parse_date(mapped["transaction_date"])
                from_phone = str(mapped["from_phone"]).strip()
                to_phone = str(mapped["to_phone"]).strip()
                amount_ils = _parse_decimal(mapped["amount_ils"])
                amount_settled = _parse_decimal(mapped.get("amount_settled_ils") or 0)
                description = str(mapped.get("description") or "").strip()

                if amount_ils <= Decimal("0"):
                    skipped += 1
                    continue

                # Idempotency check
                exists = (
                    db.query(LedgerEntry)
                    .filter_by(
                        group_jid=group_jid,
                        from_phone=from_phone,
                        to_phone=to_phone,
                        transaction_date=tx_date,
                        amount_ils=amount_ils,
                        description=description,
                    )
                    .first()
                )
                if exists:
                    skipped += 1
                    continue

                entry = LedgerEntry(
                    transaction_id=str(uuid.uuid4()),
                    group_jid=group_jid,
                    from_phone=from_phone,
                    to_phone=to_phone,
                    amount_ils=amount_ils,
                    amount_settled_ils=amount_settled,
                    description=description,
                    transaction_date=tx_date,
                    created_at=datetime.now(timezone.utc),
                )
                if not dry_run:
                    db.add(entry)
                imported += 1

            except Exception as exc:
                print(f"Row {row_num}: ERROR — {exc} (row data: {row})")
                errors += 1

        if not dry_run:
            db.commit()

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"{prefix}Done. Imported: {imported} | Skipped: {skipped} | Errors: {errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import family ledger from XLSX")
    parser.add_argument("--file", required=True, help="Path to XLSX file")
    parser.add_argument("--group-jid", required=True, help="WhatsApp group JID")
    parser.add_argument("--header-rows", type=int, default=1, help="Number of header rows to skip")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write to DB")
    args = parser.parse_args()

    import_xlsx(args.file, args.group_jid, header_rows=args.header_rows, dry_run=args.dry_run)
