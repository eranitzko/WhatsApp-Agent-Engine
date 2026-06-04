# Personal Accounting Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the family_accounting blueprint from a shared group model to per-user private groups, with an `AccountService` handling cross-group routing, 1st/2nd-party transaction classification, split bills, and sys-admin registration approval.

**Architecture:** A new `AccountService` class centralises all cross-group coordination (notifications, confirmation lifecycle, split-bill management). Existing FIFO/FX/ledger data layers are untouched. Tools are updated to delegate routing decisions to `AccountService` rather than writing to the DB directly. A new migration adds four schema changes, and two new scheduler jobs handle confirmation timeouts.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (sync), Alembic, APScheduler, httpx, pytest, pytest-asyncio

---

## File Map

| Action | Path |
|---|---|
| Create | `orchestrator/app/db/migrations/versions/011_personal_accounting.py` |
| Modify | `orchestrator/app/db/models.py` |
| Create | `orchestrator/app/accounting/__init__.py` |
| Create | `orchestrator/app/accounting/account_service.py` |
| Create | `orchestrator/app/accounting/group_registration.py` |
| Modify | `orchestrator/app/tools/accounting_tools.py` |
| Create | `orchestrator/app/tools/split_tools.py` |
| Modify | `orchestrator/app/scheduler.py` |
| Modify | `orchestrator/app/main.py` |
| Modify | `orchestrator/app/prompts/family_accounting.py` |
| Modify | `orchestrator/app/admin/api.py` |
| Modify | `orchestrator/app/static/admin/index.html` |
| Modify | `orchestrator/app/static/admin/app.js` |
| Create | `orchestrator/tests/test_account_service.py` |
| Create | `orchestrator/tests/test_group_registration.py` |
| Create | `orchestrator/tests/test_split_tools.py` |

---

## Task 1: Migration 011 — schema changes

**Files:**
- Create: `orchestrator/app/db/migrations/versions/011_personal_accounting.py`

- [ ] **Step 1: Write the migration**

```python
"""Add personal accounting tables and group_type column

Revision ID: 011
Revises: 010
Create Date: 2026-06-04
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # group_type on existing group_registry
    op.add_column(
        "group_registry",
        sa.Column("group_type", sa.String(), nullable=True, server_default="personal"),
    )

    # display_name on existing user_profiles
    op.add_column(
        "user_profiles",
        sa.Column("display_name", sa.String(), nullable=True),
    )

    # user_accounts: maps phone → group_jid with role
    op.create_table(
        "user_accounts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("group_jid", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="owner"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["group_jid"], ["group_registry.group_jid"]),
        sa.UniqueConstraint("phone", "group_jid", name="uq_user_accounts_phone_group"),
    )
    op.create_index("ix_user_accounts_phone", "user_accounts", ["phone"])
    op.create_index("ix_user_accounts_group_jid", "user_accounts", ["group_jid"])

    # split_transactions: parent record for multi-party splits
    op.create_table(
        "split_transactions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("reporter_group_jid", sa.String(), nullable=False),
        sa.Column("reporter_phone", sa.String(), nullable=False),
        sa.Column("payer_phone", sa.String(), nullable=False),
        sa.Column("total_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    # cross_group_confirmations: persistent 2nd-party and split confirmations
    op.create_table(
        "cross_group_confirmations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("split_transaction_id", sa.String(36), nullable=True),
        sa.Column("initiator_phone", sa.String(), nullable=False),
        sa.Column("initiator_group_jid", sa.String(), nullable=False),
        sa.Column("target_phone", sa.String(), nullable=False),
        sa.Column("target_group_jid", sa.String(), nullable=False),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("action_payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["split_transaction_id"], ["split_transactions.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_cgc_target_phone_status",
        "cross_group_confirmations",
        ["target_phone", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_cgc_target_phone_status", table_name="cross_group_confirmations")
    op.drop_table("cross_group_confirmations")
    op.drop_table("split_transactions")
    op.drop_index("ix_user_accounts_group_jid", table_name="user_accounts")
    op.drop_index("ix_user_accounts_phone", table_name="user_accounts")
    op.drop_table("user_accounts")
    op.drop_column("user_profiles", "display_name")
    op.drop_column("group_registry", "group_type")
```

- [ ] **Step 2: Run migration**

```bash
cd orchestrator
alembic upgrade head
```

Expected: migration 011 listed as applied, no errors.

- [ ] **Step 3: Commit**

```bash
git add orchestrator/app/db/migrations/versions/011_personal_accounting.py
git commit -m "feat: migration 011 — personal accounting schema (user_accounts, cross_group_confirmations, split_transactions, group_type)"
```

---

## Task 2: ORM models

**Files:**
- Modify: `orchestrator/app/db/models.py`

- [ ] **Step 1: Write failing test**

Create `orchestrator/tests/test_personal_accounting_models.py`:

```python
from app.db.models import UserAccount, CrossGroupConfirmation, SplitTransaction, GroupRegistry

def test_user_account_has_expected_columns():
    cols = {c.key for c in UserAccount.__table__.columns}
    assert {"id", "phone", "group_jid", "role", "created_at"} <= cols

def test_cross_group_confirmation_has_expected_columns():
    cols = {c.key for c in CrossGroupConfirmation.__table__.columns}
    assert {
        "id", "split_transaction_id", "initiator_phone", "initiator_group_jid",
        "target_phone", "target_group_jid", "action_type", "action_payload",
        "status", "expires_at", "created_at",
    } <= cols

def test_split_transaction_has_expected_columns():
    cols = {c.key for c in SplitTransaction.__table__.columns}
    assert {
        "id", "reporter_group_jid", "reporter_phone", "payer_phone",
        "total_amount", "description", "status", "created_at",
    } <= cols

def test_group_registry_has_group_type():
    cols = {c.key for c in GroupRegistry.__table__.columns}
    assert "group_type" in cols
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd orchestrator
pytest tests/test_personal_accounting_models.py -v
```

Expected: ImportError — UserAccount, CrossGroupConfirmation, SplitTransaction not defined.

- [ ] **Step 3: Add the three new models and extend existing ones in `orchestrator/app/db/models.py`**

Add after `AutomationRule`:

```python
class UserAccount(Base):
    __tablename__ = "user_accounts"

    id         = Column(String(36), primary_key=True, default=_uuid)
    phone      = Column(String, nullable=False, index=True)
    group_jid  = Column(String, ForeignKey("group_registry.group_jid"), nullable=False, index=True)
    role       = Column(String, nullable=False, default="owner")   # owner | member
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))


class SplitTransaction(Base):
    __tablename__ = "split_transactions"

    id                 = Column(String(36), primary_key=True, default=_uuid)
    reporter_group_jid = Column(String, nullable=False)
    reporter_phone     = Column(String, nullable=False)
    payer_phone        = Column(String, nullable=False)
    total_amount       = Column(Numeric(18, 4), nullable=False)
    description        = Column(Text, nullable=True)
    status             = Column(String, nullable=False, default="pending")  # pending|confirmed|suspended|cancelled
    created_at         = Column(DateTime(timezone=True), nullable=False,
                                default=lambda: datetime.now(timezone.utc))


class CrossGroupConfirmation(Base):
    __tablename__ = "cross_group_confirmations"

    id                   = Column(String(36), primary_key=True, default=_uuid)
    split_transaction_id = Column(String(36), ForeignKey("split_transactions.id", ondelete="CASCADE"), nullable=True)
    initiator_phone      = Column(String, nullable=False)
    initiator_group_jid  = Column(String, nullable=False)
    target_phone         = Column(String, nullable=False)
    target_group_jid     = Column(String, nullable=False)
    action_type          = Column(String, nullable=False)   # record_expense|record_payment|split_share
    action_payload       = Column(Text, nullable=False)     # JSON
    status               = Column(String, nullable=False, default="pending")  # pending|confirmed|rejected|timed_out
    expires_at           = Column(DateTime(timezone=True), nullable=False)
    created_at           = Column(DateTime(timezone=True), nullable=False,
                                  default=lambda: datetime.now(timezone.utc))
```

Also add `group_type` and extend `UserProfile` — find the `GroupRegistry` class and add:

```python
    group_type = Column(String, nullable=True, default="personal")  # personal|shared|sys_admin|unregistered
```

Find the `UserProfile` class and add:

```python
    display_name = Column(String, nullable=True)
```

- [ ] **Step 4: Run tests**

```bash
cd orchestrator
pytest tests/test_personal_accounting_models.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/db/models.py orchestrator/tests/test_personal_accounting_models.py
git commit -m "feat: ORM models for UserAccount, SplitTransaction, CrossGroupConfirmation; group_type on GroupRegistry"
```

---

## Task 3: AccountService — user/group resolution and permission checks

**Files:**
- Create: `orchestrator/app/accounting/__init__.py`
- Create: `orchestrator/app/accounting/account_service.py`
- Create: `orchestrator/tests/test_account_service.py`

- [ ] **Step 1: Write failing tests**

Create `orchestrator/tests/test_account_service.py`:

