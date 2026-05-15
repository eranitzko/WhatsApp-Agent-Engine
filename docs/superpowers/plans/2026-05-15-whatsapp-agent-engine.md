# WhatsApp Agent Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a multi-role WhatsApp AI agent platform that routes messages to blueprint-configured Claude agents per group, with the Invoice Curator migrated in and a new Notion productivity agent added.

**Architecture:** A Node.js Baileys bridge (copied from Invoice Curator, unchanged) forwards WebSocket events to a Python FastAPI orchestrator. The orchestrator resolves each group's assigned Blueprint, runs a generic AgentRunner tool-use loop via the Anthropic SDK, and dispatches tool calls through a sandboxed ToolRegistry. All state lives in SQLite on the host filesystem.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy + Alembic (SQLite), Anthropic SDK, Notion SDK (`notion-client`), pytest + pytest-asyncio, Node.js 20, Baileys, Docker Compose.

---

## File Structure

```
WhatsApp Agent Engine/
├── bridge/                             # Copied from Invoice Curator — zero changes
│   ├── src/
│   │   ├── index.js
│   │   ├── connection.js
│   │   ├── server.js
│   │   ├── forwarder.js
│   │   └── adminCache.js
│   ├── package.json
│   └── Dockerfile
├── orchestrator/
│   ├── app/
│   │   ├── main.py                     # NEW: FastAPI entry point (replaces IC main.py)
│   │   ├── config.py                   # MODIFIED: add NOTION_API_KEY, ADMIN_PHONE_NUMBER, BOT_PHONE_NUMBER, LEGACY_GROUP_JID, NOTION_TASKS_DATABASE_ID
│   │   ├── router.py                   # NEW: group_jid → (Blueprint, GroupRegistry)
│   │   ├── agent_runner.py             # NEW: generic Claude tool-use loop
│   │   ├── tool_registry.py            # NEW: central tool map
│   │   ├── command_handler.py          # NEW: /bind, /unbind, /pause, /resume, /blueprints
│   │   ├── seeder.py                   # NEW: startup DB seeding
│   │   ├── agent/
│   │   │   ├── context.py              # MODIFIED: accept max_pairs + idle_minutes params
│   │   │   └── confirmation.py         # REUSED unchanged
│   │   ├── tools/
│   │   │   ├── __init__.py
│   │   │   ├── invoice_tools.py        # MOVED from agent/tools.py
│   │   │   └── notion_tools.py         # NEW
│   │   ├── prompts/
│   │   │   ├── __init__.py
│   │   │   ├── invoice_curator.py      # EXTRACTED from agent/agent.py
│   │   │   └── notion_assistant.py     # NEW
│   │   ├── pipeline/                   # REUSED unchanged
│   │   │   ├── pipeline.py
│   │   │   ├── dedup.py
│   │   │   ├── extractor.py
│   │   │   ├── storage.py
│   │   │   └── converter.py
│   │   ├── reports/                    # REUSED unchanged
│   │   │   ├── generator.py
│   │   │   ├── data.py
│   │   │   ├── pdf_report.py
│   │   │   └── excel_report.py
│   │   ├── mailer/                     # REUSED unchanged
│   │   │   └── gmail.py
│   │   └── db/
│   │       ├── models.py               # MODIFIED: add Blueprint, GroupRegistry, AdminNumbers
│   │       ├── session.py              # REUSED unchanged
│   │       └── migrations/
│   │           ├── env.py              # REUSED unchanged
│   │           └── versions/
│   │               └── 004_add_blueprint_registry.py   # NEW migration
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── test_router.py
│   │   ├── test_tool_registry.py
│   │   ├── test_command_handler.py
│   │   ├── test_agent_runner.py
│   │   └── test_notion_tools.py
│   ├── Dockerfile
│   └── requirements.txt
├── data/
│   ├── auth/                           # Baileys session (gitignored)
│   └── db/                             # SQLite DB (gitignored)
├── docker-compose.yml
├── .env.example
└── .gitignore
```

---

## Task 1: Project Scaffold

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `docker-compose.yml` (skeleton — finalized in Task 12)
- Create: `data/.gitkeep`

- [ ] **Step 1: Initialize git repo**

```bash
cd "C:\Users\Eranitzkovitch\Documents\Software Projects\WhatsApp Agent Engine"
git init
```

Expected: `Initialized empty Git repository in .../WhatsApp Agent Engine/.git/`

- [ ] **Step 2: Create .gitignore**

```
# env
.env
.env.local

# data
data/auth/
data/db/

# python
__pycache__/
*.pyc
*.pyo
.pytest_cache/
*.egg-info/
dist/
.venv/
venv/

# node
node_modules/
*.log

# editors
.DS_Store
.vscode/
```

- [ ] **Step 3: Create .env.example**

```
# Anthropic
ANTHROPIC_API_KEY=

# Google Gemini (invoice OCR)
GEMINI_API_KEY=

# Notion
NOTION_API_KEY=
NOTION_TASKS_DATABASE_ID=

# Cloudflare R2 (invoice image storage)
CLOUDFLARE_R2_ACCESS_KEY=
CLOUDFLARE_R2_SECRET_KEY=
CLOUDFLARE_R2_BUCKET=
CLOUDFLARE_R2_ENDPOINT=

# Gmail (report delivery)
GMAIL_APP_PASSWORD=
GMAIL_FROM_ADDRESS=

# WhatsApp
BOT_PHONE_NUMBER=972...
ADMIN_PHONE_NUMBER=972...
LEGACY_GROUP_JID=120363...@g.us

# DB
DATABASE_URL=sqlite:////data/db/whatsapp_agent.db

# Bridge
BRIDGE_URL=http://bridge:3000
ORCHESTRATOR_URL=http://orchestrator:8000
```

- [ ] **Step 4: Create data directories**

```bash
mkdir -p data/auth data/db
echo "" > data/.gitkeep
```

- [ ] **Step 5: Create skeleton docker-compose.yml**

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
      - ./data/db:/data/db
      - ./data/auth:/data/auth
    env_file: .env
    depends_on:
      - bridge
```

- [ ] **Step 6: Commit**

```bash
git add .gitignore .env.example docker-compose.yml data/.gitkeep
git commit -m "chore: project scaffold with gitignore, env template, compose skeleton"
```

---

## Task 2: Copy Invoice Curator Source

**Files:**
- Create: `bridge/` (copy from Invoice Curator)
- Create: `orchestrator/` (copy from Invoice Curator)

- [ ] **Step 1: Copy the bridge**

```bash
cp -r "C:\Users\Eranitzkovitch\Documents\Software Projects\Invoice Curator\bridge" "C:\Users\Eranitzkovitch\Documents\Software Projects\WhatsApp Agent Engine\bridge"
```

- [ ] **Step 2: Copy the orchestrator Python source**

```bash
cp -r "C:\Users\Eranitzkovitch\Documents\Software Projects\Invoice Curator\app" "C:\Users\Eranitzkovitch\Documents\Software Projects\WhatsApp Agent Engine\orchestrator\app"
cp "C:\Users\Eranitzkovitch\Documents\Software Projects\Invoice Curator\requirements.txt" "C:\Users\Eranitzkovitch\Documents\Software Projects\WhatsApp Agent Engine\orchestrator\requirements.txt"
```

- [ ] **Step 3: Add new dependencies to orchestrator/requirements.txt**

Open `orchestrator/requirements.txt` and add these lines:

```
notion-client>=2.2.1
pytest-asyncio>=0.23.0
```

- [ ] **Step 4: Create orchestrator/app/tools/ and orchestrator/app/prompts/ packages**

```bash
mkdir -p orchestrator/app/tools orchestrator/app/prompts
echo "" > orchestrator/app/tools/__init__.py
echo "" > orchestrator/app/prompts/__init__.py
```

- [ ] **Step 5: Create orchestrator/tests/ directory**

```bash
mkdir -p orchestrator/tests
echo "" > orchestrator/tests/__init__.py
```

- [ ] **Step 6: Create orchestrator/Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 7: Install Python dependencies locally for development**

```bash
cd orchestrator
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

