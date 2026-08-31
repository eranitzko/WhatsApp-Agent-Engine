from app.seeder import FAMILY_ACCOUNTING_TOOLS
from app.prompts.family_accounting import FAMILY_ACCOUNTING_SYSTEM_PROMPT
from app.tools.accounting_tools import get_accounting_tools
from app.tools.split_tools import get_split_tools
from app.tools.automation_tools import get_automation_tools
from app.export.tool import get_export_tools


def test_family_accounting_tools_includes_record_split():
    """Regression: record_split existed in the tool registry and was fully
    tested (early-confirmer acks, concurrency, timeouts) but was never added
    to the family_accounting blueprint's canonical tools list, so the agent
    could never actually call it in production — every real multi-way split
    request got a fabricated "ask an admin" refusal instead (found via the
    admin-lifecycle simulation)."""
    assert "record_split" in FAMILY_ACCOUNTING_TOOLS


def test_family_accounting_tools_names_only_registered_tools():
    """Every tool name the blueprint enables must exist in the live tool
    registry — otherwise the agent is told (via ToolRegistry filtering) that
    it has a capability that silently vanishes from its schema list."""
    registered = (
        set(get_accounting_tools().keys())
        | set(get_split_tools().keys())
        | set(get_automation_tools().keys())
        | set(get_export_tools().keys())
    )
    unregistered = set(FAMILY_ACCOUNTING_TOOLS) - registered
    assert not unregistered, (
        f"family_accounting blueprint enables unregistered tools: {unregistered}"
    )


def test_family_accounting_prompt_does_not_mention_set_household():
    """set_household was never implemented as a chat tool — GroupParticipant
    .is_household has no write path (chat tool or admin panel) today, so
    the system prompt must not claim the agent can call it."""
    assert "set_household" not in FAMILY_ACCOUNTING_SYSTEM_PROMPT
