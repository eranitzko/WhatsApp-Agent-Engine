# WhatsApp Agent Engine — Design Spec
**Date:** 2026-05-15
**Status:** Approved

---

## Overview

A personal, multi-role WhatsApp AI agent platform running on a single WhatsApp number. The system dynamically selects agent behavior (persona, tools, system prompt) based on which WhatsApp group a message originates from. The first two agents are the existing Invoice Curator (migrated in) and a new Notion productivity assistant.

**Core goals:**
- Route incoming WhatsApp messages to the correct agent blueprint per group
- Enable Claude tool-use agents with isolated context per group
- Start with 1-3 groups; designed to scale to 4-10 with no architectural changes
- Deploy on a Hetzner CX22 VM via Docker Compose

---

## 1. Architecture

### Three-layer stack

```
WhatsApp Groups
      │
      ▼
┌─────────────────────────────┐
│   Baileys Bridge (Node.js)  │  ← reused from Invoice Curator, unchanged
│   WebSocket + Express HTTP  │
└────────────┬────────────────┘
             │ POST /webhook (text | image | command)
             ▼
┌─────────────────────────────────────────────────────┐
│            FastAPI Orchestrator (Python)             │
│                                                     │
│  ┌──────────┐   ┌──────────────┐   ┌─────────────┐ │
│  │  Router  │──▶│  Blueprint   │──▶│ Agent Runner│ │
│  │          │   │  Resolver    │   │             │ │
│  └──────────┘   └──────────────┘   └──────┬──────┘ │
│                                           │        │
│  ┌──────────────────────────────────────┐ │        │
│  │           Tool Registry             │◀┘        │
│  │  [invoice_tools] [notion_tools] ... │           │
│  └──────────────────────────────────────┘          │
│                                                     │
│  ┌──────────────┐   ┌───────────────────────────┐  │
│  │  SQLite DB   │   │  In-Memory Context Store  │  │
│  │              │   │  (chat history per group) │  │
│  └──────────────┘   └───────────────────────────┘  │
└─────────────────────────────────────────────────────┘
             │
             ▼
     Bridge /send or /send-file
```

### Processes

| Process | Runtime | Role |
|---------|---------|------|
| `bridge` | Node.js | Baileys WebSocket connection; forwards events to orchestrator; sends responses back to WhatsApp |
| `orchestrator` | Python 3.12 + FastAPI | Blueprint routing, Agent Runner, Tool Registry, DB |

The Invoice Curator is **not** a separate service. Its pipeline, agent tools, and DB tables live inside the orchestrator as the `invoice_curator` blueprint.

---

## 2. Blueprint System

A **Blueprint** fully defines agent behavior for a group. The orchestrator has no hardcoded personas.

### Blueprint structure

```python
{
  "id": "invoice_curator",              # unique slug
  "display_name": "Invoice Curator",
  "system_prompt": "...",               # full system prompt (cached)
  "model": "claude-sonnet-4-6",
  "tools_enabled": ["get_status", ...], # subset of Tool Registry
  "trigger_type": "always",             # always | mention | prefix
  "trigger_prefix": None,               # e.g. "!bot" if trigger_type=prefix
  "max_tool_turns": 6,
  "context_window": 8,                  # message pairs kept in history
  "context_idle_reset_minutes": 60
}
```

### Group Registry

Maps a WhatsApp group JID to a blueprint:

```python
{
  "group_jid": "120363...@g.us",
  "blueprint_id": "invoice_curator",
  "status": "active"    # active | paused
}
```

### Control commands (admin number only)

| Command | Effect |
|---------|--------|
| `/blueprints` | List available blueprints |
| `/bind <blueprint_id>` | Assign blueprint to this group; clears existing conversation history for the group |
| `/bind <blueprint_id> --trigger mention` | Assign with mention-only trigger; clears existing conversation history |
| `/unbind` | Remove agent from this group |
| `/pause` | Silence agent without unbinding |
| `/resume` | Re-activate paused agent |

