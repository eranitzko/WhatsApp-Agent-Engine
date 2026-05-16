# Family Accounting Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `family_accounting` blueprint to the WhatsApp Agent Engine that tracks debts, splits, FIFO payments, and scheduled reminders between a fixed set of family members.

**Architecture:** New blueprint alongside `invoice_curator` — shares the orchestrator, DB, bridge, and AgentRunner with no new infrastructure. Adds migration 006 (3 tables), 6 tools, APScheduler for reminders, and a one-time CLI import script.

**Tech Stack:** SQLAlchemy, Alembic, APScheduler 3.x, openpyxl, httpx (exchangerate.host), FastAPI lifespan.

---

## File Map

| Action | Path |
|---|---|
| Create | `orchestrator/app/db/migrations/versions/006_family_accounting.py` |
| Modify | `orchestrator/app/db/models.py` |
| Modify | `orchestrator/requirements.txt` |
| Create | `orchestrator/app/tools/accounting_fifo.py` |
| Create | `orchestrator/app/tools/accounting_fx.py` |
| Create | `orchestrator/app/tools/accounting_export.py` |
| Create | `orchestrator/app/tools/accounting_tools.py` |
| Create | `orchestrator/app/prompts/family_accounting.py` |
| Create | `orchestrator/app/scheduler.py` |
| Modify | `orchestrator/app/config.py` |
| Modify | `orchestrator/app/seeder.py` |
| Modify | `orchestrator/app/main.py` |
| Create | `tools/import_ledger.py` |
| Create | `orchestrator/tests/test_accounting_fifo.py` |
| Create | `orchestrator/tests/test_accounting_fx.py` |
| Create | `orchestrator/tests/test_accounting_tools.py` |
| Create | `orchestrator/tests/test_scheduler.py` |

---

## Task 1: DB Migration + ORM Models + APScheduler Dependency

**Files:**
- Create: `orchestrator/app/db/migrations/versions/006_family_accounting.py`
- Modify: `orchestrator/app/db/models.py`
- Modify: `orchestrator/requirements.txt`

- [ ] **Step 1: Add apscheduler to requirements.txt**

Open `orchestrator/requirements.txt` and append after the last line:

```
# Task scheduling (reminders)
apscheduler==3.10.4
```

- [ ] **Step 2: Install the new dependency**

```bash
cd orchestrator
pip install apscheduler==3.10.4
```

Expected: `Successfully installed apscheduler-3.10.4`

- [ ] **Step 3: Create migration 006**

Create `orchestrator/app/db/migrations/versions/006_family_accounting.py`:

```python
"""Add family accounting tables

Revision ID: 006
Revises: 005
Create Date: 2026-05-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("transaction_id", sa.String(36), nullable=False, index=True),
        sa.Column("group_jid", sa.String(255), nullable=False, index=True),
        sa.Column("from_phone", sa.String(255), nullable=False),
        sa.Column("to_phone", sa.String(255), nullable=False),
        sa.Column("amount_ils", sa.Numeric(18, 4), nullable=False),
        sa.Column("amount_settled_ils", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ledger_settlements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("payment_leg_id", sa.String(36), sa.ForeignKey("ledger_entries.id"), nullable=False),
        sa.Column("debt_leg_id", sa.String(36), sa.ForeignKey("ledger_entries.id"), nullable=False),
        sa.Column("amount_ils", sa.Numeric(18, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "scheduled_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_jid", sa.String(255), nullable=False),
        sa.Column("to_phone", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("send_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("scheduled_messages")
    op.drop_table("ledger_settlements")
    op.drop_table("ledger_entries")
```

- [ ] **Step 4: Add ORM models to models.py**

Open `orchestrator/app/db/models.py`. Add this import at the top (after the existing imports):

```python
from decimal import Decimal
```

Then append these three classes at the end of the file:

```python
class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id                 = Column(String(36), primary_key=True, default=_uuid)
    transaction_id     = Column(String(36), nullable=False, index=True)
    group_jid          = Column(String(255), nullable=False, index=True)
    from_phone         = Column(String(255), nullable=False)
    to_phone           = Column(String(255), nullable=False)
    amount_ils         = Column(Numeric(18, 4), nullable=False)
    amount_settled_ils = Column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    description        = Column(Text, nullable=False, default="")
    transaction_date   = Column(Date, nullable=False)
    created_at         = Column(DateTime(timezone=True), nullable=False,
                                default=lambda: datetime.now(timezone.utc))

    @property
    def remaining_ils(self) -> Decimal:
        return self.amount_ils - self.amount_settled_ils


class LedgerSettlement(Base):
    __tablename__ = "ledger_settlements"

    id             = Column(String(36), primary_key=True, default=_uuid)
    payment_leg_id = Column(String(36), ForeignKey("ledger_entries.id"), nullable=False)
    debt_leg_id    = Column(String(36), ForeignKey("ledger_entries.id"), nullable=False)
    amount_ils     = Column(Numeric(18, 4), nullable=False)
    created_at     = Column(DateTime(timezone=True), nullable=False,
                            default=lambda: datetime.now(timezone.utc))


class ScheduledMessage(Base):
    __tablename__ = "scheduled_messages"

    id         = Column(String(36), primary_key=True, default=_uuid)
    group_jid  = Column(String(255), nullable=False)
    to_phone   = Column(String(255), nullable=False)
    message    = Column(Text, nullable=False)
    send_at    = Column(DateTime(timezone=True), nullable=False)
    sent       = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 5: Verify tests still pass**

```bash
cd orchestrator
python -m pytest tests/ -v
```

Expected: all 51 tests pass (new models add no failures — they're just new table definitions).

- [ ] **Step 6: Commit**

```bash
git add orchestrator/requirements.txt orchestrator/app/db/migrations/versions/006_family_accounting.py orchestrator/app/db/models.py
git commit -m "feat: migration 006 — ledger_entries, ledger_settlements, scheduled_messages + ORM models"
```

---

## Task 2: FIFO Settlement Logic

**Files:**
- Create: `orchestrator/app/tools/accounting_fifo.py`
- Create: `orchestrator/tests/test_accounting_fifo.py`

- [ ] **Step 1: Write the failing tests**

Create `orchestrator/tests/test_accounting_fifo.py`:

```python
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.tools.accounting_fifo import DebtLeg, SettlementResult, apply_payment


