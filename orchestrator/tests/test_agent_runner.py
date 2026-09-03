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
def registry_with_admin_tool():
    """A registry with one user-visible tool and one admin-only tool —
    used to test the non-admin unavailable-tools notice."""
    r = ToolRegistry()
    r.register({
        "say_hello": {
            "schema": {"name": "say_hello", "description": "Greet", "input_schema": {"type": "object", "properties": {}}},
            "executor": AsyncMock(return_value="Hello there!"),
        },
        "stage_action": {
            "schema": {"name": "stage_action", "description": "Stage.", "access": "admin",
                       "input_schema": {"type": "object", "properties": {}}},
            "executor": AsyncMock(return_value="Staged."),
        },
    })
    return r


BLUEPRINT_WITH_ADMIN_TOOL = Blueprint(
    id="test_bot_admin",
    display_name="Test Bot",
    system_prompt="You are helpful.",
    model="claude-sonnet-4-6",
    tools_enabled='["say_hello", "stage_action"]',
    max_tool_turns=3,
    context_window=4,
    context_idle_reset_minutes=30,
)


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
async def test_non_admin_gets_unavailable_tools_notice(registry_with_admin_tool, context, confirmation_store):
    """Regression: a non-admin's reduced toolset (e.g. no stage_action) was
    observed to produce a fabricated confirmation message instead of an
    honest refusal. The system prompt now names exactly which tools are
    missing this turn, computed from the real allowed_tools list."""
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=make_end_turn_response("Sure."))
    runner = AgentRunner(client, registry_with_admin_tool)
    await runner.run(
        blueprint=BLUEPRINT_WITH_ADMIN_TOOL,
        group_jid="123@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        message="do something admin-only",
        context=context,
        confirmation_store=confirmation_store,
    )
    system_blocks = client.messages.create.call_args.kwargs["system"]
    notice = next((b["text"] for b in system_blocks if "unavailable this turn" in b["text"]), None)
    assert notice is not None
    assert "stage_action" in notice
    assert "say_hello" not in notice


@pytest.mark.asyncio
async def test_admin_gets_no_unavailable_tools_notice(registry_with_admin_tool, context, confirmation_store):
    """An admin has every tool, so there's nothing to warn about — the
    notice block must not appear at all (it would be a confusing no-op)."""
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=make_end_turn_response("Sure."))
    runner = AgentRunner(client, registry_with_admin_tool)
    await runner.run(
        blueprint=BLUEPRINT_WITH_ADMIN_TOOL,
        group_jid="123@g.us",
        sender="admin@s.whatsapp.net",
        is_admin=True,
        message="do something admin-only",
        context=context,
        confirmation_store=confirmation_store,
    )
    system_blocks = client.messages.create.call_args.kwargs["system"]
    assert not any("unavailable this turn" in b["text"] for b in system_blocks)


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
async def test_confirmation_execute_blocks_action_not_in_blueprint(context):
    """Regression (security review finding): the normal tool-use loop checks
    `tc.name not in allowed_tools` before executing (see
    test_run_blocks_tool_not_in_blueprint above), but the separate
    confirmation-execute path called registry.execute(pending.action, ...)
    directly against the single GLOBAL tool registry with no such check —
    so a staged action naming a tool outside the current blueprint's
    allowlist would run unchecked once confirmed. Uses the real
    ConfirmationStore/PendingAction (not a mock) since the exact shape of
    pending.action matters here."""
    from app.agent.confirmation import ConfirmationStore

    registry = ToolRegistry()
    forbidden_executor = AsyncMock(return_value="SECRET DATA")
    registry.register({
        "forbidden_tool": {
            "schema": {"name": "forbidden_tool", "description": "Forbidden", "input_schema": {"type": "object", "properties": {}}},
            "executor": forbidden_executor,
        }
    })
    real_store = ConfirmationStore()
    real_store.set("123@g.us", "forbidden_tool", {}, "do the forbidden thing", staged_by="")

    client = AsyncMock()
    runner = AgentRunner(client, registry)
    result = await runner.run(
        blueprint=BLUEPRINT,  # tools_enabled = ["say_hello"] only — forbidden_tool is not in it
        group_jid="123@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        message="yes",
        context=context,
        confirmation_store=real_store,
    )
    forbidden_executor.assert_not_called()
    client.messages.create.assert_not_called()
    assert real_store.get("123@g.us") is None  # cleared, not left dangling


