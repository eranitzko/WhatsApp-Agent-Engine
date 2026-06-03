# Automation Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a blueprint-agnostic automation engine that lets group admins schedule, recur, and trigger actions (messages or bot actions) via natural language — works for every current and future blueprint.

**Architecture:** A new `AutomationRule` DB table stores per-group rules. Five agent-callable tools (create/confirm/list/pause/cancel) manage rules via natural language. Three new APScheduler jobs (60-min interval) fire due rules via `AutomationExecutor`. Threshold conditions are evaluated by `ThresholdEvaluator`.

**Tech Stack:** SQLAlchemy/Alembic (migration), `croniter` (cron evaluation), APScheduler (existing), httpx (bridge calls), FastAPI/Python.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `orchestrator/requirements.txt` | Add `croniter` |
| Create | `orchestrator/app/db/migrations/versions/010_automation_engine.py` | DB migration |
| Modify | `orchestrator/app/db/models.py` | Add `AutomationRule` ORM model |
| Create | `orchestrator/app/automation/__init__.py` | Package marker |
| Create | `orchestrator/app/automation/evaluators.py` | `ThresholdEvaluator` — one method per metric |
| Create | `orchestrator/app/automation/executor.py` | `AutomationExecutor` — executes a single rule's action |
| Create | `orchestrator/app/tools/automation_tools.py` | Five CRUD tools + `get_automation_tools()` |
| Modify | `orchestrator/app/scheduler.py` | Three new 60-min jobs + executor wiring |
| Modify | `orchestrator/app/main.py` | Register tools, actions, executor at startup |
| Create | `orchestrator/tests/test_automation_tools.py` | Tool CRUD tests |
| Create | `orchestrator/tests/test_automation_evaluators.py` | Metric evaluator tests |
| Create | `orchestrator/tests/test_automation_scheduler.py` | Scheduler job tests |

---

## Task 1: Add croniter dependency

**Files:**
- Modify: `orchestrator/requirements.txt`

- [ ] **Step 1: Add croniter**

Append to `orchestrator/requirements.txt`:

```
# Cron expression evaluation (automation engine)
croniter==3.0.3
```

- [ ] **Step 2: Install**

```bash
cd orchestrator
pip install croniter==3.0.3
```

Expected: installs without error.

- [ ] **Step 3: Verify import**

```bash
python -c "from croniter import croniter; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add orchestrator/requirements.txt
git commit -m "chore: add croniter dependency for automation engine"
```

---

## Task 2: Migration 010 + AutomationRule ORM model

**Files:**
- Create: `orchestrator/app/db/migrations/versions/010_automation_engine.py`
- Modify: `orchestrator/app/db/models.py`
- Create: `orchestrator/tests/test_automation_tools.py` (ORM test only for now)

- [ ] **Step 1: Write the failing ORM test**

Create `orchestrator/tests/test_automation_tools.py`:

```python
import inspect
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from app.db.models import AutomationRule


# ── ORM tests ─────────────────────────────────────────────────────────────────

def test_automation_rule_model_has_required_columns(db):
    rule = AutomationRule(
        group_jid="123@g.us",
        name="Friday debt reminder",
        rule_type="recurring",
        schedule_cron="0 9 * * 5",
        action_type="send_message",
        action_config=json.dumps({"message": "Please settle debts!"}),
    )
    db.add(rule)
    db.commit()
    db.expire_all()
    fetched = db.get(AutomationRule, rule.id)
    assert fetched.name == "Friday debt reminder"
    assert fetched.rule_type == "recurring"
    assert fetched.status == "pending_confirm"
    assert fetched.last_fired_at is None


def test_automation_rule_defaults_status_to_pending_confirm(db):
    rule = AutomationRule(
        group_jid="123@g.us",
        name="test",
        rule_type="inactivity",
        inactivity_hours=48,
        action_type="send_message",
        action_config=json.dumps({"message": "hello"}),
    )
    db.add(rule)
    db.commit()
    db.expire_all()
    assert db.get(AutomationRule, rule.id).status == "pending_confirm"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd orchestrator
python -m pytest tests/test_automation_tools.py::test_automation_rule_model_has_required_columns -v
```

Expected: `ImportError` or `AttributeError` — `AutomationRule` does not exist yet.

- [ ] **Step 3: Add AutomationRule to models.py**

In `orchestrator/app/db/models.py`, add the import for `Integer` if not already present (it is), then append after the `ScheduledMessage` class:

```python
class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id               = Column(String(36), primary_key=True, default=_uuid)
    group_jid        = Column(String, ForeignKey("group_registry.group_jid"), nullable=False)
    name             = Column(String, nullable=False)
    rule_type        = Column(String, nullable=False)   # one_off|recurring|inactivity|threshold|event_trigger
    schedule_cron    = Column(String, nullable=True)    # ISO datetime str for one_off; cron expr for recurring
    inactivity_hours = Column(Integer, nullable=True)
    threshold_config = Column(Text, nullable=True)      # JSON: {"metric": str, "op": str, "value": float}
    action_type      = Column(String, nullable=False)   # send_message|run_agent_action
    action_config    = Column(Text, nullable=False)     # JSON: {"message": str} or {"action": str}
    status           = Column(String, nullable=False, default="pending_confirm")  # pending_confirm|active|paused|done
    last_fired_at    = Column(DateTime(timezone=True), nullable=True)
    created_at       = Column(DateTime(timezone=True), nullable=False,
                              default=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_automation_tools.py::test_automation_rule_model_has_required_columns tests/test_automation_tools.py::test_automation_rule_defaults_status_to_pending_confirm -v
```

Expected: 2 PASSED.

- [ ] **Step 5: Create migration 010**

Create `orchestrator/app/db/migrations/versions/010_automation_engine.py`:

```python
"""Add automation_rules table

Revision ID: 010
Revises: 009
Create Date: 2026-06-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automation_rules",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("group_jid", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("rule_type", sa.String(), nullable=False),
        sa.Column("schedule_cron", sa.String(), nullable=True),
        sa.Column("inactivity_hours", sa.Integer(), nullable=True),
        sa.Column("threshold_config", sa.Text(), nullable=True),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("action_config", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending_confirm"),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["group_jid"], ["group_registry.group_jid"]),
    )


def downgrade() -> None:
    op.drop_table("automation_rules")
```

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass (existing + 2 new).

- [ ] **Step 7: Commit**

```bash
git add orchestrator/app/db/migrations/versions/010_automation_engine.py \
        orchestrator/app/db/models.py \
        orchestrator/tests/test_automation_tools.py
git commit -m "feat: AutomationRule model + migration 010"
```

---

## Task 3: ThresholdEvaluator

