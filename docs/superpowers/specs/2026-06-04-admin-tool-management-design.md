# Admin Panel Tool Management — Design Spec

**Date:** 2026-06-04
**Status:** Approved

## Overview

Add two panels to the admin web UI that let the sysadmin wire tools to blueprints and globally enable/disable tools — without touching code or restarting the server.

---

## Goals

- **Blueprints panel** (enhanced): inline expandable tool editor per blueprint — click chips to add/remove tools, save to DB
- **Tools Registry panel** (new): full table of all registered tools with sortable columns, category filter tabs, global on/off toggle per tool, remove-from-all-blueprints action
- No code deploy required to change which tools a blueprint can use
- UI changes survive server restarts (seeder must not overwrite admin-set tools_enabled)

---

## Section 1: Data Model Changes

### 1a. `SystemConfig` table — `disabled_tools` key

A JSON list stored in the existing `SystemConfig` table under key `"disabled_tools"`. Controls which tools are globally suppressed regardless of blueprint settings.

```
key: "disabled_tools"
value: '["flag_invoice", "search_pages"]'   # JSON list of tool names
```

`AgentRunner.run()` reads this list and removes any matching names from `allowed_tools` before building tool schemas. Tools in the list still appear in the UI (greyed out) so the sysadmin can re-enable them.

### 1b. Tool `category` field in schemas

Each tool registration dict adds an optional `"category"` key to its schema. The `GET /admin/api/tools` endpoint reads this to populate the filter tabs.

```python
# Example — add to each get_*_tools() schema:
"category": "accounting"   # accounting | invoices | automation | notion | other
```

Existing tool files (`automation_tools.py`, `accounting_tools.py`, `invoice_tools.py`, `notion_tools.py`) each add the appropriate `"category"` to every schema dict.

### 1c. Seeder — stop overwriting `tools_enabled` on restart

The seeder currently overwrites `tools_enabled` on every startup, which would silently undo any UI changes. Fix: on blueprint **update** paths, update only `system_prompt` and `model` — never `tools_enabled`.

`tools_enabled` is only written on first **creation** of a blueprint row. Subsequent startups leave it untouched so admin UI changes persist.

---

## Section 2: Tool Registry Access from Admin API

The `tool_registry` is initialized in `main.py`'s lifespan. The admin API needs to read from it (to list all tools and validate tool names). A thin module-level reference solves this without circular imports:

**New file: `orchestrator/app/registry_ref.py`**
```python
"""Shared reference to the live ToolRegistry. Set by main.py at startup."""
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.tool_registry import ToolRegistry

_registry: "ToolRegistry | None" = None

def set_registry(r: "ToolRegistry") -> None:
    global _registry
    _registry = r

def get_registry() -> "ToolRegistry":
    if _registry is None:
        raise RuntimeError("ToolRegistry not yet initialised")
    return _registry
```

`main.py` calls `registry_ref.set_registry(tool_registry)` in lifespan after registering all tools. The admin API imports `get_registry()` to access the live tool list.

---

## Section 3: New Admin API Endpoints

All endpoints require `Authorization: Bearer <token>` (existing `require_auth` dependency).

### `GET /admin/api/tools`

Returns all tools currently registered in the live `ToolRegistry`.

**Response:**
```json
[
  {
    "name": "record_transaction",
    "description": "Record that someone paid for others",
    "category": "accounting",
    "blueprints_using": ["family_accounting"],
    "globally_enabled": true
  },
  ...
]
```

`globally_enabled` is `true` unless the tool name appears in `SystemConfig["disabled_tools"]`.
`blueprints_using` is computed by scanning all `Blueprint.tools_enabled` JSON arrays.

### `PATCH /admin/api/blueprints/{id}/tools`

Update the `tools_enabled` list for a blueprint. Validates that every tool name in the request exists in the live registry.

**Request body:**
```json
{ "tools_enabled": ["record_transaction", "get_balance", "create_automation"] }
```

**Response:** `{"ok": true}`

**Error:** 400 if any tool name is not registered; 404 if blueprint not found.

### `PATCH /admin/api/tools/{name}/enabled`

