"""Threshold metric evaluators for the automation engine."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session


class ThresholdEvaluator:
    """Evaluates a named metric for a group and returns a float.

    Usage:
        ev = ThresholdEvaluator()
        value = ev.evaluate(db, group_jid, "monthly_invoice_total")
    """

    def evaluate(self, db: Session, group_jid: str, metric: str) -> float:
        method = getattr(self, f"_metric_{metric}", None)
        if method is None:
            raise ValueError(f"Unknown metric: {metric!r}")
        return method(db, group_jid)

    # ── Invoice Curator metrics ───────────────────────────────────────────────

    def _metric_monthly_invoice_total(self, db: Session, group_jid: str) -> float:
        from app.db.models import Invoice
        first_of_month = date.today().replace(day=1)
        result = (
            db.query(func.sum(Invoice.amount_ils))
            .filter(
                Invoice.group_id == group_jid,
                Invoice.invoice_date >= first_of_month,
            )
            .scalar()
        )
        return float(result or 0)

    def _metric_invoice_count_this_month(self, db: Session, group_jid: str) -> float:
        from app.db.models import Invoice
        first_of_month = date.today().replace(day=1)
        result = (
            db.query(func.count(Invoice.id))
            .filter(
                Invoice.group_id == group_jid,
                Invoice.invoice_date >= first_of_month,
            )
            .scalar()
        )
        return float(result or 0)

    # ── Family Accounting metrics ─────────────────────────────────────────────

    def _metric_open_debt_amount(self, db: Session, group_jid: str) -> float:
        from app.db.models import LedgerEntry
        result = (
            db.query(func.sum(LedgerEntry.amount_ils - LedgerEntry.amount_settled_ils))
            .filter(
                LedgerEntry.group_jid == group_jid,
                LedgerEntry.amount_ils > LedgerEntry.amount_settled_ils,
            )
            .scalar()
        )
        return float(result or 0)

    def _metric_days_since_last_settlement(self, db: Session, group_jid: str) -> float:
        from app.db.models import LedgerSettlement, LedgerEntry
        result = (
            db.query(func.max(LedgerSettlement.created_at))
            .join(LedgerEntry, LedgerSettlement.payment_leg_id == LedgerEntry.id)
            .filter(LedgerEntry.group_jid == group_jid)
            .scalar()
        )
        if result is None:
            return float("inf")
        now = datetime.now(timezone.utc)
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return (now - result).total_seconds() / 86400