**Files:**
- Create: `orchestrator/app/automation/__init__.py`
- Create: `orchestrator/app/automation/evaluators.py`
- Create: `orchestrator/tests/test_automation_evaluators.py`

- [ ] **Step 1: Write the failing tests**

Create `orchestrator/tests/test_automation_evaluators.py`:

```python
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal

import pytest

from app.db.models import Invoice, LedgerEntry, LedgerSettlement
from app.automation.evaluators import ThresholdEvaluator


def _add_invoice(db, group_id, amount_ils, invoice_date=None):
    from app.db.models import Invoice
    inv = Invoice(
        group_id=group_id,
        message_id=f"msg-{amount_ils}-{invoice_date}",
        image_hash=f"hash-{amount_ils}-{invoice_date}",
        amount_ils=Decimal(str(amount_ils)),
        currency_original="ILS",
        invoice_date=invoice_date or date.today(),
    )
    db.add(inv)
    db.commit()


def _add_ledger_entry(db, group_jid, from_phone, to_phone, amount_ils, amount_settled=0):
    entry = LedgerEntry(
        transaction_id="tx-1",
        group_jid=group_jid,
        from_phone=from_phone,
        to_phone=to_phone,
        amount_ils=Decimal(str(amount_ils)),
        amount_settled_ils=Decimal(str(amount_settled)),
        description="test",
        transaction_date=date.today(),
    )
    db.add(entry)
    db.commit()
    return entry


def test_monthly_invoice_total_sums_current_month(db):
    ev = ThresholdEvaluator()
    _add_invoice(db, "123@g.us", 100, date.today())
    _add_invoice(db, "123@g.us", 250, date.today())
    result = ev.evaluate(db, "123@g.us", "monthly_invoice_total")
    assert result == pytest.approx(350.0)


def test_monthly_invoice_total_excludes_other_groups(db):
    ev = ThresholdEvaluator()
    _add_invoice(db, "123@g.us", 100, date.today())
    _add_invoice(db, "999@g.us", 9999, date.today())
    result = ev.evaluate(db, "123@g.us", "monthly_invoice_total")
    assert result == pytest.approx(100.0)


def test_monthly_invoice_total_excludes_previous_months(db):
    ev = ThresholdEvaluator()
    last_month = date.today().replace(day=1) - timedelta(days=1)
    _add_invoice(db, "123@g.us", 500, last_month)
    _add_invoice(db, "123@g.us", 100, date.today())
    result = ev.evaluate(db, "123@g.us", "monthly_invoice_total")
    assert result == pytest.approx(100.0)


def test_invoice_count_this_month(db):
    ev = ThresholdEvaluator()
    _add_invoice(db, "123@g.us", 10, date.today())
    _add_invoice(db, "123@g.us", 20, date.today())
    _add_invoice(db, "123@g.us", 30, date.today())
    result = ev.evaluate(db, "123@g.us", "invoice_count_this_month")
    assert result == 3.0


def test_open_debt_amount_sums_unsettled(db):
    ev = ThresholdEvaluator()
    _add_ledger_entry(db, "123@g.us", "111", "222", amount_ils=500, amount_settled=200)
    _add_ledger_entry(db, "123@g.us", "333", "222", amount_ils=300, amount_settled=0)
    result = ev.evaluate(db, "123@g.us", "open_debt_amount")
    assert result == pytest.approx(600.0)  # (500-200) + (300-0)


def test_open_debt_amount_ignores_fully_settled(db):
    ev = ThresholdEvaluator()
    _add_ledger_entry(db, "123@g.us", "111", "222", amount_ils=100, amount_settled=100)
    result = ev.evaluate(db, "123@g.us", "open_debt_amount")
    assert result == pytest.approx(0.0)


def test_days_since_last_settlement(db):
    ev = ThresholdEvaluator()
    entry = _add_ledger_entry(db, "123@g.us", "111", "222", amount_ils=100, amount_settled=50)
    settlement = LedgerSettlement(
        payment_leg_id=entry.id,
        debt_leg_id=entry.id,
        amount_ils=Decimal("50"),
    )
    # Force created_at to 3 days ago
    settlement.created_at = datetime.now(timezone.utc) - timedelta(days=3)
    db.add(settlement)
    db.commit()
    result = ev.evaluate(db, "123@g.us", "days_since_last_settlement")
    assert 2.9 < result < 3.1


def test_days_since_last_settlement_returns_inf_when_no_settlements(db):
    ev = ThresholdEvaluator()
    result = ev.evaluate(db, "123@g.us", "days_since_last_settlement")
    assert result == float("inf")


def test_unknown_metric_raises_value_error(db):
    ev = ThresholdEvaluator()
    with pytest.raises(ValueError, match="Unknown metric"):
        ev.evaluate(db, "123@g.us", "nonexistent_metric")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_automation_evaluators.py -v
```

Expected: `ImportError` — `app.automation.evaluators` does not exist.

- [ ] **Step 3: Create the automation package**

Create `orchestrator/app/automation/__init__.py` (empty):

```python
```

- [ ] **Step 4: Implement ThresholdEvaluator**

Create `orchestrator/app/automation/evaluators.py`:

```python
"""Threshold metric evaluators for the automation engine."""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
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
        entries = (
            db.query(LedgerEntry)
            .filter(LedgerEntry.group_jid == group_jid)
            .all()
        )
        total = sum(
            float(e.amount_ils - (e.amount_settled_ils or Decimal("0")))
            for e in entries
            if e.amount_ils > (e.amount_settled_ils or Decimal("0"))
        )
        return total

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
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_automation_evaluators.py -v
```

Expected: all 9 tests PASSED.

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add orchestrator/app/automation/__init__.py \
        orchestrator/app/automation/evaluators.py \
        orchestrator/tests/test_automation_evaluators.py
git commit -m "feat: ThresholdEvaluator with 4 metrics for automation engine"
```

---

## Task 4: AutomationExecutor

**Files:**
- Create: `orchestrator/app/automation/executor.py`

- [ ] **Step 1: Write the failing tests**

Append to `orchestrator/tests/test_automation_tools.py`:

```python
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.automation.executor import AutomationExecutor
from app.db.models import AutomationRule


def _make_rule(action_type: str, action_config: dict, rule_type="recurring") -> AutomationRule:
    rule = AutomationRule(
        group_jid="123@g.us",
        name="test rule",
        rule_type=rule_type,
        action_type=action_type,
        action_config=json.dumps(action_config),
    )
    rule.id = "rule-1"
    return rule


