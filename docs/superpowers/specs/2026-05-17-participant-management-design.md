# Participant Management Design

**Goal:** Replace the static `FAMILY_MEMBERS_JSON` / `FAMILY_HOUSEHOLD_MEMBERS` env config with a live, per-group participant roster discovered automatically from WhatsApp events and messages. Admins manage display names and household membership by talking to the agent in plain language.

**Date:** 2026-05-17

---

## Overview

Participants are discovered in two ways: `/sync` bootstraps the full current roster from `groupMetadata()`, and ongoing `group-participants.update` events keep it current automatically. Display names fill in passively as members send messages (`pushName`). Admins can override any name or toggle household membership by asking the family accounting agent directly; the agent confirms before writing.

The member list is injected into the agent's system prompt dynamically at inference time (same pattern as `custom_instructions`), so the blueprint's base prompt is generic and the roster is always fresh from the DB.

---

## Data Model

### New table: `group_participants`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `group_jid` | String FK → `group_registry` | No | Composite PK |
| `phone` | String | No | Digits only, e.g. `972501234567`. Composite PK |
| `push_name` | String | Yes | Auto-updated from WhatsApp on each message |
| `admin_name` | String | Yes | Admin override; takes priority over `push_name` |
| `is_household` | Boolean | No | True = shared "Parents" account. Default false |
| `status` | String | No | `active` or `removed`. Default `active` |
| `joined_at` | DateTime (tz) | No | Set on first upsert |
| `removed_at` | DateTime (tz) | Yes | Set when participant leaves or is removed |

**Display name resolution (in priority order):** `admin_name → push_name → phone`

Ledger entries reference phones directly, so removing a participant has no effect on history.

---

## Bridge Changes

### 1. Add `pushName` to message webhooks

Both text and image payloads gain a `pushName` field:

```javascript
pushName: msg.pushName || ''
```

### 2. Forward `group-participants.update` events

When Baileys emits a participant change, forward to the orchestrator:

```javascript
{
  type: 'participant_update',
  jid,                          // group JID
  action: 'add' | 'remove' | 'leave' | 'demote' | 'promote',
  participants: ['972...@s.whatsapp.net', ...]
}
```

Only `add`, `remove`, and `leave` trigger DB changes in the orchestrator; `demote`/`promote` are forwarded but ignored.

### 3. Extend `GET /group-meta/:jid`

Return participants alongside the description:

```javascript
{
  description: meta.desc || '',
  participants: meta.participants.map(p => ({
    jid: p.id,                  // e.g. 972501234567@s.whatsapp.net
    isAdmin: p.admin === 'admin' || p.admin === 'superadmin'
  }))
}
```

---

## Orchestrator Changes

### Migration 008

Creates `group_participants` with all columns above.

### New ORM model: `GroupParticipant`

Mirrors the table. Composite primary key `(group_jid, phone)`.

### Webhook handler additions (`main.py`)

**On `type: 'participant_update'`:**
- `add`: upsert participant — set `status=active`, `removed_at=None`. Set `joined_at` only on first insert.
- `remove` / `leave`: set `status=removed`, `removed_at=now`. Do not delete the row.
- Other actions: no DB change.

**On every message (text/image):**
Upsert the sender's `push_name` if provided — but only if `admin_name` is not already set.

### `/sync` extended

After storing the group description, iterate the `participants` array returned by the updated `/group-meta/:jid` endpoint and upsert each member: `status=active`, `joined_at=now` on first insert. This bootstraps the full roster on first sync.

### Dynamic prompt injection (`AgentRunner`)

At inference time, `AgentRunner` queries `group_participants` for all rows matching `group_jid` (any status — removed members are included so the agent can still reference them by name). It builds a member-list block:

```
Family members:
- Eran: 972501234567 [household]
- Sivan: 972509876543 [household]
- Eden: 972521111111
- (removed) Tomer: 972522222222
```

This is injected as a system block between the base prompt and `custom_instructions`. The family accounting blueprint's base `system_prompt` no longer contains `{member_list}` or `{household_section}` template variables — those are removed and replaced by the dynamic block.

### New accounting tools

Both tools require admin caller; both use the existing `request_confirmation` pattern before writing.

**`rename_participant(phone, name)`**
Sets `admin_name` on the matching `GroupParticipant` row. If `name` is empty string, clears `admin_name` (reverts to `push_name`).

**`set_household(phone, is_household)`**
Toggles `is_household` on the matching row.

Example interactions:
- Admin: "קרא ל-972501234567 'Eran'" → agent confirms → calls `rename_participant`
- Admin: "Eran and Sivan share a household account" → agent confirms → calls `set_household` for both

### Config/seeder cleanup

- Remove `family_members_json` and `family_household_members` from `config.py`
- Remove `_family_members()`, `_household_members()` helpers from `seeder.py`
- Remove `build_family_accounting_prompt()` call from seeder; family accounting blueprint gets a generic base prompt with no member-list template
- Remove `FAMILY_MEMBERS_JSON` and `FAMILY_HOUSEHOLD_MEMBERS` from `.env` and `.env.example`

---

## Accounting tools affected

`accounting_tools.py` currently reads household phones and display names from `FAMILY_MEMBERS_JSON` / `FAMILY_HOUSEHOLD_MEMBERS` via config. These helpers are replaced:

- `_household_phones(group_jid, db)` — queries `group_participants` where `is_household=True` for the given group
- `_phone_to_name(phone, group_jid, db)` — resolves `admin_name ?? push_name ?? phone`; household members resolve to `"Parents"`

All tool functions that currently take no `group_jid` argument will need it added (passed in from tool execution context).

---

## Testing

- Migration + ORM: insert/fetch/update participant rows, verify column defaults
- Webhook handler: `add` upserts correctly, `remove`/`leave` sets status without deleting, `push_name` update skips rows with `admin_name` set
- `/sync` bootstrap: mock bridge response with participants array, verify upsert
- Dynamic prompt block: verify member list appears in system blocks at inference time
- `rename_participant` / `set_household`: confirm flow, DB update, revert behavior
- `accounting_tools` helpers: verify household aggregation and name resolution from DB
