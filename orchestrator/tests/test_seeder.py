from app.seeder import FAMILY_ACCOUNTING_TOOLS


def test_family_accounting_tools_includes_record_split():
    """Regression: record_split existed in the tool registry and was fully
    tested (early-confirmer acks, concurrency, timeouts) but was never added
    to the family_accounting blueprint's canonical tools list, so the agent
    could never actually call it in production — every real multi-way split
    request got a fabricated "ask an admin" refusal instead (found via the
    admin-lifecycle simulation)."""
    assert "record_split" in FAMILY_ACCOUNTING_TOOLS