def _leg(id: str, amount: float, settled: float = 0.0, days_ago: int = 0) -> DebtLeg:
    return DebtLeg(
        id=id,
        amount_ils=Decimal(str(amount)),
        amount_settled_ils=Decimal(str(settled)),
        transaction_date=date.today() - timedelta(days=days_ago),
    )


def test_full_settlement_single_debt():
    result = apply_payment(Decimal("100"), [_leg("a", 100)])
    assert result.settlements == [("a", Decimal("100"))]
    assert result.updated_legs == [("a", Decimal("100"))]
    assert result.leftover == Decimal("0")


def test_partial_settlement_single_debt():
    result = apply_payment(Decimal("60"), [_leg("a", 100)])
    assert result.settlements == [("a", Decimal("60"))]
    assert result.updated_legs == [("a", Decimal("60"))]
    assert result.leftover == Decimal("0")


def test_fifo_oldest_settled_first():
    # debts ordered oldest-first (days_ago=5 then days_ago=1)
    debts = [_leg("old", 100, days_ago=5), _leg("new", 100, days_ago=1)]
    result = apply_payment(Decimal("120"), debts)
    assert ("old", Decimal("100")) in result.settlements
    assert ("new", Decimal("20")) in result.settlements
    assert result.leftover == Decimal("0")


def test_payment_exceeds_all_debts_leftover():
    debts = [_leg("a", 50), _leg("b", 30)]
    result = apply_payment(Decimal("100"), debts)
    assert result.leftover == Decimal("20")


def test_partial_already_settled_debt():
    result = apply_payment(Decimal("40"), [_leg("a", 100, settled=60)])
    assert result.settlements == [("a", Decimal("40"))]
    assert result.updated_legs == [("a", Decimal("100"))]
    assert result.leftover == Decimal("0")


def test_empty_debts_returns_full_leftover():
    result = apply_payment(Decimal("100"), [])
    assert result.settlements == []
    assert result.updated_legs == []
    assert result.leftover == Decimal("100")


def test_zero_payment_does_nothing():
    result = apply_payment(Decimal("0"), [_leg("a", 100)])
    assert result.settlements == []
    assert result.leftover == Decimal("0")


def test_fully_settled_debt_is_skipped():
    debts = [_leg("done", 100, settled=100), _leg("open", 50)]
    result = apply_payment(Decimal("50"), debts)
    assert all(d != "done" for d, _ in result.settlements)
    assert ("open", Decimal("50")) in result.settlements
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd orchestrator
python -m pytest tests/test_accounting_fifo.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `accounting_fifo` does not exist yet.

- [ ] **Step 3: Implement accounting_fifo.py**

Create `orchestrator/app/tools/accounting_fifo.py`:

```python
"""Pure FIFO settlement logic — no DB access, fully testable in isolation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class DebtLeg:
    id: str
    amount_ils: Decimal
    amount_settled_ils: Decimal
    transaction_date: date

    @property
    def remaining_ils(self) -> Decimal:
        return self.amount_ils - self.amount_settled_ils


@dataclass
class SettlementResult:
    settlements: list[tuple[str, Decimal]]   # (debt_leg_id, amount_applied)
    updated_legs: list[tuple[str, Decimal]]  # (debt_leg_id, new amount_settled_ils)
    leftover: Decimal                        # unspent remainder after all debts covered


def apply_payment(payment_amount: Decimal, open_debts: list[DebtLeg]) -> SettlementResult:
    """Apply payment_amount to open_debts FIFO (oldest transaction_date first).

    Args:
        payment_amount: Total ILS amount to distribute.
        open_debts: Debt legs ordered by transaction_date ASC (caller's responsibility).

    Returns:
        SettlementResult describing which legs were settled and by how much.
    """
    remaining = payment_amount
    settlements: list[tuple[str, Decimal]] = []
    updated_legs: list[tuple[str, Decimal]] = []

    for debt in open_debts:
        if remaining <= Decimal("0"):
            break
        if debt.remaining_ils <= Decimal("0"):
            continue
        apply = min(debt.remaining_ils, remaining)
        settlements.append((debt.id, apply))
        updated_legs.append((debt.id, debt.amount_settled_ils + apply))
        remaining -= apply

    return SettlementResult(
        settlements=settlements,
        updated_legs=updated_legs,
        leftover=remaining,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd orchestrator
python -m pytest tests/test_accounting_fifo.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/tools/accounting_fifo.py orchestrator/tests/test_accounting_fifo.py
git commit -m "feat: FIFO settlement logic with full test coverage"
```

---

## Task 3: Currency Conversion

**Files:**
- Create: `orchestrator/app/tools/accounting_fx.py`
- Create: `orchestrator/tests/test_accounting_fx.py`

- [ ] **Step 1: Write the failing tests**

Create `orchestrator/tests/test_accounting_fx.py`:

```python
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_ils_passthrough():
    from app.tools.accounting_fx import to_ils
    result = await to_ils(Decimal("100"), "ILS", date.today())
    assert result == Decimal("100")


@pytest.mark.asyncio
async def test_converts_usd_to_ils(monkeypatch):
    from app.tools import accounting_fx

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"success": True, "result": 370.0}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(accounting_fx.httpx, "AsyncClient", MagicMock(return_value=mock_client))

    result = await accounting_fx.to_ils(Decimal("100"), "USD", date(2026, 1, 15))
    assert result == Decimal("370.0")


@pytest.mark.asyncio
async def test_raises_on_http_error(monkeypatch):
    from app.tools import accounting_fx

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(accounting_fx.httpx, "AsyncClient", MagicMock(return_value=mock_client))

    with pytest.raises(RuntimeError, match="FX API unavailable"):
        await accounting_fx.to_ils(Decimal("100"), "USD", date.today())


@pytest.mark.asyncio
async def test_raises_on_api_success_false(monkeypatch):
    from app.tools import accounting_fx

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"success": False}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(accounting_fx.httpx, "AsyncClient", MagicMock(return_value=mock_client))

    with pytest.raises(RuntimeError, match="FX API returned error"):
        await accounting_fx.to_ils(Decimal("50"), "EUR", date.today())
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd orchestrator
python -m pytest tests/test_accounting_fx.py -v
```