```python
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import pytest
from app.db.models import (
    UserAccount, GroupRegistry, AdminNumbers, UserProfile, Blueprint,
)
from app.accounting.account_service import AccountService


def _seed_blueprint(db):
    bp = Blueprint(
        id="family_accounting", display_name="FA",
        system_prompt="x", model="claude-sonnet-4-6",
        tools_enabled='["record_transaction"]',
    )
    db.add(bp)
    db.commit()


def _seed_group(db, jid: str, group_type: str = "personal") -> GroupRegistry:
    _seed_blueprint(db)
    g = GroupRegistry(group_jid=jid, blueprint_id="family_accounting", group_type=group_type)
    db.add(g)
    db.commit()
    return g


def _seed_user(db, phone: str, group_jid: str, role: str = "owner") -> UserAccount:
    u = UserAccount(phone=phone, group_jid=group_jid, role=role)
    db.add(u)
    db.commit()
    return u


def test_resolve_user_returns_account(db):
    _seed_group(db, "grp1@g.us")
    _seed_user(db, "972501", "grp1@g.us")
    svc = AccountService()
    acct = svc.resolve_user(db, "972501")
    assert acct is not None
    assert acct.phone == "972501"


def test_resolve_user_returns_none_for_unknown(db):
    svc = AccountService()
    assert svc.resolve_user(db, "999999") is None


def test_resolve_group_owner(db):
    _seed_group(db, "grp2@g.us")
    _seed_user(db, "972502", "grp2@g.us", role="owner")
    svc = AccountService()
    assert svc.resolve_group_owner(db, "grp2@g.us") == "972502"


def test_resolve_group_owner_returns_none_when_no_owner(db):
    _seed_group(db, "grp3@g.us")
    svc = AccountService()
    assert svc.resolve_group_owner(db, "grp3@g.us") is None


def test_get_group_members_returns_all_phones(db):
    _seed_group(db, "grp4@g.us", group_type="shared")
    _seed_user(db, "972503", "grp4@g.us", role="member")
    _seed_user(db, "972504", "grp4@g.us", role="member")
    svc = AccountService()
    members = svc.get_group_members(db, "grp4@g.us")
    assert set(members) == {"972503", "972504"}


def test_get_display_name_uses_display_name_if_set(db):
    p = UserProfile(phone="972505", display_name="Eran")
    db.add(p)
    db.commit()
    svc = AccountService()
    assert svc.get_display_name(db, "972505") == "Eran"


def test_get_display_name_falls_back_to_phone(db):
    svc = AccountService()
    assert svc.get_display_name(db, "972506") == "972506"


def test_is_sys_admin_true(db):
    db.add(AdminNumbers(phone_number="972507"))
    db.commit()
    svc = AccountService()
    assert svc.is_sys_admin(db, "972507") is True


def test_is_sys_admin_false(db):
    svc = AccountService()
    assert svc.is_sys_admin(db, "999999") is False


def test_get_group_type(db):
    _seed_group(db, "grp5@g.us", group_type="sys_admin")
    svc = AccountService()
    assert svc.get_group_type(db, "grp5@g.us") == "sys_admin"


def test_get_group_type_unknown_returns_unregistered(db):
    svc = AccountService()
    assert svc.get_group_type(db, "unknown@g.us") == "unregistered"
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd orchestrator
pytest tests/test_account_service.py -v
```

Expected: ImportError — `app.accounting.account_service` not found.

- [ ] **Step 3: Create the package init**

Create `orchestrator/app/accounting/__init__.py` as an empty file.

- [ ] **Step 4: Implement AccountService core methods**

Create `orchestrator/app/accounting/account_service.py`:

```python
"""Central coordination service for personal accounting cross-group operations."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    AdminNumbers, CrossGroupConfirmation, GroupRegistry,
    SplitTransaction, UserAccount, UserProfile,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_DEFAULT_CONFIRMATION_TIMEOUT_HOURS = 24


class AccountService:
    # ── User / group resolution ───────────────────────────────────────────────

    def resolve_user(self, db: Session, phone: str) -> UserAccount | None:
        return db.query(UserAccount).filter_by(phone=phone, role="owner").first()

    def resolve_group_owner(self, db: Session, group_jid: str) -> str | None:
        row = db.query(UserAccount).filter_by(group_jid=group_jid, role="owner").first()
        return row.phone if row else None

    def get_group_members(self, db: Session, group_jid: str) -> list[str]:
        rows = db.query(UserAccount).filter_by(group_jid=group_jid).all()
        return [r.phone for r in rows]

    def get_display_name(self, db: Session, phone: str) -> str:
        row = db.query(UserProfile).filter_by(phone=phone).first()
        if row and row.display_name:
            return row.display_name
        return phone

    def is_sys_admin(self, db: Session, phone: str) -> bool:
        return db.query(AdminNumbers).filter_by(phone_number=phone).first() is not None

    def get_group_type(self, db: Session, group_jid: str) -> str:
        row = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
        if row is None:
            return "unregistered"
        return row.group_type or "unregistered"

    def get_personal_group_jid(self, db: Session, phone: str) -> str | None:
        acct = self.resolve_user(db, phone)
        return acct.group_jid if acct else None

    def _confirmation_timeout_hours(self, db: Session) -> int:
        from app.db.models import SystemConfig
        row = db.query(SystemConfig).filter_by(
            key="cross_group_confirmation_timeout_hours"
        ).first()
        if row:
            try:
                return int(row.value)
            except ValueError:
                pass
        return _DEFAULT_CONFIRMATION_TIMEOUT_HOURS
```

- [ ] **Step 5: Run tests**

```bash
cd orchestrator
pytest tests/test_account_service.py -v
```

Expected: all 11 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/accounting/ orchestrator/tests/test_account_service.py
git commit -m "feat: AccountService — user/group resolution and permission checks"
```

---

## Task 4: AccountService — notifications and confirmation lifecycle

**Files:**
- Modify: `orchestrator/app/accounting/account_service.py`
- Modify: `orchestrator/tests/test_account_service.py`

- [ ] **Step 1: Add failing tests** (append to `test_account_service.py`)

```python
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_notify_user_sends_to_personal_group(db):
    _seed_group(db, "eden_grp@g.us")
    _seed_user(db, "972510", "eden_grp@g.us")
    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        await svc.notify_user(db, "972510", "Hello")
    mock_bc.send_message.assert_awaited_once_with("eden_grp@g.us", "Hello")


@pytest.mark.asyncio
async def test_notify_user_silent_when_no_group(db):
    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        await svc.notify_user(db, "999999", "Hello")
    mock_bc.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_confirmation_creates_row(db):
    _seed_group(db, "tal_grp@g.us")
    _seed_user(db, "972511", "tal_grp@g.us")
    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        conf = await svc.request_confirmation(
            db=db,
            initiator_phone="972500",
            initiator_group_jid="eden_grp@g.us",
            target_phone="972511",
            action_type="record_expense",
            action_payload={"amount_ils": "100.00"},
            confirmation_message="Tal, Eden says you owe ₪100. Confirm?",
        )
    assert conf.id is not None
    assert conf.status == "pending"
    assert conf.target_phone == "972511"
    assert conf.target_group_jid == "tal_grp@g.us"
    mock_bc.send_message.assert_awaited_once_with(
        "tal_grp@g.us",
        "Tal, Eden says you owe ₪100. Confirm?",
    )


def test_handle_confirmation_reply_yes_flips_status(db):
    _seed_group(db, "tal_grp2@g.us")
    _seed_user(db, "972512", "tal_grp2@g.us")
    now = datetime.now(timezone.utc)
    conf = CrossGroupConfirmation(
        initiator_phone="972500",
        initiator_group_jid="eden_grp@g.us",
        target_phone="972512",
        target_group_jid="tal_grp2@g.us",
        action_type="record_expense",
        action_payload='{"amount_ils": "50.00"}',
        status="pending",
        expires_at=now + timedelta(hours=24),
    )
    db.add(conf)
    db.commit()

    svc = AccountService()
    resolved = svc.handle_confirmation_reply(db, "tal_grp2@g.us", "972512", "yes")
    assert resolved is True
    db.refresh(conf)
    assert conf.status == "confirmed"


def test_handle_confirmation_reply_no_flips_status(db):
    _seed_group(db, "tal_grp3@g.us")
    _seed_user(db, "972513", "tal_grp3@g.us")
    now = datetime.now(timezone.utc)
    conf = CrossGroupConfirmation(
        initiator_phone="972500",
        initiator_group_jid="eden_grp@g.us",
        target_phone="972513",
        target_group_jid="tal_grp3@g.us",
        action_type="record_expense",
        action_payload='{"amount_ils": "50.00"}',
        status="pending",
        expires_at=now + timedelta(hours=24),
    )
    db.add(conf)
    db.commit()

    svc = AccountService()
    resolved = svc.handle_confirmation_reply(db, "tal_grp3@g.us", "972513", "no")
    assert resolved is True
    db.refresh(conf)
    assert conf.status == "rejected"


def test_handle_confirmation_reply_returns_false_when_no_pending(db):
    svc = AccountService()
    result = svc.handle_confirmation_reply(db, "grp@g.us", "972500", "yes")
    assert result is False
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd orchestrator
pytest tests/test_account_service.py::test_notify_user_sends_to_personal_group -v
pytest tests/test_account_service.py::test_request_confirmation_creates_row -v
```

Expected: AttributeError — notify_user / request_confirmation not defined.

- [ ] **Step 3: Add async methods to AccountService**

Append to `orchestrator/app/accounting/account_service.py` (after the existing methods):

```python
    # ── Cross-group notifications ─────────────────────────────────────────────

    async def notify_user(self, db: Session, target_phone: str, message: str) -> None:
        target_jid = self.get_personal_group_jid(db, target_phone)
        if not target_jid:
            logger.warning("notify_user: no personal group for %s", target_phone)
            return
        try:
            await bridge_client.send_message(target_jid, message)
        except Exception:
            logger.exception("notify_user: failed to send to %s (%s)", target_phone, target_jid)

    async def notify_all_in_group(self, db: Session, group_jid: str, message: str) -> None:
        try:
            await bridge_client.send_message(group_jid, message)
        except Exception:
            logger.exception("notify_all_in_group: failed to send to %s", group_jid)

    # ── Confirmation lifecycle ────────────────────────────────────────────────

    async def request_confirmation(
        self,
        db: Session,
        initiator_phone: str,
        initiator_group_jid: str,
        target_phone: str,
        action_type: str,
        action_payload: dict,
        confirmation_message: str,
        split_transaction_id: str | None = None,
    ) -> CrossGroupConfirmation:
        target_jid = self.get_personal_group_jid(db, target_phone)
        if not target_jid:
            raise ValueError(f"No personal group found for {target_phone}")

        timeout_hours = self._confirmation_timeout_hours(db)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=timeout_hours)

        conf = CrossGroupConfirmation(
            split_transaction_id=split_transaction_id,
            initiator_phone=initiator_phone,
            initiator_group_jid=initiator_group_jid,
            target_phone=target_phone,
            target_group_jid=target_jid,
            action_type=action_type,
            action_payload=json.dumps(action_payload),
            status="pending",
            expires_at=expires_at,
        )
        db.add(conf)
        db.commit()
        db.refresh(conf)

        await bridge_client.send_message(target_jid, confirmation_message)
        return conf

    def handle_confirmation_reply(
        self,
        db: Session,
        group_jid: str,
        phone: str,
        reply: str,
    ) -> bool:
        """Returns True if a pending confirmation was resolved, False if none found."""
        now = datetime.now(timezone.utc)
        conf = (
            db.query(CrossGroupConfirmation)
            .filter_by(target_phone=phone, target_group_jid=group_jid, status="pending")
            .filter(CrossGroupConfirmation.expires_at > now)
            .order_by(CrossGroupConfirmation.created_at.asc())
            .first()
        )
        if conf is None:
            return False

        reply_lower = reply.strip().lower()
        if reply_lower in ("yes", "כן", "y", "אישור"):
            conf.status = "confirmed"
        elif reply_lower in ("no", "לא", "n", "ביטול"):
            conf.status = "rejected"
        else:
            return False

        db.commit()
        return True