@pytest.mark.asyncio
async def test_executor_send_message_posts_to_bridge(db):
    executor = AutomationExecutor()
    rule = _make_rule("send_message", {"message": "hello group"})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.automation.executor.httpx.AsyncClient", return_value=mock_client):
        await executor.execute(rule, db)

    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert call_kwargs.kwargs["json"]["text"] == "hello group"
    assert call_kwargs.kwargs["json"]["jid"] == "123@g.us"


@pytest.mark.asyncio
async def test_executor_send_message_with_mentions(db):
    executor = AutomationExecutor()
    rule = _make_rule("send_message", {"message": "pay up", "mentions": ["972500000001"]})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.automation.executor.httpx.AsyncClient", return_value=mock_client):
        await executor.execute(rule, db)

    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["mentions"] == ["972500000001"]


@pytest.mark.asyncio
async def test_executor_run_agent_action_calls_registered_fn(db):
    called_with = {}

    async def fake_action(group_jid, db, config):
        called_with["group_jid"] = group_jid
        called_with["config"] = config

    executor = AutomationExecutor(actions={"balance_summary": fake_action})
    rule = _make_rule("run_agent_action", {"action": "balance_summary"})
    await executor.execute(rule, db)

    assert called_with["group_jid"] == "123@g.us"
    assert called_with["config"]["action"] == "balance_summary"


@pytest.mark.asyncio
async def test_executor_unknown_action_logs_and_does_not_raise(db):
    executor = AutomationExecutor()
    rule = _make_rule("run_agent_action", {"action": "nonexistent"})
    # Should not raise
    await executor.execute(rule, db)


@pytest.mark.asyncio
async def test_executor_error_in_action_does_not_raise(db):
    async def bad_action(group_jid, db, config):
        raise RuntimeError("boom")

    executor = AutomationExecutor(actions={"bad": bad_action})
    rule = _make_rule("run_agent_action", {"action": "bad"})
    # Should swallow the exception
    await executor.execute(rule, db)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_automation_tools.py::test_executor_send_message_posts_to_bridge -v
```

Expected: `ImportError` — `app.automation.executor` does not exist.

- [ ] **Step 3: Implement AutomationExecutor**

Create `orchestrator/app/automation/executor.py`:

```python
"""AutomationExecutor — fires a single AutomationRule's action."""

from __future__ import annotations

import json
import logging
import os
from typing import Callable, Awaitable, Any

import httpx

logger = logging.getLogger(__name__)

ActionFn = Callable[..., Awaitable[None]]


def _bridge_headers() -> dict:
    secret = os.environ.get("BRIDGE_SECRET", "")
    return {"Authorization": f"Bearer {secret}"} if secret else {}


class AutomationExecutor:
    """Executes a single AutomationRule's action.

    Usage:
        executor = AutomationExecutor(actions={"balance_summary": fn})
        executor.register_action("monthly_invoice_report", fn2)
        await executor.execute(rule, db)
    """

    def __init__(self, actions: dict[str, ActionFn] | None = None):
        self._actions: dict[str, ActionFn] = dict(actions or {})

    def register_action(self, name: str, fn: ActionFn) -> None:
        self._actions[name] = fn

    async def execute(self, rule, db) -> None:
        """Execute the action for a rule. Logs errors but never raises."""
        try:
            config = json.loads(rule.action_config)
            if rule.action_type == "send_message":
                await self._send_message(rule.group_jid, config)
            elif rule.action_type == "run_agent_action":
                action_name = config.get("action", "")
                fn = self._actions.get(action_name)
                if fn is None:
                    logger.error(
                        "Automation action %r not registered (rule %s)", action_name, rule.id
                    )
                    return
                await fn(group_jid=rule.group_jid, db=db, config=config)
            else:
                logger.error("Unknown action_type %r for rule %s", rule.action_type, rule.id)
        except Exception:
            logger.exception("AutomationExecutor.execute failed for rule %s", rule.id)

    async def _send_message(self, group_jid: str, config: dict) -> None:
        from app.config import settings
        payload: dict = {"jid": group_jid, "text": config.get("message", "")}
        mentions = config.get("mentions")
        if mentions:
            payload["mentions"] = mentions
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{settings.bridge_url}/send",
                json=payload,
                headers=_bridge_headers(),
            )
```

- [ ] **Step 4: Run executor tests**

```bash
python -m pytest tests/test_automation_tools.py -k "executor" -v
```

Expected: all 5 executor tests PASSED.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/automation/executor.py orchestrator/tests/test_automation_tools.py
git commit -m "feat: AutomationExecutor — fires send_message and run_agent_action rules"
```

---

## Task 5: Automation Tools

**Files:**
- Create: `orchestrator/app/tools/automation_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `orchestrator/tests/test_automation_tools.py`:

```python
from app.tools.automation_tools import get_automation_tools
from app.db.models import AutomationRule, GroupRegistry, Blueprint


class _CM:
    """Wrap a SQLAlchemy Session as a context manager for patching SessionLocal."""
    def __init__(self, session):
        self._s = session
    def __enter__(self):
        return self._s
    def __exit__(self, *a):
        pass


def _seed_group(db):
    """Seed a GroupRegistry row so FK constraints are satisfied in strict DBs."""
    db.add(Blueprint(
        id="family_accounting",
        display_name="Family Accounting",
        system_prompt="prompt",
        tools_enabled="[]",
    ))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="family_accounting"))
    db.commit()


def test_get_automation_tools_returns_five_tools():
    tools = get_automation_tools()
    assert set(tools.keys()) == {
        "create_automation", "confirm_automation",
        "list_automations", "pause_automation", "cancel_automation",
    }


def test_each_tool_has_schema_and_async_executor():
    tools = get_automation_tools()
    for name, entry in tools.items():
        assert "schema" in entry, f"{name} missing schema"
        assert "executor" in entry, f"{name} missing executor"
        assert entry["schema"]["name"] == name
        assert inspect.iscoroutinefunction(entry["executor"]), f"{name} executor not async"


@pytest.mark.asyncio
async def test_create_automation_saves_pending_rule(db):
    _seed_group(db)
    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["create_automation"]["executor"](
            {
                "name": "Friday debt reminder",
                "rule_type": "recurring",
                "schedule_cron": "0 9 * * 5",
                "action_type": "send_message",
                "action_config": {"message": "Please settle debts!"},
            },
            group_jid="123@g.us",
        )
    assert "Friday debt reminder" in result
    rule = db.query(AutomationRule).filter_by(group_jid="123@g.us").first()
    assert rule is not None
    assert rule.status == "pending_confirm"
    assert rule.schedule_cron == "0 9 * * 5"