Expected: `ImportError` — `accounting_fx` does not exist yet.

- [ ] **Step 3: Implement accounting_fx.py**

Create `orchestrator/app/tools/accounting_fx.py`:

```python
"""Currency → ILS conversion via api.exchangerate.host."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.exchangerate.host/convert"


async def to_ils(amount: Decimal, currency: str, on_date: date) -> Decimal:
    """Convert amount in currency to ILS at the exchange rate for on_date.

    Args:
        amount: Amount to convert.
        currency: ISO 4217 source currency code (e.g. "USD", "EUR").
        on_date: Date for which to fetch the rate.

    Returns:
        Equivalent amount in ILS.

    Raises:
        RuntimeError: If the API is unavailable or returns an error — caller
            should surface this to the user and ask them to provide ILS amount manually.
    """
    if currency.upper() == "ILS":
        return amount

    params = {
        "from": currency.upper(),
        "to": "ILS",
        "date": on_date.isoformat(),
        "amount": str(amount),
    }

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(_BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(
                f"FX API unavailable ({exc}). Please provide the ILS amount manually."
            ) from exc

    if not data.get("success"):
        raise RuntimeError(
            f"FX API returned error for {currency}→ILS on {on_date}. "
            "Please provide the ILS amount manually."
        )

    return Decimal(str(data["result"]))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd orchestrator
python -m pytest tests/test_accounting_fx.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/tools/accounting_fx.py orchestrator/tests/test_accounting_fx.py
git commit -m "feat: currency conversion to ILS via exchangerate.host"
```

---

## Task 4: XLSX Export

**Files:**
- Create: `orchestrator/app/tools/accounting_export.py`

No dedicated tests for this module — it's thin glue around openpyxl and the DB. Coverage comes from the integration path through `export_ledger` tool in Task 6.

- [ ] **Step 1: Implement accounting_export.py**

Create `orchestrator/app/tools/accounting_export.py`:

```python
"""XLSX ledger export — two sheets: Balances and Transactions."""

from __future__ import annotations

import io
from decimal import Decimal

import openpyxl
from openpyxl.styles import Font

from app.db.session import SessionLocal
from app.db.models import LedgerEntry


def generate_ledger_xlsx(group_jid: str) -> bytes:
    """Return XLSX bytes with two sheets: Balances (net per pair) and Transactions (full log)."""
    with SessionLocal() as db:
        entries = (
            db.query(LedgerEntry)
            .filter_by(group_jid=group_jid)
            .order_by(LedgerEntry.transaction_date)
            .all()
        )

    wb = openpyxl.Workbook()

    ws_bal = wb.active
    ws_bal.title = "Balances"
    _write_balances_sheet(ws_bal, entries)

    ws_tx = wb.create_sheet("Transactions")
    _write_transactions_sheet(ws_tx, entries)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _compute_net_balances(entries: list) -> dict[tuple[str, str], Decimal]:
    """Return net remaining amount per ordered pair (from_phone, to_phone)."""
    raw: dict[tuple[str, str], Decimal] = {}
    for e in entries:
        remaining = e.amount_ils - e.amount_settled_ils
        if remaining <= Decimal("0"):
            continue
        key = (e.from_phone, e.to_phone)
        raw[key] = raw.get(key, Decimal("0")) + remaining

    netted: dict[tuple[str, str], Decimal] = {}
    seen: set[tuple[str, str]] = set()
    for (a, b), amt in raw.items():
        if (a, b) in seen or (b, a) in seen:
            continue
        seen.add((a, b))
        reverse = raw.get((b, a), Decimal("0"))
        net = amt - reverse
        if net > Decimal("0"):
            netted[(a, b)] = net
        elif net < Decimal("0"):
            netted[(b, a)] = -net
    return netted


def _write_balances_sheet(ws, entries: list) -> None:
    headers = ["Owes", "To", "Amount (ILS)"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for (frm, to), amt in sorted(_compute_net_balances(entries).items()):
        ws.append([frm, to, float(amt)])


def _write_transactions_sheet(ws, entries: list) -> None:
    headers = ["Date", "From", "To", "Amount ILS", "Settled ILS", "Remaining ILS", "Description", "Transaction ID"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for e in entries:
        ws.append([
            e.transaction_date.isoformat() if e.transaction_date else "",
            e.from_phone,
            e.to_phone,
            float(e.amount_ils),
            float(e.amount_settled_ils),
            float(e.amount_ils - e.amount_settled_ils),
            e.description,
            e.transaction_id,
        ])
```

- [ ] **Step 2: Verify import works**

```bash
cd orchestrator
python -c "from app.tools.accounting_export import generate_ledger_xlsx; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add orchestrator/app/tools/accounting_export.py
git commit -m "feat: XLSX ledger export — Balances and Transactions sheets"
```

---

## Task 5: Accounting Tools

**Files:**
- Create: `orchestrator/app/tools/accounting_tools.py`

- [ ] **Step 1: Implement accounting_tools.py**

Create `orchestrator/app/tools/accounting_tools.py`:

```python
"""Family Accounting tools in ToolRegistry format."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import or_

from app.db.models import LedgerEntry, LedgerSettlement, ScheduledMessage
from app.db.session import SessionLocal
from app.tools.accounting_export import generate_ledger_xlsx
from app.tools.accounting_fifo import DebtLeg, apply_payment
from app.tools.accounting_fx import to_ils

logger = logging.getLogger(__name__)

# ── Schemas ───────────────────────────────────────────────────────────────────

_SCHEMAS: dict[str, dict] = {
    "record_transaction": {
        "name": "record_transaction",
        "description": (
            "Record that someone paid for others. Claude extracts payer, participants, "
            "amount, currency, description, and date from natural language."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "payer_phone": {"type": "string", "description": "Phone of the person who paid"},
                "participant_phones": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Phones of people who owe the payer (excluding the payer)",
                },
                "amount": {"type": "number", "description": "Total amount paid"},
                "currency": {"type": "string", "description": "ISO 4217 code, e.g. ILS, USD, EUR"},
                "description": {"type": "string", "description": "What the payment was for"},
                "transaction_date": {
                    "type": "string",
                    "description": "Date YYYY-MM-DD; defaults to today if omitted",
                },
            },
            "required": ["payer_phone", "participant_phones", "amount", "currency", "description"],
        },
    },
    "record_payment": {
        "name": "record_payment",
        "description": "Record a debt repayment. Applies FIFO settlement to open debt legs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "payer_phone": {"type": "string", "description": "Phone making the payment"},
                "payee_phone": {"type": "string", "description": "Phone receiving payment"},
                "amount_ils": {"type": "number", "description": "Payment amount in ILS"},
                "payment_date": {
                    "type": "string",
                    "description": "Date YYYY-MM-DD; defaults to today if omitted",
                },
            },
            "required": ["payer_phone", "payee_phone", "amount_ils"],
        },
    },
    "get_balance": {
        "name": "get_balance",
        "description": (
            "Get net balance. With phone_a only: all open balances for that person. "
            "With phone_a and phone_b: net balance between them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone_a": {"type": "string", "description": "First person's phone"},
                "phone_b": {"type": "string", "description": "Second person's phone (optional)"},
            },
            "required": ["phone_a"],
        },
    },
    "get_history": {
        "name": "get_history",
        "description": "Get itemized transaction history, optionally filtered by person and/or date range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Filter to transactions involving this phone (optional)"},
                "from_date": {"type": "string", "description": "Start date YYYY-MM-DD (optional)"},
                "to_date": {"type": "string", "description": "End date YYYY-MM-DD (optional)"},
            },
            "required": [],
        },
    },
    "export_ledger": {
        "name": "export_ledger",
        "description": "Generate an XLSX with full balances and transaction history and email it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Email address to send the export to"},
            },
            "required": ["email"],
        },
    },
    "set_reminder": {
        "name": "set_reminder",
        "description": (
            "Schedule a reminder WhatsApp message for the sender at a future time. "
            "Only the sender can set their own reminders."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The reminder text"},
                "send_at": {"type": "string", "description": "ISO 8601 datetime, e.g. 2026-06-01T09:00:00"},
            },
            "required": ["message", "send_at"],
        },
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _net_owed(db, group_jid: str, from_phone: str, to_phone: str) -> Decimal:
    """Total remaining amount from_phone owes to_phone in this group."""
    rows = (
        db.query(LedgerEntry)
        .filter(
            LedgerEntry.group_jid == group_jid,
            LedgerEntry.from_phone == from_phone,
            LedgerEntry.to_phone == to_phone,
        )
        .all()
    )
    return sum((r.amount_ils - r.amount_settled_ils for r in rows), Decimal("0"))


# ── Executors ─────────────────────────────────────────────────────────────────

async def _exec_record_transaction(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    payer = params["payer_phone"]
    participants = params["participant_phones"]
    amount = Decimal(str(params["amount"]))
    currency = params.get("currency", "ILS")
    description = params.get("description", "")
    tx_date_str = params.get("transaction_date") or date.today().isoformat()
    tx_date = date.fromisoformat(tx_date_str)

    try:
        amount_ils = await to_ils(amount, currency, tx_date)
    except RuntimeError as exc:
        return str(exc)

    if not participants:
        return "Error: participant_phones must not be empty."

    per_person = (amount_ils / Decimal(len(participants))).quantize(Decimal("0.01"))
    desc_with_fx = (
        f"{description} (original: {amount} {currency.upper()})"
        if currency.upper() != "ILS"
        else description
    )
    transaction_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        for phone in participants:
            db.add(LedgerEntry(
                transaction_id=transaction_id,
                group_jid=group_jid,
                from_phone=phone,
                to_phone=payer,
                amount_ils=per_person,
                amount_settled_ils=Decimal("0"),
                description=desc_with_fx,
                transaction_date=tx_date,
                created_at=now,
            ))
        db.commit()

    split_info = (
        f"split equally {per_person:.2f} ILS each among {len(participants)} people"
        if len(participants) > 1
        else f"{amount_ils:.2f} ILS"
    )
    return f"Recorded: {payer} paid for {', '.join(participants)} — {split_info}. (tx: {transaction_id[:8]})"


async def _exec_record_payment(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    payer = params["payer_phone"]
    payee = params["payee_phone"]
    amount_ils = Decimal(str(params["amount_ils"]))
    pay_date_str = params.get("payment_date") or date.today().isoformat()
    pay_date = date.fromisoformat(pay_date_str)
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        open_rows = (
            db.query(LedgerEntry)
            .filter(
                LedgerEntry.group_jid == group_jid,
                LedgerEntry.from_phone == payer,
                LedgerEntry.to_phone == payee,
                LedgerEntry.amount_ils > LedgerEntry.amount_settled_ils,
            )
            .order_by(LedgerEntry.transaction_date)
            .all()
        )

        debt_legs = [
            DebtLeg(
                id=r.id,
                amount_ils=r.amount_ils,
                amount_settled_ils=r.amount_settled_ils,
                transaction_date=r.transaction_date,
            )
            for r in open_rows
        ]

        result = apply_payment(amount_ils, debt_legs)

        for leg_id, new_settled in result.updated_legs:
            row = db.get(LedgerEntry, leg_id)
            row.amount_settled_ils = new_settled

        payment_leg = LedgerEntry(
            transaction_id=str(uuid.uuid4()),
            group_jid=group_jid,
            from_phone=payee,
            to_phone=payer,
            amount_ils=amount_ils,
            amount_settled_ils=amount_ils,
            description=f"Payment on {pay_date.isoformat()}",
            transaction_date=pay_date,
            created_at=now,
        )
        db.add(payment_leg)
        db.flush()

        for debt_leg_id, applied_amount in result.settlements:
            db.add(LedgerSettlement(
                payment_leg_id=payment_leg.id,
                debt_leg_id=debt_leg_id,
                amount_ils=applied_amount,
                created_at=now,
            ))
        db.commit()

    parts = [f"{amt:.2f} ILS off debt {did[:8]}" for did, amt in result.settlements]
    summary = "; ".join(parts) if parts else "no open debts found to settle"
    leftover = f" (overpaid by {result.leftover:.2f} ILS)" if result.leftover > 0 else ""
    return f"Payment of {amount_ils:.2f} ILS recorded. {summary}.{leftover}"


async def _exec_get_balance(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    phone_a = params["phone_a"]
    phone_b = params.get("phone_b")

    with SessionLocal() as db:
        if phone_b:
            a_owes_b = _net_owed(db, group_jid, phone_a, phone_b)
            b_owes_a = _net_owed(db, group_jid, phone_b, phone_a)
            net = a_owes_b - b_owes_a
            if net > Decimal("0"):
                return f"{phone_a} owes {phone_b}: {net:.2f} ILS"
            elif net < Decimal("0"):
                return f"{phone_b} owes {phone_a}: {(-net):.2f} ILS"
            return f"{phone_a} and {phone_b} are settled up."

        rows = (
            db.query(LedgerEntry)
            .filter(
                LedgerEntry.group_jid == group_jid,
                or_(LedgerEntry.from_phone == phone_a, LedgerEntry.to_phone == phone_a),
            )
            .all()
        )
        partners = {
            r.from_phone if r.to_phone == phone_a else r.to_phone
            for r in rows
        }

        lines = []
        for partner in sorted(partners):
            a_owes = _net_owed(db, group_jid, phone_a, partner)
            p_owes = _net_owed(db, group_jid, partner, phone_a)
            net = a_owes - p_owes
            if net > Decimal("0"):
                lines.append(f"{phone_a} owes {partner}: {net:.2f} ILS")
            elif net < Decimal("0"):
                lines.append(f"{partner} owes {phone_a}: {(-net):.2f} ILS")

        return "\n".join(lines) if lines else f"No open balances for {phone_a}."


async def _exec_get_history(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    phone = params.get("phone")
    from_date = params.get("from_date")
    to_date = params.get("to_date")

    with SessionLocal() as db:
        q = db.query(LedgerEntry).filter(LedgerEntry.group_jid == group_jid)
        if phone:
            q = q.filter(or_(LedgerEntry.from_phone == phone, LedgerEntry.to_phone == phone))
        if from_date:
            q = q.filter(LedgerEntry.transaction_date >= date.fromisoformat(from_date))
        if to_date:
            q = q.filter(LedgerEntry.transaction_date <= date.fromisoformat(to_date))
        rows = q.order_by(LedgerEntry.transaction_date).all()

    if not rows:
        return "No transactions found."

    lines = []
    for r in rows:
        remaining = r.amount_ils - r.amount_settled_ils
        status = "✓ settled" if remaining <= Decimal("0") else f"{remaining:.2f} ILS remaining"
        lines.append(
            f"{r.transaction_date} | {r.from_phone} → {r.to_phone} | "
            f"{r.amount_ils:.2f} ILS | {status} | {r.description}"
        )
    return "\n".join(lines)


async def _exec_export_ledger(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    email = params["email"]

    try:
        xlsx_bytes = generate_ledger_xlsx(group_jid)
    except Exception as exc:
        logger.exception("export_ledger: XLSX generation failed")
        return f"Failed to generate report: {exc}"

    try:
        from app.mailer.gmail import send_report_email
        send_report_email(
            to=email,
            subject="Family Ledger Export",
            body="Your family ledger export is attached.",
            attachments=[("ledger.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsx_bytes)],
        )
    except Exception as exc:
        logger.exception("export_ledger: email failed")
        return f"Report generated but failed to send email: {exc}"

    return f"Ledger exported and sent to {email}."


async def _exec_set_reminder(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    sender = ctx.get("sender", "")
    to_phone = sender.split("@")[0].split(":")[0]
    message = params["message"]
    send_at_str = params["send_at"]

    try:
        send_at = datetime.fromisoformat(send_at_str)
        if send_at.tzinfo is None:
            send_at = send_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return f"Invalid datetime: '{send_at_str}'. Use ISO 8601, e.g. 2026-06-01T09:00:00."

    now = datetime.now(timezone.utc)
    if send_at <= now:
        return "send_at must be in the future."

    with SessionLocal() as db:
        db.add(ScheduledMessage(
            group_jid=group_jid,
            to_phone=to_phone,
            message=message,
            send_at=send_at,
            sent=False,
            created_at=now,
        ))
        db.commit()

    return f"Reminder set for {send_at.isoformat()}: \"{message}\""


# ── Public factory ─────────────────────────────────────────────────────────────

def get_accounting_tools() -> dict[str, dict]:
    """Return all 6 accounting tools in ToolRegistry format."""
    return {
        name: {"schema": _SCHEMAS[name], "executor": executor}
        for name, executor in [
            ("record_transaction", _exec_record_transaction),
            ("record_payment",     _exec_record_payment),
            ("get_balance",        _exec_get_balance),
            ("get_history",        _exec_get_history),
            ("export_ledger",      _exec_export_ledger),
            ("set_reminder",       _exec_set_reminder),
        ]
    }
```

