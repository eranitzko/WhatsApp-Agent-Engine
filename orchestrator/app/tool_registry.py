from typing import Any


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, dict] = {}  # tool_name -> {"schema": dict, "executor": callable}

    def register(self, tools: dict[str, dict]) -> None:
        self._tools.update(tools)

    _ANTHROPIC_SCHEMA_KEYS = {"name", "description", "input_schema", "cache_control"}

    def get_schemas(self, tool_names: list[str]) -> list[dict]:
        return [
            {k: v for k, v in self._tools[n]["schema"].items() if k in self._ANTHROPIC_SCHEMA_KEYS}
            for n in tool_names if n in self._tools
        ]

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._tools

    def list_tools(self) -> dict[str, dict]:
        """Return a shallow copy of the registered tools dict."""
        return dict(self._tools)

    async def execute(self, tool_name: str, params: dict, **ctx) -> Any:
        if tool_name not in self._tools:
            return f"Unknown tool: {tool_name}"
        return await self._tools[tool_name]["executor"](params, **ctx)
