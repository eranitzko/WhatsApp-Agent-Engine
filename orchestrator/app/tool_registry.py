from typing import Any


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, dict] = {}  # tool_name -> {"schema": dict, "executor": callable}
        self._schema_cache: dict[frozenset, list[dict]] = {}  # frozenset(names) -> schemas

    def register(self, tools: dict[str, dict]) -> None:
        self._tools.update(tools)
        self._schema_cache.clear()  # invalidate on any new registration

    _ANTHROPIC_SCHEMA_KEYS = {"name", "description", "input_schema", "cache_control"}

    def get_schemas(self, tool_names: list[str]) -> list[dict]:
        """Return Claude-API-compatible schemas for the given tool names.

        Result is cached keyed on the frozenset of names — blueprint tool lists
        are stable between turns so this eliminates the dict comprehension from
        the hot path.
        """
        key = frozenset(tool_names)
        cached = self._schema_cache.get(key)
        if cached is not None:
            return cached
        schemas = [
            {k: v for k, v in self._tools[n]["schema"].items() if k in self._ANTHROPIC_SCHEMA_KEYS}
            for n in tool_names if n in self._tools
        ]
        self._schema_cache[key] = schemas
        return schemas

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._tools

    def list_tools(self) -> dict[str, dict]:
        """Return a shallow copy of the registered tools dict."""
        return dict(self._tools)

    async def execute(self, tool_name: str, params: dict, **ctx) -> Any:
        if tool_name not in self._tools:
            return f"Unknown tool: {tool_name}"
        return await self._tools[tool_name]["executor"](params, **ctx)
