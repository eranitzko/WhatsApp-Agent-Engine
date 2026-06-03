"""AutomationExecutor — fires a single AutomationRule's action."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Callable, Awaitable

if TYPE_CHECKING:
    from app.db.models import AutomationRule
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ActionFn = Callable[..., Awaitable[None]]


class AutomationExecutor:
    """Executes a single AutomationRule's action.

    Usage:
        executor = AutomationExecutor(actions={"balance_summary": fn})
        executor.register_action("monthly_invoice_report", fn2)
        await executor.execute(rule, db)
    """

    def __init__(self, actions: dict[str, ActionFn] | None = None):
        self._actions: dict[str, ActionFn] = dict(actions or {})

    def register_action(self, name: str, fn: ActionFn) -> None:
        self._actions[name] = fn

    async def execute(self, rule: "AutomationRule", db: "Session") -> None:
        """Execute the action for a rule. Logs errors but never raises."""
        try:
            config = json.loads(rule.action_config)
            if rule.action_type == "send_message":
                await self._send_message(rule.group_jid, config)
            elif rule.action_type == "run_agent_action":
                action_name = config.get("action", "")
                fn = self._actions.get(action_name)
                if fn is None:
                    logger.error(
                        "Automation action %r not registered (rule %s)", action_name, rule.id
                    )
                    return
                await fn(group_jid=rule.group_jid, db=db, config=config)
            else:
                logger.error("Unknown action_type %r for rule %s", rule.action_type, rule.id)
        except Exception:
            logger.exception("AutomationExecutor.execute failed for rule %s", rule.id)

    async def _send_message(self, group_jid: str, config: dict) -> None:
        from app.bridge_client import send_message
        await send_message(
            group_jid,
            config.get("message", ""),
            mentions=config.get("mentions"),
        )