@pytest.mark.asyncio
async def test_stage_action_free_form_confirm_via_ai_classification(registry, context):
    """Regression: a natural non-exact-match reply (e.g. "לאשר", Hebrew for
    "to approve") previously fell straight through to the normal agent loop
    with the pending action still staged and unresolved — reply_words.py's
    word list doesn't include it. It must now be classified and, if
    unambiguous, executed exactly like an exact-match "yes" would be."""
    from app.agent.confirmation import ConfirmationStore
    real_store = ConfirmationStore()
    real_store.set("321@g.us", "say_hello", {}, "say hello to everyone", staged_by="")

    classify_block = MagicMock()
    classify_block.type = "text"
    classify_block.text = "CONFIRM"
    classify_resp = MagicMock()
    classify_resp.content = [classify_block]

    followup_resp = make_end_turn_response("Done, said hello!")

    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=[classify_resp, followup_resp])
    runner = AgentRunner(client, registry)

    result = await runner.run(
        blueprint=BLUEPRINT,
        group_jid="321@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        message="לאשר",
        context=context,
        confirmation_store=real_store,
    )
    assert result == "Done, said hello!"
    assert real_store.get("321@g.us") is None  # cleared after execution


@pytest.mark.asyncio
async def test_stage_action_unclear_reply_leaves_action_staged(registry, context):
    """A genuinely unrelated reply must not be misclassified as an approval —
    the pending action stays staged (unchanged behavior) and the message
    falls through to the normal agent loop."""
    from app.agent.confirmation import ConfirmationStore
    real_store = ConfirmationStore()
    real_store.set("322@g.us", "say_hello", {}, "say hello to everyone", staged_by="")

    classify_block = MagicMock()
    classify_block.type = "text"
    classify_block.text = "UNCLEAR"
    classify_resp = MagicMock()
    classify_resp.content = [classify_block]
    normal_resp = make_end_turn_response("I don't understand your request.")

    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=[classify_resp, normal_resp])
    runner = AgentRunner(client, registry)

    result = await runner.run(
        blueprint=BLUEPRINT,
        group_jid="322@g.us",
        sender="user@s.whatsapp.net",
        is_admin=False,
        message="what's the weather",
        context=context,
        confirmation_store=real_store,
    )
    assert result == "I don't understand your request."
    assert real_store.get("322@g.us") is not None  # still staged


@pytest.mark.asyncio
async def test_multi_confirmation_free_form_confirm_via_ai_classification(registry, context):
    """Same fix, for the multi-party (split/payment) confirmation flow —
    previously a non-exact-match reply always returned the hardcoded English
    "Please reply 'yes' or 'no'" without ever asking the model."""
    from app.agent.confirmation import ConfirmationStore
    from app.agent.multi_confirmation import MultiConfirmationStore

    mcs = MultiConfirmationStore()
    await mcs.propose(
        group_jid="325@g.us",
        awaiting_phones=["972500", "972502"],
        action="commit_payment",
        commit_params={},
        description="Payment of 50 ILS from 972500 to 972501",
    )

    classify_block = MagicMock()
    classify_block.type = "text"
    classify_block.text = "CONFIRM"
    classify_resp = MagicMock()
    classify_resp.content = [classify_block]

    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=classify_resp)
    runner = AgentRunner(client, registry)

    result = await runner.run(
        blueprint=BLUEPRINT,
        group_jid="325@g.us",
        sender="972500@s.whatsapp.net",
        is_admin=False,
        message="לאשר",
        context=context,
        confirmation_store=ConfirmationStore(),
        multi_confirmation_store=mcs,
        resolved_phone="972500",
    )
    assert "Still waiting for: @972502" in result


@pytest.mark.asyncio
async def test_multi_confirmation_unclear_reply_returns_localized_fallback(registry, context):
    """When the reply is genuinely unrelated, the "you have a pending
    confirmation" fallback is composed in the language of the user's own
    message instead of staying hardcoded English."""
    from app.agent.confirmation import ConfirmationStore
    from app.agent.multi_confirmation import MultiConfirmationStore

    mcs = MultiConfirmationStore()
    await mcs.propose(
        group_jid="326@g.us",
        awaiting_phones=["972503"],
        action="commit_payment",
        commit_params={},
        description="Payment of 50 ILS",
    )

    classify_block = MagicMock()
    classify_block.type = "text"
    classify_block.text = "UNCLEAR"
    classify_resp = MagicMock()
    classify_resp.content = [classify_block]

    localized_text = "יש לך אישור ממתין. השב 'כן' או 'לא':\nתשלום של 50 שקל"
    compose_block = MagicMock()
    compose_block.type = "text"
    compose_block.text = localized_text
    compose_resp = MagicMock()
    compose_resp.content = [compose_block]

    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=[classify_resp, compose_resp])
    runner = AgentRunner(client, registry)

    result = await runner.run(
        blueprint=BLUEPRINT,
        group_jid="326@g.us",
        sender="972503@s.whatsapp.net",
        is_admin=False,
        message="מה קורה פה",
        context=context,
        confirmation_store=ConfirmationStore(),
        multi_confirmation_store=mcs,
        resolved_phone="972503",
    )
    assert result == localized_text


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
