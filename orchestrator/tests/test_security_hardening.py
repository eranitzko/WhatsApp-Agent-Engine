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