```

Add the bridge_client import at the top of `account_service.py` (after the other imports):

```python
from app import bridge_client
```

- [ ] **Step 4: Run tests**

```bash
cd orchestrator
pytest tests/test_account_service.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/accounting/account_service.py orchestrator/tests/test_account_service.py
git commit -m "feat: AccountService — cross-group notifications and confirmation lifecycle"
```

---

## Task 5: AccountService — transaction processing (1st/2nd party routing)

**Files:**
- Modify: `orchestrator/app/accounting/account_service.py`
- Modify: `orchestrator/tests/test_account_service.py`

- [ ] **Step 1: Add failing tests** (append to `test_account_service.py`)

```python
from app.db.models import LedgerEntry
from app.tools.accounting_fx import to_ils


@pytest.mark.asyncio
async def test_process_first_party_writes_entry_and_notifies(db):
    """Sender acknowledges own debt (1st-party) → written immediately, creditor notified."""
    _seed_group(db, "eran_grp@g.us")
    _seed_user(db, "972520", "eran_grp@g.us")  # Eran — creditor
    _seed_group(db, "eden_grp@g.us")
    _seed_user(db, "972521", "eden_grp@g.us")  # Eden — reporter/debtor

    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        result = await svc.process_transaction(
            db=db,
            reporter_phone="972521",       # Eden
            reporter_group_jid="eden_grp@g.us",
            payer_phone="972520",           # Eran paid → Eden owes Eran
            debtor_phone="972521",          # Eden is the debtor
            amount_ils=Decimal("100"),
            description="dinner",
            transaction_date=__import__("datetime").date.today(),
        )

    # Ledger entry written immediately
    entry = db.query(LedgerEntry).first()
    assert entry is not None
    assert entry.from_phone == "972521"
    assert entry.to_phone == "972520"
    # Creditor (Eran) notified
    mock_bc.send_message.assert_awaited_once()
    assert "972520" in result or "Eran" in result or "notified" in result.lower()


@pytest.mark.asyncio
async def test_process_second_party_creates_confirmation(db):
    """Sender claims credit (2nd-party) → confirmation requested from debtor."""
    _seed_group(db, "eden_grp2@g.us")
    _seed_user(db, "972522", "eden_grp2@g.us")  # Eden — reporter/creditor
    _seed_group(db, "tal_grp4@g.us")
    _seed_user(db, "972523", "tal_grp4@g.us")   # Tal — debtor

    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        result = await svc.process_transaction(
            db=db,
            reporter_phone="972522",        # Eden claims Tal owes her
            reporter_group_jid="eden_grp2@g.us",
            payer_phone="972522",           # Eden is the creditor/payer
            debtor_phone="972523",          # Tal is debtor
            amount_ils=Decimal("80"),
            description="taxi",
            transaction_date=__import__("datetime").date.today(),
        )

    # No ledger entry yet — waiting for confirmation
    assert db.query(LedgerEntry).count() == 0
    # Confirmation row created
    conf = db.query(CrossGroupConfirmation).first()
    assert conf is not None
    assert conf.target_phone == "972523"
    assert conf.status == "pending"
    # Tal notified
    mock_bc.send_message.assert_awaited_once()
```

- [ ] **Step 2: Run to verify failures**

```bash
cd orchestrator
pytest tests/test_account_service.py::test_process_first_party_writes_entry_and_notifies -v
```

Expected: AttributeError — process_transaction not defined.

- [ ] **Step 3: Implement transaction processing**

Append to `orchestrator/app/accounting/account_service.py`:

```python
    # ── Transaction processing ────────────────────────────────────────────────

    def _is_first_party(self, reporter_phone: str, debtor_phone: str) -> bool:
        """True when reporter is voluntarily taking on debt (1st-party action)."""
        return reporter_phone == debtor_phone

    async def process_transaction(
        self,
        db: Session,
        reporter_phone: str,
        reporter_group_jid: str,
        payer_phone: str,
        debtor_phone: str,
        amount_ils: Decimal,
        description: str,
        transaction_date,
        split_transaction_id: str | None = None,
    ) -> str:
        from app.db.models import LedgerEntry
        import uuid as _uuid_mod

        payer_name = self.get_display_name(db, payer_phone)
        debtor_name = self.get_display_name(db, debtor_phone)

        if self._is_first_party(reporter_phone, debtor_phone):
            # Debtor is self-reporting → write immediately
            entry = LedgerEntry(
                transaction_id=str(_uuid_mod.uuid4()),
                group_jid=reporter_group_jid,
                from_phone=debtor_phone,
                to_phone=payer_phone,
                amount_ils=amount_ils,
                description=description,
                transaction_date=transaction_date,
            )
            db.add(entry)
            db.commit()

            notify_msg = (
                f"{debtor_name} acknowledged a ₪{float(amount_ils):.2f} debt to you "
                f"({description}). Your balance has been updated."
            )
            await self.notify_user(db, payer_phone, notify_msg)
            return f"Recorded. {payer_name} has been notified."
        else:
            # Reporter is creditor claiming debt on debtor's behalf → confirmation needed
            confirm_msg = (
                f"{payer_name} says you owe ₪{float(amount_ils):.2f} ({description}). "
                f"Confirm? (yes / no)"
            )
            await self.request_confirmation(
                db=db,
                initiator_phone=reporter_phone,
                initiator_group_jid=reporter_group_jid,
                target_phone=debtor_phone,
                action_type="record_expense",
                action_payload={
                    "group_jid": reporter_group_jid,
                    "payer_phone": payer_phone,
                    "debtor_phone": debtor_phone,
                    "amount_ils": str(amount_ils),
                    "description": description,
                    "transaction_date": str(transaction_date),
                    "split_transaction_id": split_transaction_id,
                },
                confirmation_message=confirm_msg,
                split_transaction_id=split_transaction_id,
            )
            return f"Confirmation request sent to {debtor_name}. I'll notify you when they respond."

    async def commit_confirmed_transaction(self, db: Session, conf: CrossGroupConfirmation) -> None:
        """Write the ledger entry for a confirmed 2nd-party transaction."""
        from app.db.models import LedgerEntry
        import uuid as _uuid_mod
        from datetime import date as _date

        payload = json.loads(conf.action_payload)
        entry = LedgerEntry(
            transaction_id=str(_uuid_mod.uuid4()),
            group_jid=payload["group_jid"],
            from_phone=payload["debtor_phone"],
            to_phone=payload["payer_phone"],
            amount_ils=Decimal(payload["amount_ils"]),
            description=payload["description"],
            transaction_date=_date.fromisoformat(payload["transaction_date"]),
        )
        db.add(entry)
        db.commit()

        # Notify both parties
        debtor_name = self.get_display_name(db, payload["debtor_phone"])
        payer_name = self.get_display_name(db, payload["payer_phone"])
        await self.notify_user(
            db, payload["payer_phone"],
            f"{debtor_name} confirmed the ₪{float(entry.amount_ils):.2f} debt ({payload['description']})."
        )
        await bridge_client.send_message(
            conf.target_group_jid,
            f"Confirmed. Your balance with {payer_name} has been updated."
        )
```

- [ ] **Step 4: Run tests**

```bash
cd orchestrator
pytest tests/test_account_service.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/accounting/account_service.py orchestrator/tests/test_account_service.py
git commit -m "feat: AccountService — 1st/2nd-party transaction processing"
```

---

## Task 6: AccountService — split transaction management

**Files:**
- Modify: `orchestrator/app/accounting/account_service.py`
- Create: `orchestrator/tests/test_split_tools.py` (split-specific tests)

- [ ] **Step 1: Write failing tests**

Create `orchestrator/tests/test_split_tools.py`:

```python
from decimal import Decimal
from datetime import date, datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import (
    UserAccount, GroupRegistry, Blueprint,
    SplitTransaction, CrossGroupConfirmation,
)
from app.accounting.account_service import AccountService


def _setup(db):
    bp = Blueprint(
        id="family_accounting", display_name="FA",
        system_prompt="x", model="claude-sonnet-4-6",
        tools_enabled='["record_transaction"]',
    )
    db.add(bp)
    for jid, phone in [
        ("eran_g@g.us", "972530"),
        ("eden_g@g.us", "972531"),
        ("tal_g@g.us", "972532"),
    ]:
        db.add(GroupRegistry(group_jid=jid, blueprint_id="family_accounting", group_type="personal"))
        db.add(UserAccount(phone=phone, group_jid=jid, role="owner"))
    db.commit()


@pytest.mark.asyncio
async def test_process_split_creates_split_transaction(db):
    _setup(db)
    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        split = await svc.process_split(
            db=db,
            reporter_phone="972530",
            reporter_group_jid="eran_g@g.us",
            payer_phone="972530",
            shares=[
                {"phone": "972531", "amount_ils": Decimal("66.67")},
                {"phone": "972532", "amount_ils": Decimal("66.67")},
            ],
            total_amount=Decimal("200"),
            description="restaurant",
            transaction_date=date.today(),
        )
    assert split.id is not None
    assert split.status == "pending"
    # Payer is reporter → both shares are 2nd-party → 2 confirmation rows
    confs = db.query(CrossGroupConfirmation).filter_by(split_transaction_id=split.id).all()
    assert len(confs) == 2
    phones = {c.target_phone for c in confs}
    assert phones == {"972531", "972532"}


