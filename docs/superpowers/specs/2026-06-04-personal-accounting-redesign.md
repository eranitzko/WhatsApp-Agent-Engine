# Personal Accounting Redesign — Design Spec

**Date:** 2026-06-04
**Status:** Approved

---

## Overview

Redesign the `family_accounting` blueprint from a single shared-group model to a per-user private model. Each user interacts with the bot through their own personal WhatsApp group (1 user + bot). Shared groups (2+ registered users + bot) are supported as a convenience. A central `AccountService` handles cross-group routing, permissions, and confirmation lifecycle — the FIFO/FX/ledger data layer stays untouched.

---

## Goals

- Each user sees and manages only their own ledger, privately
- Transactions that affect another user require their explicit confirmation
- Self-reporting (taking on debt, acknowledging payment received) is trusted immediately
- Sys-admins have system-wide visibility and control via their own personal group
- Self-service group onboarding: users create the group, sys-admin approves
- Split bills supported with equal or custom splits; one decline suspends the whole transaction

---

## Section 1: Data Model

### 1.1 New table: `user_accounts`

Maps a phone number to a registered WhatsApp group. A phone can have one `owner` row (personal group) and multiple `member` rows (shared groups).

```sql
CREATE TABLE user_accounts (
    id          TEXT PRIMARY KEY,           -- UUID
    phone       TEXT NOT NULL,              -- e.g. "972501234567"
    group_jid   TEXT NOT NULL REFERENCES group_registry(group_jid),
    role        TEXT NOT NULL,              -- "owner" | "member"
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (phone, group_jid)
);
```

Constraint enforced at application level: one `owner` row per phone.

---

### 1.2 Change: `group_registry.group_type`

New column added via migration:

| Value | Meaning |
|---|---|
| `personal` | 1 registered user + bot |
| `shared` | 2+ registered users + bot |
| `sys_admin` | sys-admin phone + bot; elevated permissions active |
| `unregistered` | Bot joined but not yet approved by sys-admin |

---

### 1.3 New table: `cross_group_confirmations`

Persists 2nd-party and split-bill confirmation state across groups. Replaces the in-memory `ConfirmationStore` for cross-group flows (in-group confirmations for other blueprints remain unchanged).