@pytest.mark.asyncio
async def test_confirm_automation_activates_rule(db):
    _seed_group(db)
    rule = AutomationRule(
        group_jid="123@g.us",
        name="test",
        rule_type="recurring",
        schedule_cron="0 9 * * 1",
        action_type="send_message",
        action_config=json.dumps({"message": "hi"}),
        status="pending_confirm",
    )
    db.add(rule)
    db.commit()
    rule_id = rule.id

    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["confirm_automation"]["executor"](
            {"id": rule_id},
            group_jid="123@g.us",
        )
    assert "active" in result.lower()
    db.expire_all()
    assert db.get(AutomationRule, rule_id).status == "active"


@pytest.mark.asyncio
async def test_confirm_automation_wrong_group_rejected(db):
    _seed_group(db)
    rule = AutomationRule(
        group_jid="123@g.us",
        name="test",
        rule_type="recurring",
        schedule_cron="0 9 * * 1",
        action_type="send_message",
        action_config=json.dumps({"message": "hi"}),
        status="pending_confirm",
    )
    db.add(rule)
    db.commit()

    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["confirm_automation"]["executor"](
            {"id": rule.id},
            group_jid="999@g.us",  # different group
        )
    assert "different group" in result.lower()


@pytest.mark.asyncio
async def test_list_automations_returns_active_and_paused(db):
    _seed_group(db)
    for name, status in [("rule-a", "active"), ("rule-b", "paused"), ("rule-c", "done")]:
        db.add(AutomationRule(
            group_jid="123@g.us", name=name, rule_type="recurring",
            schedule_cron="0 9 * * 1",
            action_type="send_message", action_config=json.dumps({"message": "x"}),
            status=status,
        ))
    db.commit()

    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["list_automations"]["executor"]({}, group_jid="123@g.us")
    assert "rule-a" in result
    assert "rule-b" in result
    assert "rule-c" not in result  # done rules not shown


@pytest.mark.asyncio
async def test_list_automations_empty_group(db):
    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["list_automations"]["executor"]({}, group_jid="empty@g.us")
    assert "no" in result.lower()


@pytest.mark.asyncio
async def test_pause_automation(db):
    _seed_group(db)
    rule = AutomationRule(
        group_jid="123@g.us", name="test", rule_type="recurring",
        schedule_cron="0 9 * * 1",
        action_type="send_message", action_config=json.dumps({"message": "x"}),
        status="active",
    )
    db.add(rule)
    db.commit()
    rule_id = rule.id

    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["pause_automation"]["executor"]({"id": rule_id}, group_jid="123@g.us")
    assert "paused" in result.lower()
    db.expire_all()
    assert db.get(AutomationRule, rule_id).status == "paused"


@pytest.mark.asyncio
async def test_cancel_automation_deletes_rule(db):
    _seed_group(db)
    rule = AutomationRule(
        group_jid="123@g.us", name="test", rule_type="recurring",
        schedule_cron="0 9 * * 1",
        action_type="send_message", action_config=json.dumps({"message": "x"}),
        status="active",
    )
    db.add(rule)
    db.commit()
    rule_id = rule.id

    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["cancel_automation"]["executor"]({"id": rule_id}, group_jid="123@g.us")
    assert "deleted" in result.lower()
    db.expire_all()
    assert db.get(AutomationRule, rule_id) is None


@pytest.mark.asyncio
async def test_create_automation_invalid_rule_type(db):
    tools = get_automation_tools()
    with patch("app.tools.automation_tools.SessionLocal", return_value=_CM(db)):
        result = await tools["create_automation"]["executor"](
            {
                "name": "bad",
                "rule_type": "nonsense",
                "action_type": "send_message",
                "action_config": {"message": "hi"},
            },
            group_jid="123@g.us",
        )
    assert "invalid" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_automation_tools.py::test_get_automation_tools_returns_five_tools -v
```

Expected: `ImportError` — `app.tools.automation_tools` does not exist.

- [ ] **Step 3: Implement automation_tools.py**

Create `orchestrator/app/tools/automation_tools.py`:

```python
"""Automation engine CRUD tools for ToolRegistry.

Five tools: create_automation, confirm_automation, list_automations,
pause_automation, cancel_automation.

group_jid is always taken from **ctx (injected by AgentRunner), never from params.
"""

from __future__ import annotations

import json
import logging

from app.db.session import SessionLocal
from app.db.models import AutomationRule

logger = logging.getLogger(__name__)

_VALID_RULE_TYPES = {"one_off", "recurring", "inactivity", "threshold", "event_trigger"}
_VALID_ACTION_TYPES = {"send_message", "run_agent_action"}
_VALID_OPS = {">", "<", ">=", "<="}
_VALID_METRICS = {
    "monthly_invoice_total",
    "invoice_count_this_month",
    "open_debt_amount",
    "days_since_last_settlement",
}


def _describe_rule(rule: AutomationRule) -> str:
    """Generate a plain-English summary of a rule."""
    parts = [f"*{rule.name}*"]
    if rule.rule_type == "recurring" and rule.schedule_cron:
        parts.append(f"Schedule: {rule.schedule_cron}")
    elif rule.rule_type == "one_off" and rule.schedule_cron:
        parts.append(f"Fires once at: {rule.schedule_cron}")
    elif rule.rule_type == "inactivity" and rule.inactivity_hours:
        parts.append(f"After {rule.inactivity_hours}h silence")
    elif rule.rule_type == "threshold" and rule.threshold_config:
        tc = json.loads(rule.threshold_config)
        parts.append(f"When {tc['metric']} {tc['op']} {tc['value']}")
    config = json.loads(rule.action_config)
    if rule.action_type == "send_message":
        preview = config.get("message", "")[:60]
        parts.append(f"Sends: \"{preview}\"")
    else:
        parts.append(f"Runs: {config.get('action', '?')}")
    return " | ".join(parts)


async def _exec_create_automation(params: dict, **ctx) -> str:
    group_jid: str = ctx.get("group_jid", "")
    rule_type = params.get("rule_type", "")
    if rule_type not in _VALID_RULE_TYPES:
        return f"Invalid rule_type '{rule_type}'. Must be one of: {', '.join(sorted(_VALID_RULE_TYPES))}"
    action_type = params.get("action_type", "")
    if action_type not in _VALID_ACTION_TYPES:
        return f"Invalid action_type '{action_type}'. Must be one of: {', '.join(sorted(_VALID_ACTION_TYPES))}"

    threshold_raw = params.get("threshold_config")
    threshold_json: str | None = None
    if threshold_raw and isinstance(threshold_raw, dict):
        if threshold_raw.get("metric") not in _VALID_METRICS:
            return (
                f"Unknown metric '{threshold_raw.get('metric')}'. "
                f"Valid metrics: {', '.join(sorted(_VALID_METRICS))}"
            )
        if threshold_raw.get("op") not in _VALID_OPS:
            return f"Invalid operator '{threshold_raw.get('op')}'. Must be one of: >, <, >=, <="
        threshold_json = json.dumps(threshold_raw)

    action_config_json = json.dumps(params.get("action_config", {}))

    rule = AutomationRule(
        group_jid=group_jid,
        name=params.get("name", "Unnamed automation"),
        rule_type=rule_type,
        schedule_cron=params.get("schedule_cron"),
        inactivity_hours=params.get("inactivity_hours"),
        threshold_config=threshold_json,
        action_type=action_type,
        action_config=action_config_json,
        status="pending_confirm",
    )
    with SessionLocal() as db:
        db.add(rule)
        db.commit()
        rule_id = rule.id
        description = _describe_rule(rule)

    return (
        f"Here's what I'll set up:\n{description}\n\n"
        f"Shall I activate this automation? Reply yes and I'll confirm it.\n"
        f"Rule ID: {rule_id}"
    )


