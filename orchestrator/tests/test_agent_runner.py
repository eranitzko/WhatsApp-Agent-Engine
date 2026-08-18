import pytest
from unittest.mock import AsyncMock, MagicMock
from app.agent_runner import AgentRunner
from app.tool_registry import ToolRegistry
from app.db.models import Blueprint

BLUEPRINT = Blueprint(
    id="test_bot",
    display_name="Test Bot",
    system_prompt="You are helpful.",
    model="claude-sonnet-4-6",
    tools_enabled='["say_hello"]',
    max_tool_turns=3,
    context_window=4,
    context_idle_reset_minutes=30,
)


def make_end_turn_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


def make_tool_use_response(tool_name: str, tool_id: str, tool_input: dict, follow_up_text: str):
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = tool_name
    tool_block.id = tool_id
    tool_block.input = tool_input

    first_response = MagicMock()
    first_response.stop_reason = "tool_use"
    first_response.content = [tool_block]

    second_response = make_end_turn_response(follow_up_text)
    return [first_response, second_response]


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register({
        "say_hello": {
            "schema": {"name": "say_hello", "description": "Greet", "input_schema": {"type": "object", "properties": {}}},
            "executor": AsyncMock(return_value="Hello there!"),
        }
    })
    return r


@pytest.fixture
def context():
    ctx = MagicMock()
    ctx.get_history = MagicMock(return_value=[])
    ctx.add = MagicMock()
    ctx.add_turn = MagicMock()
    return ctx


@pytest.fixture
def confirmation_store():
    store = MagicMock()
    store.get = MagicMock(return_value=None)
    store.clear = MagicMock()
    return store


@pytest.mark.asyncio
async def test_run_returns_text_on_end_turn(registry, context, confirmation_store):
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=make_end_turn_response("Hello, how can I help?"))
    runner = AgentRunner(client, registry)
    result = await runner.run(
        blueprint=BLUEPRINT,
        group_jid="123@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        message="hello",
        context=context,
        confirmation_store=confirmation_store,
    )
    assert result == "Hello, how can I help?"


@pytest.mark.asyncio
async def test_run_persists_history(registry, context, confirmation_store):
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=make_end_turn_response("Done."))
    runner = AgentRunner(client, registry)
    await runner.run(
        blueprint=BLUEPRINT,
        group_jid="123@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        message="do something",
        context=context,
        confirmation_store=confirmation_store,
    )
    # Now saves the full turn atomically via add_turn (not two separate add calls)
    context.add_turn.assert_called_once()
    turn_msgs = context.add_turn.call_args[0][1]  # second positional arg = turn_messages
    roles = [m["role"] for m in turn_msgs]
    assert roles == ["user", "assistant"]
    assert turn_msgs[-1]["content"] == "Done."


@pytest.mark.asyncio
async def test_run_executes_tool_and_returns_followup(registry, context, confirmation_store):
    client = AsyncMock()
    responses = make_tool_use_response("say_hello", "tu_001", {}, "I said hello!")
    client.messages.create = AsyncMock(side_effect=responses)
    runner = AgentRunner(client, registry)
    result = await runner.run(
        blueprint=BLUEPRINT,
        group_jid="123@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        message="greet me",
        context=context,
        confirmation_store=confirmation_store,
    )
    assert result == "I said hello!"


@pytest.mark.asyncio
async def test_run_blocks_tool_not_in_blueprint(context, confirmation_store):
    registry = ToolRegistry()
    registry.register({
        "forbidden_tool": {
            "schema": {"name": "forbidden_tool", "description": "Forbidden", "input_schema": {"type": "object", "properties": {}}},
            "executor": AsyncMock(return_value="SECRET DATA"),
        }
    })
    # Blueprint only allows say_hello (not in registry here), so forbidden_tool must be blocked
    client = AsyncMock()
    responses = make_tool_use_response("forbidden_tool", "tu_002", {}, "I tried.")
    client.messages.create = AsyncMock(side_effect=responses)
    runner = AgentRunner(client, registry)
    # Should NOT call forbidden_tool executor because it's not in blueprint.tools_list()
    result = await runner.run(
        blueprint=BLUEPRINT,  # tools_enabled = ["say_hello"]
        group_jid="123@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        message="do forbidden thing",
        context=context,
        confirmation_store=confirmation_store,
    )
    registry._tools["forbidden_tool"]["executor"].assert_not_called()