**Silence rule:** Any group not in the registry is completely ignored — no response, no logging. Enforced as the first check in the Router.

---

## 3. Message Lifecycle (Data Flow)

Processing runs as a FastAPI background task — the `/webhook` endpoint returns 200 immediately to prevent bridge timeouts.

```
1.  Message arrives in WhatsApp group
2.  Baileys Bridge extracts: jid, sender, isAdmin, text/image
    → POST /webhook to FastAPI orchestrator
3.  Router: check group_registry
    → not found or paused → DROP (silent)
    → active → load blueprint
4.  Trigger check
    → always  → proceed
    → mention → bot mentioned? proceed : DROP
    → prefix  → starts with prefix? proceed : DROP
5.  Control command check (admin number only)
    → /bind, /unbind, /pause, /resume, /blueprints → execute + respond : continue
6.  Rate limiter (per group_id): over limit → DROP
7.  Image pipeline (image events, blueprint-specific)
    → invoice_curator: hash → dedup → Gemini OCR → resize → R2 → convert → DB
    → other blueprints: pass image bytes directly to Agent Runner
8.  Agent Runner executes
    → load context (history) for group_id
    → assemble prompt + tool schemas for this blueprint
    → Claude tool-use loop (up to max_tool_turns)
    → dispatch tool calls via Tool Registry (sandboxed to tools_enabled)
    → collect final text response
9.  Response delivered
    → POST /send (text) or /send-file (PDF/Excel) to Baileys Bridge
10. Context persisted
    → updated history saved to in-memory store (backed by SQLite)
```

---

## 4. Agent Runner

Generic class — drives the Claude tool-use loop for any blueprint.