async def _exec_confirm_automation(params: dict, **ctx) -> str:
    rule_id = params.get("id", "")
    group_jid: str = ctx.get("group_jid", "")
    with SessionLocal() as db:
        rule = db.get(AutomationRule, rule_id)
        if rule is None:
            return f"No automation found with ID '{rule_id}'."
        if rule.group_jid != group_jid:
            return "That automation belongs to a different group."
        rule.status = "active"
        db.commit()
        name = rule.name
    return f"Automation '{name}' is now active."


async def _exec_list_automations(params: dict, **ctx) -> str:
    group_jid: str = ctx.get("group_jid", "")
    with SessionLocal() as db:
        rules = (
            db.query(AutomationRule)
            .filter(
                AutomationRule.group_jid == group_jid,
                AutomationRule.status.in_(["active", "paused"]),
            )
            .order_by(AutomationRule.created_at)
            .all()
        )
        if not rules:
            return "No active automations for this group."
        lines = [
            f"{i + 1}. [{r.status.upper()}] {_describe_rule(r)} (ID: {r.id})"
            for i, r in enumerate(rules)
        ]
    return "Automations:\n" + "\n".join(lines)


async def _exec_pause_automation(params: dict, **ctx) -> str:
    rule_id = params.get("id", "")
    group_jid: str = ctx.get("group_jid", "")
    with SessionLocal() as db:
        rule = db.get(AutomationRule, rule_id)
        if rule is None:
            return f"No automation found with ID '{rule_id}'."
        if rule.group_jid != group_jid:
            return "That automation belongs to a different group."
        rule.status = "paused"
        db.commit()
        name = rule.name
    return f"Automation '{name}' paused."


async def _exec_cancel_automation(params: dict, **ctx) -> str:
    rule_id = params.get("id", "")
    group_jid: str = ctx.get("group_jid", "")
    with SessionLocal() as db:
        rule = db.get(AutomationRule, rule_id)
        if rule is None:
            return f"No automation found with ID '{rule_id}'."
        if rule.group_jid != group_jid:
            return "That automation belongs to a different group."
        name = rule.name
        db.delete(rule)
        db.commit()
    return f"Automation '{name}' deleted."


_SCHEMAS: dict[str, dict] = {
    "create_automation": {
        "name": "create_automation",
        "description": (
            "Create a new automation rule for this group. The rule is saved as pending_confirm "
            "and must be activated with confirm_automation after user confirms. "
            "For one_off: schedule_cron is an ISO 8601 datetime string (e.g. '2026-06-15T09:00:00+00:00'). "
            "For recurring: schedule_cron is a cron expression (e.g. '0 9 * * 5' = every Friday 9am). "
            "For inactivity: supply inactivity_hours. "
            "For threshold: supply threshold_config."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human label, e.g. 'Friday debt reminder'"},
                "rule_type": {
                    "type": "string",
                    "enum": ["one_off", "recurring", "inactivity", "threshold"],
                    "description": "Trigger type",
                },
                "schedule_cron": {
                    "type": "string",
                    "description": (
                        "Cron expression for recurring (e.g. '0 9 * * 5'), "
                        "or ISO 8601 datetime for one_off (e.g. '2026-06-15T09:00:00+00:00')"
                    ),
                },
                "inactivity_hours": {
                    "type": "integer",
                    "description": "Hours of group silence before firing (inactivity rules only)",
                },
                "threshold_config": {
                    "type": "object",
                    "properties": {
                        "metric": {
                            "type": "string",
                            "enum": [
                                "monthly_invoice_total",
                                "invoice_count_this_month",
                                "open_debt_amount",
                                "days_since_last_settlement",
                            ],
                        },
                        "op": {"type": "string", "enum": [">", "<", ">=", "<="]},
                        "value": {"type": "number"},
                    },
                    "required": ["metric", "op", "value"],
                    "description": "Threshold condition (threshold rules only)",
                },
                "action_type": {
                    "type": "string",
                    "enum": ["send_message", "run_agent_action"],
                    "description": "What to do when the rule fires",
                },
                "action_config": {
                    "type": "object",
                    "description": (
                        "For send_message: {\"message\": \"...\", \"mentions\": [\"972500000001\"]} "
                        "For run_agent_action: {\"action\": \"balance_summary\"} or "
                        "{\"action\": \"monthly_invoice_report\"}"
                    ),
                },
            },
            "required": ["name", "rule_type", "action_type", "action_config"],
        },
    },
    "confirm_automation": {
        "name": "confirm_automation",
        "description": "Activate a pending automation rule after the user confirms.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The rule ID returned by create_automation"},
            },
            "required": ["id"],
        },
    },
    "list_automations": {
        "name": "list_automations",
        "description": "List all active and paused automation rules for this group.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "pause_automation": {
        "name": "pause_automation",
        "description": "Pause an active automation rule. It will not fire while paused.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The automation rule ID"},
            },
            "required": ["id"],
        },
    },
    "cancel_automation": {
        "name": "cancel_automation",
        "description": "Permanently delete an automation rule.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The automation rule ID"},
            },
            "required": ["id"],
        },
    },
}


def get_automation_tools() -> dict[str, dict]:
    """Return all automation tools for registration in ToolRegistry."""
    return {
        "create_automation": {
            "schema": _SCHEMAS["create_automation"],
            "executor": _exec_create_automation,
        },
        "confirm_automation": {
            "schema": _SCHEMAS["confirm_automation"],
            "executor": _exec_confirm_automation,
        },
        "list_automations": {
            "schema": _SCHEMAS["list_automations"],
            "executor": _exec_list_automations,
        },
        "pause_automation": {
            "schema": _SCHEMAS["pause_automation"],
            "executor": _exec_pause_automation,
        },
        "cancel_automation": {
            "schema": _SCHEMAS["cancel_automation"],
            "executor": _exec_cancel_automation,
        },
    }
