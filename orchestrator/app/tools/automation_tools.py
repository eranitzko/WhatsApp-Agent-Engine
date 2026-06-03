"""Automation engine CRUD tools for ToolRegistry.

Five tools: create_automation, confirm_automation, list_automations,
pause_automation, cancel_automation.

group_jid is always taken from **ctx (injected by AgentRunner), never from params.
"""

from __future__ import annotations

import json
import logging

from app.db.session import SessionLocal
from app.db.models import AutomationRule

logger = logging.getLogger(__name__)

_VALID_RULE_TYPES = {"one_off", "recurring", "inactivity", "threshold", "event_trigger"}
_VALID_ACTION_TYPES = {"send_message", "run_agent_action"}
_VALID_OPS = {">", "<", ">=", "<="}
_VALID_METRICS = {
    "monthly_invoice_total",
    "invoice_count_this_month",
    "open_debt_amount",
    "days_since_last_settlement",
}


def _describe_rule(rule: AutomationRule) -> str:
    """Generate a plain-English summary of a rule."""
    parts = [f"*{rule.name}*"]
    if rule.rule_type == "recurring" and rule.schedule_cron:
        parts.append(f"Schedule: {rule.schedule_cron}")
    elif rule.rule_type == "one_off" and rule.schedule_cron:
        parts.append(f"Fires once at: {rule.schedule_cron}")
    elif rule.rule_type == "inactivity" and rule.inactivity_hours:
        parts.append(f"After {rule.inactivity_hours}h silence")
    elif rule.rule_type == "threshold" and rule.threshold_config:
        tc = json.loads(rule.threshold_config)
        parts.append(f"When {tc['metric']} {tc['op']} {tc['value']}")
    config = json.loads(rule.action_config)
    if rule.action_type == "send_message":
        preview = config.get("message", "")[:60]
        parts.append(f"Sends: \"{preview}\"")
    else:
        parts.append(f"Runs: {config.get('action', '?')}")
    return " | ".join(parts)


async def _exec_create_automation(params: dict, **ctx) -> str:
    group_jid: str = ctx.get("group_jid", "")
    rule_type = params.get("rule_type", "")
    if rule_type not in _VALID_RULE_TYPES:
        return f"Invalid rule_type '{rule_type}'. Must be one of: {', '.join(sorted(_VALID_RULE_TYPES))}"
    action_type = params.get("action_type", "")
    if action_type not in _VALID_ACTION_TYPES:
        return f"Invalid action_type '{action_type}'. Must be one of: {', '.join(sorted(_VALID_ACTION_TYPES))}"

    threshold_raw = params.get("threshold_config")
    threshold_json: str | None = None
    if threshold_raw and isinstance(threshold_raw, dict):
        if threshold_raw.get("metric") not in _VALID_METRICS:
            return (
                f"Unknown metric '{threshold_raw.get('metric')}'. "
                f"Valid metrics: {', '.join(sorted(_VALID_METRICS))}"
            )
        if threshold_raw.get("op") not in _VALID_OPS:
            return f"Invalid operator '{threshold_raw.get('op')}'. Must be one of: >, <, >=, <="
        threshold_json = json.dumps(threshold_raw)

    action_config_json = json.dumps(params.get("action_config", {}))

    rule = AutomationRule(
        group_jid=group_jid,
        name=params.get("name", "Unnamed automation"),
        rule_type=rule_type,
        schedule_cron=params.get("schedule_cron"),
        inactivity_hours=params.get("inactivity_hours"),
        threshold_config=threshold_json,
        action_type=action_type,
        action_config=action_config_json,
        status="pending_confirm",
    )
    with SessionLocal() as db:
        db.add(rule)
        db.commit()
        rule_id = rule.id
        description = _describe_rule(rule)

    return (
        f"Here's what I'll set up:\n{description}\n\n"
        f"Shall I activate this automation? Reply yes and I'll confirm it.\n"
        f"Rule ID: {rule_id}"
    )


async def _exec_confirm_automation(params: dict, **ctx) -> str:
    rule_id = params.get("id", "")
    group_jid: str = ctx.get("group_jid", "")
    with SessionLocal() as db:
        rule = db.get(AutomationRule, rule_id)
        if rule is None:
            return f"No automation found with ID '{rule_id}'."
        if rule.group_jid != group_jid:
            return "That automation belongs to a different group."
        if rule.status not in ("pending_confirm", "paused"):
            return f"Automation '{rule.name}' cannot be activated (current status: {rule.status})."
        rule.status = "active"
        db.commit()
        name = rule.name
    return f"Automation '{name}' is now active."


async def _exec_list_automations(params: dict, **ctx) -> str:
    group_jid: str = ctx.get("group_jid", "")
    with SessionLocal() as db:
        rules = (
            db.query(AutomationRule)
            .filter(
                AutomationRule.group_jid == group_jid,
                AutomationRule.status.in_(["active", "paused"]),
            )
            .order_by(AutomationRule.created_at)
            .all()
        )
        if not rules:
            return "No active automations for this group."
        lines = [
            f"{i + 1}. [{r.status.upper()}] {_describe_rule(r)} (ID: {r.id})"
            for i, r in enumerate(rules)
        ]
    return "Automations:\n" + "\n".join(lines)


