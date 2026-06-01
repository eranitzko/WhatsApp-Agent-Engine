# Admin Panel Design

**Date:** 2026-05-31
**Status:** Approved

## Overview

A password-protected web admin panel for managing the WhatsApp Agent Engine. Served by the existing orchestrator container at `/admin`. Built with plain HTML + vanilla JS — no build step, no separate service.

## Scope

**In scope:**
- Group registration and removal
- Admin phone number management
- Blueprint listing (read-only)
- Password-protected access via JWT

**Out of scope (future):**
- Blueprint creation/editing
- Ledger/transaction viewing
- Participant management
- Per-group admin management

## Architecture

The panel lives entirely inside the orchestrator:

```
orchestrator/
  app/
    admin/
      __init__.py
      router.py        # FastAPI router, mounted at /admin
      auth.py          # Password check, JWT issue/verify
      api.py           # REST endpoints at /admin/api/*
    static/
      admin/
        index.html     # Single-page app shell
        app.js         # Vanilla JS — routing, API calls, renders
        style.css      # Dark theme matching mockup
```

`router.py` is mounted on the main FastAPI app in `main.py`:
```python
app.mount("/admin", admin_router)
```

## Auth

- `ADMIN_UI_PASSWORD` env var (added to `.env` and `config.py`)
- `POST /admin/api/login` accepts `{ password }`, returns a signed JWT (24h expiry) if correct
- JWT secret derived from `ADMIN_UI_PASSWORD` via SHA-256
- All `/admin/api/*` routes require `Authorization: Bearer <token>`
- Frontend stores JWT in `localStorage`, redirects to login page on 401

## Pages

### Groups
- Lists all rows in `GroupRegistry` with group name (fetched from bridge) and blueprint display name
- **Register Group** button opens a modal:
  - Dropdown: groups the bot is currently in (`GET /admin/api/bridge-groups`) minus already-registered ones
  - Dropdown: available blueprints from `Blueprint` table
  - Submit writes to `GroupRegistry`
- **Remove** button deletes the `GroupRegistry` row (bot stops responding in that group immediately)

### Admins
- Lists all rows in `AdminNumbers`
- **Add Admin** — text input for phone number (digits only, e.g. `972501234567`), writes to `AdminNumbers`
- **Remove** button deletes the row

### Blueprints
- Read-only list of `Blueprint` rows
- Shows: display name, number of tools enabled, system prompt preview (first 100 chars)

## API Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/admin/api/login` | Verify password, return JWT |
| GET | `/admin/api/groups` | List registered groups with blueprint info |
| POST | `/admin/api/groups` | Register a group `{ group_jid, blueprint_id }` |
| DELETE | `/admin/api/groups/{jid}` | Remove a group |
| GET | `/admin/api/bridge-groups` | Proxy bridge `/groups`, filter out registered ones |
| GET | `/admin/api/admins` | List admin phone numbers |
| POST | `/admin/api/admins` | Add admin `{ phone_number }` |
| DELETE | `/admin/api/admins/{phone}` | Remove admin |
| GET | `/admin/api/blueprints` | List blueprints (read-only) |

## Frontend

Single HTML file (`index.html`) with inline routing via `location.hash`:
- `#login` — login form
- `#groups` — groups page (default after login)
- `#admins` — admins page
- `#blueprints` — blueprints page

`app.js` handles all rendering and API calls. No frameworks, no bundler. Dark theme matching the mockup design.

## Configuration

Add to `.env` and `config.py`:
```
ADMIN_UI_PASSWORD=<choose a password>
```

If `ADMIN_UI_PASSWORD` is not set, the `/admin` routes return 503 with a message explaining the variable needs to be set.

## Deployment

No Docker changes needed. The static files are copied into the orchestrator image as part of the existing `COPY app/ ./app/` step. Restart orchestrator to pick up changes.

## Testing

- Unit tests for `auth.py` (valid/invalid password, expired token)
- Unit tests for each API endpoint (auth required, correct DB writes/reads)
- No frontend tests (vanilla JS, too simple to warrant it)