```

- [ ] **Step 4: Run all automation tool tests**

```bash
python -m pytest tests/test_automation_tools.py -v
```

Expected: all tests PASSED.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/tools/automation_tools.py orchestrator/tests/test_automation_tools.py
git commit -m "feat: automation CRUD tools (create/confirm/list/pause/cancel)"
```

---

## Task 6: Scheduler Extension

**Files:**
- Modify: `orchestrator/app/scheduler.py`
- Create: `orchestrator/tests/test_automation_scheduler.py`

- [ ] **Step 1: Write the failing tests**

Create `orchestrator/tests/test_automation_scheduler.py`:

```python
"""Tests for the three automation scheduler jobs."""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import AutomationRule, ConversationHistory, GroupRegistry, Blueprint
from app.automation.executor import AutomationExecutor


class _CM:
    def __init__(self, session):
        self._s = session
    def __enter__(self):
        return self._s
    def __exit__(self, *a):
        pass


def _seed_group(db, group_jid="123@g.us"):
    db.add(Blueprint(
        id="family_accounting", display_name="FA",
        system_prompt="p", tools_enabled="[]",
    ))
    db.add(GroupRegistry(group_jid=group_jid, blueprint_id="family_accounting"))
    db.commit()


def _make_rule(db, rule_type, status="active", **kwargs):
    rule = AutomationRule(
        group_jid="123@g.us",
        name="test rule",
        rule_type=rule_type,
        action_type="send_message",
        action_config=json.dumps({"message": "hello"}),
        status=status,
        **kwargs,
    )
    db.add(rule)
    db.commit()
    return rule


def _mock_executor():
    executor = MagicMock(spec=AutomationExecutor)
    executor.execute = AsyncMock()
    return executor


# ── Recurring rules ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recurring_rule_fires_when_cron_due(db):
    _seed_group(db)
    # Cron that fired in the last hour: every minute — guaranteed to match
    rule = _make_rule(db, "recurring", schedule_cron="* * * * *")
    executor = _mock_executor()

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor):
        from app.scheduler import _fire_recurring_rules
        await _fire_recurring_rules()

    executor.execute.assert_called_once()
    db.expire_all()
    assert db.get(AutomationRule, rule.id).last_fired_at is not None


@pytest.mark.asyncio
async def test_recurring_rule_does_not_fire_when_not_due(db):
    _seed_group(db)
    # Cron that will not match in the last hour: Feb 30 (impossible date)
    rule = _make_rule(db, "recurring", schedule_cron="0 0 30 2 *")
    executor = _mock_executor()

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor):
        from app.scheduler import _fire_recurring_rules
        await _fire_recurring_rules()

    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_one_off_rule_fires_and_is_marked_done(db):
    _seed_group(db)
    fire_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    rule = _make_rule(db, "one_off", schedule_cron=fire_at.isoformat())
    executor = _mock_executor()

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor):
        from app.scheduler import _fire_recurring_rules
        await _fire_recurring_rules()

    executor.execute.assert_called_once()
    db.expire_all()
    assert db.get(AutomationRule, rule.id).status == "done"


@pytest.mark.asyncio
async def test_one_off_rule_does_not_fire_when_future(db):
    _seed_group(db)
    fire_at = datetime.now(timezone.utc) + timedelta(hours=2)
    rule = _make_rule(db, "one_off", schedule_cron=fire_at.isoformat())
    executor = _mock_executor()

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor):
        from app.scheduler import _fire_recurring_rules
        await _fire_recurring_rules()

    executor.execute.assert_not_called()
    db.expire_all()
    assert db.get(AutomationRule, rule.id).status == "active"


@pytest.mark.asyncio
async def test_paused_rule_is_not_fired(db):
    _seed_group(db)
    rule = _make_rule(db, "recurring", status="paused", schedule_cron="* * * * *")
    executor = _mock_executor()

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor):
        from app.scheduler import _fire_recurring_rules
        await _fire_recurring_rules()

    executor.execute.assert_not_called()


# ── Inactivity rules ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_inactivity_rule_fires_after_long_silence(db):
    _seed_group(db)
    rule = _make_rule(db, "inactivity", inactivity_hours=24)
    # last activity was 30 hours ago
    old_active = datetime.now(timezone.utc) - timedelta(hours=30)
    db.add(ConversationHistory(
        group_id="123@g.us",
        messages_json="[]",
        last_active=old_active,
    ))
    db.commit()
    executor = _mock_executor()

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor):
        from app.scheduler import _check_inactivity
        await _check_inactivity()

    executor.execute.assert_called_once()
    db.expire_all()
    assert db.get(AutomationRule, rule.id).last_fired_at is not None


@pytest.mark.asyncio
async def test_inactivity_rule_does_not_fire_when_recently_active(db):
    _seed_group(db)
    rule = _make_rule(db, "inactivity", inactivity_hours=24)
    # last activity was 1 hour ago — not yet due
    db.add(ConversationHistory(
        group_id="123@g.us",
        messages_json="[]",
        last_active=datetime.now(timezone.utc) - timedelta(hours=1),
    ))
    db.commit()
    executor = _mock_executor()

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor):
        from app.scheduler import _check_inactivity
        await _check_inactivity()

    executor.execute.assert_not_called()


# ── Threshold rules ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_threshold_rule_fires_when_condition_met(db):
    _seed_group(db)
    rule = _make_rule(
        db, "threshold",
        threshold_config=json.dumps({"metric": "open_debt_amount", "op": ">", "value": 100}),
    )
    executor = _mock_executor()

    fake_evaluator = MagicMock()
    fake_evaluator.evaluate = MagicMock(return_value=500.0)

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor), \
         patch("app.scheduler.ThresholdEvaluator", return_value=fake_evaluator):
        from app.scheduler import _evaluate_thresholds
        await _evaluate_thresholds()

    executor.execute.assert_called_once()


@pytest.mark.asyncio
async def test_threshold_rule_does_not_fire_when_condition_not_met(db):
    _seed_group(db)
    rule = _make_rule(
        db, "threshold",
        threshold_config=json.dumps({"metric": "open_debt_amount", "op": ">", "value": 1000}),
    )
    executor = _mock_executor()

    fake_evaluator = MagicMock()
    fake_evaluator.evaluate = MagicMock(return_value=50.0)

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor), \
         patch("app.scheduler.ThresholdEvaluator", return_value=fake_evaluator):
        from app.scheduler import _evaluate_thresholds
        await _evaluate_thresholds()

    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_threshold_rule_skips_if_fired_within_24h(db):
    _seed_group(db)
    rule = _make_rule(
        db, "threshold",
        threshold_config=json.dumps({"metric": "open_debt_amount", "op": ">", "value": 100}),
        last_fired_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    executor = _mock_executor()

    fake_evaluator = MagicMock()
    fake_evaluator.evaluate = MagicMock(return_value=500.0)

    with patch("app.scheduler.SessionLocal", return_value=_CM(db)), \
         patch("app.scheduler._automation_executor", executor), \
         patch("app.scheduler.ThresholdEvaluator", return_value=fake_evaluator):
        from app.scheduler import _evaluate_thresholds
        await _evaluate_thresholds()

    executor.execute.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_automation_scheduler.py::test_recurring_rule_fires_when_cron_due -v
```