@pytest.mark.asyncio
async def test_process_split_reporter_is_participant_writes_own_share_pending(db):
    _setup(db)
    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        # Eden reports that Eran paid; Eden's own share is 1st-party
        split = await svc.process_split(
            db=db,
            reporter_phone="972531",          # Eden is reporter
            reporter_group_jid="eden_g@g.us",
            payer_phone="972530",             # Eran paid
            shares=[
                {"phone": "972531", "amount_ils": Decimal("66.67")},  # Eden — 1st-party
                {"phone": "972532", "amount_ils": Decimal("66.67")},  # Tal — 2nd-party
            ],
            total_amount=Decimal("200"),
            description="restaurant",
            transaction_date=date.today(),
        )
    assert split.status == "pending"
    confs = db.query(CrossGroupConfirmation).filter_by(split_transaction_id=split.id).all()
    # Only Tal's share needs a confirmation row; Eden's is 1st-party but held as pending in split
    assert len(confs) == 1
    assert confs[0].target_phone == "972532"
    # Eden's first-party share is stored in split metadata (action_payload of split)
    assert split.reporter_phone == "972531"


@pytest.mark.asyncio
async def test_decline_suspends_split(db):
    _setup(db)
    now = datetime.now(timezone.utc)
    split = SplitTransaction(
        reporter_group_jid="eran_g@g.us",
        reporter_phone="972530",
        payer_phone="972530",
        total_amount=Decimal("200"),
        description="restaurant",
        status="pending",
    )
    db.add(split)
    db.flush()
    conf = CrossGroupConfirmation(
        split_transaction_id=split.id,
        initiator_phone="972530",
        initiator_group_jid="eran_g@g.us",
        target_phone="972532",
        target_group_jid="tal_g@g.us",
        action_type="split_share",
        action_payload='{"amount_ils": "66.67"}',
        status="pending",
        expires_at=now + timedelta(hours=24),
    )
    db.add(conf)
    db.commit()

    svc = AccountService()
    with patch("app.accounting.account_service.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        await svc.handle_split_decline(db, conf)

    db.refresh(split)
    assert split.status == "suspended"
    # All parties notified
    assert mock_bc.send_message.await_count >= 1
```

- [ ] **Step 2: Run to verify failures**

```bash
cd orchestrator
pytest tests/test_split_tools.py -v
```

Expected: AttributeError — process_split / handle_split_decline not defined.

- [ ] **Step 3: Implement split methods**

Append to `orchestrator/app/accounting/account_service.py`:

```python
    # ── Split transaction management ──────────────────────────────────────────

    async def process_split(
        self,
        db: Session,
        reporter_phone: str,
        reporter_group_jid: str,
        payer_phone: str,
        shares: list[dict],       # [{"phone": str, "amount_ils": Decimal}]
        total_amount: Decimal,
        description: str,
        transaction_date,
    ) -> SplitTransaction:
        split = SplitTransaction(
            reporter_group_jid=reporter_group_jid,
            reporter_phone=reporter_phone,
            payer_phone=payer_phone,
            total_amount=total_amount,
            description=description,
            status="pending",
        )
        db.add(split)
        db.flush()  # get split.id

        payer_name = self.get_display_name(db, payer_phone)

        for share in shares:
            phone = share["phone"]
            amount = share["amount_ils"]

            if phone == payer_phone:
                continue  # payer's share absorbed

            if self._is_first_party(reporter_phone, phone):
                # Reporter is acknowledging their own share — held as pending in split
                # No confirmation row needed; committed on split completion
                # Store as a special "self_confirmed" confirmation so we can track it
                conf = CrossGroupConfirmation(
                    split_transaction_id=split.id,
                    initiator_phone=reporter_phone,
                    initiator_group_jid=reporter_group_jid,
                    target_phone=phone,
                    target_group_jid=reporter_group_jid,
                    action_type="split_share",
                    action_payload=json.dumps({
                        "group_jid": reporter_group_jid,
                        "payer_phone": payer_phone,
                        "debtor_phone": phone,
                        "amount_ils": str(amount),
                        "description": description,
                        "transaction_date": str(transaction_date),
                        "split_transaction_id": split.id,
                    }),
                    status="self_confirmed",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=self._confirmation_timeout_hours(db)),
                )
                db.add(conf)
            else:
                debtor_name = self.get_display_name(db, phone)
                confirm_msg = (
                    f"{debtor_name}, your share of a ₪{float(total_amount):.2f} "
                    f"{description} with {payer_name} is ₪{float(amount):.2f}. "
                    f"Confirm? (yes / no)"
                )
                await self.request_confirmation(
                    db=db,
                    initiator_phone=reporter_phone,
                    initiator_group_jid=reporter_group_jid,
                    target_phone=phone,
                    action_type="split_share",
                    action_payload={
                        "group_jid": reporter_group_jid,
                        "payer_phone": payer_phone,
                        "debtor_phone": phone,
                        "amount_ils": str(amount),
                        "description": description,
                        "transaction_date": str(transaction_date),
                        "split_transaction_id": split.id,
                    },
                    confirmation_message=confirm_msg,
                    split_transaction_id=split.id,
                )

        db.commit()
        return split

    async def handle_split_decline(
        self,
        db: Session,
        declined_conf: CrossGroupConfirmation,
    ) -> None:
        split_id = declined_conf.split_transaction_id
        if not split_id:
            return

        split = db.query(SplitTransaction).filter_by(id=split_id).first()
        if not split:
            return

        split.status = "suspended"

        # Pause all other pending confirmations
        db.query(CrossGroupConfirmation).filter_by(
            split_transaction_id=split_id, status="pending"
        ).update({"status": "paused"})
        db.commit()

        decliner_name = self.get_display_name(db, declined_conf.target_phone)
        reporter_name = self.get_display_name(db, split.reporter_phone)

        # Notify reporter
        await bridge_client.send_message(
            split.reporter_group_jid,
            f"{decliner_name} declined their share of the ₪{float(split.total_amount):.2f} "
            f"{split.description}. The split is suspended — re-submit if you agree on new amounts."
        )
        # Notify payer if different from reporter
        if split.payer_phone != split.reporter_phone:
            await self.notify_user(
                db, split.payer_phone,
                f"{decliner_name} declined their share of the ₪{float(split.total_amount):.2f} "
                f"{split.description} (reported by {reporter_name}). Transaction suspended."
            )

    async def finalize_split(self, db: Session, split: SplitTransaction) -> None:
        """Commit all ledger entries for a fully confirmed split."""
        confs = db.query(CrossGroupConfirmation).filter_by(
            split_transaction_id=split.id
        ).all()

        all_done = all(c.status in ("confirmed", "self_confirmed") for c in confs)
        if not all_done:
            return

        for conf in confs:
            await self.commit_confirmed_transaction(db, conf)

        split.status = "confirmed"
        db.commit()

        await bridge_client.send_message(
            split.reporter_group_jid,
            f"All shares confirmed. The ₪{float(split.total_amount):.2f} "
            f"{split.description} split has been recorded."
        )
```

- [ ] **Step 4: Run tests**

```bash
cd orchestrator
pytest tests/test_split_tools.py tests/test_account_service.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/accounting/account_service.py orchestrator/tests/test_split_tools.py
git commit -m "feat: AccountService — split transaction management (process, decline, finalize)"
```

---

## Task 7: Group registration handler

**Files:**
- Create: `orchestrator/app/accounting/group_registration.py`
- Create: `orchestrator/tests/test_group_registration.py`

- [ ] **Step 1: Write failing tests**

Create `orchestrator/tests/test_group_registration.py`:

```python
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
import pytest
from app.db.models import GroupRegistry, AdminNumbers, UserAccount, Blueprint
from app.accounting.group_registration import GroupRegistrationHandler


def _seed(db):
    bp = Blueprint(
        id="family_accounting", display_name="FA",
        system_prompt="x", model="claude-sonnet-4-6",
        tools_enabled='["record_transaction"]',
    )
    db.add(bp)
    # Sys-admin and their already-registered group
    db.add(AdminNumbers(phone_number="972500", label="admin"))
    db.add(GroupRegistry(group_jid="admin_g@g.us", blueprint_id="family_accounting", group_type="sys_admin"))
    db.add(UserAccount(phone="972500", group_jid="admin_g@g.us", role="owner"))
    db.commit()


@pytest.mark.asyncio
async def test_bot_joins_admin_group_registers_immediately(db):
    bp = Blueprint(
        id="family_accounting", display_name="FA",
        system_prompt="x", model="claude-sonnet-4-6",
        tools_enabled='["record_transaction"]',
    )
    db.add(bp)
    db.add(AdminNumbers(phone_number="972500", label="admin"))
    db.commit()

    handler = GroupRegistrationHandler()
    with patch("app.accounting.group_registration.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        await handler.on_bot_added_to_group(
            db=db,
            group_jid="new_admin_g@g.us",
            human_phones=["972500"],
        )

    grp = db.query(GroupRegistry).filter_by(group_jid="new_admin_g@g.us").first()
    assert grp is not None
    assert grp.group_type == "sys_admin"
    acct = db.query(UserAccount).filter_by(phone="972500", group_jid="new_admin_g@g.us").first()
    assert acct is not None
    mock_bc.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_bot_joins_unknown_user_group_notifies_admins(db):
    _seed(db)
    handler = GroupRegistrationHandler()
    with patch("app.accounting.group_registration.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        await handler.on_bot_added_to_group(
            db=db,
            group_jid="eden_g@g.us",
            human_phones=["972501"],
        )

    grp = db.query(GroupRegistry).filter_by(group_jid="eden_g@g.us").first()
    assert grp is not None
    assert grp.group_type == "unregistered"
    # Sys-admin notified
    mock_bc.send_message.assert_awaited_once_with(
        "admin_g@g.us",
        pytest.approx("", abs=1000),  # any non-empty string
    )
    call_args = mock_bc.send_message.call_args[0]
    assert "eden_g@g.us" in call_args[1] or "972501" in call_args[1]


@pytest.mark.asyncio
async def test_approve_registration_registers_group(db):
    _seed(db)
    # Unregistered group exists
    db.add(GroupRegistry(group_jid="eden_g@g.us", blueprint_id="family_accounting", group_type="unregistered"))
    db.commit()

    handler = GroupRegistrationHandler()
    # Simulate a pending registration
    handler._pending["eden_g@g.us"] = {
        "human_phones": ["972501"],
        "group_type": "personal",
        "sys_admin_jids": ["admin_g@g.us"],
        "created_at": datetime.now(timezone.utc),
    }

    with patch("app.accounting.group_registration.bridge_client") as mock_bc:
        mock_bc.send_message = AsyncMock()
        handled = await handler.handle_admin_reply(
            db=db,
            admin_group_jid="admin_g@g.us",
            reply="yes",
        )

    assert handled is True
    grp = db.query(GroupRegistry).filter_by(group_jid="eden_g@g.us").first()
    assert grp.group_type == "personal"
    acct = db.query(UserAccount).filter_by(phone="972501").first()
    assert acct is not None