- [ ] **Step 8: Verify existing tests still pass**

```bash
cd orchestrator
pytest -v
```

Expected: all existing Invoice Curator tests pass (or no tests exist yet — either is fine).

- [ ] **Step 9: Commit**

```bash
git add bridge/ orchestrator/
git commit -m "chore: copy Invoice Curator source into bridge/ and orchestrator/"
```

---

## Task 3: New DB Models

**Files:**
- Modify: `orchestrator/app/db/models.py` — add `Blueprint`, `GroupRegistry`, `AdminNumbers`
- Modify: `orchestrator/app/agent/context.py` — accept `max_pairs` and `idle_minutes` params

- [ ] **Step 1: Write failing tests for new models**

Create `orchestrator/tests/conftest.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.models import Base

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
```

Create `orchestrator/tests/test_models.py`:

```python
import json
import pytest
from app.db.models import Blueprint, GroupRegistry, AdminNumbers

def test_blueprint_tools_list_parses_json(db):
    bp = Blueprint(
        id="test",
        display_name="Test",
        system_prompt="You are helpful.",
        model="claude-sonnet-4-6",
        tools_enabled=json.dumps(["tool_a", "tool_b"]),
    )
    db.add(bp)
    db.commit()
    fetched = db.query(Blueprint).filter_by(id="test").first()
    assert fetched.tools_list() == ["tool_a", "tool_b"]

def test_group_registry_references_blueprint(db):
    db.add(Blueprint(
        id="bot",
        display_name="Bot",
        system_prompt="...",
        model="claude-sonnet-4-6",
        tools_enabled="[]",
    ))
    db.add(GroupRegistry(
        group_jid="123@g.us",
        blueprint_id="bot",
        status="active",
        trigger_type="always",
    ))
    db.commit()
    entry = db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry.blueprint_id == "bot"
    assert entry.status == "active"

def test_admin_numbers_stores_phone(db):
    db.add(AdminNumbers(phone_number="972501234567", label="owner"))
    db.commit()
    admin = db.query(AdminNumbers).filter_by(phone_number="972501234567").first()
    assert admin is not None
    assert admin.label == "owner"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd orchestrator
pytest tests/test_models.py -v
```

Expected: FAIL — `Blueprint`, `GroupRegistry`, `AdminNumbers` not defined.

- [ ] **Step 3: Add new models to orchestrator/app/db/models.py**

At the end of `orchestrator/app/db/models.py`, add:

```python
import json as _json
from datetime import datetime

class Blueprint(Base):
    __tablename__ = "blueprints"

    id = Column(String, primary_key=True)
    display_name = Column(String, nullable=False)
    system_prompt = Column(Text, nullable=False)
    model = Column(String, nullable=False, default="claude-sonnet-4-6")
    tools_enabled = Column(Text, nullable=False)  # JSON array string
    max_tool_turns = Column(Integer, default=6)
    context_window = Column(Integer, default=8)
    context_idle_reset_minutes = Column(Integer, default=60)
    created_at = Column(DateTime, default=datetime.utcnow)

    def tools_list(self) -> list[str]:
        return _json.loads(self.tools_enabled)


class GroupRegistry(Base):
    __tablename__ = "group_registry"

    group_jid = Column(String, primary_key=True)
    blueprint_id = Column(String, ForeignKey("blueprints.id"), nullable=False)
    status = Column(String, nullable=False, default="active")      # active | paused
    trigger_type = Column(String, nullable=False, default="always") # always | mention | prefix
    trigger_prefix = Column(String, nullable=True)
    bound_at = Column(DateTime, default=datetime.utcnow)


class AdminNumbers(Base):
    __tablename__ = "admin_numbers"

    phone_number = Column(String, primary_key=True)
    label = Column(String, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)
```

Note: verify that `String`, `Text`, `Integer`, `DateTime`, `Column`, `ForeignKey`, and `Base` are already imported in `models.py` from the Invoice Curator code. Add any missing imports.

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd orchestrator
pytest tests/test_models.py -v
```

Expected: PASS (3 tests).

- [ ] **Step 5: Modify GroupContext to accept blueprint config params**

In `orchestrator/app/agent/context.py`, find the `get_history` and `add` methods and update their signatures to accept optional `max_pairs` and `idle_minutes` parameters that override the instance defaults:

```python
def get_history(self, group_id: str, max_pairs: int | None = None, idle_minutes: int | None = None) -> list[dict]:
    effective_idle = idle_minutes if idle_minutes is not None else self._default_idle_minutes
    effective_max = max_pairs if max_pairs is not None else self._default_max_pairs
    # ... existing idle reset logic using effective_idle ...
    # ... existing trim logic using effective_max ...

def add(self, group_id: str, role: str, content: str, max_pairs: int | None = None) -> None:
    effective_max = max_pairs if max_pairs is not None else self._default_max_pairs
    # ... existing add + trim logic using effective_max ...
```

The existing hardcoded values (8 pairs, 60 min) become the instance defaults — no behaviour change for Invoice Curator.

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/db/models.py orchestrator/app/agent/context.py orchestrator/tests/
git commit -m "feat: add Blueprint, GroupRegistry, AdminNumbers models; extend GroupContext params"
```

---

## Task 4: Alembic Migration

**Files:**
- Create: `orchestrator/app/db/migrations/versions/004_add_blueprint_registry.py`

- [ ] **Step 1: Check current Alembic head revision**

```bash
cd orchestrator
alembic history
```

Note the revision ID of the current head (the most recent migration). You will use it as `down_revision` in the next step.

- [ ] **Step 2: Generate migration file**

```bash
alembic revision --autogenerate -m "add blueprint registry tables"
```

Expected: Creates a file in `app/db/migrations/versions/` with auto-detected changes for `blueprints`, `group_registry`, and `admin_numbers`.

- [ ] **Step 3: Review the generated migration**

Open the generated file. Confirm it contains `op.create_table` calls for all three new tables with the correct columns. If autogenerate missed anything, add it manually following the schema in Task 3, Step 3.

- [ ] **Step 4: Apply the migration**

```bash
alembic upgrade head
```

Expected: `Running upgrade <prev> -> <new>, add blueprint registry tables`

- [ ] **Step 5: Verify tables exist**

```bash
python -c "
from app.db.session import SessionLocal
from app.db.models import Blueprint, GroupRegistry, AdminNumbers
db = SessionLocal()
print('blueprints:', db.query(Blueprint).count())
print('group_registry:', db.query(GroupRegistry).count())
print('admin_numbers:', db.query(AdminNumbers).count())
db.close()
"
```

Expected: all three print `0` (empty tables, no error).

- [ ] **Step 6: Commit**

```bash
git add app/db/migrations/versions/
git commit -m "feat: alembic migration 004 — add blueprint registry tables"
```

---

## Task 5: Router

