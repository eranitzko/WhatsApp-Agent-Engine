# Email Allowlist — Design Spec

**Date:** 2026-06-08  
**Status:** Approved

## Summary

Replace the `REPORT_EMAIL_ALLOWLIST` env var with a persistent DB-backed allowlist. Admins manage entries through a Settings sub-panel. People's emails auto-sync into the list. The existing email field on the People edit modal is already wired end-to-end; this feature makes it meaningful.

---

## Current State

| Component | Current |
|---|---|
| `user_profiles.email` | Column exists in DB (migration 009) and ORM |
| `patch_person` | Already writes `profile.email` |
| People edit modal | Has email input, sends via PATCH |
| `GET /people` | Does **not** return `email` → edit modal always pre-fills blank |
| `_is_allowed()` | Reads `REPORT_EMAIL_ALLOWLIST` env var (comma-separated, no display names) |

---

## Schema Change — Migration 013

New table `email_allowlist`:

```sql
CREATE TABLE email_allowlist (
    email        TEXT PRIMARY KEY,
    display_name TEXT,
    created_at   DATETIME NOT NULL DEFAULT (now())
);
```

No changes to existing tables.

---

## ORM Model

```python
class EmailAllowlist(Base):
    __tablename__ = "email_allowlist"
    email        = Column(String, primary_key=True)
    display_name = Column(String, nullable=True)
    created_at   = Column(DateTime(timezone=True), nullable=False,
                          server_default=sa.func.now())
```

---

## Backend Changes

### `send_email_tool.py` — `_is_allowed()`

Query `email_allowlist` table instead of reading the env var.

- If table is **empty** → allow all (preserves current open-by-default behaviour).
- If table has **any entries** → recipient must be in the list.
- `REPORT_EMAIL_ALLOWLIST` env var is ignored (deprecated).

### `GET /people` — include email

Add `"email": profile.email if profile else None` to each person object in the response. Fixes the pre-fill gap in the edit modal.

### Auto-sync in `PATCH /people/{phone}`

When `body.email` is provided and non-empty:
- Upsert into `email_allowlist(email, display_name)` using the person's `display_name` from the same request (or existing `UserProfile.display_name`, falling back to their phone number).

When `body.email` is `None` or `""`:
- If the person had a previous email stored, delete that email from `email_allowlist`.

### New API endpoints — `/settings/email-allowlist`

| Method | Path | Description |
|---|---|---|
| `GET` | `/settings/email-allowlist` | List all entries: `[{email, display_name, created_at}]` |
| `POST` | `/settings/email-allowlist` | Add entry: `{email, display_name?}`. 409 if already exists. |
| `DELETE` | `/settings/email-allowlist/{email}` | Remove entry. 404 if not found. |

All three require auth (`Depends(require_auth)`).

---

## UI — Settings Sub-panel

**Location:** Below the existing settings form on the Settings page.

**Structure:**

```
─── Email Allowlist ──────────────────────────────────────

  Display Name        Email                   
  ──────────────────  ──────────────────────  ──
  Eran                eran@example.com        ✕
  Accountant          acc@firm.com            ✕

  [ Display Name     ] [ Email address        ] [ Add ]
```

- **Remove (✕):** calls `DELETE /settings/email-allowlist/{email}`, refreshes list inline.
- **Add row:** validates email is non-empty and valid format before submitting. Calls `POST /settings/email-allowlist`. Clears inputs on success.
- Display name is optional in the add form (can be blank).
- Empty state: "No addresses in the allowlist — all recipients are permitted."

---

## Behaviour Summary

| Scenario | Result |
|---|---|
| Allowlist table empty | Any recipient is permitted (open) |
| Allowlist has entries | Only listed emails pass |
| Person email set via People panel | Auto-upserted into allowlist |
| Person email cleared via People panel | Removed from allowlist |
| Manual add via Settings sub-panel | Added to allowlist (not tied to any person) |
| Manual remove via Settings sub-panel | Removed regardless of source |

---

## Out of Scope

- Seeding existing `REPORT_EMAIL_ALLOWLIST` env var values into the DB (manual migration if needed).
- Per-group allowlists.
- Email validation on the backend (frontend validates format; backend accepts any string).
