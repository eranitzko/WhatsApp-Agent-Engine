import json
from datetime import datetime, timezone
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
            if confirmation_store.is_confirm(message):
                result = await self.registry.execute(
                    pending.action, pending.params,
                    group_jid=group_jid, sender=sender, is_admin=is_admin,
                )
                confirmation_store.clear(group_jid)
                context.add(group_jid, "user", message, max_pairs=blueprint.context_window)
                context.add(group_jid, "assistant", str(result), max_pairs=blueprint.context_window)
                return str(result)
            elif confirmation_store.is_cancel(message):
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
                "text": f"Today's date: {datetime.now(timezone.utc).date()}. Sender is_admin: {is_admin}.",
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
