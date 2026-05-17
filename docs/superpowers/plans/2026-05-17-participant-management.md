# Participant Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace static `FAMILY_MEMBERS_JSON`/`FAMILY_HOUSEHOLD_MEMBERS` env config with a live per-group participant roster discovered from WhatsApp events and messages, with admin-controlled display names and household membership managed via natural language.

**Architecture:** A new `group_participants` table stores each member's phone, auto-updated push name, optional admin-override name, household flag, and active/removed status. The bridge forwards `pushName` on every message and emits `participant_update` events for joins/leaves. `/sync` bootstraps the initial roster. `AgentRunner` injects a fresh participant block as a system prompt at inference time. Two new accounting tools let the agent rename members and toggle household membership on admin request.

**Tech Stack:** SQLAlchemy/Alembic (migration + ORM), Baileys (bridge events), FastAPI/Python (webhook handlers), httpx (bridge HTTP client).

---

## File Map

| Action | Path |
|---|---|
| Create | `orchestrator/app/db/migrations/versions/008_group_participants.py` |
| Modify | `orchestrator/app/db/models.py` |
| Create | `orchestrator/tests/test_participants.py` |
| Modify | `bridge/src/connection.js` |
| Modify | `bridge/src/server.js` |
| Modify | `orchestrator/app/main.py` |
| Modify | `orchestrator/app/bridge_client.py` |
| Modify | `orchestrator/app/command_handler.py` |
| Create | `orchestrator/app/participants.py` |
| Modify | `orchestrator/app/agent_runner.py` |
| Modify | `orchestrator/app/tools/accounting_tools.py` |
| Modify | `orchestrator/app/tools/accounting_export.py` |
| Modify | `orchestrator/app/prompts/family_accounting.py` |
| Modify | `orchestrator/app/seeder.py` |
| Modify | `orchestrator/app/config.py` |
| Modify | `.env` |
| Modify | `.env.example` |

---

## Task 1: Migration 008 + GroupParticipant ORM

**Files:**
- Create: `orchestrator/app/db/migrations/versions/008_group_participants.py`
- Modify: `orchestrator/app/db/models.py`
- Create: `orchestrator/tests/test_participants.py`

- [ ] **Step 1: Write failing tests**

Create `orchestrator/tests/test_participants.py`:

```python
import pytest
from datetime import datetime, timezone
from app.db.models import GroupParticipant


def test_participant_insert_and_fetch(db):
    p = GroupParticipant(
        group_jid="123@g.us",
        phone="972501234567",
        push_name="Eran",
        status="active",
    )
    db.add(p)
    db.commit()
    db.expire_all()
    fetched = db.get(GroupParticipant, ("123@g.us", "972501234567"))
    assert fetched.push_name == "Eran"
    assert fetched.admin_name is None
    assert fetched.is_household is False
    assert fetched.status == "active"
    assert fetched.removed_at is None
    assert fetched.joined_at is not None


def test_participant_admin_name_override(db):
    p = GroupParticipant(
        group_jid="123@g.us",
        phone="972501234567",
        push_name="Eran W",
        admin_name="Eran",
        is_household=True,
        status="active",
    )
    db.add(p)
    db.commit()
    db.expire_all()
    fetched = db.get(GroupParticipant, ("123@g.us", "972501234567"))
    assert fetched.admin_name == "Eran"
    assert fetched.is_household is True


def test_participant_removed_keeps_row(db):
    from datetime import datetime, timezone
    p = GroupParticipant(
        group_jid="123@g.us",
        phone="972509999999",
        push_name="Tomer",
        status="removed",
        removed_at=datetime.now(timezone.utc),
    )
    db.add(p)
    db.commit()
    db.expire_all()
    fetched = db.get(GroupParticipant, ("123@g.us", "972509999999"))
    assert fetched.status == "removed"
    assert fetched.removed_at is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd orchestrator && python -m pytest tests/test_participants.py -v
```

Expected: `AttributeError` — `GroupParticipant` does not exist.

- [ ] **Step 3: Add GroupParticipant to models.py**

In `orchestrator/app/db/models.py`, add after the `GroupRegistry` class (after line 121):

```python
class GroupParticipant(Base):
    __tablename__ = "group_participants"

    group_jid  = Column(String, ForeignKey("group_registry.group_jid"), primary_key=True)
    phone      = Column(String, primary_key=True)
    push_name  = Column(String, nullable=True)
    admin_name = Column(String, nullable=True)
    is_household = Column(Boolean, nullable=False, default=False)
    status     = Column(String, nullable=False, default="active")   # active | removed
    joined_at  = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
    removed_at = Column(DateTime(timezone=True), nullable=True)
```

Also add `GroupParticipant` to the import in any file that uses it (it will be imported as needed in later tasks).

- [ ] **Step 4: Create migration 008**

Create `orchestrator/app/db/migrations/versions/008_group_participants.py`:

```python
"""Create group_participants table

Revision ID: 008
Revises: 007
Create Date: 2026-05-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "group_participants",
        sa.Column("group_jid", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("push_name", sa.String(), nullable=True),
        sa.Column("admin_name", sa.String(), nullable=True),
        sa.Column("is_household", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["group_jid"], ["group_registry.group_jid"]),
        sa.PrimaryKeyConstraint("group_jid", "phone"),
    )


def downgrade() -> None:
    op.drop_table("group_participants")
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd orchestrator && python -m pytest tests/test_participants.py -v
```

Expected: 3 tests pass.

- [ ] **Step 6: Run full suite**

```
cd orchestrator && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add orchestrator/app/db/migrations/versions/008_group_participants.py \
        orchestrator/app/db/models.py \
        orchestrator/tests/test_participants.py
git commit -m "feat: add group_participants table (migration 008)"
```

---

## Task 2: Bridge changes — pushName, participant events, extend /group-meta

**Files:**
- Modify: `bridge/src/connection.js`
- Modify: `bridge/src/server.js`

No automated JS tests exist. These are verified by orchestrator tests that mock bridge responses.

- [ ] **Step 1: Add pushName to message webhooks in connection.js**

In `bridge/src/connection.js`, update both `forwardToBackend` calls inside `sock.ev.on('messages.upsert', ...)`:

The image forward (around line 140) — add `pushName`:
```javascript
await forwardToBackend({
  type: 'image',
  jid,
  sender,
  messageId,
  isAdmin,
  pushName: msg.pushName || '',
  imageBase64: compressedBuffer.toString('base64'),
  mimeType: 'image/jpeg',
  caption: imageMessage.caption || '',
})
```

The text forward (around line 156) — add `pushName`:
```javascript
await forwardToBackend({
  type: 'text',
  jid,
  sender,
  messageId,
  isAdmin,
  pushName: msg.pushName || '',
  text: text.trim(),
})
```

- [ ] **Step 2: Forward group-participants.update events in connection.js**

Replace the existing `sock.ev.on('group-participants.update', ...)` handler (lines 88–90) with:

```javascript
sock.ev.on('group-participants.update', async ({ id, participants, action }) => {
  invalidateGroup(id)
  // Only forward roster-changing actions to the orchestrator
  if (action === 'add' || action === 'remove' || action === 'leave') {
    try {
      await forwardToBackend({
        type: 'participant_update',
        jid: id,
        sender: '',
        messageId: '',
        isAdmin: false,
        action,
        participants,
      })
    } catch (err) {
      console.error('Failed to forward participant update:', err.message)
    }
  }
})
```

- [ ] **Step 3: Extend GET /group-meta/:jid in server.js to include participants**

Replace the existing `/group-meta/:jid` response (line 106) with:

```javascript
res.json({
  description: meta.desc || '',
  participants: (meta.participants || []).map(p => ({
    jid: p.id,
    isAdmin: p.admin === 'admin' || p.admin === 'superadmin',
  })),
})
```

- [ ] **Step 4: Commit**

```bash
git add bridge/src/connection.js bridge/src/server.js
git commit -m "feat: bridge forwards pushName, participant events, and participant list in /group-meta"
```

---

## Task 3: Orchestrator webhook — handle participant_update + upsert pushName

**Files:**
- Modify: `orchestrator/app/main.py`
- Test: `orchestrator/tests/test_participants.py`

- [ ] **Step 1: Write failing tests**

Append to `orchestrator/tests/test_participants.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.db.models import GroupParticipant, GroupRegistry, Blueprint, AdminNumbers


def _seed_group(db):
    db.add(Blueprint(id="invoice_curator", display_name="IC", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="invoice_curator"))
    db.commit()


def _upsert_participant(db, group_jid, phone, push_name=None, admin_name=None,
                         is_household=False, status="active", removed_at=None):
    """Helper that mirrors the production upsert logic."""
    from datetime import datetime, timezone
    row = db.get(GroupParticipant, (group_jid, phone))
    if row is None:
        row = GroupParticipant(
            group_jid=group_jid, phone=phone, push_name=push_name,
            admin_name=admin_name, is_household=is_household,
            status=status, removed_at=removed_at,
        )
        db.add(row)
    else:
        if status != row.status:
            row.status = status
        if removed_at is not None:
            row.removed_at = removed_at
        if push_name is not None and row.admin_name is None and row.push_name != push_name:
            row.push_name = push_name
    db.commit()
    return row


def test_upsert_new_participant(db):
    _seed_group(db)
    _upsert_participant(db, "123@g.us", "972501234567", push_name="Eran")
    row = db.get(GroupParticipant, ("123@g.us", "972501234567"))
    assert row.push_name == "Eran"
    assert row.status == "active"


def test_upsert_updates_push_name_when_no_admin_name(db):
    _seed_group(db)
    _upsert_participant(db, "123@g.us", "972501234567", push_name="Eran")
    _upsert_participant(db, "123@g.us", "972501234567", push_name="Eran W")
    row = db.get(GroupParticipant, ("123@g.us", "972501234567"))
    assert row.push_name == "Eran W"


def test_upsert_does_not_overwrite_admin_name(db):
    _seed_group(db)
    _upsert_participant(db, "123@g.us", "972501234567", push_name="Eran W", admin_name="Eran")
    _upsert_participant(db, "123@g.us", "972501234567", push_name="New Push Name")
    row = db.get(GroupParticipant, ("123@g.us", "972501234567"))
    assert row.push_name == "Eran W"  # not updated because admin_name is set


def test_participant_remove_sets_status(db):
    from datetime import datetime, timezone
    _seed_group(db)
    _upsert_participant(db, "123@g.us", "972501234567", push_name="Eran")
    _upsert_participant(db, "123@g.us", "972501234567",
                        status="removed", removed_at=datetime.now(timezone.utc))
    row = db.get(GroupParticipant, ("123@g.us", "972501234567"))
    assert row.status == "removed"
    assert row.removed_at is not None
    assert row.push_name == "Eran"  # history intact
```

- [ ] **Step 2: Run to verify they pass (pure logic tests — no main.py needed)**

```
cd orchestrator && python -m pytest tests/test_participants.py::test_upsert_new_participant tests/test_participants.py::test_upsert_updates_push_name_when_no_admin_name tests/test_participants.py::test_upsert_does_not_overwrite_admin_name tests/test_participants.py::test_participant_remove_sets_status -v
```

Expected: 4 pass (these test the helper, not main.py).

- [ ] **Step 3: Add push_name + participant_update fields to WebhookPayload in main.py**

In `orchestrator/app/main.py`, update `WebhookPayload` (around line 53):

```python
class WebhookPayload(BaseModel):
    type: str
    jid: str
    sender: str
    message_id: str = ""
    is_admin: bool = False
    text: str | None = None
    image_base64: str | None = None
    mime_type: str | None = None
    caption: str | None = None
    push_name: str | None = None
    action: str | None = None
    participants: list[str] | None = None
```

- [ ] **Step 4: Add _upsert_participant helper and participant_update handler in main.py**

Add this helper function near the top of `orchestrator/app/main.py` (after imports, before globals):

```python
from app.db.models import GroupParticipant
```

Add this helper function after `_verify_webhook_auth`:

```python
def _upsert_participant(
    db,
    group_jid: str,
    phone: str,
    *,
    push_name: str | None = None,
    status: str = "active",
    removed_at=None,
) -> None:
    from datetime import datetime, timezone
    row = db.get(GroupParticipant, (group_jid, phone))
    if row is None:
        db.add(GroupParticipant(
            group_jid=group_jid,
            phone=phone,
            push_name=push_name,
            status=status,
            removed_at=removed_at,
        ))
    else:
        if status != row.status:
            row.status = status
        if removed_at is not None:
            row.removed_at = removed_at
        if push_name is not None and row.admin_name is None and row.push_name != push_name:
            row.push_name = push_name
    db.commit()
```

In `_process`, at the top of the function body (right after `db = SessionLocal()` and before command check), add:

```python
    # Track participant names from every incoming message
    if payload.push_name and payload.sender and payload.jid:
        sender_phone = payload.sender.split("@")[0].split(":")[0]
        if sender_phone:
            try:
                _upsert_participant(db, payload.jid, sender_phone, push_name=payload.push_name)
            except Exception:
                logger.debug("Could not upsert participant %s", sender_phone)
```

Also add a new early-return branch for `participant_update` at the top of `_process`, after the push_name upsert block and before the command check:

```python
    # Handle participant join/leave events
    if payload.type == "participant_update":
        if payload.participants and payload.action in ("add", "remove", "leave"):
            from datetime import datetime, timezone
            for jid_str in payload.participants:
                phone = jid_str.split("@")[0].split(":")[0]
                if not phone:
                    continue
                if payload.action == "add":
                    _upsert_participant(db, payload.jid, phone, status="active")
                else:
                    _upsert_participant(db, payload.jid, phone,
                                        status="removed",
                                        removed_at=datetime.now(timezone.utc))
        return
```

- [ ] **Step 5: Run full suite**

```
cd orchestrator && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/main.py orchestrator/tests/test_participants.py
git commit -m "feat: handle participant_update webhooks and upsert pushName on every message"
```

---

## Task 4: Extend /sync to bootstrap participant roster

**Files:**
- Modify: `orchestrator/app/bridge_client.py`
- Modify: `orchestrator/app/command_handler.py`
- Test: `orchestrator/tests/test_participants.py`

- [ ] **Step 1: Write failing test**

Append to `orchestrator/tests/test_participants.py`:

```python
@pytest.mark.asyncio
async def test_sync_bootstraps_participants(db):
    db.add(AdminNumbers(phone_number="972500000001"))
    db.add(Blueprint(id="invoice_curator", display_name="IC", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="invoice_curator"))
    db.commit()

    from app.command_handler import CommandHandler
    handler = CommandHandler(bridge_url="http://bridge:3000")
    with patch("app.command_handler.fetch_group_description", new=AsyncMock(return_value={
        "description": "Custom instructions here.",
        "participants": [
            {"jid": "972501234567@s.whatsapp.net", "isAdmin": False},
            {"jid": "972509876543@s.whatsapp.net", "isAdmin": True},
        ],
    })):
        reply = await handler.handle(db, "123@g.us", "972500000001", "/sync")

    assert "synced" in reply.lower()
    db.expire_all()
    p1 = db.get(GroupParticipant, ("123@g.us", "972501234567"))
    p2 = db.get(GroupParticipant, ("123@g.us", "972509876543"))
    assert p1 is not None and p1.status == "active"
    assert p2 is not None and p2.status == "active"
```

- [ ] **Step 2: Run to verify it fails**

```
cd orchestrator && python -m pytest tests/test_participants.py::test_sync_bootstraps_participants -v
```

Expected: FAIL — `fetch_group_description` currently returns a string, not a dict.

- [ ] **Step 3: Update fetch_group_description in bridge_client.py**

Replace the `fetch_group_description` function in `orchestrator/app/bridge_client.py` with:

```python
async def fetch_group_meta(jid: str) -> dict:
    """Fetch group description and participant list from the bridge.

    Returns: {"description": str, "participants": [{"jid": str, "isAdmin": bool}]}
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{settings.bridge_url}/group-meta/{jid}",
            headers=_bridge_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "description": data.get("description", "").strip(),
            "participants": data.get("participants", []),
        }
```