@pytest.mark.asyncio
async def test_approve_returns_false_when_no_pending(db):
    _seed(db)
    handler = GroupRegistrationHandler()
    with patch("app.accounting.group_registration.bridge_client"):
        handled = await handler.handle_admin_reply(
            db=db,
            admin_group_jid="admin_g@g.us",
            reply="yes",
        )
    assert handled is False
```

- [ ] **Step 2: Run to verify failures**

```bash
cd orchestrator
pytest tests/test_group_registration.py -v
```

Expected: ImportError — group_registration module not found.

- [ ] **Step 3: Implement GroupRegistrationHandler**

Create `orchestrator/app/accounting/group_registration.py`:

```python
"""Handles bot group-join events and sys-admin approval flow."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app import bridge_client
from app.db.models import (
    AdminNumbers, GroupRegistry, UserAccount, Blueprint,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_REGISTRATION_TIMEOUT_HOURS = 24
_DEFAULT_BLUEPRINT_ID = "family_accounting"


class GroupRegistrationHandler:
    def __init__(self) -> None:
        # key = target_group_jid; value = pending registration dict
        self._pending: dict[str, dict] = {}

    async def on_bot_added_to_group(
        self,
        db: Session,
        group_jid: str,
        human_phones: list[str],
    ) -> None:
        if not human_phones:
            logger.info("Bot added to empty group %s — ignoring", group_jid)
            return

        # Ensure blueprint exists
        _ensure_blueprint(db)

        # Check if all members are sys-admins
        admin_phones = {
            r.phone_number for r in db.query(AdminNumbers).all()
        }
        all_admins = all(p in admin_phones for p in human_phones)

        if all_admins:
            await self._register_group(
                db, group_jid, human_phones, "sys_admin",
                welcome="I'm ready. As a system admin you have full access to all accounts."
            )
            return

        # Determine candidate group type
        if len(human_phones) == 1:
            group_type_candidate = "personal"
        else:
            group_type_candidate = "shared"

        # Register as unregistered and notify sys-admins
        _ensure_group_registry(db, group_jid, "unregistered")

        sys_admin_groups = self._get_sys_admin_group_jids(db)
        if not sys_admin_groups:
            logger.warning("No sys-admin groups registered; cannot request approval for %s", group_jid)
            return

        phone_list = ", ".join(human_phones)
        msg = (
            f"{'Someone' if len(human_phones) == 1 else 'A group'} added me "
            f"({phone_list}). Register as their {group_type_candidate} account? (yes / no)\n"
            f"Group: {group_jid}"
        )

        self._pending[group_jid] = {
            "human_phones": human_phones,
            "group_type": group_type_candidate,
            "sys_admin_jids": sys_admin_groups,
            "created_at": datetime.now(timezone.utc),
        }

        for admin_jid in sys_admin_groups:
            try:
                await bridge_client.send_message(admin_jid, msg)
            except Exception:
                logger.exception("Failed to notify admin group %s about %s", admin_jid, group_jid)

    async def handle_admin_reply(
        self,
        db: Session,
        admin_group_jid: str,
        reply: str,
    ) -> bool:
        """Returns True if this reply resolved a pending registration."""
        target_jid = self._find_pending_for_admin(admin_group_jid)
        if target_jid is None:
            return False

        pending = self._pending.pop(target_jid)
        reply_lower = reply.strip().lower()

        if reply_lower in ("yes", "כן", "y"):
            await self._register_group(
                db, target_jid, pending["human_phones"], pending["group_type"],
                welcome=f"Your account is ready. You can start recording transactions here."
            )
            # Notify other admins who got the request
            for jid in pending["sys_admin_jids"]:
                if jid != admin_group_jid:
                    try:
                        await bridge_client.send_message(
                            jid, f"Registration for {target_jid} was approved by another admin."
                        )
                    except Exception:
                        pass
            return True

        if reply_lower in ("no", "לא", "n"):
            db.query(GroupRegistry).filter_by(group_jid=target_jid).delete()
            db.commit()
            try:
                await bridge_client.send_message(
                    target_jid, "This group was not approved. I'll be leaving now."
                )
            except Exception:
                pass
            return True

        return False

    def is_pending_reply(self, db: Session, admin_group_jid: str, text: str) -> bool:
        """True if this group has a pending registration and text is yes/no."""
        if self._find_pending_for_admin(admin_group_jid) is None:
            return False
        return text.strip().lower() in ("yes", "no", "כן", "לא", "y", "n")

    def _find_pending_for_admin(self, admin_group_jid: str) -> str | None:
        for target_jid, info in self._pending.items():
            if admin_group_jid in info["sys_admin_jids"]:
                return target_jid
        return None

    def _get_sys_admin_group_jids(self, db: Session) -> list[str]:
        rows = db.query(GroupRegistry).filter_by(group_type="sys_admin").all()
        return [r.group_jid for r in rows]

    async def _register_group(
        self,
        db: Session,
        group_jid: str,
        human_phones: list[str],
        group_type: str,
        welcome: str,
    ) -> None:
        _ensure_blueprint(db)
        _ensure_group_registry(db, group_jid, group_type)

        for phone in human_phones:
            role = "owner" if len(human_phones) == 1 else "member"
            existing = db.query(UserAccount).filter_by(phone=phone, group_jid=group_jid).first()
            if not existing:
                db.add(UserAccount(phone=phone, group_jid=group_jid, role=role))
        db.commit()

        try:
            await bridge_client.send_message(group_jid, welcome)
        except Exception:
            logger.exception("Failed to send welcome to %s", group_jid)


def _ensure_blueprint(db: Session) -> None:
    if not db.query(Blueprint).filter_by(id=_DEFAULT_BLUEPRINT_ID).first():
        logger.warning("Blueprint %s not found — group registration may fail", _DEFAULT_BLUEPRINT_ID)


def _ensure_group_registry(db: Session, group_jid: str, group_type: str) -> GroupRegistry:
    existing = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
    if existing:
        existing.group_type = group_type
    else:
        existing = GroupRegistry(
            group_jid=group_jid,
            blueprint_id=_DEFAULT_BLUEPRINT_ID,
            group_type=group_type,
            status="active",
        )
        db.add(existing)
    db.commit()
    return existing
```

- [ ] **Step 4: Run tests**

```bash
cd orchestrator
pytest tests/test_group_registration.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/accounting/group_registration.py orchestrator/tests/test_group_registration.py
git commit -m "feat: GroupRegistrationHandler — bot-join detection, sys-admin approval flow"
```

---

## Task 8: Update accounting tools for 1st/2nd-party routing

**Files:**
- Modify: `orchestrator/app/tools/accounting_tools.py`

The existing `record_transaction` tool currently builds multi-party in-group confirmations. In the new model, it delegates to `AccountService` which routes 1st/2nd-party shares correctly. The tool's schema (inputs) stays the same; only the executor logic changes.

- [ ] **Step 1: Write failing test** (add to `orchestrator/tests/test_accounting_tools.py`)

Append to the existing test file:

```python
@pytest.mark.asyncio
async def test_record_transaction_uses_account_service_when_injected(db):
    """When AccountService is set, record_transaction delegates routing to it."""
    from unittest.mock import AsyncMock, MagicMock
    import app.tools.accounting_tools as at_module

    mock_svc = MagicMock()
    mock_svc.process_transaction = AsyncMock(return_value="Confirmation sent to Tal.")
    at_module.set_account_service(mock_svc)

    with patch("app.tools.accounting_tools.SessionLocal", return_value=_CM(db)), \
         patch("app.tools.accounting_tools.to_ils", new=AsyncMock(return_value=Decimal("100"))):
        tools = get_accounting_tools()
        result = await tools["record_transaction"]["executor"](
            {
                "payer_phone": "972500000001",
                "participant_phones": ["972500000002"],
                "amount": 100,
                "currency": "ILS",
                "description": "dinner",
            },
            group_jid="123@g.us",
            sender="972500000001@s.whatsapp.net",
            is_admin=False,
            multi_confirmation_store=None,
        )

    mock_svc.process_transaction.assert_awaited_once()
    assert "Confirmation" in result or "recorded" in result.lower()

    at_module.set_account_service(None)  # clean up
```

- [ ] **Step 2: Run to verify failure**

```bash
cd orchestrator
pytest tests/test_accounting_tools.py::test_record_transaction_uses_account_service_when_injected -v
```

Expected: AttributeError — set_account_service not defined.

- [ ] **Step 3: Add module-level setter and update `record_transaction` executor**

Add at the top of `orchestrator/app/tools/accounting_tools.py` (after imports):

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.accounting.account_service import AccountService

_account_service: "AccountService | None" = None


def set_account_service(service: "AccountService | None") -> None:
    global _account_service
    _account_service = service
```

Find the `record_transaction` executor function (the async function that handles the `record_transaction` tool). Replace its body with the following logic:

```python
async def _execute_record_transaction(params: dict, **kwargs) -> str:
    from datetime import date as _date
    ctx = kwargs
    sender = ctx.get("sender", "")
    sender_phone = sender.split("@")[0].split(":")[0]
    group_jid = ctx.get("group_jid", "")

    payer_phone: str = params["payer_phone"]
    participant_phones: list[str] = params["participant_phones"]
    amount: float = params["amount"]
    currency: str = params.get("currency", "ILS")
    description: str = params.get("description", "")
    tx_date_str: str | None = params.get("transaction_date")
    transaction_date = (
        _date.fromisoformat(tx_date_str) if tx_date_str else _date.today()
    )

    with SessionLocal() as db:
        amount_ils = await to_ils(Decimal(str(amount)), currency, transaction_date, db)

        if _account_service:
            # New cross-group routing path
            results = []
            for debtor_phone in participant_phones:
                msg = await _account_service.process_transaction(
                    db=db,
                    reporter_phone=sender_phone,
                    reporter_group_jid=group_jid,
                    payer_phone=payer_phone,
                    debtor_phone=debtor_phone,
                    amount_ils=amount_ils,
                    description=description,
                    transaction_date=transaction_date,
                )
                results.append(msg)
            return " ".join(results)
        else:
            # Legacy same-group path (other blueprints)
            multi_confirmation_store = ctx.get("multi_confirmation_store")
            return await _legacy_record_transaction(
                db, sender_phone, group_jid, payer_phone, participant_phones,
                amount_ils, description, transaction_date, multi_confirmation_store, ctx
            )
```

The legacy path (`_legacy_record_transaction`) is the existing executor body — extract it to a named helper function to avoid duplication.

- [ ] **Step 4: Run all accounting tool tests**

```bash
cd orchestrator
pytest tests/test_accounting_tools.py -v
```

Expected: all tests PASS (existing tests still pass, new test passes).

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/tools/accounting_tools.py orchestrator/tests/test_accounting_tools.py
git commit -m "feat: accounting_tools — delegate to AccountService for cross-group routing"
```

---

## Task 9: Split bill tool

**Files:**
- Create: `orchestrator/app/tools/split_tools.py`
- Modify: `orchestrator/tests/test_split_tools.py`

- [ ] **Step 1: Add tool registration test** (append to `test_split_tools.py`)

```python
def test_split_tools_registration():
    from app.tools.split_tools import get_split_tools
    tools = get_split_tools()
    assert "record_split" in tools
    entry = tools["record_split"]
    assert "schema" in entry and "executor" in entry
    assert entry["schema"]["name"] == "record_split"


@pytest.mark.asyncio
async def test_record_split_equal_split(db):
    _setup(db)
    from app.tools.split_tools import get_split_tools, set_account_service as set_svc
    mock_svc = AsyncMock()
    mock_svc.process_split = AsyncMock(return_value=SplitTransaction(
        id="split-1", reporter_group_jid="eran_g@g.us", reporter_phone="972530",
        payer_phone="972530", total_amount=Decimal("200"), description="restaurant",
        status="pending",
    ))
    set_svc(mock_svc)

    with patch("app.tools.split_tools.SessionLocal", return_value=_CM(db)), \
         patch("app.tools.split_tools.to_ils", new=AsyncMock(return_value=Decimal("200"))):
        tools = get_split_tools()
        result = await tools["record_split"]["executor"](
            {
                "payer_phone": "972530",
                "all_phones": ["972530", "972531", "972532"],
                "amount": 200,
                "currency": "ILS",
                "description": "restaurant",
            },
            group_jid="eran_g@g.us",
            sender="972530@s.whatsapp.net",
            is_admin=False,
        )

    mock_svc.process_split.assert_awaited_once()
    call_kwargs = mock_svc.process_split.call_args[1]
    shares = call_kwargs["shares"]
    # 3 participants, payer's share excluded, 2 non-payer shares
    assert len(shares) == 2
    # Equal split: 200 / 3 ≈ 66.67
    amounts = {s["amount_ils"] for s in shares}
    assert all(abs(float(a) - 66.67) < 0.1 for a in amounts)
    set_svc(None)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd orchestrator
pytest tests/test_split_tools.py::test_split_tools_registration -v
```

Expected: ImportError — split_tools not found.

- [ ] **Step 3: Create split tool**

Create `orchestrator/app/tools/split_tools.py`:

```python
"""Split-bill tool for the personal accounting blueprint."""

from __future__ import annotations

import logging
from datetime import date as _date
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING

from app.db.session import SessionLocal
from app.tools.accounting_fx import to_ils

if TYPE_CHECKING:
    from app.accounting.account_service import AccountService

logger = logging.getLogger(__name__)

_account_service: "AccountService | None" = None


def set_account_service(service: "AccountService | None") -> None:
    global _account_service
    _account_service = service


_SCHEMA = {
    "record_split": {
        "name": "record_split",
        "description": (
            "Record a split bill where one person paid and the cost is shared. "
            "The payer's own share is absorbed. All other participants either confirm "
            "their share (2nd-party) or self-report it (1st-party if reporter is a participant). "
            "Amounts default to equal split; pass custom_shares to override per-person amounts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "payer_phone": {"type": "string", "description": "Phone of the person who paid the full bill"},
                "all_phones": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "All participants including the payer",
                },
                "amount": {"type": "number", "description": "Total bill amount"},
                "currency": {"type": "string", "description": "ISO 4217, e.g. ILS, USD"},
                "description": {"type": "string", "description": "What the bill was for"},
                "custom_shares": {
                    "type": "object",
                    "description": (
                        "Optional per-phone override amounts (excluding payer). "
                        "Unspecified participants split the remainder equally. "
                        "Example: {\"972501\": 80, \"972502\": 50}"
                    ),
                },
                "transaction_date": {
                    "type": "string",
                    "description": "Date YYYY-MM-DD; defaults to today",
                },
            },
            "required": ["payer_phone", "all_phones", "amount", "currency", "description"],
        },
    }
}


async def _execute_record_split(params: dict, **kwargs) -> str:
    sender = kwargs.get("sender", "")
    sender_phone = sender.split("@")[0].split(":")[0]
    group_jid = kwargs.get("group_jid", "")

    payer_phone: str = params["payer_phone"]
    all_phones: list[str] = params["all_phones"]
    amount: float = params["amount"]
    currency: str = params.get("currency", "ILS")
    description: str = params.get("description", "")
    custom_shares: dict = params.get("custom_shares") or {}
    tx_date_str: str | None = params.get("transaction_date")
    transaction_date = _date.fromisoformat(tx_date_str) if tx_date_str else _date.today()

    non_payer_phones = [p for p in all_phones if p != payer_phone]
    if not non_payer_phones:
        return "No participants to split with (all phones are the payer)."

    with SessionLocal() as db:
        total_ils = await to_ils(Decimal(str(amount)), currency, transaction_date, db)

        shares = _compute_shares(total_ils, non_payer_phones, custom_shares)

        if _account_service is None:
            return "AccountService not configured — split bill unavailable."

        split = await _account_service.process_split(
            db=db,
            reporter_phone=sender_phone,
            reporter_group_jid=group_jid,
            payer_phone=payer_phone,
            shares=shares,
            total_amount=total_ils,
            description=description,
            transaction_date=transaction_date,
        )

    share_summary = ", ".join(
        f"₪{float(s['amount_ils']):.2f} → {s['phone']}" for s in shares
    )
    return (
        f"Split bill created (₪{float(total_ils):.2f} {description}). "
        f"Shares: {share_summary}. "
        f"Waiting for confirmations — all must confirm for the split to be recorded."
    )


def _compute_shares(
    total: Decimal,
    non_payer_phones: list[str],
    custom_shares: dict,
) -> list[dict]:
    n = len(non_payer_phones)
    specified = {p: Decimal(str(v)) for p, v in custom_shares.items() if p in non_payer_phones}
    specified_total = sum(specified.values(), Decimal("0"))

    unspecified = [p for p in non_payer_phones if p not in specified]
    remaining = total - specified_total

    if unspecified:
        per_person = (remaining / len(unspecified)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        per_person = Decimal("0")

    shares = []
    for phone in non_payer_phones:
        if phone in specified:
            shares.append({"phone": phone, "amount_ils": specified[phone]})
        else:
            shares.append({"phone": phone, "amount_ils": per_person})

    return shares


def get_split_tools() -> dict:
    return {
        "record_split": {
            "schema": _SCHEMA["record_split"],
            "executor": _execute_record_split,
        }
    }
```

- [ ] **Step 4: Run tests**

```bash
cd orchestrator
pytest tests/test_split_tools.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/tools/split_tools.py orchestrator/tests/test_split_tools.py
git commit -m "feat: record_split tool with equal/custom split computation"
```

---

## Task 10: Scheduler — confirmation expiry jobs

**Files:**
- Modify: `orchestrator/app/scheduler.py`

- [ ] **Step 1: Write failing test** (new file `orchestrator/tests/test_cross_group_expiry.py`)

```python
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
import pytest

from app.db.models import (
    CrossGroupConfirmation, SplitTransaction, GroupRegistry,
    UserAccount, Blueprint, AdminNumbers,
)
from app.scheduler import _expire_cross_group_confirmations


def _seed(db):
    bp = Blueprint(
        id="family_accounting", display_name="FA",
        system_prompt="x", model="claude-sonnet-4-6",
        tools_enabled='["record_transaction"]',
    )
    db.add(bp)
    for jid, phone in [("grp_a@g.us", "972540"), ("grp_b@g.us", "972541")]:
        db.add(GroupRegistry(group_jid=jid, blueprint_id="family_accounting", group_type="personal"))
        db.add(UserAccount(phone=phone, group_jid=jid, role="owner"))
    db.commit()


@pytest.mark.asyncio
async def test_expire_cross_group_confirmations_flips_timed_out(db):
    _seed(db)
    now = datetime.now(timezone.utc)
    expired_conf = CrossGroupConfirmation(
        initiator_phone="972540",
        initiator_group_jid="grp_a@g.us",
        target_phone="972541",
        target_group_jid="grp_b@g.us",
        action_type="record_expense",
        action_payload='{"amount_ils": "100"}',
        status="pending",
        expires_at=now - timedelta(hours=1),  # already expired
    )
    db.add(expired_conf)
    db.commit()

    with patch("app.scheduler.SessionLocal") as mock_sl, \
         patch("app.scheduler.bridge_client") as mock_bc:
        mock_sl.return_value.__enter__ = lambda s: db
        mock_sl.return_value.__exit__ = lambda s, *a: None
        mock_bc.send_message = AsyncMock()
        await _expire_cross_group_confirmations()

    db.refresh(expired_conf)
    assert expired_conf.status == "timed_out"
    # Both parties notified
    assert mock_bc.send_message.await_count == 2


@pytest.mark.asyncio
async def test_active_confirmation_not_expired(db):
    _seed(db)
    now = datetime.now(timezone.utc)
    active_conf = CrossGroupConfirmation(
        initiator_phone="972540",
        initiator_group_jid="grp_a@g.us",
        target_phone="972541",
        target_group_jid="grp_b@g.us",
        action_type="record_expense",
        action_payload='{"amount_ils": "100"}',
        status="pending",
        expires_at=now + timedelta(hours=23),  # not yet expired
    )
    db.add(active_conf)
    db.commit()

    with patch("app.scheduler.SessionLocal") as mock_sl, \
         patch("app.scheduler.bridge_client") as mock_bc:
        mock_sl.return_value.__enter__ = lambda s: db
        mock_sl.return_value.__exit__ = lambda s, *a: None
        mock_bc.send_message = AsyncMock()
        await _expire_cross_group_confirmations()

    db.refresh(active_conf)
    assert active_conf.status == "pending"
    mock_bc.send_message.assert_not_awaited()
```

- [ ] **Step 2: Run to verify failure**

```bash
cd orchestrator
pytest tests/test_cross_group_expiry.py -v
```

Expected: ImportError — `_expire_cross_group_confirmations` not defined in scheduler.

- [ ] **Step 3: Add two expiry jobs to `orchestrator/app/scheduler.py`**

Add after the existing `_evaluate_thresholds` function (before `start_scheduler`):

```python
async def _expire_cross_group_confirmations() -> None:
    """Flip pending cross-group confirmations past their expiry to timed_out and notify parties."""
    from app.db.models import CrossGroupConfirmation, SplitTransaction
    from app import bridge_client

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        expired = (
            db.query(CrossGroupConfirmation)
            .filter(
                CrossGroupConfirmation.status == "pending",
                CrossGroupConfirmation.expires_at <= now,
            )
            .all()
        )
        for conf in expired:
            conf.status = "timed_out"
        db.commit()

        for conf in expired:
            # If part of a split, check if we should suspend it
            if conf.split_transaction_id:
                split = db.query(SplitTransaction).filter_by(
                    id=conf.split_transaction_id, status="pending"
                ).first()
                if split:
                    split.status = "suspended"
                    db.query(CrossGroupConfirmation).filter_by(
                        split_transaction_id=split.id, status="pending"
                    ).update({"status": "paused"})
                    db.commit()
                    msg = (
                        f"The split ({split.description}, ₪{float(split.total_amount):.2f}) "
                        f"was not confirmed in time and has been suspended."
                    )
                    try:
                        await bridge_client.send_message(split.reporter_group_jid, msg)
                    except Exception:
                        logger.exception("Failed to notify split reporter %s", split.reporter_group_jid)
            else:
                # Standalone 2-party confirmation timeout
                msg = (
                    f"A transaction confirmation timed out and was not recorded "
                    f"({conf.action_type})."
                )
                for jid in {conf.initiator_group_jid, conf.target_group_jid}:
                    try:
                        await bridge_client.send_message(jid, msg)
                    except Exception:
                        logger.exception("Failed to send timeout notice to %s", jid)


async def _expire_split_transactions() -> None:
    """Suspend pending splits where all confirmations have timed out."""
    from app.db.models import CrossGroupConfirmation, SplitTransaction
    from app import bridge_client

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        pending_splits = db.query(SplitTransaction).filter_by(status="pending").all()
        for split in pending_splits:
            confs = db.query(CrossGroupConfirmation).filter_by(
                split_transaction_id=split.id
            ).all()
            all_resolved = all(
                c.status in ("confirmed", "self_confirmed", "rejected", "timed_out", "paused")
                for c in confs
            )
            if not all_resolved:
                continue
            any_pending_past_expiry = any(
                c.status == "timed_out" for c in confs
            )
            if any_pending_past_expiry:
                split.status = "suspended"
        db.commit()
```

In `start_scheduler`, add:

```python
    _scheduler.add_job(
        _expire_cross_group_confirmations, "interval", minutes=60, id="expire_cross_group_confirmations"
    )
    _scheduler.add_job(
        _expire_split_transactions, "interval", minutes=60, id="expire_split_transactions"
    )
```

Also add `from app import bridge_client` at the top of `scheduler.py` imports.

- [ ] **Step 4: Run tests**

```bash
cd orchestrator
pytest tests/test_cross_group_expiry.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/scheduler.py orchestrator/tests/test_cross_group_expiry.py
git commit -m "feat: scheduler — cross-group confirmation and split transaction expiry jobs"
```

---

## Task 11: Handle confirmation replies and group-join events in main.py

**Files:**
- Modify: `orchestrator/app/main.py`

This wires everything together: `AccountService` and `GroupRegistrationHandler` are initialized at startup and injected into tools; `_process` intercepts confirmation replies and registration approvals before passing to the agent.

- [ ] **Step 1: Add imports and globals to `main.py`**

Add to the imports section:

```python
from app.accounting.account_service import AccountService
from app.accounting.group_registration import GroupRegistrationHandler
from app.tools.accounting_tools import set_account_service
from app.tools.split_tools import get_split_tools
from app.tools.split_tools import set_account_service as set_split_account_service
```

Add to the globals block (near `agent_runner: AgentRunner | None = None`):

```python
account_service: AccountService = AccountService()
group_registration_handler: GroupRegistrationHandler = GroupRegistrationHandler()
```

- [ ] **Step 2: Wire AccountService at startup and register split tools**

In the `lifespan` function, after `tool_registry.register(get_accounting_tools())`, add:

```python
    set_account_service(account_service)
    set_split_account_service(account_service)
    tool_registry.register(get_split_tools())
```

- [ ] **Step 3: Handle bot-join events in `_process`**

In the `participant_update` handler block (where `payload.action in ("add", "remove", "leave")` is checked), add detection for the bot being added to a new group:

```python
        if payload.type == "participant_update":
            if payload.participants and payload.action in ("add", "remove", "leave"):
                from datetime import datetime, timezone
                bot_phone = settings.bot_phone_number or ""
                for jid_str in payload.participants:
                    phone = jid_str.split("@")[0].split(":")[0]
                    if not phone:
                        continue
                    if payload.action == "add":
                        _upsert_participant(db, payload.jid, phone, status="active")
                        # Check if the bot itself was added to a new group
                        if bot_phone and phone == bot_phone:
                            from app.bridge_client import fetch_group_meta
                            try:
                                meta = await fetch_group_meta(payload.jid)
                                human_phones = [
                                    p["jid"].split("@")[0].split(":")[0]
                                    for p in meta.get("participants", [])
                                    if p["jid"].split("@")[0].split(":")[0] != bot_phone
                                ]
                                await group_registration_handler.on_bot_added_to_group(
                                    db, payload.jid, human_phones
                                )
                            except Exception:
                                logger.exception(
                                    "Failed to handle bot-join for group %s", payload.jid
                                )
                    else:
                        _upsert_participant(db, payload.jid, phone,
                                            status="removed",
                                            removed_at=datetime.now(timezone.utc))
            return
```

- [ ] **Step 4: Intercept confirmation replies and registration approvals in `_process`**

In `_process`, after the command handler check and before blueprint resolution, add:

```python
        # Intercept sys-admin registration approvals
        if text.strip().lower() in ("yes", "no", "כן", "לא", "y", "n"):
            sender_phone = payload.sender.split("@")[0].split(":")[0]
            group_type = account_service.get_group_type(db, payload.jid)
            if group_type == "sys_admin":
                if group_registration_handler.is_pending_reply(db, payload.jid, text):
                    handled = await group_registration_handler.handle_admin_reply(
                        db, payload.jid, text
                    )
                    if handled:
                        return

        # Intercept cross-group confirmation replies (yes/no to 2nd-party transactions)
        if text.strip().lower() in ("yes", "no", "כן", "לא", "y", "n", "אישור", "ביטול"):
            sender_phone = payload.sender.split("@")[0].split(":")[0]
            resolved = account_service.handle_confirmation_reply(
                db, payload.jid, sender_phone, text
            )
            if resolved:
                conf = (
                    db.query(__import__("app.db.models", fromlist=["CrossGroupConfirmation"]).CrossGroupConfirmation)
                    .filter_by(target_phone=sender_phone, target_group_jid=payload.jid)
                    .order_by(__import__("app.db.models", fromlist=["CrossGroupConfirmation"]).CrossGroupConfirmation.created_at.desc())
                    .first()
                )
                if conf and conf.status == "confirmed":
                    if conf.split_transaction_id:
                        # Check if split is fully confirmed
                        from app.db.models import SplitTransaction
                        split = db.query(SplitTransaction).filter_by(id=conf.split_transaction_id).first()
                        if split:
                            await account_service.finalize_split(db, split)
                    else:
                        await account_service.commit_confirmed_transaction(db, conf)
                elif conf and conf.status == "rejected":
                    if conf.split_transaction_id:
                        await account_service.handle_split_decline(db, conf)
                    else:
                        await bridge_client.send_message(
                            conf.initiator_group_jid,
                            f"Your transaction was declined by the other party."
                        )
                return

        # Guard: shared group with unregistered member
        if payload.jid:
            sender_phone = payload.sender.split("@")[0].split(":")[0] if payload.sender else ""
            group_type = account_service.get_group_type(db, payload.jid)
            if group_type == "shared":
                members = account_service.get_group_members(db, payload.jid)
                registered_phones = {
                    r.phone
                    for r in db.query(__import__("app.db.models", fromlist=["UserAccount"]).UserAccount)
                    .filter(__import__("app.db.models", fromlist=["UserAccount"]).UserAccount.group_jid == payload.jid)
                    .all()
                }
                group_phones = {
                    p["jid"].split("@")[0].split(":")[0]
                    for p in []  # resolved lazily on first unregistered encounter
                }
                unregistered = [
                    p for p in members
                    if account_service.resolve_user(db, p) is None
                ]
                if unregistered:
                    await _send(
                        payload.jid,
                        "I can't process requests until all members have a registered account. "
                        "Please ask an admin to register any unregistered members, "
                        "or remove them from this group."
                    )
                    return
```

**Note:** The cross-group confirmation intercept block is intentionally concise — use `from app.db.models import CrossGroupConfirmation` at the top of `main.py` to clean up the inline imports above.

- [ ] **Step 5: Clean up inline imports**

Move all DB model imports used in `_process` to the top of `main.py`:

```python
from app.db.models import (
    GroupParticipant, CrossGroupConfirmation, SplitTransaction, UserAccount,
)
```

Then replace the inline `__import__` calls with the direct class names.

- [ ] **Step 6: Run the full test suite**

```bash
cd orchestrator
pytest -v
```

Expected: all tests PASS. Check for no regressions in existing tests.

- [ ] **Step 7: Commit**

```bash
git add orchestrator/app/main.py
git commit -m "feat: wire AccountService and GroupRegistrationHandler into main.py; intercept confirmation replies"
```

---

## Task 12: System prompt rewrite

**Files:**
- Modify: `orchestrator/app/prompts/family_accounting.py`

- [ ] **Step 1: Replace `FAMILY_ACCOUNTING_SYSTEM_PROMPT`**

Replace the entire content of `orchestrator/app/prompts/family_accounting.py` with:

```python
"""System prompt for the personal accounting blueprint.