Expected: `ImportError` or `AttributeError` — `_fire_recurring_rules` / `_automation_executor` not in scheduler.

- [ ] **Step 3: Extend scheduler.py**

Replace the full contents of `orchestrator/app/scheduler.py` with:

```python
"""APScheduler — dispatches due ScheduledMessages, fires automation rules,
and expires stale multi-confirmations."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings

if TYPE_CHECKING:
    from app.automation.executor import AutomationExecutor

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()
_BRIDGE_SECRET: str = os.environ.get("BRIDGE_SECRET", "")

# Set at startup by main.py via set_automation_executor()
_automation_executor: "AutomationExecutor | None" = None


def set_automation_executor(executor: "AutomationExecutor") -> None:
    global _automation_executor
    _automation_executor = executor


def _bridge_headers() -> dict:
    return {"Authorization": f"Bearer {_BRIDGE_SECRET}"} if _BRIDGE_SECRET else {}


# ── Existing jobs ─────────────────────────────────────────────────────────────

async def _expire_multi_confirmations() -> None:
    """Cancel timed-out multi-party confirmations and notify their groups."""
    from app.agent.multi_confirmation import multi_confirmation_store
    expired = multi_confirmation_store.drain_expired()
    for mc in expired:
        timed_out_phones = [p for p, done in mc.awaiting.items() if not done]
        timed_out_str = ", ".join(f"@{p}" for p in timed_out_phones)
        msg = (
            f"Transaction cancelled — {timed_out_str} did not confirm in time.\n"
            f"{mc.description}"
        )
        mentions = [f"{p}@s.whatsapp.net" for p in timed_out_phones]
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{settings.bridge_url}/send",
                    json={"jid": mc.group_jid, "text": msg, "mentions": mentions},
                    headers=_bridge_headers(),
                )
            logger.info("Sent expiry notice for mc %s to %s", mc.id, mc.group_jid)
        except Exception:
            logger.exception("Failed to send expiry notice for mc %s to %s", mc.id, mc.group_jid)


async def _dispatch_due_messages() -> None:
    """Query due scheduled messages, send each via bridge, mark sent."""
    from app.db.models import ScheduledMessage
    from app.db.session import SessionLocal
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
                        headers=_bridge_headers(),
                    )
                msg.sent = True
                logger.info("Dispatched scheduled message %s to %s", msg.id, msg.group_jid)
            except Exception:
                logger.exception("Failed to dispatch scheduled message %s", msg.id)
        db.commit()


# ── Automation jobs ───────────────────────────────────────────────────────────

async def _fire_recurring_rules() -> None:
    """Fire recurring and one_off automation rules that are due."""
    from app.db.models import AutomationRule
    from app.db.session import SessionLocal
    from croniter import croniter

    if _automation_executor is None:
        logger.warning("_fire_recurring_rules: no automation executor configured")
        return

    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        rules = (
            db.query(AutomationRule)
            .filter(
                AutomationRule.status == "active",
                AutomationRule.rule_type.in_(["recurring", "one_off"]),
            )
            .all()
        )
        for rule in rules:
            if not rule.schedule_cron:
                continue
            try:
                if rule.rule_type == "one_off":
                    fire_at = datetime.fromisoformat(rule.schedule_cron)
                    if fire_at.tzinfo is None:
                        fire_at = fire_at.replace(tzinfo=timezone.utc)
                    if fire_at > now:
                        continue
                else:  # recurring
                    base = now - timedelta(hours=1)
                    itr = croniter(rule.schedule_cron, base)
                    next_dt = itr.get_next(datetime)
                    if next_dt > now:
                        continue
            except Exception:
                logger.exception(
                    "Invalid schedule_cron for rule %s: %r", rule.id, rule.schedule_cron
                )
                continue

            rule.last_fired_at = now
            if rule.rule_type == "one_off":
                rule.status = "done"
            db.commit()

            await _automation_executor.execute(rule, db)
            logger.info("Fired automation rule %s (%s)", rule.id, rule.name)


async def _check_inactivity() -> None:
    """Fire inactivity rules for groups that have been silent long enough."""
    from app.db.models import AutomationRule, ConversationHistory
    from app.db.session import SessionLocal

    if _automation_executor is None:
        logger.warning("_check_inactivity: no automation executor configured")
        return

    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        rules = (
            db.query(AutomationRule)
            .filter(
                AutomationRule.status == "active",
                AutomationRule.rule_type == "inactivity",
            )
            .all()
        )
        for rule in rules:
            if not rule.inactivity_hours:
                continue
            history = db.query(ConversationHistory).filter_by(group_id=rule.group_jid).first()
            if history is None:
                continue
            last_active = history.last_active
            if last_active.tzinfo is None:
                last_active = last_active.replace(tzinfo=timezone.utc)
            silence_hours = (now - last_active).total_seconds() / 3600
            if silence_hours < rule.inactivity_hours:
                continue

            rule.last_fired_at = now
            db.commit()
            await _automation_executor.execute(rule, db)
            logger.info(
                "Fired inactivity rule %s for %s (%.1fh silence)",
                rule.id, rule.group_jid, silence_hours,
            )


async def _evaluate_thresholds() -> None:
    """Fire threshold rules whose metric condition is met (max once per 24h)."""
    from app.db.models import AutomationRule
    from app.db.session import SessionLocal
    from app.automation.evaluators import ThresholdEvaluator
    import json as _json

    if _automation_executor is None:
        logger.warning("_evaluate_thresholds: no automation executor configured")
        return

    now = datetime.now(timezone.utc)
    evaluator = ThresholdEvaluator()
    _OPS = {
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
    }

    with SessionLocal() as db:
        rules = (
            db.query(AutomationRule)
            .filter(
                AutomationRule.status == "active",
                AutomationRule.rule_type == "threshold",
            )
            .all()
        )
        for rule in rules:
            # Skip if already fired within the last 24 hours
            if rule.last_fired_at:
                hours_since = (now - rule.last_fired_at).total_seconds() / 3600
                if hours_since < 24:
                    continue
            if not rule.threshold_config:
                continue
            try:
                tc = _json.loads(rule.threshold_config)
                metric = tc["metric"]
                op = tc["op"]
                target = float(tc["value"])
                actual = evaluator.evaluate(db, rule.group_jid, metric)
                op_fn = _OPS.get(op)
                if op_fn is None or not op_fn(actual, target):
                    continue
            except Exception:
                logger.exception("Failed to evaluate threshold for rule %s", rule.id)
                continue

            rule.last_fired_at = now
            db.commit()
            await _automation_executor.execute(rule, db)
            logger.info(
                "Fired threshold rule %s (%s %s %s, actual=%.2f)",
                rule.id, metric, op, target, actual,
            )


# ── Scheduler lifecycle ───────────────────────────────────────────────────────

def start_scheduler() -> None:
    _scheduler.add_job(_dispatch_due_messages, "interval", seconds=60, id="dispatch_messages")
    _scheduler.add_job(
        _expire_multi_confirmations, "interval", seconds=60, id="expire_multi_confirmations"
    )
    _scheduler.add_job(
        _fire_recurring_rules, "interval", minutes=60, id="fire_recurring_rules"
    )
    _scheduler.add_job(
        _check_inactivity, "interval", minutes=60, id="check_inactivity"
    )
    _scheduler.add_job(
        _evaluate_thresholds, "interval", minutes=60, id="evaluate_thresholds"
    )
    _scheduler.start()
    logger.info("APScheduler started — 2 × 60s jobs, 3 × 60min automation jobs")


def stop_scheduler() -> None:
    _scheduler.shutdown(wait=False)
    logger.info("APScheduler stopped")
```