Remove the old `fetch_group_description` function entirely (it's replaced by `fetch_group_meta`).

- [ ] **Step 4: Update command_handler.py to use fetch_group_meta and upsert participants**

In `orchestrator/app/command_handler.py`, update the import:

```python
from app.bridge_client import fetch_group_meta
```

Update the `/sync` handler:

```python
        if cmd == "/sync":
            entry = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
            if not entry:
                return "No agent is bound to this group."
            if not self._bridge_url:
                return "Bridge URL not configured — cannot fetch group description."
            try:
                meta = await fetch_group_meta(group_jid)
            except Exception as exc:
                return f"Failed to fetch group metadata: {exc}"

            description = meta["description"]
            entry.custom_instructions = description or None

            # Bootstrap participant roster
            from datetime import datetime, timezone
            from app.db.models import GroupParticipant
            for p in meta.get("participants", []):
                phone = p["jid"].split("@")[0].split(":")[0]
                if not phone:
                    continue
                row = db.get(GroupParticipant, (group_jid, phone))
                if row is None:
                    db.add(GroupParticipant(
                        group_jid=group_jid,
                        phone=phone,
                        status="active",
                    ))
                elif row.status == "removed":
                    row.status = "active"
                    row.removed_at = None

            db.commit()

            n = len(meta.get("participants", []))
            if description:
                preview = description[:80] + ("…" if len(description) > 80 else "")
                return f"Synced {n} participants. Custom instructions: \"{preview}\""
            return f"Synced {n} participants. No custom instructions set."
```

- [ ] **Step 5: Update all tests that mock fetch_group_description**

In `orchestrator/tests/test_custom_instructions.py`, find the three tests that patch `"app.command_handler.fetch_group_description"` and update the patch target and mock return value:

`test_sync_stores_description`:
```python
    with patch("app.command_handler.fetch_group_meta", new=AsyncMock(return_value={
        "description": "Work invoices only. USD.",
        "participants": [],
    })):
```

`test_sync_clears_instructions_when_description_empty`:
```python
    with patch("app.command_handler.fetch_group_meta", new=AsyncMock(return_value={
        "description": "",
        "participants": [],
    })):
```

`test_sync_bridge_http_error`:
```python
    with patch("app.command_handler.fetch_group_meta", new=AsyncMock(side_effect=Exception("connection refused"))):
```

Also update the assertion in `test_sync_stores_description` — the reply now says "synced" + participant count:
```python
    assert "synced" in reply.lower()
```
That assertion still passes. No change needed there.

- [ ] **Step 6: Run full suite**

```
cd orchestrator && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add orchestrator/app/bridge_client.py \
        orchestrator/app/command_handler.py \
        orchestrator/tests/test_participants.py \
        orchestrator/tests/test_custom_instructions.py
git commit -m "feat: /sync bootstraps participant roster alongside group description"
```

---

## Task 5: Dynamic participant block (participants.py + AgentRunner)

**Files:**
- Create: `orchestrator/app/participants.py`
- Modify: `orchestrator/app/agent_runner.py`
- Modify: `orchestrator/app/main.py`
- Test: `orchestrator/tests/test_participants.py`

- [ ] **Step 1: Write failing tests**

Append to `orchestrator/tests/test_participants.py`:

```python
from app.participants import build_participant_block


def test_build_participant_block_basic(db):
    db.add(Blueprint(id="fa", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="fa"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972501111111",
                             push_name="Eran", is_household=True, status="active"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972502222222",
                             push_name="Sivan", is_household=True, status="active"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972503333333",
                             push_name="Eden", status="active"))
    db.commit()

    block = build_participant_block(db, "123@g.us")
    assert block is not None
    assert "972501111111" in block
    assert "972502222222" in block
    assert "Eden" in block
    assert "household" in block.lower() or "parents" in block.lower()


def test_build_participant_block_removed_included(db):
    db.add(Blueprint(id="fa", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="456@g.us", blueprint_id="fa"))
    db.add(GroupParticipant(group_jid="456@g.us", phone="972501111111",
                             push_name="Eran", status="active"))
    db.add(GroupParticipant(group_jid="456@g.us", phone="972509999999",
                             push_name="Tomer", status="removed"))
    db.commit()

    block = build_participant_block(db, "456@g.us")
    assert "Tomer" in block
    assert "(removed)" in block


def test_build_participant_block_admin_name_takes_priority(db):
    db.add(Blueprint(id="fa", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="789@g.us", blueprint_id="fa"))
    db.add(GroupParticipant(group_jid="789@g.us", phone="972501111111",
                             push_name="Eran W.", admin_name="Eran", status="active"))
    db.commit()

    block = build_participant_block(db, "789@g.us")
    assert "Eran" in block
    assert "Eran W." not in block


def test_build_participant_block_empty_group(db):
    block = build_participant_block(db, "no-such-group@g.us")
    assert block is None
```

- [ ] **Step 2: Run to verify they fail**

```
cd orchestrator && python -m pytest tests/test_participants.py::test_build_participant_block_basic -v
```

Expected: `ImportError` — `app.participants` does not exist.

- [ ] **Step 3: Create orchestrator/app/participants.py**

```python
"""Builds the per-group participant system-prompt block for AgentRunner."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import GroupParticipant


def build_participant_block(db: Session, group_jid: str) -> str | None:
    """Return a formatted participant list for injection into the system prompt.

    Includes removed members so the agent can still reference them by name.
    Returns None if no participants are recorded for the group.
    """
    rows = (
        db.query(GroupParticipant)
        .filter_by(group_jid=group_jid)
        .order_by(GroupParticipant.joined_at)
        .all()
    )
    if not rows:
        return None

    household_phones = {r.phone for r in rows if r.is_household}

    lines = []
    for r in rows:
        display = r.admin_name or r.push_name or r.phone
        prefix = "(removed) " if r.status == "removed" else ""
        lines.append(f"- {prefix}{display}: {r.phone}")

    block = "Family members in this group:\n" + "\n".join(lines)

    active_household = [
        (r.admin_name or r.push_name or r.phone)
        for r in rows
        if r.is_household and r.status == "active"
    ]
    if len(active_household) >= 2:
        names_str = " and ".join(active_household)
        block += (
            f"\n\nShared household: {names_str} share a single account "
            f"(shown as \"Parents\" in reports and balances). "
            f"Do not track or report debts between them."
        )

    return block
```

- [ ] **Step 4: Run the 4 participant block tests**

```
cd orchestrator && python -m pytest tests/test_participants.py::test_build_participant_block_basic tests/test_participants.py::test_build_participant_block_removed_included tests/test_participants.py::test_build_participant_block_admin_name_takes_priority tests/test_participants.py::test_build_participant_block_empty_group -v
```

Expected: 4 pass.

- [ ] **Step 5: Update AgentRunner to accept participant_block**

Replace the full contents of `orchestrator/app/agent_runner.py` with:

```python
import json
from datetime import datetime, timezone
import anthropic
from app.db.models import Blueprint
from app.tool_registry import ToolRegistry


class AgentRunner:
    def __init__(self, client: anthropic.AsyncAnthropic, tool_registry: ToolRegistry):
        self.client = client
        self.registry = tool_registry

    async def run(
        self,
        blueprint: Blueprint,
        group_jid: str,
        sender: str,
        is_admin: bool,
        message: str,
        context,
        confirmation_store,
        custom_instructions: str | None = None,
        participant_block: str | None = None,
    ) -> str:
        allowed_tools = blueprint.tools_list()

        pending = confirmation_store.get(group_jid)
        if pending and not pending.is_expired():
            if confirmation_store.is_confirm(message):
                result = await self.registry.execute(
                    pending.action, pending.params,
                    group_jid=group_jid, sender=sender, is_admin=is_admin,
                )
                confirmation_store.clear(group_jid)
                context.add(group_jid, "user", message, max_pairs=blueprint.context_window)
                context.add(group_jid, "assistant", str(result), max_pairs=blueprint.context_window)
                return str(result)
            elif confirmation_store.is_cancel(message):
                confirmation_store.clear(group_jid)
                reply = "Action cancelled."
                context.add(group_jid, "user", message, max_pairs=blueprint.context_window)
                context.add(group_jid, "assistant", reply, max_pairs=blueprint.context_window)
                return reply

        history = context.get_history(
            group_jid,
            max_pairs=blueprint.context_window,
            idle_minutes=blueprint.context_idle_reset_minutes,
        )
        messages = history + [{"role": "user", "content": message}]
        system = [
            {
                "type": "text",
                "text": blueprint.system_prompt,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": f"Today's date: {datetime.now(timezone.utc).date()}. Sender is_admin: {is_admin}.",
            },
        ]
        if participant_block:
            system.append({"type": "text", "text": participant_block})
        if custom_instructions:
            system.append({
                "type": "text",
                "text": f"Group-specific instructions:\n{custom_instructions}",
            })
        tool_schemas = self.registry.get_schemas(allowed_tools)

        for _ in range(blueprint.max_tool_turns):
            response = await self.client.messages.create(
                model=blueprint.model,
                max_tokens=4096,
                system=system,
                tools=tool_schemas,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                text = next(
                    (b.text for b in response.content if hasattr(b, "text") and b.type == "text"),
                    "",
                )
                context.add(group_jid, "user", message, max_pairs=blueprint.context_window)
                context.add(group_jid, "assistant", text, max_pairs=blueprint.context_window)
                return text

            if response.stop_reason == "tool_use":
                tool_calls = [b for b in response.content if b.type == "tool_use"]
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for tc in tool_calls:
                    if tc.name not in allowed_tools:
                        result_text = f"Tool '{tc.name}' is not permitted for this agent."
                    else:
                        raw = await self.registry.execute(
                            tc.name, tc.input,
                            group_jid=group_jid, sender=sender, is_admin=is_admin,
                            confirmation_store=confirmation_store,
                        )
                        result_text = raw if isinstance(raw, str) else json.dumps(raw)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": result_text,
                    })
                messages.append({"role": "user", "content": tool_results})

        fallback = "I reached my processing limit. Please try a simpler request."
        context.add(group_jid, "user", message, max_pairs=blueprint.context_window)
        context.add(group_jid, "assistant", fallback, max_pairs=blueprint.context_window)
        return fallback
```

- [ ] **Step 6: Update main.py to build and pass participant_block**

In `orchestrator/app/main.py`, add import at the top:

```python
from app.participants import build_participant_block
```

In `_process`, after the `blueprint, entry = router.resolve(db, payload.jid)` check and before the rate limiting block, add:

```python
        participant_block = build_participant_block(db, payload.jid)
```

Then update the `agent_runner.run()` call to pass `participant_block`:

```python
        reply = await agent_runner.run(
            blueprint=blueprint,
            group_jid=payload.jid,
            sender=payload.sender,
            is_admin=payload.is_admin,
            message=agent_message,
            context=context_store,
            confirmation_store=confirmation_store,
            custom_instructions=entry.custom_instructions,
            participant_block=participant_block,
        )
```

- [ ] **Step 7: Run full suite**

```
cd orchestrator && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add orchestrator/app/participants.py \
        orchestrator/app/agent_runner.py \
        orchestrator/app/main.py \
        orchestrator/tests/test_participants.py
git commit -m "feat: inject dynamic participant block into AgentRunner system prompt"
```

---

## Task 6: New accounting tools — rename_participant + set_household

**Files:**
- Modify: `orchestrator/app/tools/accounting_tools.py`
- Modify: `orchestrator/app/seeder.py`
- Test: `orchestrator/tests/test_participants.py`

- [ ] **Step 1: Write failing tests**

Append to `orchestrator/tests/test_participants.py`:

```python
import pytest
from app.tools.accounting_tools import get_accounting_tools
from app.tool_registry import ToolRegistry


def _make_registry():
    registry = ToolRegistry()
    registry.register(get_accounting_tools())
    return registry


@pytest.mark.asyncio
async def test_rename_participant_sets_admin_name(db):
    db.add(Blueprint(id="fa", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="fa"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972501111111",
                             push_name="Eran W.", status="active"))
    db.commit()

    registry = _make_registry()
    result = await registry.execute(
        "rename_participant",
        {"phone": "972501111111", "name": "Eran"},
        group_jid="123@g.us",
        sender="admin@s.whatsapp.net",
        is_admin=True,
        db=db,
    )
    assert "renamed" in result.lower() or "eran" in result.lower()
    db.expire_all()
    row = db.get(GroupParticipant, ("123@g.us", "972501111111"))
    assert row.admin_name == "Eran"


@pytest.mark.asyncio
async def test_rename_participant_rejects_non_admin(db):
    db.add(Blueprint(id="fa", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="fa"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972501111111",
                             push_name="Eran", status="active"))
    db.commit()

    registry = _make_registry()
    result = await registry.execute(
        "rename_participant",
        {"phone": "972501111111", "name": "X"},
        group_jid="123@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        db=db,
    )
    assert "admin" in result.lower()
    db.expire_all()
    row = db.get(GroupParticipant, ("123@g.us", "972501111111"))
    assert row.admin_name is None


@pytest.mark.asyncio
async def test_set_household_marks_participant(db):
    db.add(Blueprint(id="fa", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="fa"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972501111111",
                             push_name="Eran", status="active"))
    db.commit()

    registry = _make_registry()
    result = await registry.execute(
        "set_household",
        {"phone": "972501111111", "is_household": True},
        group_jid="123@g.us",
        sender="admin@s.whatsapp.net",
        is_admin=True,
        db=db,
    )
    assert "household" in result.lower()
    db.expire_all()
    row = db.get(GroupParticipant, ("123@g.us", "972501111111"))
    assert row.is_household is True
```

Note: The tests pass `db=db` as a kwarg so the tools use the test's in-memory DB. You'll wire this up in Step 3.

- [ ] **Step 2: Run to verify they fail**

```
cd orchestrator && python -m pytest tests/test_participants.py::test_rename_participant_sets_admin_name -v
```

Expected: `KeyError` — `rename_participant` not in registry.

- [ ] **Step 3: Add rename_participant and set_household to accounting_tools.py**

In `orchestrator/app/tools/accounting_tools.py`:

**Add two schemas** to `_SCHEMAS` dict:

```python
    "rename_participant": {
        "name": "rename_participant",
        "description": (
            "Set or clear the display name for a group participant. "
            "Pass empty string to revert to their WhatsApp push name. Admin only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "The participant's phone number (digits only)"},
                "name": {"type": "string", "description": "New display name, or empty string to clear override"},
            },
            "required": ["phone", "name"],
        },
    },
    "set_household": {
        "name": "set_household",
        "description": (
            "Mark or unmark a participant as part of the shared household (shown as 'Parents'). "
            "Admin only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "The participant's phone number (digits only)"},
                "is_household": {"type": "boolean", "description": "True to add to household, False to remove"},
            },
            "required": ["phone", "is_household"],
        },
    },
```

**Add two executor functions** (after `_exec_set_reminder`):

```python
async def _exec_rename_participant(params: dict, **ctx) -> str:
    if not ctx.get("is_admin"):
        return "Only admins can rename participants."
    group_jid = ctx.get("group_jid", "")
    phone = params["phone"]
    name = params["name"].strip()

    db = ctx.get("db")
    close_db = db is None
    if db is None:
        db = SessionLocal()

    try:
        from app.db.models import GroupParticipant
        row = db.get(GroupParticipant, (group_jid, phone))
        if row is None:
            return f"Participant {phone} not found in this group."
        row.admin_name = name or None
        db.commit()
        display = name or row.push_name or phone
        if name:
            return f"Renamed {phone} to \"{display}\"."
        return f"Display name cleared for {phone} — reverted to WhatsApp name."
    finally:
        if close_db:
            db.close()


async def _exec_set_household(params: dict, **ctx) -> str:
    if not ctx.get("is_admin"):
        return "Only admins can change household membership."
    group_jid = ctx.get("group_jid", "")
    phone = params["phone"]
    is_household = params["is_household"]

    db = ctx.get("db")
    close_db = db is None
    if db is None:
        db = SessionLocal()

    try:
        from app.db.models import GroupParticipant
        row = db.get(GroupParticipant, (group_jid, phone))
        if row is None:
            return f"Participant {phone} not found in this group."
        row.is_household = is_household
        db.commit()
        name = row.admin_name or row.push_name or phone
        action = "added to" if is_household else "removed from"
        return f"{name} {action} the shared household account."
    finally:
        if close_db:
            db.close()
```

**Add both to the factory** at the bottom of `get_accounting_tools()`:

```python
def get_accounting_tools() -> dict[str, dict]:
    """Return all 8 accounting tools in ToolRegistry format."""
    return {
        name: {"schema": _SCHEMAS[name], "executor": executor}
        for name, executor in [
            ("record_transaction",  _exec_record_transaction),
            ("record_payment",      _exec_record_payment),
            ("get_balance",         _exec_get_balance),
            ("get_history",         _exec_get_history),
            ("export_ledger",       _exec_export_ledger),
            ("set_reminder",        _exec_set_reminder),
            ("rename_participant",  _exec_rename_participant),
            ("set_household",       _exec_set_household),
        ]
    }
```

- [ ] **Step 4: Add new tools to FAMILY_ACCOUNTING_TOOLS in seeder.py**

In `orchestrator/app/seeder.py`, update:

```python
FAMILY_ACCOUNTING_TOOLS = [
    "record_transaction", "record_payment", "get_balance",
    "get_history", "export_ledger", "set_reminder",
    "rename_participant", "set_household",
]
```

- [ ] **Step 5: Run the new tests**

```
cd orchestrator && python -m pytest tests/test_participants.py::test_rename_participant_sets_admin_name tests/test_participants.py::test_rename_participant_rejects_non_admin tests/test_participants.py::test_set_household_marks_participant -v
```

Expected: 3 pass.

- [ ] **Step 6: Run full suite**

```
cd orchestrator && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add orchestrator/app/tools/accounting_tools.py \
        orchestrator/app/seeder.py \
        orchestrator/tests/test_participants.py
git commit -m "feat: add rename_participant and set_household accounting tools"
```

---

## Task 7: Update accounting helpers to read participants from DB

**Files:**
- Modify: `orchestrator/app/tools/accounting_tools.py`
- Modify: `orchestrator/app/tools/accounting_export.py`
- Test: `orchestrator/tests/test_participants.py`

- [ ] **Step 1: Write failing tests**

Append to `orchestrator/tests/test_participants.py`:

```python
from app.tools.accounting_tools import _household_phones_from_db, _phone_to_name_from_db


def test_household_phones_from_db(db):
    db.add(Blueprint(id="fa", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="fa"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972501111111",
                             push_name="Eran", is_household=True, status="active"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972502222222",
                             push_name="Sivan", is_household=True, status="active"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972503333333",
                             push_name="Eden", is_household=False, status="active"))
    db.commit()

    phones = _household_phones_from_db(db, "123@g.us")
    assert phones == {"972501111111", "972502222222"}


def test_phone_to_name_from_db_household_maps_to_parents(db):
    db.add(Blueprint(id="fa", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="fa"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972501111111",
                             admin_name="Eran", is_household=True, status="active"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972503333333",
                             push_name="Eden", status="active"))
    db.commit()

    names = _phone_to_name_from_db(db, "123@g.us")
    assert names["972501111111"] == "Parents"
    assert names["972503333333"] == "Eden"


def test_phone_to_name_from_db_admin_name_priority(db):
    db.add(Blueprint(id="fa", display_name="FA", system_prompt="p", tools_enabled="[]"))
    db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="fa"))
    db.add(GroupParticipant(group_jid="123@g.us", phone="972501111111",
                             push_name="Eran W.", admin_name="Eran", status="active"))
    db.commit()

    names = _phone_to_name_from_db(db, "123@g.us")
    assert names["972501111111"] == "Eran"
```

- [ ] **Step 2: Run to verify they fail**

```
cd orchestrator && python -m pytest tests/test_participants.py::test_household_phones_from_db -v
```

Expected: `ImportError` — `_household_phones_from_db` not defined.

- [ ] **Step 3: Replace _household_phones and _phone_to_name in accounting_tools.py**

In `orchestrator/app/tools/accounting_tools.py`, replace the two existing helper functions:

```python
# Remove: _household_phones() and _phone_to_name() (the old env-based ones)
```

Replace with:

```python
def _household_phones_from_db(db, group_jid: str) -> set[str]:
    """Return phone numbers of participants with is_household=True for this group."""
    from app.db.models import GroupParticipant
    rows = db.query(GroupParticipant).filter_by(group_jid=group_jid, is_household=True).all()
    return {r.phone for r in rows}


def _phone_to_name_from_db(db, group_jid: str) -> dict[str, str]:
    """Return phone → display name. Household members map to 'Parents'."""
    from app.db.models import GroupParticipant
    rows = db.query(GroupParticipant).filter_by(group_jid=group_jid).all()
    household = {r.phone for r in rows if r.is_household}
    result = {}
    for r in rows:
        name = r.admin_name or r.push_name or r.phone
        result[r.phone] = "Parents" if r.phone in household else name
    return result
```

Also remove the `from app.config import settings` import if it's only used by the old helpers (check if settings is used elsewhere in the file first — if yes, keep it).

- [ ] **Step 4: Update _exec_get_balance and _exec_get_history in accounting_tools.py**

Replace `_exec_get_balance` entirely (the old version calls `_household_phones()` and `_phone_to_name()` before the db block; the new version calls the DB-based helpers inside the db block):

```python
async def _exec_get_balance(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    phone_a = params["phone_a"]
    phone_b = params.get("phone_b")

    def net_vs_group(db, group_jid, from_phone, to_phones):
        owes = sum(_net_owed(db, group_jid, from_phone, cp) for cp in to_phones)
        owed = sum(_net_owed(db, group_jid, cp, from_phone) for cp in to_phones)
        return owes - owed

    with SessionLocal() as db:
        household = _household_phones_from_db(db, group_jid)
        names = _phone_to_name_from_db(db, group_jid)

        def label(phone: str) -> str:
            return names.get(phone, phone)

        if phone_b:
            if phone_a in household and phone_b in household:
                return f"{label(phone_a)} and {label(phone_b)} share a household — no debt tracked between them."
            counterparts_a = household if phone_b in household else {phone_b}
            counterparts_b = household if phone_a in household else {phone_a}
            net = net_vs_group(db, group_jid, phone_a, counterparts_a) if phone_a not in household \
                else -net_vs_group(db, group_jid, phone_b, counterparts_b)
            la, lb = label(phone_a), label(phone_b)
            if net > Decimal("0"):
                return f"{la} owes {lb}: {net:.2f} ILS"
            elif net < Decimal("0"):
                return f"{lb} owes {la}: {(-net):.2f} ILS"
            return f"{la} and {lb} are settled up."

        rows = (
            db.query(LedgerEntry)
            .filter(
                LedgerEntry.group_jid == group_jid,
                or_(LedgerEntry.from_phone == phone_a, LedgerEntry.to_phone == phone_a),
            )
            .all()
        )
        all_partners = {r.from_phone if r.to_phone == phone_a else r.to_phone for r in rows}
        all_partners.discard(phone_a)
        if phone_a in household:
            all_partners -= household
        household_partners = all_partners & household
        individual_partners = sorted(all_partners - household)
        lines = []
        if household_partners and phone_a not in household:
            net = net_vs_group(db, group_jid, phone_a, household_partners)
            la = label(phone_a)
            if net > Decimal("0"):
                lines.append(f"{la} owes Parents: {net:.2f} ILS")
            elif net < Decimal("0"):
                lines.append(f"Parents owe {la}: {(-net):.2f} ILS")
        for partner in individual_partners:
            a_owes = _net_owed(db, group_jid, phone_a, partner)
            p_owes = _net_owed(db, group_jid, partner, phone_a)
            net = a_owes - p_owes
            la, lp = label(phone_a), label(partner)
            if net > Decimal("0"):
                lines.append(f"{la} owes {lp}: {net:.2f} ILS")
            elif net < Decimal("0"):
                lines.append(f"{lp} owes {la}: {(-net):.2f} ILS")
        return "\n".join(lines) if lines else f"No open balances for {label(phone_a)}."
```

Replace `_exec_get_history` entirely:

```python
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
        names = _phone_to_name_from_db(db, group_jid)

    if not rows:
        return "No transactions found."

    lines = []
    for r in rows:
        remaining = r.amount_ils - (r.amount_settled_ils or Decimal("0"))
        status = "settled" if remaining <= Decimal("0") else f"{remaining:.2f} ILS remaining"
        frm = names.get(r.from_phone, r.from_phone)
        to = names.get(r.to_phone, r.to_phone)
        lines.append(
            f"{r.transaction_date} | {frm} → {to} | "
            f"{r.amount_ils:.2f} ILS | {status} | {r.description}"
        )
    return "\n".join(lines)
```

- [ ] **Step 5: Update accounting_export.py to read from DB**

In `orchestrator/app/tools/accounting_export.py`, replace the `_phone_to_name()` function with:

```python
def _phone_to_name_from_db(db, group_jid: str) -> dict[str, str]:
    """Return phone → display name. Household members map to 'Parents'."""
    from app.db.models import GroupParticipant
    rows = db.query(GroupParticipant).filter_by(group_jid=group_jid).all()
    household = {r.phone for r in rows if r.is_household}
    result = {}
    for r in rows:
        name = r.admin_name or r.push_name or r.phone
        result[r.phone] = "Parents" if r.phone in household else name
    return result
```

Update `generate_ledger_xlsx` to call it inside the existing `with SessionLocal() as db:` block:

```python
def generate_ledger_xlsx(group_jid: str) -> bytes:
    with SessionLocal() as db:
        entries = (
            db.query(LedgerEntry)
            .filter_by(group_jid=group_jid)
            .order_by(LedgerEntry.transaction_date)
            .all()
        )
        names = _phone_to_name_from_db(db, group_jid)

    wb = openpyxl.Workbook()
    ws_bal = wb.active
    ws_bal.title = "Balances"
    _write_balances_sheet(ws_bal, entries, names)

    ws_tx = wb.create_sheet("Transactions")
    _write_transactions_sheet(ws_tx, entries, names)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

Remove the old `from app.config import settings` import from accounting_export.py.

- [ ] **Step 6: Run full suite**

```
cd orchestrator && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add orchestrator/app/tools/accounting_tools.py \
        orchestrator/app/tools/accounting_export.py \
        orchestrator/tests/test_participants.py
git commit -m "feat: accounting helpers read participant names and household from DB instead of env"
```

---

## Task 8: Cleanup — family_accounting prompt, seeder, config, .env

**Files:**
- Modify: `orchestrator/app/prompts/family_accounting.py`
- Modify: `orchestrator/app/seeder.py`
- Modify: `orchestrator/app/config.py`
- Modify: `.env`
- Modify: `.env.example`

- [ ] **Step 1: Rewrite family_accounting.py — remove template variables**

Replace the entire contents of `orchestrator/app/prompts/family_accounting.py`:

```python
"""System prompt for the Family Accounting blueprint.

The per-group member list and household configuration are injected dynamically
at inference time by AgentRunner via build_participant_block() in participants.py.
This prompt contains only the static rules that apply to all groups.
"""

FAMILY_ACCOUNTING_SYSTEM_PROMPT = """\
You are a family accounting assistant. You track who paid what for whom, and manage debts and repayments between family members over WhatsApp.

## Rules

1. **Always confirm before recording.** Before calling record_transaction or record_payment, summarize what you understood and ask for confirmation. Example:
   - "Eran שילם 300₪ על ארוחת ערב, מתחלק שווה בין Dana ו-Yael (150₪ כל אחד). לרשום?"

2. **Resolve "I" from sender.** When someone writes "I paid" or "אני שילמתי", use their WhatsApp sender phone as the payer. The sender's phone is provided in context.

3. **Splits are equal by default.** Divide equally unless the user specifies different shares.

4. **Currency defaults to ILS.** If no currency is mentioned, assume ILS.

5. **Reminders are self-only.** The set_reminder tool may only be used for the sender themselves. Never schedule a reminder targeting another person.

6. **Parent description.** When a household member (parent) pays or is paid, always include their first name in the description so the record is clear who acted. Example: "ארוחת ערב (שולם ע"י Eran)" or "Eden paid back Sivan".

7. **Respond in the user's language** — Hebrew or English, matching what they wrote.

8. **Be concise.** After recording, confirm with a short one-line summary.

9. **Admin management.** When an admin (is_admin=True) asks to rename a participant or change household membership, confirm what you understood, then call rename_participant or set_household. Non-admins who request these changes should be told only admins can do this.
"""
```

- [ ] **Step 2: Update seeder.py — remove env-based prompt building**

In `orchestrator/app/seeder.py`:

1. Remove imports:
```python
# Remove these lines:
from app.prompts.family_accounting import build_family_accounting_prompt
from app.config import settings
```

2. Add new import:
```python
from app.prompts.family_accounting import FAMILY_ACCOUNTING_SYSTEM_PROMPT
```

3. Remove helper functions `_family_members()` and `_household_members()` entirely.

4. Replace the family accounting blueprint seeding block. The old block rebuilds the prompt on every startup. Replace with a simple insert-if-not-exists (no more upsert, since the prompt is now static):

```python
    if not db.query(Blueprint).filter_by(id="family_accounting").first():
        db.add(Blueprint(
            id="family_accounting",
            display_name="Family Accounting",
            system_prompt=FAMILY_ACCOUNTING_SYSTEM_PROMPT,
            model="claude-sonnet-4-6",
            tools_enabled=json.dumps(FAMILY_ACCOUNTING_TOOLS),
            max_tool_turns=5,
            context_window=8,
            context_idle_reset_minutes=120,
        ))
```

**Important:** If the DB already has a `family_accounting` blueprint row with the old template-based prompt, this will leave the stale prompt in place. To handle this, use an upsert that sets the prompt unconditionally on startup (same pattern as before):

```python
    fa_bp = db.query(Blueprint).filter_by(id="family_accounting").first()
    if fa_bp:
        fa_bp.system_prompt = FAMILY_ACCOUNTING_SYSTEM_PROMPT
    else:
        db.add(Blueprint(
            id="family_accounting",
            display_name="Family Accounting",
            system_prompt=FAMILY_ACCOUNTING_SYSTEM_PROMPT,
            model="claude-sonnet-4-6",
            tools_enabled=json.dumps(FAMILY_ACCOUNTING_TOOLS),
            max_tool_turns=5,
            context_window=8,
            context_idle_reset_minutes=120,
        ))
```

- [ ] **Step 3: Update config.py — remove family member env vars**

In `orchestrator/app/config.py`, remove these two fields and their comments:

```python
# Remove:
    # Family accounting: JSON object mapping display name → phone
    family_members_json: str = ""

    # Comma-separated names of members who share a single household account
    family_household_members: str = ""
```

- [ ] **Step 4: Update .env**

In `.env`, remove the `FAMILY_MEMBERS_JSON` and `FAMILY_HOUSEHOLD_MEMBERS` lines entirely.

- [ ] **Step 5: Update .env.example**

In `.env.example`, remove the `FAMILY_MEMBERS_JSON` and `FAMILY_HOUSEHOLD_MEMBERS` lines entirely.

- [ ] **Step 6: Run full suite**

```
cd orchestrator && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add orchestrator/app/prompts/family_accounting.py \
        orchestrator/app/seeder.py \
        orchestrator/app/config.py \
        .env \
        .env.example
git commit -m "feat: remove FAMILY_MEMBERS_JSON/FAMILY_HOUSEHOLD_MEMBERS env config — participants now live in DB"
```

---

## After deploy

1. Send `/sync` in the family accounting group — bootstraps all current members.
2. Members' display names fill in automatically as they send messages.
3. To rename: tell the agent "Call 972501234567 'Eran'" — it confirms, then calls `rename_participant`.
4. To set household: "Eran and Sivan share a household account" — agent confirms, calls `set_household` for both.
5. When someone leaves or is removed from the group, their history remains; their entry is marked `(removed)` in the participant block.
