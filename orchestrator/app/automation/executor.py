"""AutomationExecutor — fires a single AutomationRule's action."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app import registry_ref
from app.bridge_client import send_message

if TYPE_CHECKING:
    from app.db.models import AutomationRule
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class AutomationExecutor:
    """Executes a single AutomationRule's action via the live ToolRegistry.

    Supported action_types:
      - send_message: sends a WhatsApp message to the group.
      - run_agent_action: calls one tool from the ToolRegistry.
      - workflow: calls a sequence of tools in order; stops on first failure.
        confirmation_store is passed in ctx so stage_action works.
    """

    async def execute(self, rule: "AutomationRule", db: "Session") -> None:
        """Execute the action for a rule. Logs errors but never raises."""
        # DIAG: dump rule state at execution time
        logger.info(
            "DIAG AutomationExecutor.execute | rule_id=%s | name=%r | rule_type=%s | "
            "action_type=%s | status=%s | last_fired_at=%s | schedule_cron=%r",
            rule.id, rule.name, rule.rule_type, rule.action_type,
            rule.status, rule.last_fired_at, rule.schedule_cron,
        )
        try:
            config = json.loads(rule.action_config)
            if rule.action_type == "send_message":
                await self._send_message(rule.group_jid, config)
            elif rule.action_type == "run_agent_action":
                await self._run_tool(rule.group_jid, config)
            elif rule.action_type == "workflow":
                await self._run_workflow(rule.group_jid, config, db=db)
            else:
                logger.error("Unknown action_type %r for rule %s", rule.action_type, rule.id)
        except Exception:
            logger.exception("AutomationExecutor.execute failed for rule %s", rule.id)

    async def _send_message(self, group_jid: str, config: dict) -> None:
        await send_message(
            group_jid,
            config.get("message", ""),
            mentions=config.get("mentions"),
        )

    async def _run_tool(self, group_jid: str, config: dict) -> None:
        from app.agent.confirmation import confirmation_store as _store

        tool_name = config.get("action", "")
        params = {k: v for k, v in config.items() if k != "action"}

        try:
            reg = registry_ref.get_registry()
        except RuntimeError:
            logger.error("AutomationExecutor: tool registry not available for action %r", tool_name)
            return

        if not reg.has_tool(tool_name):
            logger.warning("Automation: tool %r not in registry (rule group %s)", tool_name, group_jid)
            await send_message(
                group_jid,
                f"⚙️ Automation could not run: tool '{tool_name}' is not available. "
                f"Ask your administrator to enable it.",
            )
            return

        result = await reg.execute(tool_name, params, group_jid=group_jid, is_admin=True, sender="", confirmation_store=_store)
        logger.info("Automation tool %r returned for %s: %s", tool_name, group_jid, result)

    async def _run_workflow(self, group_jid: str, config: dict, db: "Session | None" = None) -> None:
        """Execute a sequence of tool steps with shared WorkflowContext.

        - Resolves {{variable}} templates in all step params before execution.
        - Stores step outputs under output_key for use in later steps.
        - Passes confirmation_store in ctx so stage_action works.
        - Stops at the first step that raises or uses an unknown tool.
        """
        from app.agent.confirmation import confirmation_store as _store
        from app.automation.context import WorkflowContext

        steps = config.get("steps", [])
        if not steps:
            logger.warning("Automation workflow has no steps for group %s", group_jid)
            return

        try:
            reg = registry_ref.get_registry()
        except RuntimeError:
            logger.error("AutomationExecutor: tool registry not available for workflow")
            return

        ctx = WorkflowContext(group_jid, db=db)

        for i, step in enumerate(steps):
            tool_name = step.get("tool", "")
            output_key = step.get("output_key")
            raw_params = step.get("params", {})
            params = ctx.resolve_dict(raw_params)

            # DIAG: log each step with raw and resolved params so we can see template substitution
            logger.info(
                "DIAG workflow step %d | group=%s | tool=%r | output_key=%r | "
                "raw_params=%s | resolved_params=%s",
                i, group_jid, tool_name, output_key,
                {k: (v[:80] if isinstance(v, str) and len(v) > 80 else v) for k, v in raw_params.items()},
                {k: (v[:80] if isinstance(v, str) and len(v) > 80 else v) for k, v in params.items()},
            )

            if not reg.has_tool(tool_name):
                logger.warning(
                    "Automation workflow step %d: tool %r not in registry (group %s)",
                    i, tool_name, group_jid,
                )
                await send_message(
                    group_jid,
                    f"⚙️ Automation workflow could not run step {i + 1}: "
                    f"tool '{tool_name}' is not available. "
                    f"Ask your administrator to enable it.",
                )
                return

            try:
                result = await reg.execute(
                    tool_name, params,
                    group_jid=group_jid,
                    is_admin=True,
                    sender="",
                    confirmation_store=_store,
                )
                logger.info(
                    "Automation workflow step %d (%r) returned for %s: %s",
                    i, tool_name, group_jid, result,
                )
                if tool_name == "stage_action":
                    # Surface the confirmation prompt in the group and stop.
                    # Remaining steps don't run — the confirmed action in
                    # confirmation_store fires when the user replies "yes".
                    if isinstance(result, str):
                        await send_message(group_jid, result)
                    return
                if output_key and isinstance(result, str):
                    ctx.store(output_key, result)
            except Exception:
                logger.exception(
                    "Automation workflow stopped at step %d (%r) for group %s",
                    i, tool_name, group_jid,
                )
                return