@pytest.mark.asyncio
async def test_run_executes_multiple_tool_calls_sequentially_not_concurrently(context, confirmation_store):
    """Tool executors write to SQLite, and some (e.g. request_confirmation)
    hold a write transaction open across an awaited network call. Running
    several tool calls from one turn concurrently (asyncio.gather) caused a
    real production bug: recording a multi-person expense split issues one
    record_expense call per person, and the resulting concurrent SQLite
    writes collided with 'database is locked', aborting the entire turn
    with no reply at all. Tool calls within a turn must run sequentially."""
    import asyncio as _asyncio

    order: list[str] = []

    async def slow_tool_a(params, **ctx):
        order.append("a-start")
        await _asyncio.sleep(0.05)
        order.append("a-end")
        return "a-done"

    async def slow_tool_b(params, **ctx):
        order.append("b-start")
        await _asyncio.sleep(0.05)
        order.append("b-end")
        return "b-done"

    registry = ToolRegistry()
    registry.register({
        "tool_a": {
            "schema": {"name": "tool_a", "description": "x", "input_schema": {"type": "object", "properties": {}}},
            "executor": slow_tool_a,
        },
        "tool_b": {
            "schema": {"name": "tool_b", "description": "y", "input_schema": {"type": "object", "properties": {}}},
            "executor": slow_tool_b,
        },
    })

    block_a = MagicMock()
    block_a.type = "tool_use"
    block_a.name = "tool_a"
    block_a.id = "tu_a"
    block_a.input = {}
    block_b = MagicMock()
    block_b.type = "tool_use"
    block_b.name = "tool_b"
    block_b.id = "tu_b"
    block_b.input = {}

    first_response = MagicMock()
    first_response.stop_reason = "tool_use"
    first_response.content = [block_a, block_b]
    second_response = make_end_turn_response("Both done.")

    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=[first_response, second_response])

    blueprint = Blueprint(
        id="test_bot_two_tools",
        display_name="Test Bot",
        system_prompt="You are helpful.",
        model="claude-sonnet-4-6",
        tools_enabled='["tool_a", "tool_b"]',
        max_tool_turns=3,
        context_window=4,
        context_idle_reset_minutes=30,
    )

    runner = AgentRunner(client, registry)
    result = await runner.run(
        blueprint=blueprint,
        group_jid="123@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        message="do both",
        context=context,
        confirmation_store=confirmation_store,
    )

    assert result == "Both done."
    # Sequential: a fully finishes before b starts. Concurrent (asyncio.gather)
    # would interleave as ["a-start", "b-start", "a-end", "b-end"].
    assert order == ["a-start", "a-end", "b-start", "b-end"]


@pytest.mark.asyncio
async def test_admin_tools_filtered_for_non_admins():
    """Non-admin users must not see admin-only tools in the API call."""
    import json
    from unittest.mock import MagicMock, AsyncMock
    from app.agent_runner import AgentRunner
    from app.tool_registry import ToolRegistry
    from app.db.models import Blueprint

    reg = ToolRegistry()
    reg.register({
        "user_tool": {
            "schema": {
                "name": "user_tool", "description": "x",
                "input_schema": {"type": "object", "properties": {}, "required": []},
                "access": "user",
            },
            "executor": AsyncMock(),
        },
        "admin_tool": {
            "schema": {
                "name": "admin_tool", "description": "y",
                "input_schema": {"type": "object", "properties": {}, "required": []},
                "access": "admin",
            },
            "executor": AsyncMock(),
        },
    })

    captured: list = []

    async def fake_create(**kwargs):
        captured.extend(kwargs.get("tools", []))
        block = MagicMock(); block.type = "text"; block.text = "ok"
        resp = MagicMock(); resp.stop_reason = "end_turn"; resp.content = [block]
        return resp

    client = MagicMock()
    client.messages.create = fake_create
    runner = AgentRunner(client, reg)

    bp = Blueprint(
        id="bp", system_prompt="p", model="m", max_tool_turns=1,
        context_window=4, context_idle_reset_minutes=60,
        tools_enabled=json.dumps(["user_tool", "admin_tool"]),
    )
    context = MagicMock()
    context.get_history.return_value = []
    context.add = MagicMock()
    cs = MagicMock(); cs.get.return_value = None

    await runner.run(
        blueprint=bp, group_jid="g@g.us", sender="p@s.whatsapp.net",
        is_admin=False, message="hi", context=context, confirmation_store=cs,
    )

    tool_names = [t["name"] for t in captured]
    assert "user_tool" in tool_names
    assert "admin_tool" not in tool_names  # filtered out for non-admin