Toggle global enabled/disabled for a tool. Reads and updates `SystemConfig["disabled_tools"]`.

**Request body:** `{"enabled": false}`

**Response:** `{"ok": true}`

**Error:** 404 if tool not in registry.

### `DELETE /admin/api/tools/{name}/blueprints`

Remove a tool from all blueprint `tools_enabled` arrays in the DB. Does not remove it from the live registry (tools stay registered in code). Useful for cleaning up tools that shouldn't be accessible to any agent.

**Response:** `{"ok": true, "blueprints_updated": ["family_accounting", "invoice_curator"]}`

---

## Section 4: AgentRunner — Global Disable Filter

In `AgentRunner.run()`, after reading `blueprint.tools_list()`, filter out globally disabled tools:

```python
from app.db.session import SessionLocal
from app.db.models import SystemConfig
import json

# Read disabled tools from DB (cached per request — cheap)
with SessionLocal() as db:
    row = db.get(SystemConfig, "disabled_tools")
    disabled = set(json.loads(row.value)) if row else set()

allowed_tools = [t for t in blueprint.tools_list() if t not in disabled]
```

This filter runs on every message. Since `SystemConfig` is a simple key-value lookup by PK, it is fast.

---

## Section 5: Frontend Changes

### 5a. Navigation

Add `🔧 Tools` to the nav array in `layout()` (sidebar + mobile bottom nav), pointing to `#tools`. Update `route()` to call `renderTools(app)` for `hash === 'tools'`.

### 5b. Enhanced Blueprints panel

`renderBlueprints()` fetches `/admin/api/blueprints` (existing) and `/admin/api/tools` (new) in parallel.

Each blueprint row gets a **🔧 Edit tools** button. Clicking expands an inline row beneath it (matching existing expand pattern) showing:
- All registered tools as clickable chips
- Blue chip = currently in `tools_enabled` for this blueprint
- Grey strikethrough chip = not enabled
- **Save changes** button → PATCH `/admin/api/blueprints/{id}/tools` with current blue chips

The existing system prompt expand/collapse remains unchanged.

### 5c. New Tools Registry panel

`renderTools(app)` fetches `/admin/api/tools`.

**Table columns (all sortable — click header to toggle asc/desc, arrow indicator):**
- Tool name + description (two lines)
- Category (badge)
- Used by (blueprint pills)
- Enabled (toggle switch) — fires PATCH `/admin/api/tools/{name}/enabled`
- Remove button → DELETE `/admin/api/tools/{name}/blueprints` with confirm dialog

**Category filter tabs** above the table (All, Accounting, Invoices, Automation, Notion). Tab click re-filters client-side — no extra API call.

**Sorting** is fully client-side on the fetched data.

---

## File Map

| Action | Path |
|---|---|
| Create | `orchestrator/app/registry_ref.py` |
| Modify | `orchestrator/app/admin/api.py` — 4 new endpoints |
| Modify | `orchestrator/app/agent_runner.py` — global disable filter |
| Modify | `orchestrator/app/main.py` — call `registry_ref.set_registry()` at startup |
| Modify | `orchestrator/app/seeder.py` — stop overwriting `tools_enabled` on update |
| Modify | `orchestrator/app/tools/automation_tools.py` — add `"category": "automation"` to each schema |
| Modify | `orchestrator/app/tools/accounting_tools.py` — add `"category": "accounting"` to each schema |
| Modify | `orchestrator/app/tools/invoice_tools.py` — add `"category": "invoices"` to each schema |
| Modify | `orchestrator/app/tools/notion_tools.py` — add `"category": "notion"` to each schema |
| Modify | `orchestrator/app/static/admin/app.js` — new Tools panel, enhanced Blueprints panel |
| Modify | `orchestrator/app/static/admin/style.css` — chip, toggle, sortable-th styles |
| Create | `orchestrator/tests/test_admin_tool_management.py` |

---

## Out of Scope

- Creating or deleting blueprints from the UI
- Editing system prompts via the Tools panel
- Per-group tool overrides (tools are set at blueprint level, not group level)
- Tool discovery beyond what's registered in the live ToolRegistry at startup