**Files:**
- Create: `orchestrator/app/router.py`
- Create: `orchestrator/tests/test_router.py`

- [ ] **Step 1: Write failing tests**

Create `orchestrator/tests/test_router.py`:

```python
import pytest
from app.db.models import Blueprint, GroupRegistry, AdminNumbers
from app.router import Router

BLUEPRINT_DEFAULTS = dict(
    display_name="Test Bot",
    system_prompt="You are helpful.",
    model="claude-sonnet-4-6",
    tools_enabled="[]",
    max_tool_turns=3,
    context_window=4,
    context_idle_reset_minutes=30,
)

@pytest.fixture
def seeded_db(db):
    db.add(Blueprint(id="test_bot", **BLUEPRINT_DEFAULTS))
    db.add(GroupRegistry(
        group_jid="123@g.us",
        blueprint_id="test_bot",
        status="active",
        trigger_type="always",
    ))
    db.commit()
    return db


def test_resolve_known_active_group(seeded_db):
    router = Router()
    blueprint, entry = router.resolve(seeded_db, "123@g.us")
    assert blueprint is not None
    assert blueprint.id == "test_bot"
    assert entry.status == "active"


def test_resolve_unknown_group_returns_none(seeded_db):
    router = Router()
    blueprint, entry = router.resolve(seeded_db, "unknown@g.us")
    assert blueprint is None
    assert entry is None


def test_resolve_paused_group_returns_none(seeded_db):
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    entry.status = "paused"
    seeded_db.commit()
    router = Router()
    blueprint, result = router.resolve(seeded_db, "123@g.us")
    assert blueprint is None


def test_trigger_always_accepts_any_message(seeded_db):
    router = Router()
    _, entry = router.resolve(seeded_db, "123@g.us")
    assert router.check_trigger(entry, text="anything", bot_phone="972501234567") is True


def test_trigger_mention_accepts_when_bot_mentioned(seeded_db):
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    entry.trigger_type = "mention"
    seeded_db.commit()
    router = Router()
    _, entry = router.resolve(seeded_db, "123@g.us")
    assert router.check_trigger(entry, text="hey @972501234567 what's up", bot_phone="972501234567") is True


def test_trigger_mention_blocks_without_mention(seeded_db):
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    entry.trigger_type = "mention"
    seeded_db.commit()
    router = Router()
    _, entry = router.resolve(seeded_db, "123@g.us")
    assert router.check_trigger(entry, text="hello world", bot_phone="972501234567") is False


def test_trigger_prefix_accepts_matching_prefix(seeded_db):
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    entry.trigger_type = "prefix"
    entry.trigger_prefix = "!bot"
    seeded_db.commit()
    router = Router()
    _, entry = router.resolve(seeded_db, "123@g.us")
    assert router.check_trigger(entry, text="!bot what time is it", bot_phone="972501234567") is True


def test_trigger_prefix_blocks_without_prefix(seeded_db):
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    entry.trigger_type = "prefix"
    entry.trigger_prefix = "!bot"
    seeded_db.commit()
    router = Router()
    _, entry = router.resolve(seeded_db, "123@g.us")
    assert router.check_trigger(entry, text="hello world", bot_phone="972501234567") is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd orchestrator
pytest tests/test_router.py -v
```

Expected: FAIL — `app.router` not found.

- [ ] **Step 3: Implement orchestrator/app/router.py**

```python
from sqlalchemy.orm import Session
from app.db.models import Blueprint, GroupRegistry


class Router:
    def resolve(self, db: Session, group_jid: str) -> tuple[Blueprint | None, GroupRegistry | None]:
        entry = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
        if not entry or entry.status != "active":
            return None, None
        blueprint = db.query(Blueprint).filter_by(id=entry.blueprint_id).first()
        return blueprint, entry

    def check_trigger(self, entry: GroupRegistry, text: str, bot_phone: str) -> bool:
        if entry.trigger_type == "always":
            return True
        if entry.trigger_type == "mention":
            return f"@{bot_phone}" in (text or "")
        if entry.trigger_type == "prefix":
            return (text or "").startswith(entry.trigger_prefix or "")
        return False
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd orchestrator
pytest tests/test_router.py -v
```

Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/router.py orchestrator/tests/test_router.py
git commit -m "feat: router — group JID to blueprint resolution with trigger check"
```

---

## Task 6: Tool Registry

**Files:**
- Create: `orchestrator/app/tool_registry.py`
- Create: `orchestrator/tests/test_tool_registry.py`

- [ ] **Step 1: Write failing tests**

Create `orchestrator/tests/test_tool_registry.py`:

```python
import pytest
from unittest.mock import AsyncMock
from app.tool_registry import ToolRegistry

HELLO_SCHEMA = {
    "name": "say_hello",
    "description": "Says hello",
    "input_schema": {"type": "object", "properties": {}},
}

@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register({
        "say_hello": {
            "schema": HELLO_SCHEMA,
            "executor": AsyncMock(return_value="hello"),
        }
    })
    return r


def test_get_schemas_returns_only_requested(registry):
    schemas = registry.get_schemas(["say_hello"])
    assert len(schemas) == 1
    assert schemas[0]["name"] == "say_hello"


def test_get_schemas_ignores_unknown_names(registry):
    schemas = registry.get_schemas(["say_hello", "nonexistent"])
    assert len(schemas) == 1


def test_has_tool_returns_true_for_registered(registry):
    assert registry.has_tool("say_hello") is True


def test_has_tool_returns_false_for_unknown(registry):
    assert registry.has_tool("unknown") is False


@pytest.mark.asyncio
async def test_execute_known_tool(registry):
    result = await registry.execute("say_hello", {})
    assert result == "hello"


@pytest.mark.asyncio
async def test_execute_unknown_tool_returns_error_string(registry):
    result = await registry.execute("nonexistent", {})
    assert "Unknown tool" in result


def test_register_merges_multiple_tool_sets(registry):
    registry.register({
        "say_bye": {
            "schema": {"name": "say_bye", "description": "Bye", "input_schema": {"type": "object", "properties": {}}},
            "executor": AsyncMock(return_value="bye"),
        }
    })
    assert registry.has_tool("say_hello") is True
    assert registry.has_tool("say_bye") is True
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd orchestrator
pytest tests/test_tool_registry.py -v
```

Expected: FAIL — `app.tool_registry` not found.

- [ ] **Step 3: Implement orchestrator/app/tool_registry.py**

```python
from typing import Any


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, dict] = {}  # tool_name -> {"schema": dict, "executor": callable}

    def register(self, tools: dict[str, dict]) -> None:
        self._tools.update(tools)

    def get_schemas(self, tool_names: list[str]) -> list[dict]:
        return [self._tools[n]["schema"] for n in tool_names if n in self._tools]

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._tools

    async def execute(self, tool_name: str, params: dict, **ctx) -> Any:
        if tool_name not in self._tools:
            return f"Unknown tool: {tool_name}"
        return await self._tools[tool_name]["executor"](params, **ctx)
```

- [ ] **Step 4: Add pytest-asyncio config**

Create `orchestrator/pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd orchestrator
pytest tests/test_tool_registry.py -v
```

Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add orchestrator/app/tool_registry.py orchestrator/tests/test_tool_registry.py orchestrator/pytest.ini
git commit -m "feat: tool registry — central sandboxed tool map"
```

---

## Task 7: Migrate Invoice Tools

**Files:**
- Create: `orchestrator/app/tools/invoice_tools.py` (moved from `app/agent/tools.py`)
- Create: `orchestrator/app/prompts/invoice_curator.py` (extracted from `app/agent/agent.py`)