async def _exec_pause_automation(params: dict, **ctx) -> str:
    rule_id = params.get("id", "")
    group_jid: str = ctx.get("group_jid", "")
    with SessionLocal() as db:
        rule = db.get(AutomationRule, rule_id)
        if rule is None:
            return f"No automation found with ID '{rule_id}'."
        if rule.group_jid != group_jid:
            return "That automation belongs to a different group."
        if rule.status != "active":
            return f"Automation '{rule.name}' is not active (current status: {rule.status})."
        rule.status = "paused"
        db.commit()
        name = rule.name
    return f"Automation '{name}' paused."


async def _exec_cancel_automation(params: dict, **ctx) -> str:
    rule_id = params.get("id", "")
    group_jid: str = ctx.get("group_jid", "")
    with SessionLocal() as db:
        rule = db.get(AutomationRule, rule_id)
        if rule is None:
            return f"No automation found with ID '{rule_id}'."
        if rule.group_jid != group_jid:
            return "That automation belongs to a different group."
        name = rule.name
        db.delete(rule)
        db.commit()
    return f"Automation '{name}' deleted."


_SCHEMAS: dict[str, dict] = {
    "create_automation": {
        "name": "create_automation",
        "description": (
            "Create a new automation rule for this group. The rule is saved as pending_confirm "
            "and must be activated with confirm_automation after user confirms. "
            "For one_off: schedule_cron is an ISO 8601 datetime string (e.g. '2026-06-15T09:00:00+00:00'). "
            "For recurring: schedule_cron is a cron expression (e.g. '0 9 * * 5' = every Friday 9am). "
            "For inactivity: supply inactivity_hours. "
            "For threshold: supply threshold_config."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human label, e.g. 'Friday debt reminder'"},
                "rule_type": {
                    "type": "string",
                    "enum": ["one_off", "recurring", "inactivity", "threshold"],
                    "description": "Trigger type",
                },
                "schedule_cron": {
                    "type": "string",
                    "description": (
                        "Cron expression for recurring (e.g. '0 9 * * 5'), "
                        "or ISO 8601 datetime for one_off (e.g. '2026-06-15T09:00:00+00:00')"
                    ),
                },
                "inactivity_hours": {
                    "type": "integer",
                    "description": "Hours of group silence before firing (inactivity rules only)",
                },
                "threshold_config": {
                    "type": "object",
                    "properties": {
                        "metric": {
                            "type": "string",
                            "enum": [
                                "monthly_invoice_total",
                                "invoice_count_this_month",
                                "open_debt_amount",
                                "days_since_last_settlement",
                            ],
                        },
                        "op": {"type": "string", "enum": [">", "<", ">=", "<="]},
                        "value": {"type": "number"},
                    },
                    "required": ["metric", "op", "value"],
                    "description": "Threshold condition (threshold rules only)",
                },
                "action_type": {
                    "type": "string",
                    "enum": ["send_message", "run_agent_action"],
                    "description": "What to do when the rule fires",
                },
                "action_config": {
                    "type": "object",
                    "description": (
                        "For send_message: {\"message\": \"...\", \"mentions\": [\"972500000001\"]} "
                        "For run_agent_action: {\"action\": \"balance_summary\"} or "
                        "{\"action\": \"monthly_invoice_report\"}"
                    ),
                },
            },
            "required": ["name", "rule_type", "action_type", "action_config"],
        },
    },
    "confirm_automation": {
        "name": "confirm_automation",
        "description": "Activate a pending automation rule after the user confirms.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The rule ID returned by create_automation"},
            },
            "required": ["id"],
        },
    },
    "list_automations": {
        "name": "list_automations",
        "description": "List all active and paused automation rules for this group.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "pause_automation": {
        "name": "pause_automation",
        "description": "Pause an active automation rule. It will not fire while paused.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The automation rule ID"},
            },
            "required": ["id"],
        },
    },
    "cancel_automation": {
        "name": "cancel_automation",
        "description": "Permanently delete an automation rule.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The automation rule ID"},
            },
            "required": ["id"],
        },
    },
}


def get_automation_tools() -> dict[str, dict]:
    """Return all automation tools for registration in ToolRegistry."""
    return {
        "create_automation": {
            "schema": _SCHEMAS["create_automation"],
            "executor": _exec_create_automation,
        },
        "confirm_automation": {
            "schema": _SCHEMAS["confirm_automation"],
            "executor": _exec_confirm_automation,
        },
        "list_automations": {
            "schema": _SCHEMAS["list_automations"],
            "executor": _exec_list_automations,
        },
        "pause_automation": {
            "schema": _SCHEMAS["pause_automation"],
            "executor": _exec_pause_automation,
        },
        "cancel_automation": {
            "schema": _SCHEMAS["cancel_automation"],
            "executor": _exec_cancel_automation,
        },
    }
