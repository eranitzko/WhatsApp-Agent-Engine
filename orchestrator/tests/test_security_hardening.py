import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import os


@pytest.mark.asyncio
async def test_send_includes_bridge_secret_header():
    """_send() must include Authorization: Bearer header when BRIDGE_SECRET is set."""
    import app.main as main_mod

    mock_response = MagicMock()
    mock_response.status_code = 200

    captured_headers = {}

    async def fake_post(url, *, json=None, headers=None, timeout=None):
        captured_headers.update(headers or {})
        return mock_response

    fake_client = MagicMock()
    fake_client.post = fake_post
    main_mod._http_client = fake_client

    with patch.object(main_mod, "_BRIDGE_SECRET", "test-secret-123"):
        await main_mod._send("123@g.us", "hello")

    assert "Authorization" in captured_headers
    assert captured_headers["Authorization"] == "Bearer test-secret-123"


@pytest.mark.asyncio
async def test_send_no_header_when_secret_empty():
    """_send() must not include Authorization header when BRIDGE_SECRET is empty."""
    import app.main as main_mod

    captured_headers = {}

    async def fake_post(url, *, json=None, headers=None, timeout=None):
        captured_headers.update(headers or {})
        return MagicMock()

    fake_client = MagicMock()
    fake_client.post = fake_post
    main_mod._http_client = fake_client

    with patch.object(main_mod, "_BRIDGE_SECRET", ""):
        await main_mod._send("123@g.us", "hello")

    assert "Authorization" not in captured_headers