```sql
CREATE TABLE cross_group_confirmations (
    id                   TEXT PRIMARY KEY,   -- UUID
    split_transaction_id TEXT REFERENCES split_transactions(id),  -- nullable
    initiator_phone      TEXT NOT NULL,
    initiator_group_jid  TEXT NOT NULL,
    target_phone         TEXT NOT NULL,
    target_group_jid     TEXT NOT NULL,
    action_type          TEXT NOT NULL,      -- "record_expense" | "record_debt" | "split_share"
    action_payload       TEXT NOT NULL,      -- JSON
    status               TEXT NOT NULL DEFAULT 'pending',  -- pending | confirmed | rejected | timed_out
    expires_at           DATETIME NOT NULL,
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

Timeout window is configurable via admin Settings panel (default: 24 hours).

---

### 1.4 New table: `split_transactions`

Groups the confirmation rows for a single split bill. Controls suspension logic.

```sql
CREATE TABLE split_transactions (
    id                  TEXT PRIMARY KEY,   -- UUID
    reporter_group_jid  TEXT NOT NULL,
    reporter_phone      TEXT NOT NULL,
    payer_phone         TEXT NOT NULL,
    total_amount        REAL NOT NULL,
    description         TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending | confirmed | suspended | cancelled
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

### 1.5 Existing tables — unchanged

`ledger_entries`, `ledger_settlements`, `scheduled_messages` — no schema changes.

`user_profiles` (migration 009) — extended in the admin UI to expose display name editing, but no schema change required.

---

## Section 2: AccountService

New file: `orchestrator/app/accounting/account_service.py`

A coordination and routing layer. Does not own ledger math — that stays in `accounting_fifo.py` and `accounting_fx.py`. Does not define tool schemas — those stay in `accounting_tools.py`.

### 2.1 Responsibilities

| Responsibility | Description |
|---|---|
| User resolution | Given `group_jid` → member phones. Given `phone` → personal group JID. |
| Group type lookup | Classify any `group_jid` as `personal`, `shared`, `sys_admin`, or `unregistered` |
| Permission check | Given `(phone, group_jid, action)` → allowed / denied |
| Cross-group routing | Look up target phone's group JID, dispatch message via `bridge_client` |
| Confirmation lifecycle | Create `CrossGroupConfirmation` row, send request, handle reply, timeout |
| Split transaction management | Create `SplitTransaction` + linked confirmations; handle suspension |

### 2.2 Interface

```python
class AccountService:
    async def resolve_user(self, phone: str) -> UserAccount | None
    async def resolve_group_owner(self, group_jid: str) -> str | None
    async def get_group_members(self, group_jid: str) -> list[str]
    async def get_display_name(self, phone: str) -> str          # falls back to phone
    async def is_sys_admin(self, phone: str) -> bool
    async def get_group_type(self, group_jid: str) -> str

    async def notify_user(self, target_phone: str, message: str) -> None

    async def request_confirmation(
        self,
        initiator_phone: str,
        initiator_group_jid: str,
        target_phone: str,
        action_type: str,
        action_payload: dict,
        confirmation_message: str,
        split_transaction_id: str | None = None,
    ) -> CrossGroupConfirmation

    async def handle_confirmation_reply(
        self,
        group_jid: str,
        phone: str,
        reply: str,     # "yes" | "no"
    ) -> bool           # True if a pending confirmation was resolved

    async def create_split_transaction(
        self,
        reporter_phone: str,
        reporter_group_jid: str,
        payer_phone: str,
        total_amount: float,
        description: str,
        shares: list[dict],   # [{"phone": ..., "amount": ...}, ...]
    ) -> SplitTransaction
```

### 2.3 Sys-admin permission scoping

Elevated permissions activate **only** when the sys-admin is messaging from their `sys_admin` group. The same phone in a personal group operates as a regular user. This prevents accidental privileged actions in casual use.

---

## Section 3: Group Registration Flow

### 3.1 Bot added to a group (group-join event)

```
Bot added to group
    │
    ├─ 0 human members → leave silently
    │
    ├─ 1 member
    │   ├─ Phone in admin_numbers?
    │   │   YES → register immediately as sys_admin; welcome message sent
    │   │   NO  → register as unregistered; notify all sys-admins:
    │   │           "[Name] added me to a group. Register as their personal account? yes / no"
    │
    ├─ 2+ members
    │   ├─ All phones in user_accounts (registered)?
    │   │   YES → notify sys-admins:
    │   │           "[Name] and [Name] created a shared group. Register it? yes / no"
    │   │   NO  → register as unregistered; notify sys-admins with unregistered phone(s) listed
    │
    └─ Any group with an unregistered member after registration:
            Bot stays in group but refuses accounting requests.
            On first accounting message: "I can't process requests until all members have
            a registered account. [Name] needs to register, or leave this group."
            After that: silent on accounting requests unless directly asked.
```

### 3.2 Sys-admin confirmation

Confirmation request goes to **all** sys-admin groups simultaneously. First to reply wins; others receive: "Already handled by [Name]."

- **yes** → `group_registry` row created with correct `group_type`; `user_accounts` row(s) created; welcome message sent to new group
- **no** → bot sends "This group was not approved." and leaves
- **timeout** (same configurable window) → bot leaves silently; sys-admins notified

### 3.3 Bootstrap

`ADMIN_PHONE_NUMBER` env var seeds `admin_numbers` at startup. A personal group for that phone auto-registers on first join — no confirmation required for phones already in `admin_numbers`.

---

## Section 4: Transaction Flow

### 4.1 Transaction type classification

The rule: **actions that hurt your own position are self-authorizing; actions that benefit you at someone else's expense require their consent.**

| What the user says | Type | Ledger effect on sender |
|---|---|---|
| "I owe Eran ₪200" | 1st-party (notify only) | Sender takes on debt |
| "Eran paid for me ₪200" | 1st-party (notify only) | Sender acknowledges debt to Eran |
| "I received ₪200 from Tal" | 1st-party (notify only) | Sender reduces own credit |
| "Eran owes me ₪200" | 2nd-party (confirm required) | Sender claims credit at Eran's expense |
| "I paid ₪200 for Eden" | 2nd-party (confirm required) | Sender claims credit at Eden's expense |
| "I paid ₪200 with Eden and Tal" | Split bill | Hybrid — see 4.3 |

### 4.2 1st-party flow

```
User: "Eran paid ₪150 for me"
  → AccountService writes ledger entry immediately
  → AccountService.notify_user(eran,
       "[Name] acknowledged that you paid ₪150 for them. Your balance has been updated.")
  → Bot to sender: "Recorded. Eran has been notified."
```

### 4.3 2nd-party flow

```
User: "Tal owes me ₪200 for dinner"
  → CrossGroupConfirmation row created (pending, expires_at = now + timeout)
  → Bot sends to Tal's group: "[Name] says you owe them ₪200 (dinner). Confirm? yes / no"
  → Bot to sender: "Confirmation request sent to Tal."

On yes  → ledger entry written; both parties notified
On no   → no entry; both parties notified
Timeout → both parties notified; row marked timed_out
```

### 4.4 Split bill flow

Any participant can report the split. The payer is identified explicitly; the payer's share is absorbed (no ledger entry for the payer). Each non-payer participant's transaction type depends on their relationship to the **reporter**:

- Reporter's own share (reporter ≠ payer): **1st-party** — reporter is acknowledging their own debt
- Other non-payer participants' shares: **2nd-party** — reporter is creating debt on their behalf

**Split amount rules:**
- **Default:** equal split among all participants; payer absorbs rounding remainder
- **Override:** explicit amounts given per participant; payer absorbs remainder (`total - sum of shares`)
- **Partial override:** specified participants get their amounts; remainder split equally among unspecified non-payer participants; payer absorbs rounding
- **Validation:** sum of specified shares must not exceed total; agent flags and asks to correct if exceeded

**Example — reporter is not the payer:**
```
Eden says: "Eran paid ₪200 at the restaurant for me and Tal. Tal owes 80."
  Participants: Eran (payer, absorbed), Eden (reporter), Tal
  Tal's share: ₪80 (explicit)
  Eden's share: ₪200 - ₪80 = ₪120 (remainder, since only Tal's share was explicit)
  Payer absorbs: total - sum_of_others = 200 - 80 - 120 = 0 (nothing left; split is exact)

  Eden's ₪120: 1st-party → written as pending; Eran notified
  Tal's ₪80:   2nd-party → confirmation request sent to Tal

  Both shares held in pending state until split resolves.
```

**One decline suspends the entire split:**
```
Tal declines ₪80:
  → split_transactions.status = suspended
  → All other pending confirmations for this split are paused
  → All participants notified:
      Eran: "Tal declined their share of the ₪200 restaurant bill. Transaction suspended."
      Eden: "Tal declined. The split is suspended — re-submit if you agree on new amounts."
      Tal:  "You declined your share. Let [Eden] know how to proceed."

Suspended ≠ cancelled:
  → Eden can re-submit with corrected amounts (new split_transaction created)
  → Or explicitly cancel
  → Reporter's 1st-party share (Eden's ₪120) was held in pending state and is rolled back on cancellation
  → No automatic retry; manual resolution required
```

### 4.5 Duplicate detection

When a 2nd-party entry is submitted, `AccountService` checks for an open ledger entry matching same counterparty pair + approximate amount + ±1 day window.

If found: *"This looks like it may already be recorded. Here's the existing entry — is this the same transaction? yes / no"*
- yes → no-op
- no → new confirmation request proceeds normally

---

## Section 5: Permission Model

Three tiers resolved at routing time:

| Tier | Activation | Capabilities |
|---|---|---|
| **User** | Registered phone in personal/shared group | Record own transactions, query own balance, confirm/deny 2nd-party requests, manage own automations |
| **Sys-admin** | `admin_numbers` phone messaging from `sys_admin` group | All user capabilities + view any ledger, record/settle on behalf of any user, approve group registrations, force-cancel any pending confirmation, manage user profiles |
| **Unregistered** | Phone not in `user_accounts` | No accounting interaction. Bot responds once with registration instructions, then silent |

---

## Section 6: Admin Panel Changes

Panel renamed (e.g. "Control Panel").

### New: Users page

- Lists all phones from `user_accounts` + `admin_numbers`
- Editable display name per phone (stored in `user_profiles`)
- Shows personal group JID and registration date
- Sys-admins can deregister a user (removes `user_accounts` rows, bot leaves their groups)

### New: Settings page

Configurable system-wide values stored in `system_config` KV table (already exists):

| Setting key | Default | Description |
|---|---|---|
| `cross_group_confirmation_timeout_hours` | 24 | Window for 2nd-party and split confirmations |
| `group_registration_timeout_hours` | 24 | Window for sys-admin group approval |

---

## Section 7: Scheduler Changes

Two new jobs added alongside existing automation scheduler jobs:

| Job | Interval | Description |
|---|---|---|
| `_expire_cross_group_confirmations` | 60 min | Scans `pending` rows past `expires_at`; flips to `timed_out`; notifies both parties |
| `_expire_split_transactions` | 60 min | Scans `pending` split transactions where all confirmations are expired; flips to `suspended`; notifies all participants |

---

## Section 8: Delta from Current Design

### Unchanged

| Component | Notes |
|---|---|
| `accounting_fifo.py` | Untouched |
| `accounting_fx.py` | Untouched |
| `ledger_entries`, `ledger_settlements` tables | No schema changes |
| `AgentRunner` | Untouched |
| `AutomationEngine` | Automations attach to `group_jid` as before |
| Bridge / Baileys | Untouched |
| `confirmation.py` (in-memory) | Kept for other blueprints; cross-group flows use new persistent table |

### Modified

| Component | Change |
|---|---|
| `group_registry` | + `group_type` column (migration) |
| `user_profiles` | Wired into admin panel Users page |
| `accounting_tools.py` | Tools call `AccountService`; new `record_split` tool added |
| `command_handler.py` | Group-join event routed here; sys-admin approval flow added |
| Admin panel | Renamed; + Users page; + Settings page |
| `family_accounting` system prompt | Rewritten for personal-group context; teaches Claude 1st/2nd-party distinction, split bill syntax |

### New

| Component | Description |
|---|---|
| `user_accounts` table | Phone → group_jid(s) with role |
| `cross_group_confirmations` table | Persistent cross-group confirmation state |
| `split_transactions` table | Groups split-bill confirmations; suspension logic |
| `orchestrator/app/accounting/account_service.py` | Central coordination service |
| Scheduler: confirmation timeout job | Hourly expiry scan |
| Scheduler: split transaction cleanup job | Hourly expiry scan |
| Admin panel: Users page | Name/phone registry with deregistration |
| Admin panel: Settings page | Configurable timeout windows |

---

## Out of Scope (v1)

- Group-to-group balance settlement (e.g. a shared group settling net balances automatically)
- Audit log of sys-admin actions
- Multi-currency per-user preference (FX conversion stays as-is)
- Export from personal group (deferred; sys-admin can export via their group)
