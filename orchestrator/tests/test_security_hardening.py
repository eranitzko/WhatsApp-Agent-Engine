import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import os


@pytest.mark.asyncio
async def test_send_includes_bridge_secret_header():
    """_send() must include Authorization: Bearer header when BRIDGE_SECRET is set."""
    import app.main as main_mod
    import app.bridge_client as bc_mod

    captured_headers = {}

    async def fake_post(url, *, json=None, headers=None, timeout=None):
        captured_headers.update(headers or {})
        return MagicMock()

    fake_client = MagicMock()
    fake_client.post = fake_post

    with patch.object(main_mod, "_http_client", fake_client), \
         patch.object(bc_mod, "_BRIDGE_SECRET", "test-secret-123"):
        await main_mod._send("123@g.us", "hello")

    assert "Authorization" in captured_headers
    assert captured_headers["Authorization"] == "Bearer test-secret-123"


@pytest.mark.asyncio
async def test_send_no_header_when_secret_empty():
    """_send() must not include Authorization header when BRIDGE_SECRET is empty."""
    import app.main as main_mod
    import app.bridge_client as bc_mod

    captured_headers = {}

    async def fake_post(url, *, json=None, headers=None, timeout=None):
        captured_headers.update(headers or {})
        return MagicMock()

    fake_client = MagicMock()
    fake_client.post = fake_post

    with patch.object(main_mod, "_http_client", fake_client), \
         patch.object(bc_mod, "_BRIDGE_SECRET", ""):
        await main_mod._send("123@g.us", "hello")

    assert "Authorization" not in captured_headers


def test_confirmation_store_toctou_guard():
    """set() must not overwrite a non-expired pending action."""
    from app.agent.confirmation import ConfirmationStore

    store = ConfirmationStore()
    result_a = store.set("grp1", "delete_invoice", {"id": "abc"}, "Delete invoice ABC")
    assert result_a is True

    result_b = store.set("grp1", "send_email", {"to": "x@y.com"}, "Send email")
    assert result_b is False

    pending = store.get("grp1")
    assert pending is not None
    assert pending.action == "delete_invoice"


def test_confirmation_store_set_after_clear():
    """set() succeeds after the slot is cleared."""
    from app.agent.confirmation import ConfirmationStore

    store = ConfirmationStore()
    store.set("grp1", "action_a", {}, "A")
    store.clear("grp1")
    result = store.set("grp1", "action_b", {}, "B")
    assert result is True
    assert store.get("grp1").action == "action_b"


def test_confirmation_store_set_after_expiry():
    """set() succeeds when the previous action has expired."""
    from app.agent.confirmation import ConfirmationStore, PendingAction
    from datetime import datetime, timedelta, timezone

    store = ConfirmationStore()
    store._store["grp1"] = PendingAction(
        action="old_action", params={}, description="old",
        expires=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    result = store.set("grp1", "new_action", {}, "new")
    assert result is True
    assert store.get("grp1").action == "new_action"
