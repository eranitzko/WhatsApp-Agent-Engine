# Automation Engine — Design Spec

**Date:** 2026-06-04
**Status:** Approved

## Overview

A blueprint-agnostic automation engine that lets group admins configure scheduled, recurring, inactivity-triggered, and threshold-triggered actions via natural language in WhatsApp. Built once at the engine level — every current and future blueprint inherits it automatically.

---

## Goals

- One-off and recurring scheduled actions (e.g. "every Friday at 9am send a debt summary")
- Inactivity detection (e.g. "if no invoices for 3 days, remind the group")
- Threshold triggers (e.g. "when monthly spending exceeds ₪5,000, notify the group")
- Event triggers (placeholder — group join/leave, extensible)
- All management via natural language + confirmation before any rule is saved
- Works for all blueprints: invoice_curator, family_accounting, and future bots

---

## Section 1: Data Model

### `AutomationRule` table (migration `010_automation_engine.py`)

| Column | Type | Notes |
|---|---|---|
| `id` | String(36) UUID PK | |
| `group_jid` | String FK → group_registry | |
| `name` | String | Human label, e.g. "Friday debt reminder" |
| `rule_type` | String | `one_off`, `recurring`, `inactivity`, `threshold`, `event_trigger` |
| `schedule_cron` | String, nullable | e.g. `0 9 * * 5` for recurring/one_off |
| `inactivity_hours` | Integer, nullable | Fire after N hours of silence in the group |
| `threshold_config` | Text (JSON), nullable | `{"metric": "monthly_total", "op": ">", "value": 5000}` |
| `action_type` | String | `send_message`, `run_agent_action` |
| `action_config` | Text (JSON) | `{"message": "...", "mentions": [...]}` or `{"action": "monthly_report"}` |
| `status` | String | `pending_confirm`, `active`, `paused`, `done` |
| `last_fired_at` | DateTime(tz), nullable | Used to prevent double-firing |
| `created_at` | DateTime(tz) | |

### `rule_type` values

| Value | Description |
|---|---|
| `one_off` | Fires once at a specific future datetime |
| `recurring` | Repeats on a cron schedule |
| `inactivity` | Fires when group has been silent for `inactivity_hours` |
| `threshold` | Fires when a DB metric crosses a configured value |
| `event_trigger` | Placeholder for group join/leave events |

### V1 threshold metrics (`threshold_config.metric`)

| Metric key | Blueprint | Description |
|---|---|---|
| `monthly_invoice_total` | invoice_curator | Sum of `Invoice.amount_ils` for current month in this group |
| `invoice_count_this_month` | invoice_curator | Count of invoices this month in this group |
| `open_debt_amount` | family_accounting | Sum of unsettled `LedgerEntry` amounts in this group |
| `days_since_last_settlement` | family_accounting | Days since last `LedgerSettlement` in this group |

Supported operators: `>`, `<`, `>=`, `<=`.

---

## Section 2: Automation Tools

Five tools registered in the existing `ToolRegistry` at startup — available to every blueprint. The agent handles all natural language understanding; it calls these tools with structured params derived from conversation.

| Tool | Signature | Description |
|---|---|---|
| `create_automation` | `(group_jid, name, rule_type, schedule_cron?, inactivity_hours?, threshold_config?, action_type, action_config)` | Saves rule with `status=pending_confirm`, returns plain-English confirmation summary |
| `confirm_automation` | `(id)` | Flips status to `active` |
| `list_automations` | `(group_jid)` | Returns all `active`/`paused` rules for this group |
| `pause_automation` | `(id)` | Sets `status=paused` |
| `cancel_automation` | `(id)` | Deletes rule permanently |

### Admin flow example

> Admin: *"Remind the group every Friday if there are open debts"*
> Agent calls `create_automation(...)` → tool returns:
> *"Every Friday at 9am I'll check for open debts and send a reminder to this group. Want me to set this up?"*
> Admin: *"yes"*
> Agent calls `confirm_automation(id)` → *"Done, automation saved."*

