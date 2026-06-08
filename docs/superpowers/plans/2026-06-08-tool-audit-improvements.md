# Tool Audit Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all findings from the Claude Council tool audit: schema/description quality, naming consistency, missing tools, and structural issues.

**Architecture:** Three independent phases. Phase 1 (schema/naming) is pure text edits — no new executors, no DB changes. Phase 2 (new tools) adds executors and schemas. Phase 3 (structural) adds access metadata and blueprint separation. Each phase is shippable on its own.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (sync), APScheduler, Anthropic Claude tool_use schema format.

---

## File map

| File | Phase | What changes |
|------|-------|-------------|
| `orchestrator/app/agent/tools.py` | 1 | `get_status` description (config-only), `get_preview`→`get_invoice_summary` description, two-step doc on `set_invoice_amount`/`add_date_format`, rename `request_confirmation`→`stage_action` |
| `orchestrator/app/agent/agent.py` | 1 | **DELETE** — dead code; not imported anywhere; has stale `_STATIC_SYSTEM` prompt and unreachable `_execute_confirmed` dispatcher |
| `orchestrator/app/tools/invoice_tools.py` | 1 | Re-registration key: `stage_action` |
| `orchestrator/app/tools/accounting_tools.py` | 1, 2 | Rename `record_transaction`→`record_expense`, `apply_correction`→`commit_correction`, `save_email`→`set_report_email`; add step-labels to `correct_transaction`; new tools: `list_reminders`, `cancel_reminder`, `list_participants`, `get_debt_summary`, `get_transaction` |
| `orchestrator/app/tools/automation_tools.py` | 1, 2 | Rename `confirm_automation`→`activate_automation`; add `edit_automation` |
| `orchestrator/app/tools/send_email_tool.py` | 1 | Description disambiguation |
| `orchestrator/app/export/tool.py` | 1 | Split into `export_invoice_report` + `export_accounting_report`; invoice-specific params (`month`, `year`, `attach_images`, `start_date`, `end_date`) removed entirely from accounting schema |
| `orchestrator/app/prompts/invoice_curator.py` | 1 | Update tool name references; keep "Admin only — decline if is_admin is false" until Task 13 |
| `orchestrator/app/prompts/family_accounting.py` | 1 | Update tool name references; keep admin enforcement text until Task 13 |
| `orchestrator/app/seeder.py` | 1, 2 | Update tool lists for all renames + new tools |
| `orchestrator/app/agent_runner.py` | 3 | Role-filter tool list against `is_admin` before API call |
| `orchestrator/app/agent/tools.py` | 3 | Add `"access"` field to all invoice tool schemas |
| `orchestrator/app/tools/accounting_tools.py` | 3 | Add `"access"` field to all accounting tool schemas |
| `orchestrator/app/tools/automation_tools.py` | 3 | Add `"access"` field to all automation tool schemas |
| `orchestrator/tests/test_invoice_tools.py` | 1 | Update renamed tool references |
| `orchestrator/tests/test_accounting_tools.py` | 1, 2 | Update renamed tool references + new tool tests |
| `orchestrator/tests/test_automation_tools.py` | 1, 2 | Update renamed tool references + edit_automation test |

---

# PHASE 1 — Schema & naming cleanup

*No new executors. No DB migrations. Pure text changes to schemas, descriptions, and system prompts. All tests must pass at end of phase.*

---

### Task 1: Sharply split `get_status` and `get_invoice_summary`

**Files:**
- Modify: `orchestrator/app/agent/tools.py` (TOOL_SCHEMAS list, lines ~31–69)
- Modify: `orchestrator/app/tools/invoice_tools.py` (re-exports, line ~36)
- Modify: `orchestrator/app/prompts/invoice_curator.py` (line ~14)

**Background:** Both `get_status` and `get_preview` currently return invoice counts + totals for the current month. The agent calls both because their descriptions don't exclude each other. The fix: `get_status` returns config only; `get_preview` is renamed `get_invoice_summary` and returns stats only. Each description explicitly says what the OTHER tool is for.

- [ ] **Step 1: Write failing test**

```python
# orchestrator/tests/test_invoice_tools.py — add this test
def test_get_status_and_invoice_summary_descriptions_exclusive():
    """get_status must not mention stats; get_invoice_summary must not mention config."""
    from app.agent.tools import TOOL_SCHEMAS
    schemas = {s["name"]: s for s in TOOL_SCHEMAS}

    status_desc = schemas["get_status"]["description"].lower()
    summary_desc = schemas["get_invoice_summary"]["description"].lower()

    # get_status must be config-only
    assert "invoice count" not in status_desc
    assert "total" not in status_desc
    assert "get_invoice_summary" in status_desc  # cross-reference

    # get_invoice_summary must be stats-only
    assert "language" not in summary_desc
    assert "configuration" not in summary_desc
    assert "get_status" in summary_desc  # cross-reference
```

- [ ] **Step 2: Run — expect FAIL**

```
cd orchestrator
pytest tests/test_invoice_tools.py::test_get_status_and_invoice_summary_descriptions_exclusive -v
```
Expected: `FAILED` — `get_invoice_summary` key not in TOOL_SCHEMAS.

- [ ] **Step 3: Update `get_status` description and rename `get_preview` → `get_invoice_summary` in `agent/tools.py`**

```python
# In TOOL_SCHEMAS, replace the get_status dict:
{
    "name": "get_status",
    "description": (
        "Returns group configuration only: language setting, report header, "
        "report author, and dual-currency flag. "
        "Does NOT return invoice counts or totals — use get_invoice_summary for those."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
},
# Replace the get_preview dict:
{
    "name": "get_invoice_summary",
    "description": (
        "Returns invoice statistics for a month: count, total ILS, and number of flagged invoices. "
        "Defaults to the current month. "
        "Does not return configuration — use get_status for language, header, and settings."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "month": {"type": "integer", "description": "Month number 1–12. Defaults to current month."},
            "year":  {"type": "integer", "description": "4-digit year. Defaults to current year."},
        },
        "required": [],
    },
},
```

- [ ] **Step 4: Update the registry key in `invoice_tools.py`**

In `_SCHEMA_BY_NAME` (built from TOOL_SCHEMAS), the key is derived from `s["name"]` so it auto-updates. In `get_invoice_tools()`, the executor pairs list uses string names — change the entry:

```python
# In _tool_executor_pairs, change:
("get_preview",  _orig.exec_get_preview),
# to:
("get_invoice_summary", _orig.exec_get_preview),
```

- [ ] **Step 5: Update system prompt reference**

```python
# orchestrator/app/prompts/invoice_curator.py — change line:
# - get_preview — user wants a count/total summary for a month
# + get_invoice_summary — user wants a count/total summary for a month
```

- [ ] **Step 6: Update seeder tools list**

```python
# orchestrator/app/seeder.py — in INVOICE_CURATOR_TOOLS, change:
# - "get_preview",
# + "get_invoice_summary",
```

- [ ] **Step 7: Run tests — expect PASS**

```
pytest tests/test_invoice_tools.py -v
```
Expected: all pass.

- [ ] **Step 8: Commit**

```
git add orchestrator/app/agent/tools.py orchestrator/app/tools/invoice_tools.py \
        orchestrator/app/prompts/invoice_curator.py orchestrator/app/seeder.py
git commit -m "refactor: rename get_preview to get_invoice_summary; sharpen get_status/summary descriptions"
```

---

### Task 2: Document two-step flows with Step N of M labels

**Files:**
- Modify: `orchestrator/app/tools/accounting_tools.py` (~lines 235–330)
- Modify: `orchestrator/app/tools/automation_tools.py` (~lines 178–262)

**Background:** Three two-step flows exist with no consistent pattern. `correct_transaction` / `apply_correction` and `create_automation` / `confirm_automation` each need "Step 1 of 2 / Step 2 of 2" labels and cross-references so Claude never skips a step or calls them out of order.

