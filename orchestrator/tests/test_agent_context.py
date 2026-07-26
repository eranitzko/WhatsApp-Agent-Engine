"""Regression test for the orphaned tool_use/tool_result corruption bug.

Reproduces the exact mechanism identified in production: a naive count-based
trim (`messages[-n:]`) can land between an assistant tool_use block and its
paired user tool_result block, keeping the tool_result while dropping its
tool_use — which the Anthropic API rejects with "unexpected tool_use_id".

`GroupContext.add()` used to do exactly this. `add_turn()` was already safe
(it only cuts at logical turn-start boundaries). The fix makes `add()` and
`get_history()` share the same turn-boundary-safe trim as `add_turn()`.
"""
from unittest.mock import patch

from app.agent.context import GroupContext
from tests.conftest import SessionCM


def _has_orphaned_tool_result(messages: list[dict]) -> bool:
    """True if any tool_result block's tool_use_id has no preceding tool_use."""
    known_tool_use_ids: set[str] = set()
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                known_tool_use_ids.add(block["id"])
            elif block.get("type") == "tool_result":
                if block["tool_use_id"] not in known_tool_use_ids:
                    return True
    return False


def test_add_never_orphans_a_tool_result(db):
    """Reproduces the production corruption: two sequential add() calls at a
    message count that would land a naive slice between a tool_use and its
    tool_result. Before the fix, the second add() call orphaned the
    tool_result. After the fix, add() shares add_turn's boundary-safe trim.
    """
    with patch("app.agent.context.SessionLocal", return_value=SessionCM(db)):
        ctx = GroupContext("test-group")
        # Seed a complete tool-use turn directly (as if written earlier).
        ctx.messages = [
            {"role": "user", "content": "record my expense"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1", "name": "record_expense", "input": {}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "Recorded."}
            ]},
            {"role": "assistant", "content": "Recorded, thanks!"},
        ]

        # max_pairs=2 -> max_messages=4 in the old naive-slice code — chosen so
        # two more plain-text messages push the cut exactly between tu_1's
        # tool_use and tool_result.
        ctx.add("user", "one more message", max_pairs=2)
        assert not _has_orphaned_tool_result(ctx.messages), (
            "add() orphaned a tool_result after the first append"
        )

        ctx.add("assistant", "a reply", max_pairs=2)
        assert not _has_orphaned_tool_result(ctx.messages), (
            "add() orphaned a tool_result after the second append — "
            "this is the exact production corruption"
        )


def test_get_history_never_returns_an_orphaned_tool_result(db):
    """The read path (get_history) has the same naive-slice hazard as add()
    did: even if stored messages are internally consistent, a count-based
    read-time slice can still start mid-turn.
    """
    with patch("app.agent.context.SessionLocal", return_value=SessionCM(db)):
        ctx = GroupContext("test-group-2")
        ctx.messages = [
            {"role": "user", "content": "first turn"},
            {"role": "assistant", "content": "first reply"},
            {"role": "user", "content": "record my expense"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_2", "name": "record_expense", "input": {}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_2", "content": "Recorded."}
            ]},
            {"role": "assistant", "content": "Recorded, thanks!"},
            {"role": "user", "content": "third turn"},
            {"role": "assistant", "content": "third reply"},
        ]
        ctx.touch()

        # max_pairs=2 -> max_messages=4: a naive messages[-4:] would return
        # [tool_result, assistant_text, third_turn, third_reply] — an
        # orphaned tool_result at the start of the returned history.
        history = ctx.get_history(max_pairs=2)
        assert not _has_orphaned_tool_result(history), (
            "get_history() returned a slice with an orphaned tool_result"
        )
