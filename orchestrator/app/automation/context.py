"""WorkflowContext — shared state and template resolver for automation workflows."""

from __future__ import annotations

import re
import calendar
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from app.automation.evaluators import ThresholdEvaluator

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

# DB metrics resolvable via ThresholdEvaluator
_INT_METRICS = {"invoice_count_this_month", "days_since_last_settlement"}
_FLOAT_METRICS = {"monthly_invoice_total", "open_debt_amount"}


def _previous_month(today: date) -> tuple[int, int]:
    if today.month == 1:
        return 12, today.year - 1
    return today.month - 1, today.year


class WorkflowContext:
    """Holds variables for a single workflow execution.

    Built-in variables are pre-computed from clock. DB metrics are computed
    lazily on first reference. Step outputs stored via store().

    Usage:
        ctx = WorkflowContext(group_jid, db=db)
        ctx.store("step1_result", "some value")
        text = ctx.resolve("Amount: {{monthly_invoice_total}} for {{previous_month}}")
        params = ctx.resolve_dict({"body": "Hi {{previous_month}}", "count": 5})
    """

    def __init__(
        self,
        group_jid: str,
        extra_vars: dict[str, str] | None = None,
        db: "Session | None" = None,
    ) -> None:
        self._group_jid = group_jid
        self._db = db
        self._db_cache: dict[str, str] = {}

        today = date.today()
        prev_month, prev_year = _previous_month(today)

        self._vars: dict[str, str] = {
            "group_jid": group_jid,
            "today": today.isoformat(),
            "current_month": f"{calendar.month_name[today.month]} {today.year}",
            "current_month_number": str(today.month),
            "current_year": str(today.year),
            "previous_month": f"{calendar.month_name[prev_month]} {prev_year}",
            "previous_month_number": str(prev_month),
            "previous_month_name": calendar.month_name[prev_month],
            "previous_month_year": str(prev_year),
        }
        if extra_vars:
            self._vars.update(extra_vars)

    def store(self, key: str, value: str) -> None:
        """Store a step output under key for use in later steps."""
        self._vars[key] = str(value)

    def resolve(self, text: str) -> str:
        """Replace all {{var}} placeholders. Unknown vars left as-is."""
        def replace(m: re.Match) -> str:
            key = m.group(1)
            if key in self._vars:
                return self._vars[key]
            val = self._resolve_db(key)
            if val is not None:
                self._vars[key] = val
                return val
            return m.group(0)
        return _PLACEHOLDER.sub(replace, text)

    def resolve_dict(self, params: dict) -> dict:
        """Return copy of params with all string values resolved."""
        return {k: self.resolve(v) if isinstance(v, str) else v for k, v in params.items()}

    def _resolve_db(self, key: str) -> str | None:
        if self._db is None:
            return None
        if key in self._db_cache:
            return self._db_cache[key]

        evaluator = ThresholdEvaluator()

        if key in _FLOAT_METRICS:
            try:
                val = evaluator.evaluate(self._db, self._group_jid, key)
                result = f"{val:,.2f}"
                self._db_cache[key] = result
                return result
            except Exception:
                return None

        if key in _INT_METRICS:
            try:
                val = evaluator.evaluate(self._db, self._group_jid, key)
                result = "∞" if val == float("inf") else f"{val:.0f}"
                self._db_cache[key] = result
                return result
            except Exception:
                return None

        if key == "previous_month_invoice_total":
            try:
                from app.db.models import Invoice
                from sqlalchemy import func
                today = date.today()
                prev_month, prev_year = _previous_month(today)
                first = date(prev_year, prev_month, 1)
                last = date(prev_year + 1, 1, 1) if prev_month == 12 else date(prev_year, prev_month + 1, 1)
                val = (
                    self._db.query(func.sum(Invoice.amount_ils))
                    .filter(
                        Invoice.group_id == self._group_jid,
                        Invoice.invoice_date >= first,
                        Invoice.invoice_date < last,
                    )
                    .scalar()
                )
                result = f"{float(val or 0):,.2f}"
                self._db_cache[key] = result
                return result
            except Exception:
                return None

        return None