`create_automation` handles natural-language-to-cron translation internally (e.g. "every Friday" → `0 9 * * 5`, "1st of every month" → `0 9 1 * *`) using `croniter`.

### File location

`orchestrator/app/tools/automation_tools.py` — registered via `get_automation_tools()` in `main.py`.

---

## Section 3: Scheduler Extension

Three new APScheduler jobs added to `orchestrator/app/scheduler.py`, all on a **60-minute interval**. Rules firing up to 59 minutes late is acceptable for reminders and summaries.

| Job | Interval | Description |
|---|---|---|
| `_fire_recurring_rules` | 60 min | Finds `active` `recurring`/`one_off` rules whose `schedule_cron` is due; sets `last_fired_at`; marks `one_off` as `done` |
| `_check_inactivity` | 60 min | For each group with an `inactivity` rule, checks last message timestamp in `ConversationHistory`; fires if silence ≥ `inactivity_hours` |
| `_evaluate_thresholds` | 60 min | For each `threshold` rule, runs the metric evaluator; fires if condition met and `last_fired_at` is not within last 24h |

**Cron evaluation:** uses `croniter.match(cron_expr, now)` to determine if a rule is due in the current hour window.

**Inactivity tracking:** queries `MAX(created_at)` from `ConversationHistory` for the group — no new tracking table needed.

**Double-fire prevention:** `last_fired_at` is set inside a DB transaction before the action executes. `one_off` rules get `status = done` after firing.

---

## Section 4: Action Execution

A single `AutomationExecutor` class in `orchestrator/app/automation/executor.py` handles all rule firings.

### `send_message`

Posts `action_config["message"]` to bridge `/send`. Supports optional `action_config["mentions"]` (list of phone numbers).

### `run_agent_action`

Invokes a named function from a startup-registered dict `{action_name → async callable(group_jid, db, config)}`.

**V1 registered actions:**

| Action name | Blueprint | Description |
|---|---|---|
| `monthly_invoice_report` | invoice_curator | Generates and sends the monthly PDF/Excel report to the group |
| `balance_summary` | family_accounting | Fetches open debts, formats a summary, sends to group |

Actions are registered in `main.py` at startup alongside tools. Adding a new bot's action = one new entry in the registry dict.

### Error handling

If an action raises, the executor logs the exception, leaves `last_fired_at` set (prevents immediate retry), and does not crash the scheduler. The rule remains `active` and will retry on the next hourly tick.

---

## Section 5: Threshold Evaluators

`orchestrator/app/automation/evaluators.py` — a `ThresholdEvaluator` class with one method per metric. Each method queries the DB and returns a `float`. The executor compares this against `threshold_config["value"]` using `threshold_config["op"]`.

**Fire-once-per-24h guard:** threshold rules skip re-evaluation if `last_fired_at` is within the last 24 hours, preventing spam when a condition stays true for multiple days.

**Adding a new metric:** add one method to `ThresholdEvaluator` — no changes to executor or scheduler.

---

## File Map

| Action | Path |
|---|---|
| Create | `orchestrator/app/db/migrations/versions/010_automation_engine.py` |
| Modify | `orchestrator/app/db/models.py` — add `AutomationRule` |
| Create | `orchestrator/app/automation/__init__.py` |
| Create | `orchestrator/app/automation/executor.py` |
| Create | `orchestrator/app/automation/evaluators.py` |
| Create | `orchestrator/app/tools/automation_tools.py` |
| Modify | `orchestrator/app/scheduler.py` — add 3 new jobs |
| Modify | `orchestrator/app/main.py` — register automation tools + actions |
| Create | `orchestrator/tests/test_automation_tools.py` |
| Create | `orchestrator/tests/test_automation_scheduler.py` |
| Create | `orchestrator/tests/test_automation_evaluators.py` |

---

## Dependencies

- `croniter` — cron expression evaluation (add to `requirements.txt`)

---

## Out of Scope (v1)

- Full agent inference on automation fire (synthetic message through `AgentRunner`) — deferred
- Event triggers (join/leave) — placeholder only, no implementation
- Admin panel UI for automation rules — deferred
- Editing an existing rule (cancel + recreate is the v1 workflow)