- [ ] **Step 1: Create orchestrator/app/tools/invoice_tools.py**

Move the entire contents of `orchestrator/app/agent/tools.py` into `orchestrator/app/tools/invoice_tools.py`. Then change the final lines to export the registry format instead of a list:

At the end of `invoice_tools.py`, add:

```python
# Registry export — maps tool name to schema + executor for ToolRegistry
def get_invoice_tools(db_session_factory, bridge_client, report_generator, mailer) -> dict[str, dict]:
    """
    Returns a dict of {tool_name: {"schema": ..., "executor": ...}} for all 11 invoice tools.
    Call this at startup and pass the result to ToolRegistry.register().
    """
    return {
        "get_status":            {"schema": GET_STATUS_SCHEMA,            "executor": make_get_status_executor(db_session_factory)},
        "list_invoices":         {"schema": LIST_INVOICES_SCHEMA,         "executor": make_list_invoices_executor(db_session_factory)},
        "get_preview":           {"schema": GET_PREVIEW_SCHEMA,           "executor": make_get_preview_executor(db_session_factory)},
        "generate_report":       {"schema": GENERATE_REPORT_SCHEMA,       "executor": make_generate_report_executor(db_session_factory, report_generator, bridge_client)},
        "flag_invoice":          {"schema": FLAG_INVOICE_SCHEMA,          "executor": make_flag_invoice_executor(db_session_factory)},
        "unflag_invoice":        {"schema": UNFLAG_INVOICE_SCHEMA,        "executor": make_unflag_invoice_executor(db_session_factory)},
        "set_invoice_date":      {"schema": SET_INVOICE_DATE_SCHEMA,      "executor": make_set_invoice_date_executor(db_session_factory)},
        "set_invoice_amount":    {"schema": SET_INVOICE_AMOUNT_SCHEMA,    "executor": make_set_invoice_amount_executor(db_session_factory)},
        "add_date_format":       {"schema": ADD_DATE_FORMAT_SCHEMA,       "executor": make_add_date_format_executor(db_session_factory)},
        "update_config":         {"schema": UPDATE_CONFIG_SCHEMA,         "executor": make_update_config_executor(db_session_factory)},
        "request_confirmation":  {"schema": REQUEST_CONFIRMATION_SCHEMA,  "executor": make_request_confirmation_executor()},
    }
```

Note: the `make_*_executor` functions are closures — wrap each existing tool executor function in a closure that injects `db_session_factory` (and other dependencies) so it matches the `async def executor(params, **ctx) -> str` signature expected by ToolRegistry. Adapt the existing executor functions accordingly; their logic stays unchanged.

- [ ] **Step 2: Extract system prompt to orchestrator/app/prompts/invoice_curator.py**

Find the system prompt string in `orchestrator/app/agent/agent.py` (the large string passed to Claude as the static system block). Move it to:

```python
# orchestrator/app/prompts/invoice_curator.py

INVOICE_CURATOR_SYSTEM_PROMPT = """
<paste the full existing system prompt here>
"""
```

- [ ] **Step 3: Verify the existing agent still imports cleanly**

```bash
cd orchestrator
python -c "from app.tools.invoice_tools import get_invoice_tools; print('ok')"
python -c "from app.prompts.invoice_curator import INVOICE_CURATOR_SYSTEM_PROMPT; print(len(INVOICE_CURATOR_SYSTEM_PROMPT), 'chars')"
```

Expected: both print without error.

- [ ] **Step 4: Commit**

```bash
git add orchestrator/app/tools/invoice_tools.py orchestrator/app/prompts/invoice_curator.py
git commit -m "refactor: extract invoice tools and system prompt into tools/ and prompts/"
```

---

## Task 8: Agent Runner

**Files:**
- Create: `orchestrator/app/agent_runner.py`
- Create: `orchestrator/tests/test_agent_runner.py`

- [ ] **Step 1: Write failing tests**

Create `orchestrator/tests/test_agent_runner.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.agent_runner import AgentRunner
from app.tool_registry import ToolRegistry
from app.db.models import Blueprint

BLUEPRINT = Blueprint(
    id="test_bot",
    display_name="Test Bot",
    system_prompt="You are helpful.",
    model="claude-sonnet-4-6",
    tools_enabled='["say_hello"]',
    max_tool_turns=3,
    context_window=4,
    context_idle_reset_minutes=30,
)


def make_end_turn_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


def make_tool_use_response(tool_name: str, tool_id: str, tool_input: dict, follow_up_text: str):
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = tool_name
    tool_block.id = tool_id
    tool_block.input = tool_input

    first_response = MagicMock()
    first_response.stop_reason = "tool_use"
    first_response.content = [tool_block]

    second_response = make_end_turn_response(follow_up_text)
    return [first_response, second_response]


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register({
        "say_hello": {
            "schema": {"name": "say_hello", "description": "Greet", "input_schema": {"type": "object", "properties": {}}},
            "executor": AsyncMock(return_value="Hello there!"),
        }
    })
    return r


@pytest.fixture
def context():
    ctx = MagicMock()
    ctx.get_history = MagicMock(return_value=[])
    ctx.add = MagicMock()
    return ctx


@pytest.fixture
def confirmation_store():
    store = MagicMock()
    store.get = MagicMock(return_value=None)
    store.clear = MagicMock()
    return store


@pytest.mark.asyncio
async def test_run_returns_text_on_end_turn(registry, context, confirmation_store):
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=make_end_turn_response("Hello, how can I help?"))
    runner = AgentRunner(client, registry)
    result = await runner.run(
        blueprint=BLUEPRINT,
        group_jid="123@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        message="hello",
        context=context,
        confirmation_store=confirmation_store,
    )
    assert result == "Hello, how can I help?"


@pytest.mark.asyncio
async def test_run_persists_history(registry, context, confirmation_store):
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=make_end_turn_response("Done."))
    runner = AgentRunner(client, registry)
    await runner.run(
        blueprint=BLUEPRINT,
        group_jid="123@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        message="do something",
        context=context,
        confirmation_store=confirmation_store,
    )
    assert context.add.call_count == 2  # user message + assistant reply


@pytest.mark.asyncio
async def test_run_executes_tool_and_returns_followup(registry, context, confirmation_store):
    client = AsyncMock()
    responses = make_tool_use_response("say_hello", "tu_001", {}, "I said hello!")
    client.messages.create = AsyncMock(side_effect=responses)
    runner = AgentRunner(client, registry)
    result = await runner.run(
        blueprint=BLUEPRINT,
        group_jid="123@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        message="greet me",
        context=context,
        confirmation_store=confirmation_store,
    )
    assert result == "I said hello!"


@pytest.mark.asyncio
async def test_run_blocks_tool_not_in_blueprint(context, confirmation_store):
    registry = ToolRegistry()
    registry.register({
        "forbidden_tool": {
            "schema": {"name": "forbidden_tool", "description": "Forbidden", "input_schema": {"type": "object", "properties": {}}},
            "executor": AsyncMock(return_value="SECRET DATA"),
        }
    })
    # Blueprint only allows say_hello (not in registry here), so forbidden_tool must be blocked
    client = AsyncMock()
    responses = make_tool_use_response("forbidden_tool", "tu_002", {}, "I tried.")
    client.messages.create = AsyncMock(side_effect=responses)
    runner = AgentRunner(client, registry)
    # Should NOT call forbidden_tool executor because it's not in blueprint.tools_list()
    result = await runner.run(
        blueprint=BLUEPRINT,  # tools_enabled = ["say_hello"]
        group_jid="123@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        message="do forbidden thing",
        context=context,
        confirmation_store=confirmation_store,
    )
    registry._tools["forbidden_tool"]["executor"].assert_not_called()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd orchestrator
pytest tests/test_agent_runner.py -v
```