- [ ] **Step 2: Verify import works**

```bash
cd orchestrator
python -c "from app.tools.accounting_tools import get_accounting_tools; t = get_accounting_tools(); print(list(t.keys()))"
```

Expected: `['record_transaction', 'record_payment', 'get_balance', 'get_history', 'export_ledger', 'set_reminder']`

- [ ] **Step 3: Commit**

```bash
git add orchestrator/app/tools/accounting_tools.py
git commit -m "feat: accounting tools — record_transaction, record_payment, get_balance, get_history, export_ledger, set_reminder"
```

---

## Task 6: Tool Tests

**Files:**
- Create: `orchestrator/tests/test_accounting_tools.py`

- [ ] **Step 1: Write the tests**

Create `orchestrator/tests/test_accounting_tools.py`:

```python
import inspect
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import LedgerEntry, ScheduledMessage
from app.tools.accounting_tools import get_accounting_tools

EXPECTED_TOOLS = [
    "record_transaction", "record_payment", "get_balance",
    "get_history", "export_ledger", "set_reminder",
]


def test_get_accounting_tools_returns_all_six():
    tools = get_accounting_tools()
    assert set(tools.keys()) == set(EXPECTED_TOOLS)


def test_each_tool_has_schema_and_executor():
    tools = get_accounting_tools()
    for name, entry in tools.items():
        assert "schema" in entry, f"{name} missing schema"
        assert "executor" in entry, f"{name} missing executor"
        assert entry["schema"]["name"] == name


def test_each_schema_has_required_keys():
    tools = get_accounting_tools()
    for name, entry in tools.items():
        missing = {"name", "description", "input_schema"} - entry["schema"].keys()
        assert not missing, f"{name}: schema missing {missing}"


def test_all_executors_are_async():
    tools = get_accounting_tools()
    for name, entry in tools.items():
        assert inspect.iscoroutinefunction(entry["executor"]), f"{name} executor is not async"


@pytest.mark.asyncio
async def test_record_transaction_creates_legs_for_each_participant(db):
    from app.db.session import SessionLocal
    with patch("app.tools.accounting_tools.SessionLocal", return_value=db):
        # Patch to_ils to return the amount unchanged
        with patch("app.tools.accounting_tools.to_ils", new=AsyncMock(return_value=Decimal("300"))):
            tools = get_accounting_tools()
            result = await tools["record_transaction"]["executor"](
                {
                    "payer_phone": "972500000001",
                    "participant_phones": ["972500000002", "972500000003"],
                    "amount": 300,
                    "currency": "ILS",
                    "description": "dinner",
                },
                group_jid="123@g.us",
                sender="972500000001@s.whatsapp.net",
                is_admin=False,
                confirmation_store=None,
            )
    assert "recorded" in result.lower() or "972500000001" in result


@pytest.mark.asyncio
async def test_get_balance_settled_up(db):
    now = datetime.now(timezone.utc)
    db.add(LedgerEntry(
        id="e1", transaction_id="t1", group_jid="123@g.us",
        from_phone="A", to_phone="B",
        amount_ils=Decimal("100"), amount_settled_ils=Decimal("100"),
        description="test", transaction_date=date.today(), created_at=now,
    ))
    db.commit()

    with patch("app.tools.accounting_tools.SessionLocal", return_value=db):
        tools = get_accounting_tools()
        result = await tools["get_balance"]["executor"](
            {"phone_a": "A", "phone_b": "B"},
            group_jid="123@g.us",
            sender="A@s.whatsapp.net",
            is_admin=False,
            confirmation_store=None,
        )
    assert "settled" in result.lower()


@pytest.mark.asyncio
async def test_get_balance_shows_debt(db):
    now = datetime.now(timezone.utc)
    db.add(LedgerEntry(
        id="e2", transaction_id="t2", group_jid="123@g.us",
        from_phone="A", to_phone="B",
        amount_ils=Decimal("100"), amount_settled_ils=Decimal("0"),
        description="test", transaction_date=date.today(), created_at=now,
    ))
    db.commit()

    with patch("app.tools.accounting_tools.SessionLocal", return_value=db):
        tools = get_accounting_tools()
        result = await tools["get_balance"]["executor"](
            {"phone_a": "A", "phone_b": "B"},
            group_jid="123@g.us",
            sender="A@s.whatsapp.net",
            is_admin=False,
            confirmation_store=None,
        )
    assert "100.00" in result
    assert "A" in result


@pytest.mark.asyncio
async def test_set_reminder_creates_scheduled_message(db):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with patch("app.tools.accounting_tools.SessionLocal", return_value=db):
        tools = get_accounting_tools()
        result = await tools["set_reminder"]["executor"](
            {"message": "pay Dana", "send_at": future},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=False,
            confirmation_store=None,
        )
    assert "reminder set" in result.lower()
    msg = db.query(ScheduledMessage).first()
    assert msg is not None
    assert msg.message == "pay Dana"
    assert msg.to_phone == "972500000001"


@pytest.mark.asyncio
async def test_set_reminder_rejects_past_datetime(db):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with patch("app.tools.accounting_tools.SessionLocal", return_value=db):
        tools = get_accounting_tools()
        result = await tools["set_reminder"]["executor"](
            {"message": "too late", "send_at": past},
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=False,
            confirmation_store=None,
        )
    assert "future" in result.lower()
```