**Responsibilities:**
- Assemble prompt: `[system_prompt (cached)] + [history] + [incoming_message]`
- Apply prompt caching to system prompt + tool schemas (Anthropic cache_control)
- Run tool-use loop up to `blueprint.max_tool_turns`
- Dispatch tool calls to Tool Registry (sandboxed)
- Enforce admin authorization in tool executors (never trust Claude's reasoning)
- Manage confirmation flow for destructive actions (5-min TTL, "yes"/"no" reply)
- Persist updated conversation history after each turn

**Tool sandboxing:** The allowed-tools set is built from `blueprint.tools_enabled` before the loop starts. Any tool not in that list is rejected by the dispatcher regardless of Claude's request.

---

## 5. Tool Registry

Central map of `tool_name → (schema, executor_function)`.

```
tools/
├── invoice_tools.py    # 11 tools migrated from Invoice Curator
│   ├── get_status
│   ├── list_invoices
│   ├── get_preview
│   ├── generate_report
│   ├── flag_invoice
│   ├── unflag_invoice
│   ├── set_invoice_date
│   ├── set_invoice_amount
│   ├── add_date_format
│   ├── update_config
│   └── request_confirmation
└── notion_tools.py     # new, built on official Notion SDK
    ├── search_pages
    ├── create_task
    ├── append_to_page
    └── list_database_items
```

Each tool file exports a dict: `{tool_name: {"schema": {...}, "executor": async fn}}`. The registry merges all tool files at startup.

---

## 6. Database Schema

### Existing tables (Invoice Curator — unchanged)

- `invoices` — invoice records
- `group_config` — per-group Invoice Curator settings
- `conversation_history` — chat history per group (shared by all blueprints)
- `exchange_rate_cache` — currency rate cache
- `system_config` — global KV store

### New tables (added via Alembic migration)

```sql
CREATE TABLE blueprints (
    id                          TEXT PRIMARY KEY,
    display_name                TEXT NOT NULL,
    system_prompt               TEXT NOT NULL,
    model                       TEXT NOT NULL DEFAULT 'claude-sonnet-4-6',
    tools_enabled               TEXT NOT NULL,       -- JSON array
    trigger_type                TEXT NOT NULL DEFAULT 'always',
    trigger_prefix              TEXT,
    max_tool_turns              INTEGER DEFAULT 6,
    context_window              INTEGER DEFAULT 8,
    context_idle_reset_minutes  INTEGER DEFAULT 60,
    created_at                  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE group_registry (
    group_jid     TEXT PRIMARY KEY,
    blueprint_id  TEXT NOT NULL REFERENCES blueprints(id),
    status        TEXT NOT NULL DEFAULT 'active',
    bound_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE admin_numbers (
    phone_number  TEXT PRIMARY KEY,   -- e.g. "972501234567"
    label         TEXT,
    added_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Startup seeding

On first boot, if not present, the orchestrator seeds:
- `invoice_curator` and `notion_assistant` blueprint rows
- The existing Invoice Curator group JID from `LEGACY_GROUP_JID` env var → `group_registry` row

---

## 7. Invoice Curator Migration

The Invoice Curator is absorbed into the orchestrator. No separate process.

### Reused unchanged
- `bridge/` — entire Baileys bridge
- `pipeline/` — extraction, dedup, storage, currency conversion
- `db/models.py` + `db/migrations/` — existing schema + Alembic history
- `agent/context.py` — GroupContext (shared by all blueprints)
- `agent/confirmation.py` — PendingAction/ConfirmationStore (shared by all blueprints)
- `mailer/`, `reports/` — unchanged

### Refactored
- `agent/agent.py` — `InvoiceAgent` retired; replaced by generic `AgentRunner`. System prompt and tool list become the blueprint config row.
- `main.py` — replaced by new orchestrator `main.py` with Router, Blueprint Resolver, registry endpoints, and control command handler. `/webhook` endpoint signature stays identical — bridge needs zero changes.
- `config.py` — extended with Notion API key, admin phone number

---

## 8. Deployment (Hetzner CX22)

**Server:** Hetzner CX22 (2 vCPU, 4GB RAM, ~€4.5/month)

### Docker Compose

```yaml
services:
  bridge:
    build: ./bridge
    restart: unless-stopped
    volumes:
      - ./data/auth:/data/auth
    environment:
      - ORCHESTRATOR_URL=http://orchestrator:8000

  orchestrator:
    build: ./orchestrator
    restart: unless-stopped
    volumes:
      - ./data/db:/data
      - ./data/auth:/data/auth
    env_file: .env
    depends_on:
      - bridge
```

### Persistent data (host filesystem)

```
data/
├── auth/               # Baileys session credentials
└── db/
    └── whatsapp_agent.db
```

### Environment variables (.env — never committed)

```
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
NOTION_API_KEY=
CLOUDFLARE_R2_ACCESS_KEY=
CLOUDFLARE_R2_SECRET_KEY=
CLOUDFLARE_R2_BUCKET=
GMAIL_APP_PASSWORD=
ADMIN_PHONE_NUMBER=972...    # seeds the first admin_numbers row on startup
LEGACY_GROUP_JID=120363...@g.us
```

### Deployment workflow

1. `git pull` on Hetzner box
2. `docker compose up -d --build`
3. First run only: scan QR code from bridge logs → session persists thereafter

Nothing exposed to public internet. Bridge and orchestrator communicate over Docker's internal network only.

---

## 9. Decisions Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Language | Python (orchestrator) + Node.js (bridge) | Matches Invoice Curator stack; avoids rewriting Gemini OCR, reports, pipeline in TS |
| WhatsApp library | Baileys | No browser dependency; Invoice Curator already uses it |
| WhatsApp Cloud API | Abstraction layer via bridge interface | Can swap Baileys for official API later without touching orchestrator |
| AI model | Claude claude-sonnet-4-6 (Anthropic SDK) | Strong tool use; prompt caching for token efficiency |
| DB | SQLite (start) → Postgres+Redis (scale) | Zero ops for personal use; clear migration path |
| Architecture | Monolith with internal modules | 1-10 groups; no inter-process complexity needed |
| Deployment | Hetzner CX22 + Docker Compose | Persistent filesystem (Baileys auth); always-on; cheap |
| Invoice Curator | Absorbed into orchestrator (not separate service) | No HTTP hop; shared DB; single deploy |