Expected: FAIL — `app.agent_runner` not found.

- [ ] **Step 3: Implement orchestrator/app/agent_runner.py**

```python
import json
from datetime import datetime
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
        context,           # GroupContext instance
        confirmation_store, # ConfirmationStore instance
    ) -> str:
        allowed_tools = blueprint.tools_list()

        # Check pending confirmation
        pending = confirmation_store.get(group_jid)
        if pending and not pending.is_expired():
            normalized = message.strip().lower()
            if normalized in ("yes", "כן", "confirm", "אישור"):
                result = await self.registry.execute(
                    pending.action, pending.params,
                    group_jid=group_jid, sender=sender, is_admin=is_admin,
                )
                confirmation_store.clear(group_jid)
                context.add(group_jid, "user", message, max_pairs=blueprint.context_window)
                context.add(group_jid, "assistant", str(result), max_pairs=blueprint.context_window)
                return str(result)
            elif normalized in ("no", "לא", "cancel"):
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
                "text": f"Today's date: {datetime.utcnow().date()}. Sender is_admin: {is_admin}.",
            },
        ]
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

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd orchestrator
pytest tests/test_agent_runner.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/agent_runner.py orchestrator/tests/test_agent_runner.py
git commit -m "feat: generic AgentRunner — Claude tool-use loop with sandboxing and confirmation"
```

---

## Task 9: Command Handler

**Files:**
- Create: `orchestrator/app/command_handler.py`
- Create: `orchestrator/tests/test_command_handler.py`

- [ ] **Step 1: Write failing tests**

Create `orchestrator/tests/test_command_handler.py`:

```python
import json
import pytest
from app.db.models import Blueprint, GroupRegistry, AdminNumbers, ConversationHistory
from app.command_handler import CommandHandler

BLUEPRINT_ROW = Blueprint(
    id="notion_assistant",
    display_name="Notion Assistant",
    system_prompt="You are a Notion assistant.",
    model="claude-sonnet-4-6",
    tools_enabled=json.dumps(["search_pages"]),
)

@pytest.fixture
def seeded_db(db):
    db.add(AdminNumbers(phone_number="972501234567", label="owner"))
    db.add(BLUEPRINT_ROW)
    db.commit()
    return db


@pytest.mark.asyncio
async def test_bind_assigns_blueprint(seeded_db):
    handler = CommandHandler()
    result = await handler.handle(seeded_db, "123@g.us", "972501234567", "/bind notion_assistant")
    assert "Notion Assistant" in result
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry is not None
    assert entry.blueprint_id == "notion_assistant"
    assert entry.status == "active"
    assert entry.trigger_type == "always"


@pytest.mark.asyncio
async def test_bind_with_trigger_mention(seeded_db):
    handler = CommandHandler()
    await handler.handle(seeded_db, "123@g.us", "972501234567", "/bind notion_assistant --trigger mention")
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry.trigger_type == "mention"


@pytest.mark.asyncio
async def test_bind_clears_conversation_history(seeded_db):
    seeded_db.add(ConversationHistory(group_id="123@g.us", history="[]"))
    seeded_db.commit()
    handler = CommandHandler()
    await handler.handle(seeded_db, "123@g.us", "972501234567", "/bind notion_assistant")
    history = seeded_db.query(ConversationHistory).filter_by(group_id="123@g.us").first()
    assert history is None


@pytest.mark.asyncio
async def test_bind_unknown_blueprint_returns_error(seeded_db):
    handler = CommandHandler()
    result = await handler.handle(seeded_db, "123@g.us", "972501234567", "/bind nonexistent")
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_non_admin_returns_none(seeded_db):
    handler = CommandHandler()
    result = await handler.handle(seeded_db, "123@g.us", "999999999", "/bind notion_assistant")
    assert result is None


@pytest.mark.asyncio
async def test_unbind_removes_entry(seeded_db):
    seeded_db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="notion_assistant", status="active", trigger_type="always"))
    seeded_db.commit()
    handler = CommandHandler()
    await handler.handle(seeded_db, "123@g.us", "972501234567", "/unbind")
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry is None


@pytest.mark.asyncio
async def test_pause_sets_paused_status(seeded_db):
    seeded_db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="notion_assistant", status="active", trigger_type="always"))
    seeded_db.commit()
    handler = CommandHandler()
    await handler.handle(seeded_db, "123@g.us", "972501234567", "/pause")
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry.status == "paused"


@pytest.mark.asyncio
async def test_resume_sets_active_status(seeded_db):
    seeded_db.add(GroupRegistry(group_jid="123@g.us", blueprint_id="notion_assistant", status="paused", trigger_type="always"))
    seeded_db.commit()
    handler = CommandHandler()
    await handler.handle(seeded_db, "123@g.us", "972501234567", "/resume")
    entry = seeded_db.query(GroupRegistry).filter_by(group_jid="123@g.us").first()
    assert entry.status == "active"


@pytest.mark.asyncio
async def test_blueprints_lists_all(seeded_db):
    handler = CommandHandler()
    result = await handler.handle(seeded_db, "123@g.us", "972501234567", "/blueprints")
    assert "notion_assistant" in result
    assert "Notion Assistant" in result


def test_is_command_recognizes_slash_commands():
    handler = CommandHandler()
    assert handler.is_command("/bind notion_assistant") is True
    assert handler.is_command("/unbind") is True
    assert handler.is_command("/pause") is True
    assert handler.is_command("/resume") is True
    assert handler.is_command("/blueprints") is True
    assert handler.is_command("hello world") is False
    assert handler.is_command("") is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd orchestrator
pytest tests/test_command_handler.py -v
```

Expected: FAIL — `app.command_handler` not found.

- [ ] **Step 3: Implement orchestrator/app/command_handler.py**