- [ ] **Step 1: Write failing tests**

```python
# orchestrator/tests/test_accounting_tools.py — add:
def test_correct_transaction_has_step_label():
    from app.tools.accounting_tools import get_accounting_tools
    tools = get_accounting_tools()
    desc = tools["correct_transaction"]["schema"]["description"]
    assert "Step 1 of 2" in desc
    assert "commit_correction" in desc

def test_commit_correction_has_step_label():
    from app.tools.accounting_tools import get_accounting_tools
    tools = get_accounting_tools()
    desc = tools["commit_correction"]["schema"]["description"]
    assert "Step 2 of 2" in desc
    assert "correct_transaction" in desc
```

```python
# orchestrator/tests/test_automation_tools.py — add:
def test_create_automation_has_step_label():
    from app.tools.automation_tools import get_automation_tools
    tools = get_automation_tools()
    desc = tools["create_automation"]["schema"]["description"]
    assert "Step 1 of 2" in desc
    assert "activate_automation" in desc

def test_activate_automation_has_step_label():
    from app.tools.automation_tools import get_automation_tools
    tools = get_automation_tools()
    desc = tools["activate_automation"]["schema"]["description"]
    assert "Step 2 of 2" in desc
    assert "create_automation" in desc
```

- [ ] **Step 2: Run — expect FAIL**

```
pytest tests/test_accounting_tools.py::test_correct_transaction_has_step_label \
       tests/test_accounting_tools.py::test_commit_correction_has_step_label \
       tests/test_automation_tools.py::test_create_automation_has_step_label \
       tests/test_automation_tools.py::test_activate_automation_has_step_label -v
```
Expected: all FAIL (wrong names + missing labels).

- [ ] **Step 3: Update `correct_transaction` description in `accounting_tools.py`**

```python
"correct_transaction": {
    "name": "correct_transaction",
    "category": "accounting",
    "description": (
        "Step 1 of 2 — proposes a correction to an existing transaction. Admin only. "
        "Accepts partial updates: new date, new amount in ILS, participants to add or remove. "
        "Returns a human-readable diff and a correction_token. "
        "You MUST then call commit_correction with that token to apply the change. "
        "Do not tell the user the change is applied until commit_correction succeeds."
    ),
    ...
},
```

- [ ] **Step 4: Rename `apply_correction` → `commit_correction` and update its description**

In `_SCHEMAS` dict in `accounting_tools.py`, rename the key and the `"name"` field:

```python
"commit_correction": {
    "name": "commit_correction",
    "category": "accounting",
    "description": (
        "Step 2 of 2 — applies a staged transaction correction. Admin only. "
        "Only call this after calling correct_transaction and receiving a correction_token. "
        "Requires the token returned by correct_transaction. "
        "Returns confirmation that the correction was applied."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "token": {"type": "string", "description": "The correction_token returned by correct_transaction."},
            "admin_phone": {"type": "string", "description": "Admin's phone number."},
        },
        "required": ["token"],
    },
},
```

Update the executor registration tuple at the bottom of `get_accounting_tools()`:
```python
("apply_correction",    _exec_apply_correction),
# becomes:
("commit_correction",   _exec_apply_correction),
```

- [ ] **Step 5: Rename `confirm_automation` → `activate_automation` and update descriptions**

In `automation_tools.py`, update `_SCHEMAS`:

```python
# create_automation description — add step label and cross-reference:
"description": (
    "Step 1 of 2 — saves a scheduled, recurring, inactivity, or threshold-triggered rule. "
    "The rule is saved as pending and must be activated with activate_automation after the user confirms. "
    ...
    "Returns: rule ID and a human-readable summary for the user to confirm."
),

# confirm_automation → activate_automation:
"activate_automation": {
    "name": "activate_automation",
    "category": "automation",
    "description": (
        "Step 2 of 2 — activates a pending automation rule created with create_automation. "
        "Only call this after the user has said yes to the create_automation summary. "
        "Returns: confirmation that the rule is now active."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The rule ID returned by create_automation"},
        },
        "required": ["id"],
    },
},
```

Update the executor registration dict in `get_automation_tools()`:
```python
"activate_automation": {
    "schema": _SCHEMAS["activate_automation"],
    "executor": _exec_confirm_automation,
},
```

- [ ] **Step 6: Update seeder + system prompts**

```python
# seeder.py — AUTOMATION_TOOLS: change "confirm_automation" → "activate_automation"
# prompts/invoice_curator.py — change "confirm_automation" → "activate_automation"
# prompts/family_accounting.py — change "confirm_automation" → "activate_automation"
# prompts/invoice_curator.py — change "apply_correction" → "commit_correction" where referenced
# prompts/family_accounting.py — change "correct_transaction / apply_correction" → "correct_transaction / commit_correction"
```

- [ ] **Step 7: Run tests — expect PASS**

```
pytest tests/test_accounting_tools.py tests/test_automation_tools.py -v
```

- [ ] **Step 8: Commit**

```
git add orchestrator/app/tools/accounting_tools.py orchestrator/app/tools/automation_tools.py \
        orchestrator/app/prompts/ orchestrator/app/seeder.py
git commit -m "refactor: step-label two-step flows; rename confirm_automation→activate_automation, apply_correction→commit_correction"
```

---

### Task 3: Remove "never call directly" anti-pattern from invoice tool descriptions

**Files:**
- Modify: `orchestrator/app/agent/tools.py` (~lines 131–170)

**Background:** `set_invoice_amount` and `add_date_format` descriptions tell Claude not to call them while they appear as callable tools. Replace negative instructions with positive precondition language.

- [ ] **Step 1: Write failing test**

```python
# orchestrator/tests/test_invoice_tools.py — add:
def test_no_negative_call_instructions_in_descriptions():
    from app.agent.tools import TOOL_SCHEMAS
    for schema in TOOL_SCHEMAS:
        desc = schema["description"].lower()
        assert "never call" not in desc, f"{schema['name']} contains 'never call'"
        assert "never call this" not in desc, f"{schema['name']} contains 'never call this'"
        assert "only execute after" not in desc, f"{schema['name']} contains 'only execute after'"
```

- [ ] **Step 2: Run — expect FAIL**

```
pytest tests/test_invoice_tools.py::test_no_negative_call_instructions_in_descriptions -v
```

- [ ] **Step 3: Rewrite `set_invoice_amount` description**

```python
{
    "name": "set_invoice_amount",
    "description": (
        "Corrects an invoice's extracted amount. Admin only. "
        "Requires prior approval: call stage_action with action='set_invoice_amount' first "
        "and wait for the user to confirm before calling this tool. "
        "Recalculates the ILS amount using the stored exchange rate for the invoice's date. "
        "Returns: confirmation of the corrected amount."
    ),
    ...
},
```

- [ ] **Step 4: Rewrite `add_date_format` description**

```python
{
    "name": "add_date_format",
    "description": (
        "Registers a new date format for invoice date parsing (e.g. MM/DD/YYYY). Admin only. "
        "Requires prior approval: call stage_action with action='add_date_format' first "
        "and wait for the user to confirm before calling this tool. "
        "Adds to existing formats without replacing them. "
        "Returns: confirmation of the added format."
    ),
    ...
},
```

- [ ] **Step 5: Run — expect PASS**

```
pytest tests/test_invoice_tools.py::test_no_negative_call_instructions_in_descriptions -v
```

- [ ] **Step 6: Commit**

```
git add orchestrator/app/agent/tools.py
git commit -m "refactor: replace 'never call directly' anti-pattern with positive precondition language"
```

---

### Task 4: Remaining tool renames