- [ ] **Step 2: Run the tests**

```bash
cd orchestrator
python -m pytest tests/test_accounting_tools.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 3: Run the full suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass (51 original + 9 new + FIFO + FX = 73 total).

- [ ] **Step 4: Commit**

```bash
git add orchestrator/tests/test_accounting_tools.py
git commit -m "test: accounting tools — schema, executor, balance, reminder coverage"
```

---

## Task 7: System Prompt + Config + Seeder

**Files:**
- Create: `orchestrator/app/prompts/family_accounting.py`
- Modify: `orchestrator/app/config.py`
- Modify: `orchestrator/app/seeder.py`

- [ ] **Step 1: Create the system prompt**

Create `orchestrator/app/prompts/family_accounting.py`:

```python
"""System prompt template for the Family Accounting blueprint."""

_TEMPLATE = """\
You are a family accounting assistant. You track who paid what for whom, and manage debts and repayments between family members over WhatsApp.

## Family Members
Resolve names (including "I" / "אני") to phone numbers using this list and the sender's phone from context:

{member_list}

## Rules

1. **Always confirm before recording.** Before calling record_transaction or record_payment, summarize what you understood and ask for confirmation. Example:
   - "Eran שילם 300₪ על ארוחת ערב, מתחלק שווה בין Dana ו-Yael (150₪ כל אחד). לרשום?"

2. **Resolve "I" from sender.** When someone writes "I paid" or "אני שילמתי", use their WhatsApp sender phone as the payer.

3. **Splits are equal by default.** Divide equally unless the user specifies different shares.

4. **Currency defaults to ILS.** If no currency is mentioned, assume ILS.

5. **Reminders are self-only.** The set_reminder tool may only be used for the sender themselves. Never schedule a reminder targeting another person.

6. **Respond in the user's language** — Hebrew or English, matching what they wrote.

7. **Be concise.** After recording, confirm with a short one-line summary.
"""