The sender's phone, display name, and group type are injected dynamically
at inference time by AgentRunner. This prompt contains only the static rules.
"""

FAMILY_ACCOUNTING_SYSTEM_PROMPT = """\
You are a personal accounting assistant. You help individuals track what they owe and are owed across their family or household. Each user interacts with you privately.

## Context you receive

- sender_phone: the phone number of the person you're talking with
- group_type: "personal" (this user only), "shared" (2+ registered users), or "sys_admin" (elevated permissions)
- participant_block: display names and phones of registered users

## Transaction types — critical distinction

### 1st-party (self-reporting) — record immediately, notify counterpart

Use when the sender is voluntarily taking on debt or acknowledging a reduction in credit:
- "I owe Eran ₪200" → sender is the debtor
- "Eran paid for me ₪150" → sender acknowledges debt to Eran
- "I received ₪100 from Tal" → sender is reducing their own credit

**Always call `record_transaction` directly. Do NOT ask the other party to confirm.**
Notify the counterpart automatically after recording.

### 2nd-party (claiming credit at someone else's expense) — require counterpart confirmation

Use when the sender benefits at the other person's expense:
- "Tal owes me ₪200" → sender claims credit; Tal must confirm
- "I paid ₪200 for Eden" → sender claims credit; Eden must confirm