**Files:**
- Modify: `orchestrator/app/agent/tools.py` — rename `request_confirmation` → `stage_action`
- Modify: `orchestrator/app/tools/invoice_tools.py` — update registration key
- Modify: `orchestrator/app/tools/accounting_tools.py` — rename `record_transaction` → `record_expense`, `save_email` → `set_report_email`
- Modify: `orchestrator/app/prompts/invoice_curator.py`
- Modify: `orchestrator/app/prompts/family_accounting.py`
- Modify: `orchestrator/app/seeder.py`

**Background:** `request_confirmation` sounds like a system call; `record_transaction` conflicts with `record_payment` visually; `save_email` is confusable with `send_email`.

- [ ] **Step 1: Write failing tests**

```python
# orchestrator/tests/test_invoice_tools.py — add:
def test_stage_action_tool_exists():
    from app.tools.invoice_tools import get_invoice_tools
    tools = get_invoice_tools()
    assert "stage_action" in tools
    assert "request_confirmation" not in tools

# orchestrator/tests/test_accounting_tools.py — add:
def test_record_expense_tool_exists():
    from app.tools.accounting_tools import get_accounting_tools
    tools = get_accounting_tools()
    assert "record_expense" in tools
    assert "record_transaction" not in tools

def test_set_report_email_tool_exists():
    from app.tools.accounting_tools import get_accounting_tools
    tools = get_accounting_tools()
    assert "set_report_email" in tools
    assert "save_email" not in tools
```

- [ ] **Step 2: Run — expect FAIL**

```
pytest tests/test_invoice_tools.py::test_stage_action_tool_exists \
       tests/test_accounting_tools.py::test_record_expense_tool_exists \
       tests/test_accounting_tools.py::test_set_report_email_tool_exists -v
```

- [ ] **Step 3: Rename `request_confirmation` → `stage_action` in `agent/tools.py`**

In TOOL_SCHEMAS, change the last entry's `"name"` field:
```python
{
    "name": "stage_action",
    "description": (
        "Stages a destructive or external action for user approval. "
        "Use before: removing an invoice, correcting its amount, adding a date format, "
        "or sending anything outside the group. "
        "Tell the user what will happen and ask them to reply yes. "
        "The action only executes when the user confirms. "
        "Returns: a confirmation prompt to relay to the user."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["remove_invoice", "send_email", "set_invoice_amount", "add_date_format"],
                "description": "Action to stage for confirmation.",
            },
            "params": {
                "type": "object",
                "description": (
                    "Parameters for the action. "
                    "remove_invoice: {invoice_id}. "
                    "send_email: {to, month?, year?, start_date?, end_date?, format, attach_images, dual_currency?}. "
                    "set_invoice_amount: {invoice_id, new_amount}. "
                    "add_date_format: {format_string}. "
                    "Note: use key 'to' (not 'to_email') for the recipient address."
                ),
            },
            "description": {
                "type": "string",
                "description": "Short label for the confirmation message (e.g. vendor name, date, amount). No warnings or caveats.",
            },
        },
        "required": ["action", "params", "description"],
    },
},
```

- [ ] **Step 4: Update `invoice_tools.py` registration key**

```python
# In get_invoice_tools(), change:
registry["request_confirmation"] = {
    "schema": _SCHEMA_BY_NAME["request_confirmation"],
    "executor": _exec_request_confirmation,
}
# to:
registry["stage_action"] = {
    "schema": _SCHEMA_BY_NAME["stage_action"],
    "executor": _exec_request_confirmation,
}
```

- [ ] **Step 5: Rename `record_transaction` → `record_expense` in `accounting_tools.py`**

In `_SCHEMAS`, change the key and `"name"` field:
```python
"record_expense": {
    "name": "record_expense",
    "category": "accounting",
    "description": (
        "Use when one person paid for others, or a user acknowledges a debt. "
        "Examples: 'Eran paid for me ₪150', 'I owe Tal ₪200', 'I paid for Eden'. "
        "Handles routing automatically: 1st-party (self-reported debt) is recorded immediately; "
        "2nd-party (claimed credit at another's expense) sends a confirmation request first. "
        "Returns: 'Recorded.' or 'Confirmation request sent to [Name].'"
    ),
    ...  # input_schema unchanged
},
```

Update executor registration tuple:
```python
("record_expense", _exec_record_transaction),
```

- [ ] **Step 6: Rename `save_email` → `set_report_email` in `accounting_tools.py`**

```python
"set_report_email": {
    "name": "set_report_email",
    "category": "accounting",
    "description": (
        "Saves the user's email address for PDF/XLSX report delivery. "
        "Use when a user says 'send reports to my email' or provides an address for export. "
        "Returns: confirmation that the email was saved."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "email": {"type": "string", "description": "Email address to save for report delivery."},
        },
        "required": ["email"],
    },
},
```

Update executor registration tuple:
```python
("set_report_email", _exec_save_email),
```

- [ ] **Step 7: Update both system prompts and seeder**

`prompts/invoice_curator.py`:
```python
# Change all occurrences:
# request_confirmation → stage_action
# confirm_automation → activate_automation  (already done in Task 2)
```

`prompts/family_accounting.py`:
```python
# record_transaction → record_expense
# save_email → set_report_email
# apply_correction → commit_correction  (already done in Task 2)
# confirm_automation → activate_automation  (already done in Task 2)
```

`seeder.py`:
```python
INVOICE_CURATOR_TOOLS = [
    "get_status", "list_invoices", "get_invoice_summary",   # renamed
    "flag_invoice", "unflag_invoice", "set_invoice_date", "set_invoice_amount",
    "add_date_format", "update_config", "stage_action",     # renamed
    *AUTOMATION_TOOLS,
]

AUTOMATION_TOOLS = [
    "create_automation", "activate_automation", "list_automations",  # renamed
    "pause_automation", "cancel_automation",
    "export_invoice_report", "export_accounting_report",             # split in Task 5
    "send_email",
]

FAMILY_ACCOUNTING_TOOLS = [
    "record_expense", "record_payment", "get_balance",               # renamed
    "get_history", "set_reminder",
    "set_report_email", "rename_participant", "set_household",       # renamed
    "correct_transaction", "commit_correction",                      # renamed
    "create_report_format", "list_report_formats", "delete_report_format",
    *AUTOMATION_TOOLS,
]
```

- [ ] **Step 8: Run all tests — expect PASS**

```
pytest --tb=short -q
```

- [ ] **Step 9: Delete dead code `agent/agent.py`**

`orchestrator/app/agent/agent.py` is not imported anywhere in the project. It contains a stale `_STATIC_SYSTEM` prompt (references `request_confirmation` by name) and an unreachable `_execute_confirmed` dispatcher. Delete it:

```
git rm orchestrator/app/agent/agent.py
```

Verify nothing broke:
```
pytest --tb=short -q
```

- [ ] **Step 10: Commit**

```
git add orchestrator/app/agent/tools.py orchestrator/app/tools/invoice_tools.py \
        orchestrator/app/tools/accounting_tools.py orchestrator/app/prompts/ \
        orchestrator/app/seeder.py
git rm orchestrator/app/agent/agent.py
git commit -m "refactor: rename request_confirmation→stage_action, record_transaction→record_expense, save_email→set_report_email; delete dead agent.py"
```

---

### Task 5: Split `export_report` into two typed tools

**Files:**
- Modify: `orchestrator/app/export/tool.py`
- Modify: `orchestrator/app/seeder.py` (already updated in Task 4)
- Modify: `orchestrator/app/prompts/invoice_curator.py`
- Modify: `orchestrator/app/prompts/family_accounting.py`

**Background:** One `export_report` tool handles both group types with params documented as "invoice curator only" in prose but present in the schema for all groups. This causes Claude to pass invoice-specific params (`attach_images`, `month`, etc.) when generating accounting reports. Fix: two tools with explicit param sets.

- [ ] **Step 1: Write failing tests**