```python
from datetime import datetime
from sqlalchemy.orm import Session
from app.db.models import Blueprint, GroupRegistry, AdminNumbers, ConversationHistory


class CommandHandler:
    COMMANDS = {"/bind", "/unbind", "/pause", "/resume", "/blueprints"}

    def is_command(self, text: str) -> bool:
        if not text:
            return False
        return any(text.strip().startswith(cmd) for cmd in self.COMMANDS)

    async def handle(self, db: Session, group_jid: str, sender_phone: str, text: str) -> str | None:
        if not self._is_admin(db, sender_phone):
            return None

        parts = text.strip().split()
        cmd = parts[0].lower()

        if cmd == "/blueprints":
            blueprints = db.query(Blueprint).all()
            if not blueprints:
                return "No blueprints available."
            lines = [f"• {b.id} — {b.display_name}" for b in blueprints]
            return "Available blueprints:\n" + "\n".join(lines)

        if cmd == "/bind":
            if len(parts) < 2:
                return "Usage: /bind <blueprint_id> [--trigger always|mention|prefix] [--prefix <word>]"
            blueprint_id = parts[1]
            blueprint = db.query(Blueprint).filter_by(id=blueprint_id).first()
            if not blueprint:
                ids = [b.id for b in db.query(Blueprint).all()]
                return f"Blueprint '{blueprint_id}' not found. Available: {', '.join(ids)}"

            trigger_type = "always"
            trigger_prefix = None
            if "--trigger" in parts:
                idx = parts.index("--trigger")
                if idx + 1 < len(parts):
                    trigger_type = parts[idx + 1]
            if "--prefix" in parts:
                idx = parts.index("--prefix")
                if idx + 1 < len(parts):
                    trigger_prefix = parts[idx + 1]

            # Clear conversation history on rebind
            db.query(ConversationHistory).filter_by(group_id=group_jid).delete()

            existing = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
            if existing:
                existing.blueprint_id = blueprint_id
                existing.status = "active"
                existing.trigger_type = trigger_type
                existing.trigger_prefix = trigger_prefix
                existing.bound_at = datetime.utcnow()
            else:
                db.add(GroupRegistry(
                    group_jid=group_jid,
                    blueprint_id=blueprint_id,
                    status="active",
                    trigger_type=trigger_type,
                    trigger_prefix=trigger_prefix,
                ))
            db.commit()
            return f"Bound '{blueprint.display_name}' to this group (trigger: {trigger_type})."

        if cmd == "/unbind":
            db.query(GroupRegistry).filter_by(group_jid=group_jid).delete()
            db.commit()
            return "Agent unbound from this group."

        if cmd == "/pause":
            entry = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
            if entry:
                entry.status = "paused"
                db.commit()
            return "Agent paused."

        if cmd == "/resume":
            entry = db.query(GroupRegistry).filter_by(group_jid=group_jid).first()
            if entry:
                entry.status = "active"
                db.commit()
            return "Agent resumed."

        return None

    def _is_admin(self, db: Session, phone: str) -> bool:
        return db.query(AdminNumbers).filter_by(phone_number=phone).first() is not None
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd orchestrator
pytest tests/test_command_handler.py -v
```

Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/command_handler.py orchestrator/tests/test_command_handler.py
git commit -m "feat: command handler — /bind, /unbind, /pause, /resume, /blueprints"
```

---

## Task 10: Startup Seeder

**Files:**
- Create: `orchestrator/app/seeder.py`

- [ ] **Step 1: Create orchestrator/app/prompts/notion_assistant.py** (needed by seeder)

```python
# orchestrator/app/prompts/notion_assistant.py

NOTION_ASSISTANT_SYSTEM_PROMPT = """You are a personal productivity assistant connected to the user's Notion workspace. You help them manage tasks, notes, and projects through WhatsApp.

You have four tools:
- search_pages: find pages or databases by keyword
- create_task: create a new task in the tasks database
- append_to_page: add content to an existing page
- list_database_items: list items from a Notion database

When the user asks you to find something, use search_pages.
When they ask you to create a task or todo, use create_task.
When they ask you to add notes to an existing page, use append_to_page.
When they ask to see their tasks or list items, use list_database_items.

Always confirm what you did in one or two sentences. Be concise — this is WhatsApp."""
```

- [ ] **Step 2: Create orchestrator/app/seeder.py**

```python
import json
from sqlalchemy.orm import Session
from app.db.models import Blueprint, GroupRegistry, AdminNumbers
from app.prompts.invoice_curator import INVOICE_CURATOR_SYSTEM_PROMPT
from app.prompts.notion_assistant import NOTION_ASSISTANT_SYSTEM_PROMPT


INVOICE_CURATOR_TOOLS = [
    "get_status", "list_invoices", "get_preview", "generate_report",
    "flag_invoice", "unflag_invoice", "set_invoice_date", "set_invoice_amount",
    "add_date_format", "update_config", "request_confirmation",
]

NOTION_ASSISTANT_TOOLS = [
    "search_pages", "create_task", "append_to_page", "list_database_items",
]

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
    for bp_data in DEFAULT_BLUEPRINTS:
        existing = db.query(Blueprint).filter_by(id=bp_data["id"]).first()
        if not existing:
            db.add(Blueprint(**bp_data))

    if admin_phone and not db.query(AdminNumbers).filter_by(phone_number=admin_phone).first():
        db.add(AdminNumbers(phone_number=admin_phone, label="owner"))

    if legacy_group_jid:
        existing = db.query(GroupRegistry).filter_by(group_jid=legacy_group_jid).first()
        if not existing:
            db.add(GroupRegistry(
                group_jid=legacy_group_jid,
                blueprint_id="invoice_curator",
                status="active",
                trigger_type="always",
            ))

    db.commit()
```

- [ ] **Step 3: Verify seeder runs cleanly**

```bash
cd orchestrator
python -c "
from app.db.session import SessionLocal
from app import seeder
db = SessionLocal()
seeder.seed(db, admin_phone='972501234567', legacy_group_jid=None)
from app.db.models import Blueprint
blueprints = db.query(Blueprint).all()
for b in blueprints:
    print(b.id, b.display_name)
db.close()
"
```

Expected:
```
invoice_curator Invoice Curator
notion_assistant Notion Assistant
```

- [ ] **Step 4: Commit**

```bash
git add orchestrator/app/seeder.py orchestrator/app/prompts/notion_assistant.py
git commit -m "feat: startup seeder — seeds blueprint rows and admin number on first boot"
```

---

## Task 11: New Orchestrator main.py

**Files:**
- Create: `orchestrator/app/main.py` (replaces Invoice Curator's main.py)
- Modify: `orchestrator/app/config.py` — add new env vars

- [ ] **Step 1: Update orchestrator/app/config.py**

Find the Pydantic Settings class (likely `class Settings(BaseSettings)`) and add these fields:

```python
# WhatsApp platform
bot_phone_number: str = ""
admin_phone_number: str = ""
legacy_group_jid: str = ""

# Notion
notion_api_key: str = ""
notion_tasks_database_id: str = ""

# Bridge
bridge_url: str = "http://bridge:3000"
```

- [ ] **Step 2: Create orchestrator/app/main.py**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
import anthropic

from app.config import settings
from app.db.session import SessionLocal
from app import seeder
from app.router import Router
from app.agent_runner import AgentRunner
from app.tool_registry import ToolRegistry
from app.command_handler import CommandHandler
from app.agent.context import GroupContext
from app.agent.confirmation import ConfirmationStore
from app.tools.invoice_tools import get_invoice_tools
from app.tools.notion_tools import get_notion_tools
from app.pipeline.pipeline import InvoicePipeline
from app.reports.generator import ReportGenerator
from app.mailer.gmail import GmailMailer


# --- Globals (initialized at startup) ---
router = Router()
command_handler = CommandHandler()
context_store = GroupContext()
confirmation_store = ConfirmationStore()
tool_registry = ToolRegistry()
agent_runner: AgentRunner | None = None
invoice_pipeline: InvoicePipeline | None = None


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_runner, invoice_pipeline

    db = SessionLocal()
    seeder.seed(
        db,
        admin_phone=settings.admin_phone_number,
        legacy_group_jid=settings.legacy_group_jid or None,
    )
    db.close()

    anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    report_generator = ReportGenerator()
    mailer = GmailMailer()

    tool_registry.register(get_invoice_tools(SessionLocal, None, report_generator, mailer))
    tool_registry.register(get_notion_tools(settings.notion_api_key, settings.notion_tasks_database_id))

    agent_runner = AgentRunner(anthropic_client, tool_registry)
    invoice_pipeline = InvoicePipeline(settings)

    yield


app = FastAPI(lifespan=lifespan)


@app.post("/webhook")
async def webhook(payload: WebhookPayload, background_tasks: BackgroundTasks):
    background_tasks.add_task(_process, payload)
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}


async def _process(payload: WebhookPayload) -> None:
    db = SessionLocal()
    try:
        blueprint, entry = router.resolve(db, payload.jid)
        if blueprint is None:
            return

        text = payload.text or payload.caption or ""

        if command_handler.is_command(text):
            sender_phone = payload.sender.split("@")[0].split(":")[0]
            reply = await command_handler.handle(db, payload.jid, sender_phone, text)
            if reply:
                await _send(payload.jid, reply)
            return

        if not router.check_trigger(entry, text=text, bot_phone=settings.bot_phone_number):
            return

        agent_message = text
        if payload.type == "image" and entry.blueprint_id == "invoice_curator":
            pipeline_result = await invoice_pipeline.process(payload, db)
            agent_message = pipeline_result.agent_message

        reply = await agent_runner.run(
            blueprint=blueprint,
            group_jid=payload.jid,
            sender=payload.sender,
            is_admin=payload.is_admin,
            message=agent_message,
            context=context_store,
            confirmation_store=confirmation_store,
        )
        await _send(payload.jid, reply)
    finally:
        db.close()


async def _send(jid: str, text: str) -> None:
    import httpx
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{settings.bridge_url}/send",
            json={"jid": jid, "text": text},
            timeout=10,
        )
```