**Call `record_transaction` — the system will automatically send a confirmation request to the other party. Do NOT re-ask the sender for additional confirmation.**

### Split bills — use `record_split`

Use for any bill shared between multiple people:
- "I paid ₪200 at the restaurant with Eden and Tal"
- "Eran paid ₪300 for us (me and Tal)"

The payer can be anyone — including someone other than the sender. Use `record_split`. Each non-payer participant receives a separate confirmation request. **One decline suspends the entire split.**

## Permissions

### Regular user (group_type: personal or shared)
- Can record, query, and confirm/deny their own transactions
- Can only view their own balance and history
- Can set their own reminders
- Cannot view other users' full ledgers

### Sys-admin (group_type: sys_admin)
- Can view any user's balance and history
- Can record or settle transactions on behalf of any user
- Can rename participants

## Rules

1. **Resolve "I" from sender.** "I paid" means sender_phone is the payer.
2. **Splits are equal by default.** Unless amounts are specified per person.
3. **Currency defaults to ILS.** If unspecified, assume ILS.
4. **Respond in the user's language** — Hebrew or English, matching what they wrote.
5. **Be concise.** One-line confirmation after recording.
6. **Never ask the sender for confirmation again** after calling a tool — the tool handles the flow.
7. **Reminders are self-only.** `set_reminder` can only be used for the sender themselves.
"""
```

- [ ] **Step 2: Verify seeder still seeds this blueprint**

```bash
cd orchestrator
grep -n "family_accounting" app/seeder.py
```

If the seeder hardcodes the old prompt, update it to import and use `FAMILY_ACCOUNTING_SYSTEM_PROMPT` from `app.prompts.family_accounting`.

- [ ] **Step 3: Commit**

```bash
git add orchestrator/app/prompts/family_accounting.py
git commit -m "feat: rewrite family_accounting prompt for personal-group model with 1st/2nd-party rules"
```

---

## Task 13: Admin panel — Users page and Settings page

**Files:**
- Modify: `orchestrator/app/admin/api.py`
- Modify: `orchestrator/app/static/admin/index.html`
- Modify: `orchestrator/app/static/admin/app.js`

- [ ] **Step 1: Add Users and Settings API endpoints to `api.py`**

Append to `orchestrator/app/admin/api.py`:

```python
from app.db.models import UserProfile, UserAccount

# -- Users -------------------------------------------------------------------

@router.get("/users")
def list_users(_=Depends(require_auth)):
    with SessionLocal() as db:
        accounts = db.query(UserAccount).filter_by(role="owner").all()
        result = []
        for acct in accounts:
            profile = db.query(UserProfile).filter_by(phone=acct.phone).first()
            result.append({
                "phone": acct.phone,
                "display_name": profile.display_name if profile else None,
                "group_jid": acct.group_jid,
                "created_at": acct.created_at.isoformat() if acct.created_at else None,
            })
        return result


class UpdateUserRequest(BaseModel):
    display_name: str


@router.put("/users/{phone}")
def update_user(phone: str, body: UpdateUserRequest, _=Depends(require_auth)):
    with SessionLocal() as db:
        profile = db.query(UserProfile).filter_by(phone=phone).first()
        if profile is None:
            profile = UserProfile(phone=phone, display_name=body.display_name)
            db.add(profile)
        else:
            profile.display_name = body.display_name
        db.commit()
        return {"phone": phone, "display_name": body.display_name}


@router.delete("/users/{phone}")
def delete_user(phone: str, _=Depends(require_auth)):
    with SessionLocal() as db:
        db.query(UserAccount).filter_by(phone=phone).delete()
        db.commit()
        return {"deleted": phone}


# -- Settings ----------------------------------------------------------------

_EDITABLE_SETTINGS = {
    "cross_group_confirmation_timeout_hours",
    "group_registration_timeout_hours",
}


@router.get("/settings")
def get_settings(_=Depends(require_auth)):
    with SessionLocal() as db:
        from app.db.models import SystemConfig
        rows = db.query(SystemConfig).filter(
            SystemConfig.key.in_(_EDITABLE_SETTINGS)
        ).all()
        result = {r.key: r.value for r in rows}
        # Fill in defaults for any missing keys
        defaults = {
            "cross_group_confirmation_timeout_hours": "24",
            "group_registration_timeout_hours": "24",
        }
        for k, v in defaults.items():
            result.setdefault(k, v)
        return result


class UpdateSettingRequest(BaseModel):
    value: str


@router.put("/settings/{key}")
def update_setting(key: str, body: UpdateSettingRequest, _=Depends(require_auth)):
    if key not in _EDITABLE_SETTINGS:
        raise HTTPException(status_code=400, detail=f"Unknown setting: {key}")
    with SessionLocal() as db:
        from app.db.models import SystemConfig
        row = db.query(SystemConfig).filter_by(key=key).first()
        if row is None:
            row = SystemConfig(key=key, value=body.value)
            db.add(row)
        else:
            row.value = body.value
        db.commit()
        return {"key": key, "value": body.value}
```

- [ ] **Step 2: Add Users and Settings nav links to `index.html`**

Find the nav `<ul>` in `orchestrator/app/static/admin/index.html` and add:

```html
<li><a href="#" onclick="navigate('users')">Users</a></li>
<li><a href="#" onclick="navigate('settings')">Settings</a></li>
```

- [ ] **Step 3: Add page renderers to `app.js`**

Append to `orchestrator/app/static/admin/app.js`:

```javascript
// ── Users page ──────────────────────────────────────────────────────────────
async function renderUsers() {
  const res = await apiFetch('/admin/api/users');
  const users = await res.json();
  document.getElementById('content').innerHTML = `
    <h2>Users</h2>
    <table>
      <thead><tr><th>Phone</th><th>Display Name</th><th>Group JID</th><th>Registered</th><th></th></tr></thead>
      <tbody>
        ${users.map(u => `
          <tr>
            <td>${u.phone}</td>
            <td>
              <input id="name-${u.phone}" value="${u.display_name || ''}"
                     onblur="saveDisplayName('${u.phone}')" style="width:140px">
            </td>
            <td>${u.group_jid}</td>
            <td>${u.created_at ? u.created_at.slice(0,10) : ''}</td>
            <td><button onclick="deleteUser('${u.phone}')">Remove</button></td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

async function saveDisplayName(phone) {
  const name = document.getElementById(`name-${phone}`).value;
  await apiFetch(`/admin/api/users/${phone}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_name: name }),
  });
}

async function deleteUser(phone) {
  if (!confirm(`Remove user ${phone}? This will unregister their account.`)) return;
  await apiFetch(`/admin/api/users/${phone}`, { method: 'DELETE' });
  renderUsers();
}

// ── Settings page ────────────────────────────────────────────────────────────
async function renderSettings() {
  const res = await apiFetch('/admin/api/settings');
  const settings = await res.json();
  const labels = {
    cross_group_confirmation_timeout_hours: 'Cross-group confirmation timeout (hours)',
    group_registration_timeout_hours: 'Group registration approval timeout (hours)',
  };
  document.getElementById('content').innerHTML = `
    <h2>Settings</h2>
    <table>
      <tbody>
        ${Object.entries(settings).map(([k, v]) => `
          <tr>
            <td>${labels[k] || k}</td>
            <td>
              <input id="setting-${k}" type="number" min="1" max="168" value="${v}"
                     style="width:80px">
              <button onclick="saveSetting('${k}')">Save</button>
            </td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

async function saveSetting(key) {
  const value = document.getElementById(`setting-${key}`).value;
  await apiFetch(`/admin/api/settings/${key}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value }),
  });
  alert('Saved.');
}
```

In the `navigate(page)` function (find it in `app.js`), add cases for the new pages:

```javascript
  if (page === 'users') return renderUsers();
  if (page === 'settings') return renderSettings();
```

- [ ] **Step 4: Run the full test suite**

```bash
cd orchestrator
pytest -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/admin/api.py orchestrator/app/static/admin/index.html orchestrator/app/static/admin/app.js
git commit -m "feat: admin panel — Users page (display name editing, deregister) and Settings page (timeout config)"
```

---

## Final verification

- [ ] **Run full test suite**

```bash
cd orchestrator
pytest -v --tb=short
```

Expected: all tests PASS with no regressions.

- [ ] **Smoke test: migration runs clean on a fresh DB**

```bash
cd orchestrator
rm -f /tmp/test_personal.db
DATABASE_URL=sqlite:////tmp/test_personal.db alembic upgrade head
```

Expected: all 11 migrations applied with no errors.

- [ ] **Verify tools are registered at startup**

Start the orchestrator locally and hit `/health`:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
curl http://localhost:8000/health
```

Check logs for: `WhatsApp Agent Engine started — N tools registered` where N includes `record_split`.

- [ ] **Final commit**

```bash
git add -A
git commit -m "feat: personal accounting redesign — complete implementation"
```