```python
# orchestrator/tests/test_export_tool.py — add:
def test_export_invoice_report_tool_exists():
    from app.export.tool import get_export_tools
    tools = get_export_tools()
    assert "export_invoice_report" in tools
    assert "export_accounting_report" in tools
    assert "export_report" not in tools

def test_export_invoice_report_has_month_param():
    from app.export.tool import get_export_tools
    tools = get_export_tools()
    props = tools["export_invoice_report"]["schema"]["input_schema"]["properties"]
    assert "month" in props
    assert "attach_images" in props

def test_export_accounting_report_has_no_month_param():
    from app.export.tool import get_export_tools
    tools = get_export_tools()
    props = tools["export_accounting_report"]["schema"]["input_schema"]["properties"]
    assert "month" not in props
    assert "attach_images" not in props
```

- [ ] **Step 2: Run — expect FAIL**

```
pytest tests/test_export_tool.py::test_export_invoice_report_tool_exists -v
```

- [ ] **Step 3: Add two schemas and update `get_export_tools()` in `export/tool.py`**

Add alongside the existing `_SCHEMA` (keep old one for backward compat temporarily):

```python
_SCHEMA_INVOICE = {
    "name": "export_invoice_report",
    "category": "export",
    "description": (
        "Generates and delivers an invoice report (PDF or XLSX) for a given month. Admin only. "
        "Delivers to the group chat, by email, or both. "
        "PDF can optionally include invoice images as an appendix. "
        "Returns: confirmation of what was sent and where."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "format":        {"type": "string", "enum": ["pdf", "xlsx", "both"], "description": "Report format. Default: pdf."},
            "delivery":      {"type": "string", "enum": ["group", "email", "both"], "description": "Delivery destination. Default: group."},
            "email":         {"type": "string", "description": "Recipient email. Optional — uses saved email if omitted."},
            "month":         {"type": "integer", "description": "Month 1–12. Defaults to current month."},
            "year":          {"type": "integer", "description": "4-digit year. Defaults to current year."},
            "attach_images": {"type": "boolean", "description": "Include invoice images in PDF. Default: false."},
            "start_date":    {"type": "string", "description": "Custom range start YYYY-MM-DD. Overrides month/year."},
            "end_date":      {"type": "string", "description": "Custom range end YYYY-MM-DD. Overrides month/year."},
            "subject":       {"type": "string", "description": "Email subject. Supports {{variables}}. Email delivery only."},
            "body":          {"type": "string", "description": "Email body. Supports {{variables}}. Email delivery only."},
        },
        "required": [],
    },
}

_SCHEMA_ACCOUNTING = {
    "name": "export_accounting_report",
    "category": "export",
    "description": (
        "Generates and delivers an accounting ledger report (PDF or XLSX). Admin only. "
        "PDF shows net balances and full transaction history. "
        "Delivers to the group chat, by email, or both. "
        "Returns: confirmation of what was sent and where."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "format":   {"type": "string", "enum": ["pdf", "xlsx", "both"], "description": "Report format. Default: pdf."},
            "delivery": {"type": "string", "enum": ["group", "email", "both"], "description": "Delivery destination. Default: group."},
            "email":    {"type": "string", "description": "Recipient email. Optional — uses saved email if omitted."},
            "subject":  {"type": "string", "description": "Email subject. Supports {{variables}}. Email delivery only."},
            "body":     {"type": "string", "description": "Email body. Supports {{variables}}. Email delivery only."},
        },
        "required": [],
    },
}
```

Update `get_export_tools()`:
```python
def get_export_tools() -> dict[str, dict]:
    return {
        "export_invoice_report":   {"schema": _SCHEMA_INVOICE,    "executor": _exec_export_report},
        "export_accounting_report": {"schema": _SCHEMA_ACCOUNTING, "executor": _exec_export_report},
    }
```

The existing `_exec_export_report` already branches on group context; no executor changes needed.

> **⚠ Important:** `_SCHEMA_ACCOUNTING`'s `input_schema.properties` must NOT include `month`, `year`, `attach_images`, `start_date`, or `end_date`. Remove these keys entirely from the accounting schema dict — do not leave them as optional with amended descriptions. Claude will still pass them if they appear in the schema at all.

- [ ] **Step 4: Update system prompt references**

```python
# prompts/invoice_curator.py:
# "export_report" → "export_invoice_report"

# prompts/family_accounting.py:
# "export_report" → "export_accounting_report"
```

- [ ] **Step 5: Run all tests — expect PASS**

```
pytest --tb=short -q
```

- [ ] **Step 6: Commit**

```
git add orchestrator/app/export/tool.py orchestrator/app/prompts/ orchestrator/app/seeder.py
git commit -m "refactor: split export_report into export_invoice_report + export_accounting_report"
```

---

### Task 6: Disambiguate `send_email` from `export_*_report`

**Files:**
- Modify: `orchestrator/app/tools/send_email_tool.py` (description only)
- Modify: `orchestrator/app/prompts/invoice_curator.py` (add disambiguation note)

**Background:** Two tools can email a report. `send_email` is for automation workflows and custom messages; `export_invoice_report delivery=email` is for generating and delivering structured reports. Without disambiguation, Claude picks the wrong one.

- [ ] **Step 1: Update `send_email` description**

```python
_SCHEMA = {
    "name": "send_email",
    "category": "export",
    "description": (
        "Sends a custom plain-text email with template variable support. Admin only. "
        "Use for custom messages, notifications, or automation workflows — "
        "NOT for delivering generated PDF/XLSX reports. "
        "To email a report, use export_invoice_report or export_accounting_report with delivery='email'. "
        "Supported variables: {{previous_month}}, {{previous_month_invoice_total}}, "
        "{{monthly_invoice_total}}, {{open_debt_amount}}, {{today}}, {{current_month}}, "
        "{{previous_month_name}}, {{previous_month_year}}, plus workflow step outputs."
    ),
    ...
}
```

- [ ] **Step 2: Add disambiguation note to invoice_curator prompt**

In the tool selection criteria section of `prompts/invoice_curator.py`, add:
```
- send_email — for custom email messages in automations only; NOT for report delivery; use export_invoice_report with delivery=email to send reports by email
```

- [ ] **Step 3: Run tests**

```
pytest --tb=short -q
```

> **⚠ Ordering note:** Do NOT remove the "Admin only — decline if is_admin is false" lines from system prompts during Phase 1. Those lines stay until Task 13 (Phase 3) adds `access` metadata and role-filtering. Removing them early creates a window where non-admins can call admin tools with no enforcement.

- [ ] **Step 4: Commit and deploy Phase 1**

```
git add orchestrator/app/tools/send_email_tool.py orchestrator/app/prompts/invoice_curator.py
git commit -m "refactor: disambiguate send_email from export_*_report in descriptions and prompts"
```

Deploy Phase 1:
```
git push
ssh -i "C:\Users\Eranitzkovitch\.ssh\hetzner_ta125" -o StrictHostKeyChecking=no root@178.105.63.248 \
  "cd /opt/whatsapp && git pull && docker compose up --build -d 2>&1"
```

---

# PHASE 2 — New tools

*Adds new executors and schemas. No DB migrations except for `cancel_reminder` which requires a `cancelled` column on `scheduled_messages` (migration 014).*

---

### Task 7: `list_reminders` + `cancel_reminder`

**Files:**
- Create migration: `orchestrator/app/db/migrations/versions/014_scheduled_message_cancelled.py`
- Modify: `orchestrator/app/db/models.py` — add `cancelled` column to `ScheduledMessage`
- Modify: `orchestrator/app/tools/accounting_tools.py` — add two tools + executors
- Modify: `orchestrator/app/seeder.py` — add to `FAMILY_ACCOUNTING_TOOLS`
- Modify: `orchestrator/app/prompts/family_accounting.py` — add tool references
- Modify: `orchestrator/app/scheduler.py` — filter `cancelled=True` in `_dispatch_due_messages`