Note: `InvoicePipeline` must have a `.process(payload, db)` method that returns an object with `.agent_message: str`. Adapt the existing `pipeline.py` call site from `agent/agent.py` to match this interface. The logic is unchanged — only the call signature is wrapped.

- [ ] **Step 3: Start the orchestrator locally and verify it boots**

```bash
cd orchestrator
uvicorn app.main:app --reload --port 8000
```

Expected: Server starts, logs show seeder running, no import errors. Press Ctrl+C to stop.

- [ ] **Step 4: Commit**

```bash
git add orchestrator/app/main.py orchestrator/app/config.py
git commit -m "feat: orchestrator main.py — router, agent runner, command handler wired together"
```

---

## Task 12: Notion Tools

**Files:**
- Create: `orchestrator/app/tools/notion_tools.py`
- Create: `orchestrator/tests/test_notion_tools.py`

- [ ] **Step 1: Write failing tests**

Create `orchestrator/tests/test_notion_tools.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.tools.notion_tools import get_notion_tools


@pytest.fixture
def mock_notion_client():
    client = MagicMock()
    client.search = AsyncMock(return_value={
        "results": [
            {"object": "page", "id": "page-001", "properties": {"title": {"title": [{"plain_text": "My Note"}]}}, "url": "https://notion.so/my-note"}
        ]
    })
    client.pages.create = AsyncMock(return_value={
        "id": "page-002", "url": "https://notion.so/new-task"
    })
    client.blocks.children.append = AsyncMock(return_value={"results": []})
    client.databases.query = AsyncMock(return_value={
        "results": [
            {
                "id": "item-001",
                "properties": {
                    "Name": {"title": [{"plain_text": "Buy groceries"}]},
                    "Status": {"select": {"name": "In Progress"}},
                }
            }
        ]
    })
    return client


@pytest.fixture
def tools(mock_notion_client):
    with patch("app.tools.notion_tools.AsyncClient", return_value=mock_notion_client):
        return get_notion_tools(api_key="test-key", tasks_database_id="db-001")


@pytest.mark.asyncio
async def test_search_pages_returns_results(tools, mock_notion_client):
    result = await tools["search_pages"]["executor"]({"query": "My Note"})
    assert "My Note" in result
    mock_notion_client.search.assert_called_once()


@pytest.mark.asyncio
async def test_search_pages_empty_returns_message(tools, mock_notion_client):
    mock_notion_client.search = AsyncMock(return_value={"results": []})
    result = await tools["search_pages"]["executor"]({"query": "nonexistent"})
    assert "No pages found" in result


@pytest.mark.asyncio
async def test_create_task_creates_page_in_database(tools, mock_notion_client):
    result = await tools["create_task"]["executor"]({"title": "Fix bug", "notes": "urgent"})
    assert "Fix bug" in result or "created" in result.lower()
    mock_notion_client.pages.create.assert_called_once()
    call_kwargs = mock_notion_client.pages.create.call_args[1]
    assert call_kwargs["parent"]["database_id"] == "db-001"


@pytest.mark.asyncio
async def test_append_to_page_calls_blocks_append(tools, mock_notion_client):
    mock_notion_client.search = AsyncMock(return_value={
        "results": [{"object": "page", "id": "page-001", "properties": {"title": {"title": [{"plain_text": "My Note"}]}}, "url": "https://notion.so/my-note"}]
    })
    result = await tools["append_to_page"]["executor"]({"page_title": "My Note", "content": "New paragraph"})
    mock_notion_client.blocks.children.append.assert_called_once()
    assert "appended" in result.lower() or "added" in result.lower()


@pytest.mark.asyncio
async def test_list_database_items_returns_items(tools, mock_notion_client):
    result = await tools["list_database_items"]["executor"]({"database_id": "db-001"})
    assert "Buy groceries" in result


def test_all_four_tools_are_present(tools):
    assert "search_pages" in tools
    assert "create_task" in tools
    assert "append_to_page" in tools
    assert "list_database_items" in tools


def test_all_tools_have_schema_and_executor(tools):
    for name, entry in tools.items():
        assert "schema" in entry, f"{name} missing schema"
        assert "executor" in entry, f"{name} missing executor"
        assert entry["schema"]["name"] == name
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd orchestrator
pytest tests/test_notion_tools.py -v
```

Expected: FAIL — `app.tools.notion_tools` not found.

- [ ] **Step 3: Implement orchestrator/app/tools/notion_tools.py**