def build_family_accounting_prompt(members: dict[str, str]) -> str:
    """Build the system prompt with the family member list injected.

    Args:
        members: Dict of {display_name: phone_number}.
                 Example: {"Eran": "972501234567", "Dana": "972509876543"}
    """
    if not members:
        member_list = "(no family members configured — set FAMILY_MEMBERS_JSON in .env)"
    else:
        member_list = "\n".join(f"- {name}: {phone}" for name, phone in members.items())
    return _TEMPLATE.format(member_list=member_list)
```

- [ ] **Step 2: Add FAMILY_MEMBERS_JSON to config**

Open `orchestrator/app/config.py` and add this field inside the `Settings` class after `notion_tasks_database_id`:

```python
    # Family accounting: JSON object mapping display name → phone
    # Example: '{"Eran": "972501234567", "Dana": "972509876543"}'
    family_members_json: str = ""
```

- [ ] **Step 3: Update seeder.py**

Open `orchestrator/app/seeder.py` and replace its entire content with:

```python
import json
from sqlalchemy.orm import Session
from app.db.models import Blueprint, GroupRegistry, AdminNumbers
from app.prompts.invoice_curator import INVOICE_CURATOR_SYSTEM_PROMPT
from app.prompts.notion_assistant import NOTION_ASSISTANT_SYSTEM_PROMPT
from app.prompts.family_accounting import build_family_accounting_prompt
from app.config import settings


INVOICE_CURATOR_TOOLS = [
    "get_status", "list_invoices", "get_preview", "generate_report",
    "flag_invoice", "unflag_invoice", "set_invoice_date", "set_invoice_amount",
    "add_date_format", "update_config", "request_confirmation",
]

NOTION_ASSISTANT_TOOLS = [
    "search_pages", "create_task", "append_to_page", "list_database_items",
]

FAMILY_ACCOUNTING_TOOLS = [
    "record_transaction", "record_payment", "get_balance",
    "get_history", "export_ledger", "set_reminder",
]


def _family_members() -> dict[str, str]:
    """Parse FAMILY_MEMBERS_JSON from settings. Returns empty dict on error."""
    if not settings.family_members_json:
        return {}
    try:
        return json.loads(settings.family_members_json)
    except (json.JSONDecodeError, ValueError):
        return {}


DEFAULT_BLUEPRINTS = [
    {
        "id": "invoice_curator",
        "display_name": "Invoice Curator",
        "system_prompt": INVOICE_CURATOR_SYSTEM_PROMPT,
        "model": "claude-sonnet-4-6",
        "tools_enabled": json.dumps(INVOICE_CURATOR_TOOLS),
        "max_tool_turns": 6,
        "context_window": 8,
        "context_idle_reset_minutes": 60,
    },
    {
        "id": "notion_assistant",
        "display_name": "Notion Assistant",
        "system_prompt": NOTION_ASSISTANT_SYSTEM_PROMPT,
        "model": "claude-sonnet-4-6",
        "tools_enabled": json.dumps(NOTION_ASSISTANT_TOOLS),
        "max_tool_turns": 4,
        "context_window": 6,
        "context_idle_reset_minutes": 30,
    },
]


def seed(db: Session, admin_phone: str, legacy_group_jid: str | None = None) -> None:
    # Static blueprints
    for bp_data in DEFAULT_BLUEPRINTS:
        if not db.query(Blueprint).filter_by(id=bp_data["id"]).first():
            db.add(Blueprint(**bp_data))

    # Family accounting blueprint — system prompt is built from config at seed time
    if not db.query(Blueprint).filter_by(id="family_accounting").first():
        db.add(Blueprint(
            id="family_accounting",
            display_name="Family Accounting",
            system_prompt=build_family_accounting_prompt(_family_members()),
            model="claude-sonnet-4-6",
            tools_enabled=json.dumps(FAMILY_ACCOUNTING_TOOLS),
            max_tool_turns=5,
            context_window=8,
            context_idle_reset_minutes=120,
        ))

    if admin_phone and not db.query(AdminNumbers).filter_by(phone_number=admin_phone).first():
        db.add(AdminNumbers(phone_number=admin_phone, label="owner"))

    if legacy_group_jid:
        if not db.query(GroupRegistry).filter_by(group_jid=legacy_group_jid).first():
            db.add(GroupRegistry(
                group_jid=legacy_group_jid,
                blueprint_id="invoice_curator",
                status="active",
                trigger_type="always",
            ))

    db.commit()
```

- [ ] **Step 4: Verify tests still pass**

```bash
cd orchestrator
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/prompts/family_accounting.py orchestrator/app/config.py orchestrator/app/seeder.py
git commit -m "feat: family accounting system prompt, config field, seeder blueprint"
```

---

## Task 8: APScheduler

**Files:**
- Create: `orchestrator/app/scheduler.py`
- Create: `orchestrator/tests/test_scheduler.py`

- [ ] **Step 1: Write the failing tests**

Create `orchestrator/tests/test_scheduler.py`:

```python
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import ScheduledMessage


@pytest.mark.asyncio
async def test_due_message_is_sent_and_marked(db):
    now = datetime.now(timezone.utc)
    db.add(ScheduledMessage(
        id="m1",
        group_jid="123@g.us",
        to_phone="972500000001",
        message="pay Dana",
        send_at=now - timedelta(minutes=1),
        sent=False,
        created_at=now,
    ))
    db.commit()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.scheduler.SessionLocal", return_value=db), \
         patch("app.scheduler.httpx.AsyncClient", return_value=mock_client):
        from app.scheduler import _dispatch_due_messages
        await _dispatch_due_messages()

    mock_client.post.assert_called_once()
    msg = db.get(ScheduledMessage, "m1")
    assert msg.sent is True


@pytest.mark.asyncio
async def test_future_message_is_not_sent(db):
    now = datetime.now(timezone.utc)
    db.add(ScheduledMessage(
        id="m2",
        group_jid="123@g.us",
        to_phone="972500000001",
        message="future reminder",
        send_at=now + timedelta(hours=1),
        sent=False,
        created_at=now,
    ))
    db.commit()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.scheduler.SessionLocal", return_value=db), \
         patch("app.scheduler.httpx.AsyncClient", return_value=mock_client):
        from app.scheduler import _dispatch_due_messages
        await _dispatch_due_messages()

    mock_client.post.assert_not_called()
    msg = db.get(ScheduledMessage, "m2")
    assert msg.sent is False