**Background:** `set_reminder` is a dead end — users can't see or cancel pending reminders. Fix: add `cancelled Boolean default False` column, list pending reminders, cancel by ID (shown as short prefix).

- [ ] **Step 1: Create migration 014**

```python
# orchestrator/app/db/migrations/versions/014_scheduled_message_cancelled.py
"""Add cancelled column to scheduled_messages."""
revision = "014"
down_revision = "013"

import sqlalchemy as sa
from alembic import op

def upgrade():
    op.add_column(
        "scheduled_messages",
        sa.Column("cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

def downgrade():
    op.drop_column("scheduled_messages", "cancelled")
```

- [ ] **Step 2: Add `cancelled` field to `ScheduledMessage` ORM model**

```python
# In orchestrator/app/db/models.py, inside class ScheduledMessage:
cancelled  = Column(Boolean, nullable=False, default=False, server_default="0")
```

- [ ] **Step 3: Update `_dispatch_due_messages` in scheduler.py to skip cancelled reminders**

```python
due = (
    db.query(ScheduledMessage)
    .filter(
        ScheduledMessage.sent == False,
        ScheduledMessage.cancelled == False,   # ← add this
        ScheduledMessage.send_at <= now,
    )
    .all()
)
```

- [ ] **Step 4: Write failing tests**

```python
# orchestrator/tests/test_accounting_tools.py — add:
@pytest.mark.asyncio
async def test_list_reminders_returns_pending(db):
    from app.tools.accounting_tools import get_accounting_tools
    from app.db.models import ScheduledMessage
    from datetime import datetime, timezone, timedelta

    future = datetime.now(timezone.utc) + timedelta(hours=2)
    db.add(ScheduledMessage(
        id="rem-1", group_jid="g@g.us", to_phone="972501234567",
        message="Test reminder", send_at=future, sent=False, cancelled=False,
    ))
    db.commit()

    tools = get_accounting_tools()
    with patch_session(db):
        result = await tools["list_reminders"]["executor"](
            {}, group_jid="g@g.us", sender="972501234567@s.whatsapp.net", is_admin=False
        )
    assert "Test reminder" in result
    assert "rem-1"[:8] in result

@pytest.mark.asyncio
async def test_cancel_reminder_marks_cancelled(db):
    from app.tools.accounting_tools import get_accounting_tools
    from app.db.models import ScheduledMessage
    from datetime import datetime, timezone, timedelta

    future = datetime.now(timezone.utc) + timedelta(hours=2)
    db.add(ScheduledMessage(
        id="rem-cancel-1", group_jid="g@g.us", to_phone="972501234567",
        message="Cancel me", send_at=future, sent=False, cancelled=False,
    ))
    db.commit()

    tools = get_accounting_tools()
    with patch_session(db):
        result = await tools["cancel_reminder"]["executor"](
            {"reminder_id": "rem-cancel"}, group_jid="g@g.us",
            sender="972501234567@s.whatsapp.net", is_admin=False
        )
    assert "cancelled" in result.lower()
    db.refresh(db.get(ScheduledMessage, "rem-cancel-1"))
    assert db.get(ScheduledMessage, "rem-cancel-1").cancelled is True
```

- [ ] **Step 5: Run — expect FAIL**

```
pytest tests/test_accounting_tools.py::test_list_reminders_returns_pending -v
```

- [ ] **Step 6: Add schema and executor for `list_reminders` in `accounting_tools.py`**

Schema:
```python
"list_reminders": {
    "name": "list_reminders",
    "category": "accounting",
    "description": (
        "Lists pending (not yet sent) reminders for the current user in this group. "
        "Shows each reminder's ID prefix, message, and scheduled time. "
        "Returns: numbered list of pending reminders, or 'No pending reminders.'"
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
},
```

Executor:
```python
async def _exec_list_reminders(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    to_phone = _sender_phone(ctx)
    if not to_phone:
        return "Error: could not determine sender phone."
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        rows = (
            db.query(ScheduledMessage)
            .filter(
                ScheduledMessage.group_jid == group_jid,
                ScheduledMessage.to_phone == to_phone,
                ScheduledMessage.sent == False,
                ScheduledMessage.cancelled == False,
                ScheduledMessage.send_at > now,
            )
            .order_by(ScheduledMessage.send_at)
            .all()
        )
    if not rows:
        return "No pending reminders."
    lines = [
        f"{i+1}. [{r.id[:8]}] {r.send_at.strftime('%d/%m/%Y %H:%M')} — {r.message}"
        for i, r in enumerate(rows)
    ]
    return "\n".join(lines)
```

- [ ] **Step 7: Add schema and executor for `cancel_reminder` in `accounting_tools.py`**

Schema:
```python
"cancel_reminder": {
    "name": "cancel_reminder",
    "category": "accounting",
    "description": (
        "Cancels a pending reminder by its ID prefix. "
        "Use the ID shown by list_reminders (first 8 characters). "
        "Returns: confirmation that the reminder was cancelled."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reminder_id": {
                "type": "string",
                "description": "The reminder ID prefix shown by list_reminders (at least 4 characters).",
            },
        },
        "required": ["reminder_id"],
    },
},
```

Executor:
```python
async def _exec_cancel_reminder(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    to_phone = _sender_phone(ctx)
    if not to_phone:
        return "Error: could not determine sender phone."
    reminder_id_prefix = params.get("reminder_id", "").strip()
    if len(reminder_id_prefix) < 4:
        return "Please provide at least 4 characters of the reminder ID."
    with SessionLocal() as db:
        row = (
            db.query(ScheduledMessage)
            .filter(
                ScheduledMessage.group_jid == group_jid,
                ScheduledMessage.to_phone == to_phone,
                ScheduledMessage.id.startswith(reminder_id_prefix),
                ScheduledMessage.sent == False,
                ScheduledMessage.cancelled == False,
            )
            .first()
        )
        if row is None:
            return f"No pending reminder found matching '{reminder_id_prefix}'."
        row.cancelled = True
        db.commit()
    return f"Reminder cancelled: \"{row.message}\""
```

- [ ] **Step 8: Register in `get_accounting_tools()`**

```python
("list_reminders",  _exec_list_reminders),
("cancel_reminder", _exec_cancel_reminder),
```

- [ ] **Step 9: Update seeder + prompt**

```python
# seeder.py FAMILY_ACCOUNTING_TOOLS — add after "set_reminder":
"list_reminders", "cancel_reminder",

# prompts/family_accounting.py — add to tool selection criteria:
# - list_reminders — user asks to see their pending reminders
# - cancel_reminder — user wants to cancel a scheduled reminder; use the ID from list_reminders
```

- [ ] **Step 10: Run tests — expect PASS**

```
pytest tests/test_accounting_tools.py -v
```

- [ ] **Step 11: Commit**

```
git add orchestrator/app/db/migrations/versions/014_scheduled_message_cancelled.py \
        orchestrator/app/db/models.py orchestrator/app/scheduler.py \
        orchestrator/app/tools/accounting_tools.py orchestrator/app/seeder.py \
        orchestrator/app/prompts/family_accounting.py
git commit -m "feat: add list_reminders and cancel_reminder tools; add cancelled column to scheduled_messages"
```

---

### Task 8: `list_participants`

**Files:**
- Modify: `orchestrator/app/tools/accounting_tools.py` — add schema + executor
- Modify: `orchestrator/app/seeder.py`
- Modify: `orchestrator/app/prompts/family_accounting.py`