```python
from notion_client import AsyncClient


def get_notion_tools(api_key: str, tasks_database_id: str) -> dict[str, dict]:
    client = AsyncClient(auth=api_key)

    async def search_pages(params: dict, **ctx) -> str:
        query = params.get("query", "")
        response = await client.search(query=query, filter={"property": "object", "value": "page"})
        results = response.get("results", [])
        if not results:
            return f"No pages found matching '{query}'."
        lines = []
        for page in results[:5]:
            props = page.get("properties", {})
            title_prop = next(
                (v for v in props.values() if v.get("type") == "title"),
                None,
            )
            title = (
                title_prop["title"][0]["plain_text"]
                if title_prop and title_prop["title"]
                else "(untitled)"
            )
            url = page.get("url", "")
            lines.append(f"• {title} — {url}")
        return "Found pages:\n" + "\n".join(lines)

    async def create_task(params: dict, **ctx) -> str:
        title = params.get("title", "Untitled Task")
        notes = params.get("notes", "")
        children = []
        if notes:
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": notes}}]},
            })
        response = await client.pages.create(
            parent={"database_id": tasks_database_id},
            properties={
                "Name": {"title": [{"type": "text", "text": {"content": title}}]},
            },
            children=children,
        )
        url = response.get("url", "")
        return f"Task '{title}' created. {url}"

    async def append_to_page(params: dict, **ctx) -> str:
        page_title = params.get("page_title", "")
        content = params.get("content", "")
        search_result = await client.search(
            query=page_title,
            filter={"property": "object", "value": "page"},
        )
        results = search_result.get("results", [])
        if not results:
            return f"No page found with title '{page_title}'."
        page_id = results[0]["id"]
        await client.blocks.children.append(
            block_id=page_id,
            children=[{
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]},
            }],
        )
        return f"Content appended to '{page_title}'."

    async def list_database_items(params: dict, **ctx) -> str:
        db_id = params.get("database_id", tasks_database_id)
        response = await client.databases.query(database_id=db_id)
        items = response.get("results", [])
        if not items:
            return "No items found in this database."
        lines = []
        for item in items[:10]:
            props = item.get("properties", {})
            name_prop = next(
                (v for v in props.values() if v.get("type") == "title"),
                None,
            )
            name = (
                name_prop["title"][0]["plain_text"]
                if name_prop and name_prop["title"]
                else "(untitled)"
            )
            status_prop = props.get("Status", {})
            status = status_prop.get("select", {}).get("name", "") if status_prop else ""
            line = f"• {name}"
            if status:
                line += f" [{status}]"
            lines.append(line)
        return "Items:\n" + "\n".join(lines)

    return {
        "search_pages": {
            "schema": {
                "name": "search_pages",
                "description": "Search for pages in the Notion workspace by keyword.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keyword"},
                    },
                    "required": ["query"],
                },
            },
            "executor": search_pages,
        },
        "create_task": {
            "schema": {
                "name": "create_task",
                "description": "Create a new task in the Notion tasks database.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Task title"},
                        "notes": {"type": "string", "description": "Optional notes or description"},
                    },
                    "required": ["title"],
                },
            },
            "executor": create_task,
        },
        "append_to_page": {
            "schema": {
                "name": "append_to_page",
                "description": "Append text content to an existing Notion page.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "page_title": {"type": "string", "description": "Title of the page to find"},
                        "content": {"type": "string", "description": "Text to append"},
                    },
                    "required": ["page_title", "content"],
                },
            },
            "executor": append_to_page,
        },
        "list_database_items": {
            "schema": {
                "name": "list_database_items",
                "description": "List items from a Notion database. Defaults to the tasks database.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "database_id": {
                            "type": "string",
                            "description": "Notion database ID. Omit to use the default tasks database.",
                        },
                    },
                    "required": [],
                },
            },
            "executor": list_database_items,
        },
    }
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd orchestrator
pytest tests/test_notion_tools.py -v
```

Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add orchestrator/app/tools/notion_tools.py orchestrator/tests/test_notion_tools.py
git commit -m "feat: notion tools — search_pages, create_task, append_to_page, list_database_items"
```

---

## Task 13: Docker Compose + Dockerfiles

**Files:**
- Modify: `docker-compose.yml` (finalize)
- Verify: `bridge/Dockerfile` (should already exist from Invoice Curator)
- Verify: `orchestrator/Dockerfile` (created in Task 2)

- [ ] **Step 1: Finalize docker-compose.yml**

```yaml
services:
  bridge:
    build: ./bridge
    restart: unless-stopped
    volumes:
      - ./data/auth:/data/auth
    environment:
      - ORCHESTRATOR_URL=http://orchestrator:8000
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3000/health"]
      interval: 30s
      timeout: 5s
      retries: 3

  orchestrator:
    build: ./orchestrator
    restart: unless-stopped
    volumes:
      - ./data/db:/data/db
      - ./data/auth:/data/auth
    env_file: .env
    depends_on:
      bridge:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

- [ ] **Step 2: Build both images locally**

```bash
docker compose build
```

Expected: Both images build without error.

- [ ] **Step 3: Copy .env.example to .env and fill in values**

```bash
cp .env.example .env
```

Fill in all values in `.env`. At minimum for local testing:
- `ANTHROPIC_API_KEY`
- `ADMIN_PHONE_NUMBER` (your number in international format, no +)
- `BOT_PHONE_NUMBER` (the WhatsApp number this bot uses, no +)
- `DATABASE_URL=sqlite:////data/db/whatsapp_agent.db`
- `BRIDGE_URL=http://bridge:3000`

- [ ] **Step 4: Start the stack**

```bash
docker compose up
```

Watch the logs. On first start:
1. Bridge logs will show a QR code — scan it with the bot's WhatsApp account.
2. Orchestrator logs should show seeder running and both blueprints registered.

- [ ] **Step 5: Verify /health endpoints**

In a separate terminal:

```bash
curl http://localhost:8000/health
```

Expected: `{"status": "ok"}`

- [ ] **Step 6: Run full test suite**

```bash
cd orchestrator
pytest -v
```

Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml
git commit -m "chore: finalize docker-compose with healthchecks"
```

---

## Task 14: Hetzner Deployment

**Files:** No new files — deployment uses existing Docker Compose on the remote server.

- [ ] **Step 1: Push repo to GitHub (or preferred remote)**

```bash
git remote add origin <your-repo-url>
git push -u origin main
```

- [ ] **Step 2: SSH into Hetzner box and install Docker**

```bash
ssh root@<hetzner-ip>
apt update && apt install -y docker.io docker-compose-plugin
systemctl enable docker
```

- [ ] **Step 3: Clone the repo on Hetzner**

```bash
git clone <your-repo-url> /opt/whatsapp-agent
cd /opt/whatsapp-agent
```

- [ ] **Step 4: Create .env on the server**

```bash
cp .env.example .env
nano .env   # fill in all production values
```

- [ ] **Step 5: Create data directories**

```bash
mkdir -p data/auth data/db
```

- [ ] **Step 6: Start the stack**

```bash
docker compose up -d
```

- [ ] **Step 7: Scan the QR code**

```bash
docker compose logs bridge -f
```

Scan the QR code shown in the logs using the bot's WhatsApp account. After scanning, session files are written to `data/auth/` and will persist across restarts.

- [ ] **Step 8: Verify the Invoice Curator group still works**

Send any message to the Invoice Curator group. The bot should respond as before.

- [ ] **Step 9: Bind the Notion assistant to a new group**

From your admin WhatsApp number, send to any new group:

```
/bind notion_assistant
```

Expected response: `Bound 'Notion Assistant' to this group (trigger: always).`

- [ ] **Step 10: Test Notion assistant**

Send: `create a task: test Notion integration`

Expected: Claude calls `create_task`, task appears in Notion, bot replies with confirmation.

- [ ] **Step 11: Final commit**

```bash
git add .
git commit -m "chore: deployment verified on Hetzner"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Architecture (bridge + orchestrator monolith) — Tasks 2, 13, 14
- ✅ Blueprint system (config, group registry, control commands) — Tasks 3, 4, 9, 10
- ✅ Message lifecycle (router, trigger, rate limiter, image pipeline, agent, response) — Task 11
- ✅ Agent Runner (tool-use loop, sandboxing, confirmation, caching) — Task 8
- ✅ Tool Registry — Task 6
- ✅ Invoice tools migration — Task 7
- ✅ Notion tools — Task 12
- ✅ DB schema (3 new tables + Alembic migration) — Tasks 3, 4
- ✅ Invoice Curator absorbed (not separate service) — Tasks 7, 11
- ✅ Startup seeder (blueprint rows + admin + legacy JID) — Task 10
- ✅ Deployment on Hetzner — Task 14
- ✅ Silence rule (unregistered groups dropped) — Task 5 + Task 11

**Rate limiter:** The spec mentions rate limiting (20 texts/min, 10 images/hour) but the existing Invoice Curator already has this in `main.py`. The new `main.py` (Task 11) must preserve the existing rate limiting call. Verify the existing `rate_limiter` import and call is carried over when rewriting `main.py`.
