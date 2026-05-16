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
    assert context.add.call_count == 2  # user message + assistant reply


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