**Background:** `rename_participant`, `set_household`, `get_balance`, and `record_expense` all operate on phone numbers. Claude currently guesses names from conversation history, causing errors when WhatsApp display names differ from stored identifiers.

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_list_participants_returns_group_members(db):
    from app.tools.accounting_tools import get_accounting_tools
    from app.db.models import GroupParticipant

    db.add(GroupParticipant(group_jid="g@g.us", phone="972501111111",
                            push_name="Eran", status="active"))
    db.add(GroupParticipant(group_jid="g@g.us", phone="972502222222",
                            push_name="Tal", admin_name="Tal (admin)", status="active"))
    db.commit()

    tools = get_accounting_tools()
    with patch_session(db):
        result = await tools["list_participants"]["executor"](
            {}, group_jid="g@g.us", sender="972501111111@s.whatsapp.net", is_admin=False
        )
    assert "972501111111" in result
    assert "Eran" in result
```

- [ ] **Step 2: Run — expect FAIL**

```
pytest tests/test_accounting_tools.py::test_list_participants_returns_group_members -v
```

- [ ] **Step 3: Add schema**

```python
"list_participants": {
    "name": "list_participants",
    "category": "accounting",
    "description": (
        "Returns the list of active group members with their display names and phone numbers. "
        "Use this before calling get_balance, record_expense, rename_participant, or set_household "
        "when you need to resolve a name to a phone number. "
        "Returns: each participant's phone, display name, and household status."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
},
```

- [ ] **Step 4: Add executor**

```python
async def _exec_list_participants(params: dict, **ctx) -> str:
    from app.db.models import GroupParticipant
    group_jid = ctx.get("group_jid", "")
    with SessionLocal() as db:
        rows = (
            db.query(GroupParticipant)
            .filter(
                GroupParticipant.group_jid == group_jid,
                GroupParticipant.status == "active",
            )
            .all()
        )
    if not rows:
        return "No participants found."
    lines = []
    for r in rows:
        name = r.admin_name or r.push_name or r.phone
        household = " [household]" if r.is_household else ""
        lines.append(f"{r.phone} — {name}{household}")
    return "\n".join(lines)
```

- [ ] **Step 5: Register, update seeder + prompt**

```python
# get_accounting_tools() executor pairs:
("list_participants", _exec_list_participants),

# seeder.py FAMILY_ACCOUNTING_TOOLS — add:
"list_participants",

# prompts/family_accounting.py tool selection criteria:
# - list_participants — look up who is in the group with their phone numbers and display names; use before get_balance or record_expense when a name needs to be resolved
```

- [ ] **Step 6: Run tests, commit**

```
pytest tests/test_accounting_tools.py -v
git add orchestrator/app/tools/accounting_tools.py orchestrator/app/seeder.py \
        orchestrator/app/prompts/family_accounting.py
git commit -m "feat: add list_participants tool"
```

---

### Task 9: `get_transaction` (single record detail)

**Files:**
- Modify: `orchestrator/app/tools/accounting_tools.py`
- Modify: `orchestrator/app/seeder.py`
- Modify: `orchestrator/app/prompts/family_accounting.py`

**Background:** `correct_transaction` needs full detail for one record but only the transaction ID prefix is known. Without this tool Claude must guess values.

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_get_transaction_returns_detail(db):
    from app.tools.accounting_tools import get_accounting_tools
    from app.db.models import LedgerEntry
    from decimal import Decimal
    from datetime import date

    db.add(LedgerEntry(
        id="tx-abc-123", transaction_id="txn-abc-123",
        group_jid="g@g.us", from_phone="972501111111", to_phone="972502222222",
        amount_ils=Decimal("150.00"), amount_settled_ils=Decimal("0"),
        description="Restaurant", transaction_date=date(2026, 5, 1),
    ))
    db.commit()

    tools = get_accounting_tools()
    with patch_session(db):
        result = await tools["get_transaction"]["executor"](
            {"transaction_id": "txn-abc"},
            group_jid="g@g.us", sender="972501111111@s.whatsapp.net", is_admin=True
        )
    assert "Restaurant" in result
    assert "150" in result
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add schema + executor**

Schema:
```python
"get_transaction": {
    "name": "get_transaction",
    "category": "accounting",
    "description": (
        "Returns full detail for a single transaction: all participants, amounts, date, "
        "description, and settlement status. Admin only. "
        "Use the transaction_id prefix (at least 8 chars) shown in get_history. "
        "Use this before correct_transaction to confirm you have the right record."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "transaction_id": {
                "type": "string",
                "description": "Transaction ID prefix (at least 8 characters) from get_history.",
            },
        },
        "required": ["transaction_id"],
    },
},
```

Executor (reads from `LedgerEntry` by `transaction_id` prefix, groups legs):
```python
async def _exec_get_transaction(params: dict, **ctx) -> str:
    group_jid = ctx.get("group_jid", "")
    is_admin = ctx.get("is_admin", False)
    if not is_admin:
        return "get_transaction is admin only."
    tx_prefix = params.get("transaction_id", "").strip()
    if len(tx_prefix) < 8:
        return "Please provide at least 8 characters of the transaction ID."
    with SessionLocal() as db:
        rows = (
            db.query(LedgerEntry)
            .filter(
                LedgerEntry.group_jid == group_jid,
                LedgerEntry.transaction_id.startswith(tx_prefix),
            )
            .all()
        )
    if not rows:
        return f"No transaction found matching '{tx_prefix}'."
    first = rows[0]
    settled_total = sum(r.amount_settled_ils or Decimal("0") for r in rows)
    total = sum(r.amount_ils for r in rows)
    lines = [
        f"Transaction: {first.transaction_id[:16]}",
        f"Date: {first.transaction_date}",
        f"Description: {first.description}",
        f"Total: ₪{float(total):,.2f} | Settled: ₪{float(settled_total):,.2f}",
        "Legs:",
    ]
    for r in rows:
        status = "settled" if r.amount_settled_ils >= r.amount_ils else "open"
        lines.append(f"  {r.from_phone} → {r.to_phone}: ₪{float(r.amount_ils):,.2f} [{status}]")
    return "\n".join(lines)