@pytest.mark.asyncio
async def test_already_sent_message_is_not_resent(db):
    now = datetime.now(timezone.utc)
    db.add(ScheduledMessage(
        id="m3",
        group_jid="123@g.us",
        to_phone="972500000001",
        message="already sent",
        send_at=now - timedelta(minutes=5),
        sent=True,
        created_at=now,
    ))
    db.commit()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.scheduler.SessionLocal", return_value=db), \
         patch("app.scheduler.httpx.AsyncClient", return_value=mock_client):
        from app.scheduler import _dispatch_due_messages
        await _dispatch_due_messages()

    mock_client.post.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd orchestrator
python -m pytest tests/test_scheduler.py -v
```

Expected: `ImportError` — `app.scheduler` does not exist yet.

- [ ] **Step 3: Implement scheduler.py**

Create `orchestrator/app/scheduler.py`:

```python
"""APScheduler — dispatches due ScheduledMessages via the bridge."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.db.models import ScheduledMessage
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()


async def _dispatch_due_messages() -> None:
    """Query due scheduled messages, send each via bridge, mark sent."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        due = (
            db.query(ScheduledMessage)
            .filter(ScheduledMessage.sent == False, ScheduledMessage.send_at <= now)
            .all()
        )
        for msg in due:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"{settings.bridge_url}/send",
                        json={"jid": msg.group_jid, "text": msg.message},
                    )
                msg.sent = True
                logger.info("Dispatched scheduled message %s to %s", msg.id, msg.group_jid)
            except Exception:
                logger.exception("Failed to dispatch scheduled message %s", msg.id)
        db.commit()


def start_scheduler() -> None:
    _scheduler.add_job(_dispatch_due_messages, "interval", seconds=60, id="dispatch_messages")
    _scheduler.start()
    logger.info("APScheduler started — polling every 60s")


def stop_scheduler() -> None:
    _scheduler.shutdown(wait=False)
    logger.info("APScheduler stopped")
```

- [ ] **Step 4: Run the scheduler tests**

```bash
cd orchestrator
python -m pytest tests/test_scheduler.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/scheduler.py orchestrator/tests/test_scheduler.py
git commit -m "feat: APScheduler — dispatches due WhatsApp reminders every 60s"
```

---

## Task 9: Wire into main.py

**Files:**
- Modify: `orchestrator/app/main.py`

- [ ] **Step 1: Update main.py**

Open `orchestrator/app/main.py`. Make these three changes:

**Add imports** (after the existing `from app.tools.invoice_tools import get_invoice_tools` line):

```python
from app.tools.accounting_tools import get_accounting_tools
from app.scheduler import start_scheduler, stop_scheduler
```

**Register accounting tools** (inside `lifespan`, after `tool_registry.register(get_invoice_tools())`):

```python
    tool_registry.register(get_accounting_tools())
```

**Wire scheduler** (inside `lifespan`, after the `agent_runner = AgentRunner(...)` line):

```python
    start_scheduler()
```

**Stop scheduler on shutdown** (inside `lifespan`, after `await _http_client.aclose()`):

```python
    stop_scheduler()
```

The full lifespan function should look like this after the changes:

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    global agent_runner, _http_client

    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required but not set")

    db = SessionLocal()
    seeder.seed(
        db,
        admin_phone=settings.admin_phone_number,
        legacy_group_jid=settings.legacy_group_jid or None,
    )
    db.close()

    anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    _http_client = httpx.AsyncClient()

    tool_registry.register(get_invoice_tools())
    tool_registry.register(get_accounting_tools())
    if settings.notion_api_key:
        from app.tools.notion_tools import get_notion_tools
        tool_registry.register(get_notion_tools(settings.notion_api_key, settings.notion_tasks_database_id))
    else:
        logger.warning("NOTION_API_KEY not set — Notion tools disabled")

    agent_runner = AgentRunner(anthropic_client, tool_registry)
    start_scheduler()

    logger.info("WhatsApp Agent Engine started — %d tools registered", len(tool_registry._tools))
    yield
    await _http_client.aclose()
    stop_scheduler()
    logger.info("Shutting down.")
```

- [ ] **Step 2: Verify full test suite still passes**

```bash
cd orchestrator
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add orchestrator/app/main.py
git commit -m "feat: wire accounting tools and APScheduler into FastAPI lifespan"
```

---

## Task 10: CLI Import Script

**Files:**
- Create: `tools/import_ledger.py`

> **Note:** The `COLUMN_MAP` in this script has commented-out example entries. Before running, fill in the actual column letters to match the source XLSX (to be determined when the file is shared).

- [ ] **Step 1: Create the import script**

Create `tools/import_ledger.py`:

```python
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
    if isinstance(val, (datetime,)):
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
```

- [ ] **Step 2: Verify the script can be imported (syntax check)**

```bash
python -c "import ast; ast.parse(open('tools/import_ledger.py').read()); print('syntax ok')"
```

Expected: `syntax ok`

- [ ] **Step 3: Run full test suite one final time**

```bash
cd orchestrator
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tools/import_ledger.py
git commit -m "feat: one-time XLSX import script for historical ledger data"
```

---

## Post-Deployment Steps (Manual — Not Coded)

After deploying to the server:

1. Add to `.env` on the server:
   ```
   FAMILY_MEMBERS_JSON={"Eran": "972501234567", "Dana": "972509876543"}
   ```
   (replace with actual names and phone numbers)

2. Run Alembic migration:
   ```bash
   docker compose exec orchestrator alembic upgrade head
   ```

3. Restart orchestrator to seed the `family_accounting` blueprint:
   ```bash
   docker compose restart orchestrator
   ```

4. In the family accounting WhatsApp group, run:
   ```
   /bind family_accounting
   ```

5. When you have the historical XLSX, fill in `COLUMN_MAP` in `tools/import_ledger.py` and run:
   ```bash
   python tools/import_ledger.py --file ledger.xlsx --group-jid "GROUP_JID@g.us" --dry-run
   # review output, then:
   python tools/import_ledger.py --file ledger.xlsx --group-jid "GROUP_JID@g.us"
   ```
