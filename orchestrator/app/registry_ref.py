"""Shared reference to the live ToolRegistry. Set by main.py at startup.

Import get_registry() anywhere that needs access to the live tool list
without creating circular imports through main.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.tool_registry import ToolRegistry

_registry: "ToolRegistry | None" = None


def set_registry(r: "ToolRegistry") -> None:
    """Called once by main.py lifespan after all tools are registered."""
    global _registry
    _registry = r


def get_registry() -> "ToolRegistry":
    """Return the live ToolRegistry. Raises RuntimeError if not yet set."""
    if _registry is None:
        raise RuntimeError("ToolRegistry not yet initialised — call set_registry() first")
    return _registry