```

- [ ] **Step 4: Register, update seeder + prompt, run tests, commit**

```
git commit -m "feat: add get_transaction tool for single-record detail"
```

---

### Task 10: `get_debt_summary`

**Files:**
- Modify: `orchestrator/app/tools/accounting_tools.py`
- Modify: `orchestrator/app/seeder.py`
- Modify: `orchestrator/app/prompts/family_accounting.py`

**Background:** "Who owes me money?" is the most common accounting query. `get_balance` returns net numbers per counterparty; a human-readable debt summary is more useful for this query.

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_get_debt_summary_format(db):
    from app.tools.accounting_tools import get_accounting_tools
    from app.db.models import LedgerEntry
    from decimal import Decimal
    from datetime import date

    db.add(LedgerEntry(
        id="d1", transaction_id="t1", group_jid="g@g.us",
        from_phone="972502222222", to_phone="972501111111",
        amount_ils=Decimal("200.00"), amount_settled_ils=Decimal("0"),
        description="Groceries", transaction_date=date(2026, 4, 1),
    ))
    db.commit()

    tools = get_accounting_tools()
    with patch_session(db):
        result = await tools["get_debt_summary"]["executor"](
            {}, group_jid="g@g.us", sender="972501111111@s.whatsapp.net", is_admin=False
        )
    assert "200" in result
    assert "972502222222" in result or "owes" in result.lower()
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add schema + executor**

Schema:
```python
"get_debt_summary": {
    "name": "get_debt_summary",
    "category": "accounting",
    "description": (
        "Returns a human-readable list of who owes what to whom. "
        "Non-admins see only debts involving themselves. "
        "Admins see the full group debt table. "
        "Returns: each open debt with debtor, creditor, net ILS owed, and oldest open date."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
},
```

Executor (aggregates open LedgerEntry rows into net per debtor→creditor pair):
```python
async def _exec_get_debt_summary(params: dict, **ctx) -> str:
    from collections import defaultdict
    group_jid = ctx.get("group_jid", "")
    is_admin = ctx.get("is_admin", False)
    sender_phone = _sender_phone(ctx)

    with SessionLocal() as db:
        q = db.query(LedgerEntry).filter(
            LedgerEntry.group_jid == group_jid,
            LedgerEntry.amount_ils > LedgerEntry.amount_settled_ils,
        )
        if not is_admin:
            q = q.filter(
                or_(LedgerEntry.from_phone == sender_phone,
                    LedgerEntry.to_phone == sender_phone)
            )
        rows = q.order_by(LedgerEntry.transaction_date).all()

    if not rows:
        return "No open debts." if is_admin else "You have no open debts."

    # Aggregate net per (debtor, creditor) pair
    net: dict[tuple, Decimal] = defaultdict(Decimal)
    oldest: dict[tuple, date] = {}
    for r in rows:
        key = (r.from_phone, r.to_phone)
        net[key] += r.amount_ils - (r.amount_settled_ils or Decimal("0"))
        if key not in oldest or r.transaction_date < oldest[key]:
            oldest[key] = r.transaction_date

    lines = []
    for (debtor, creditor), amount in sorted(net.items(), key=lambda x: -x[1]):
        if amount <= Decimal("0"):
            continue
        lines.append(
            f"{debtor} owes {creditor}: ₪{float(amount):,.2f} "
            f"(since {oldest[(debtor, creditor)]})"
        )
    return "\n".join(lines) if lines else "No open debts."
```

- [ ] **Step 4: Register, update seeder + prompt, run tests, commit**

```
git commit -m "feat: add get_debt_summary tool"
```

---

### Task 11: `edit_automation`

**Files:**
- Modify: `orchestrator/app/tools/automation_tools.py`
- Modify: `orchestrator/app/seeder.py`
- Modify: `orchestrator/app/prompts/invoice_curator.py`
- Modify: `orchestrator/app/prompts/family_accounting.py`

**Background:** Users must cancel + recreate rules to change anything. An `edit_automation` tool accepts an ID and partial fields to update.

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_edit_automation_updates_name(db):
    from app.tools.automation_tools import get_automation_tools
    from app.db.models import AutomationRule
    import json

    db.add(AutomationRule(
        id="rule-1", group_jid="g@g.us", name="Old name",
        rule_type="recurring", schedule_cron="0 9 * * 1",
        action_type="send_message",
        action_config=json.dumps({"message": "hello"}),
        status="active",
    ))
    db.commit()

    tools = get_automation_tools()
    with patch_session(db):
        result = await tools["edit_automation"]["executor"](
            {"id": "rule-1", "name": "New name"},
            group_jid="g@g.us", is_admin=True
        )
    assert "New name" in result
    db.refresh(db.get(AutomationRule, "rule-1"))
    assert db.get(AutomationRule, "rule-1").name == "New name"
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add schema**

```python
"edit_automation": {
    "name": "edit_automation",
    "category": "automation",
    "description": (
        "Updates one or more fields of an existing automation rule. Admin only. "
        "All fields are optional — only provided fields are changed. "
        "Returns: updated rule summary."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "id":              {"type": "string", "description": "The automation rule ID to edit."},
            "name":            {"type": "string", "description": "New human label."},
            "schedule_cron":   {"type": "string", "description": "New cron expression or ISO 8601 datetime."},
            "inactivity_hours":{"type": "integer", "description": "New inactivity threshold in hours."},
            "action_config":   {"type": "object",  "description": "New action configuration object."},
        },
        "required": ["id"],
    },
},
```

- [ ] **Step 4: Add executor**

```python
async def _exec_edit_automation(params: dict, **ctx) -> str:
    rule_id = params.get("id", "")
    group_jid = ctx.get("group_jid", "")
    with SessionLocal() as db:
        rule = db.get(AutomationRule, rule_id)
        if rule is None:
            return f"No automation found with ID '{rule_id}'."
        if rule.group_jid != group_jid:
            return "That automation belongs to a different group."

        if "name" in params:
            rule.name = params["name"]
        if "schedule_cron" in params:
            rule.schedule_cron = params["schedule_cron"]
        if "inactivity_hours" in params:
            rule.inactivity_hours = params["inactivity_hours"]
        if "action_config" in params:
            ac = params["action_config"]
            rule.action_config = json.dumps(ac) if isinstance(ac, dict) else ac

        db.commit()
        description = _describe_rule(rule)
    return f"Automation updated:\n{description}"
```

- [ ] **Step 5: Register, update seeder + prompts, run tests, commit**

```python
# seeder.py AUTOMATION_TOOLS — add:
"edit_automation",

# prompts/invoice_curator.py + family_accounting.py — add:
# - edit_automation — change the name, schedule, or action of an existing automation
```

```
git commit -m "feat: add edit_automation tool"
```

---

### Task 12: Deploy Phase 2

- [ ] **Run full test suite**

```
pytest --tb=short -q
```
Expected: all pass.

- [ ] **Push and deploy**

```
git push
ssh -i "C:\Users\Eranitzkovitch\.ssh\hetzner_ta125" -o StrictHostKeyChecking=no root@178.105.63.248 \
  "cd /opt/whatsapp && git pull && docker compose up --build -d 2>&1"
```

---

# PHASE 3 — Structural improvements

*Adds `access` metadata to tool schemas and filters tool list by user role at the AgentRunner call site. Removes automation tools from live blueprint tool lists.*

---

### Task 13: Role-filter tool list in AgentRunner

**Files:**
- Modify: `orchestrator/app/tool_registry.py` — add `access` field awareness
- Modify: `orchestrator/app/agent_runner.py` — filter by `is_admin` before API call
- Modify: all tool schema files — add `"access": "admin"` or `"access": "user"` to each schema

**Background:** Admin-only tools mixed with user tools in the same list means Claude either calls admin tools for non-admins (fails at runtime) or avoids them even for admins. Adding `access` metadata and filtering at the tool-schema level before the API call means the agent never sees tools it can't use.

**Admin-only tools:**
- Invoice: `flag_invoice`, `unflag_invoice`, `set_invoice_date`, `set_invoice_amount`, `add_date_format`, `update_config`, `stage_action`, `export_invoice_report`
- Accounting: `rename_participant`, `set_household`, `correct_transaction`, `commit_correction`, `export_accounting_report`, `create_report_format`, `list_report_formats`, `delete_report_format`, `get_transaction`
- Automation: all 6 automation tools

**User-accessible tools:**
- Invoice: `get_status`, `list_invoices`, `get_invoice_summary`
- Accounting: `record_expense`, `record_payment`, `get_balance`, `get_history`, `set_reminder`, `list_reminders`, `cancel_reminder`, `set_report_email`, `list_participants`, `get_debt_summary`

- [ ] **Step 1: Write failing test**

```python
# orchestrator/tests/test_agent_runner.py — add:
@pytest.mark.asyncio
async def test_admin_tools_filtered_for_non_admins():
    """Non-admin users must not see admin-only tools in the API call."""
    import anthropic, json
    from unittest.mock import MagicMock, AsyncMock
    from app.agent_runner import AgentRunner
    from app.tool_registry import ToolRegistry
    from app.db.models import Blueprint

    reg = ToolRegistry()
    reg.register({
        "user_tool": {
            "schema": {"name": "user_tool", "description": "x",
                       "input_schema": {"type": "object", "properties": {}, "required": []},
                       "access": "user"},
            "executor": AsyncMock(),
        },
        "admin_tool": {
            "schema": {"name": "admin_tool", "description": "y",
                       "input_schema": {"type": "object", "properties": {}, "required": []},
                       "access": "admin"},
            "executor": AsyncMock(),
        },
    })

    captured: list = []

    async def fake_create(**kwargs):
        captured.extend(kwargs.get("tools", []))
        block = MagicMock(); block.type = "text"; block.text = "ok"
        resp = MagicMock(); resp.stop_reason = "end_turn"; resp.content = [block]
        return resp

    client = MagicMock()
    client.messages.create = fake_create
    runner = AgentRunner(client, reg)

    bp = Blueprint(
        id="bp", system_prompt="p", model="m", max_tool_turns=1,
        context_window=4, context_idle_reset_minutes=60,
        tools_enabled=json.dumps(["user_tool", "admin_tool"]),
    )
    context = MagicMock()
    context.get_history.return_value = []
    context.add = MagicMock()
    cs = MagicMock(); cs.get.return_value = None

    await runner.run(
        blueprint=bp, group_jid="g@g.us", sender="p@s.whatsapp.net",
        is_admin=False, message="hi", context=context, confirmation_store=cs,
    )

    tool_names = [t["name"] for t in captured]
    assert "user_tool" in tool_names
    assert "admin_tool" not in tool_names  # filtered out for non-admin
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Update `ToolRegistry.get_schemas()` to accept and propagate `access` field**