- [ ] **Step 4: Run the scheduler tests**

```bash
python -m pytest tests/test_automation_scheduler.py -v
```

Expected: all 11 tests PASSED.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass (including existing scheduler tests).

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/scheduler.py orchestrator/tests/test_automation_scheduler.py
git commit -m "feat: three automation scheduler jobs (recurring, inactivity, threshold)"
```

---

## Task 7: Wire up in main.py

**Files:**
- Modify: `orchestrator/app/main.py`

No new unit tests — integration is covered by the scheduler and tool tests. Manual smoke-test steps are provided.

- [ ] **Step 1: Add imports to main.py**

At the top of `orchestrator/app/main.py`, after the existing imports, add:

```python
from app.tools.automation_tools import get_automation_tools
from app.automation.executor import AutomationExecutor
from app.scheduler import start_scheduler, stop_scheduler, set_automation_executor
```

Replace the existing `from app.scheduler import start_scheduler, stop_scheduler` line with the three-import version above.

- [ ] **Step 2: Define the two automation action functions**

In `orchestrator/app/main.py`, add these two functions before the `lifespan` context manager (after the `_verify_webhook_auth` function):

```python
async def _balance_summary_action(group_jid: str, db, config: dict) -> None:
    """Automation action: send an open-debt summary to the group."""
    from app.db.models import LedgerEntry
    from decimal import Decimal
    entries = db.query(LedgerEntry).filter(LedgerEntry.group_jid == group_jid).all()
    open_debts = [
        e for e in entries
        if (e.amount_ils - (e.amount_settled_ils or Decimal("0"))) > 0
    ]
    if not open_debts:
        await _send(group_jid, "Balance summary: No open debts — all settled! ✅")
        return
    lines = [
        f"• {e.from_phone} → {e.to_phone}: ₪{float(e.amount_ils - (e.amount_settled_ils or Decimal('0'))):.2f}"
        f" ({e.description})"
        for e in open_debts
    ]
    await _send(group_jid, "Balance summary:\n" + "\n".join(lines))


async def _monthly_invoice_report_action(group_jid: str, db, config: dict) -> None:
    """Automation action: send a monthly invoice summary to the group."""
    from app.db.models import Invoice
    from datetime import date
    today = date.today()
    first_of_month = today.replace(day=1)
    invoices = (
        db.query(Invoice)
        .filter(Invoice.group_id == group_jid, Invoice.invoice_date >= first_of_month)
        .all()
    )
    month_label = today.strftime("%B %Y")
    if not invoices:
        await _send(group_jid, f"Monthly report ({month_label}): No invoices this month.")
        return
    total = sum(float(inv.amount_ils or 0) for inv in invoices)
    lines = [
        f"• {inv.vendor or 'Unknown'}: ₪{float(inv.amount_ils or 0):.2f}"
        for inv in invoices
    ]
    await _send(
        group_jid,
        f"Monthly report ({month_label}) — {len(invoices)} invoices, ₪{total:.2f} total:\n"
        + "\n".join(lines),
    )
```

- [ ] **Step 3: Register tools and executor in lifespan**

In the `lifespan` function, after the existing `tool_registry.register(get_invoice_tools())` block and before `start_scheduler()`, add:

```python
    tool_registry.register(get_automation_tools())

    automation_executor = AutomationExecutor()
    automation_executor.register_action("balance_summary", _balance_summary_action)
    automation_executor.register_action("monthly_invoice_report", _monthly_invoice_report_action)
    set_automation_executor(automation_executor)
```

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/main.py
git commit -m "feat: wire automation tools + executor into main.py lifespan"
```

---

## Task 8: Push to GitHub

- [ ] **Step 1: Push**

```bash
git push origin HEAD
```

Expected: branch pushed to remote.

---

## Self-Review Checklist

### Spec coverage

| Spec requirement | Task |
|---|---|
| `AutomationRule` table with all columns | Task 2 |
| Migration 010 | Task 2 |
| `create_automation` tool | Task 5 |
| `confirm_automation` tool | Task 5 |
| `list_automations` tool | Task 5 |
| `pause_automation` tool | Task 5 |
| `cancel_automation` tool | Task 5 |
| `ThresholdEvaluator` with 4 metrics | Task 3 |
| `AutomationExecutor` (send_message + run_agent_action) | Task 4 |
| `_fire_recurring_rules` scheduler job (60 min) | Task 6 |
| `_check_inactivity` scheduler job (60 min) | Task 6 |
| `_evaluate_thresholds` scheduler job (60 min) | Task 6 |
| `balance_summary` action | Task 7 |
| `monthly_invoice_report` action | Task 7 |
| Tools registered in main.py | Task 7 |
| `croniter` dependency | Task 1 |
| 24h guard for threshold rules | Task 6 |
| `one_off` → `status="done"` after firing | Task 6 |
| `event_trigger` — placeholder only | ✅ (rule_type value accepted, never fired) |

All requirements covered. ✅