The `_ANTHROPIC_SCHEMA_KEYS` set controls which fields are passed to Claude. `access` should NOT be sent to Claude (it's internal metadata). It stays in the raw schema dict and is read before building the Claude payload.

Add a helper method to ToolRegistry:
```python
def get_allowed_tool_names(self, tool_names: list[str], is_admin: bool) -> list[str]:
    """Filter tool names by access level. Admin sees all; non-admin sees only access='user' tools."""
    result = []
    for name in tool_names:
        if name not in self._tools:
            continue
        access = self._tools[name]["schema"].get("access", "user")
        if is_admin or access == "user":
            result.append(name)
    return result
```

- [ ] **Step 4: Use `get_allowed_tool_names` in `AgentRunner.run()`**

In `agent_runner.py`, after the `disabled_tools` filter, add:
```python
allowed_tools = self.registry.get_allowed_tool_names(allowed_tools, is_admin)
```

- [ ] **Step 5: Add `"access"` field to all tool schemas — three files**

Apply to all three files that define schemas. `access` is intentionally excluded from `_ANTHROPIC_SCHEMA_KEYS` in ToolRegistry so it is never sent to Claude.

**File 1: `orchestrator/app/agent/tools.py`** (invoice tools in TOOL_SCHEMAS list)
- `"access": "user"` → `get_status`, `list_invoices`, `get_invoice_summary`
- `"access": "admin"` → `flag_invoice`, `unflag_invoice`, `set_invoice_date`, `set_invoice_amount`, `add_date_format`, `update_config`, `stage_action`

**File 2: `orchestrator/app/tools/accounting_tools.py`** (accounting tools in `_SCHEMAS` dict)
- `"access": "user"` → `record_expense`, `record_payment`, `get_balance`, `get_history`, `set_reminder`, `list_reminders`, `cancel_reminder`, `set_report_email`, `list_participants`, `get_debt_summary`
- `"access": "admin"` → `rename_participant`, `set_household`, `correct_transaction`, `commit_correction`, `create_report_format`, `list_report_formats`, `delete_report_format`, `get_transaction`

**File 3: `orchestrator/app/tools/automation_tools.py`** (automation tools in `_SCHEMAS` dict)
- `"access": "admin"` → all 6: `create_automation`, `activate_automation`, `list_automations`, `pause_automation`, `cancel_automation`, `edit_automation`

**File 4: `orchestrator/app/export/tool.py`**
- `"access": "admin"` → `export_invoice_report`, `export_accounting_report`

Example of how to add the field:

```python
# agent/tools.py — get_status:
{"name": "get_status", "access": "user", "description": ..., "input_schema": ...}

# agent/tools.py — flag_invoice:
{"name": "flag_invoice", "access": "admin", "description": ..., "input_schema": ...}

# accounting_tools.py — get_balance:
"get_balance": {"name": "get_balance", "access": "user", "category": "accounting", ...}

# accounting_tools.py — rename_participant:
"rename_participant": {"name": "rename_participant", "access": "admin", "category": "accounting", ...}
```

Apply consistently to all ~34 tools across all tool files.

- [ ] **Step 6: Update system prompts — remove manual admin checks**

Since role-filtering now happens at the schema level, the system prompts no longer need to say "admin only — decline if is_admin is false." Remove those instructions to reduce prompt clutter:

```python
# prompts/invoice_curator.py — remove:
# "## Admin enforcement\nTools marked admin only must not be called if is_admin is false."

# prompts/family_accounting.py — remove "admin only" qualifiers from tool descriptions
# (the tools won't appear in non-admin calls at all)
```

- [ ] **Step 7: Run all tests — expect PASS**

```
pytest --tb=short -q
```

- [ ] **Step 8: Commit**

```
git add orchestrator/app/tool_registry.py orchestrator/app/agent_runner.py \
        orchestrator/app/agent/tools.py orchestrator/app/tools/ \
        orchestrator/app/prompts/
git commit -m "feat: role-filter tool list in AgentRunner; add access metadata to all tool schemas"
```

---

### Task 14: Remove `send_email` from live blueprint tool lists

**Files:**
- Modify: `orchestrator/app/seeder.py`
- Modify: `orchestrator/app/prompts/invoice_curator.py`

**Background:** `send_email` is an automation-workflow tool. Having it in the live blueprint's tool list causes Claude to use it for direct user requests instead of `export_invoice_report`. It should only appear inside automation workflow steps, which call it via the executor directly (not through the agent's tool list).

- [ ] **Step 1: Remove `send_email` from `AUTOMATION_TOOLS` in seeder.py**

```python
AUTOMATION_TOOLS = [
    "create_automation", "activate_automation", "list_automations",
    "pause_automation", "cancel_automation", "edit_automation",
    "export_invoice_report", "export_accounting_report",
    # "send_email" removed — automation executor calls it directly; not a user-facing tool
]
```

- [ ] **Step 2: Remove `send_email` reference from invoice_curator.py prompt**

- [ ] **Step 3: Run tests, commit, deploy Phase 3**

```
pytest --tb=short -q
git commit -m "refactor: remove send_email from live blueprint tool lists (automation-only)"
git push
ssh -i "C:\Users\Eranitzkovitch\.ssh\hetzner_ta125" -o StrictHostKeyChecking=no root@178.105.63.248 \
  "cd /opt/whatsapp && git pull && docker compose up --build -d 2>&1"
```

---

## Self-review

**Spec coverage check:**

| Finding | Task |
|---------|------|
| get_status/get_preview overlap | Task 1 |
| two-step flow labels | Task 2 |
| "never call directly" anti-pattern | Task 3 |
| request_confirmation → stage_action | Task 4 |
| record_transaction → record_expense | Task 4 |
| save_email → set_report_email | Task 4 |
| confirm_automation → activate_automation | Task 2 |
| apply_correction → commit_correction | Task 2 |
| export_report split | Task 5 |
| send_email disambiguation | Task 6 |
| list_reminders + cancel_reminder | Task 7 |
| list_participants | Task 8 |
| get_transaction | Task 9 |
| get_debt_summary | Task 10 |
| edit_automation | Task 11 |
| Role-filtering + access metadata | Task 13 |
| send_email hidden from blueprints | Task 14 |

**Not included (intentional YAGNI):**
- `dry_run` param on mutating tools (high effort, can be addressed after these land)
- `get_debt_summary` → `get_invoice_summary` naming overlap avoided (different domains)
- Automation tools in a separate admin blueprint (a deeper architectural change that requires a new blueprint + seeder update; deferred to a follow-on session)

**Placeholder scan:** No TBDs or "implement later" present. All executor code is complete.

**Type consistency:** `stage_action` used consistently across Task 3, 4, and prompts. `commit_correction` used consistently in Task 2 and Task 9 cross-references. `activate_automation` used consistently in Tasks 2 and 11.
