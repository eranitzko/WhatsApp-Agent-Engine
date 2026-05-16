from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_ils_passthrough():
    from app.tools.accounting_fx import to_ils
    result = await to_ils(Decimal("100"), "ILS", date.today())
    assert result == Decimal("100")


@pytest.mark.asyncio
async def test_converts_usd_to_ils(monkeypatch):
    from app.tools import accounting_fx

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"success": True, "result": 370.0}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(accounting_fx.httpx, "AsyncClient", MagicMock(return_value=mock_client))

    result = await accounting_fx.to_ils(Decimal("100"), "USD", date(2026, 1, 15))
    assert result == Decimal("370.0")


@pytest.mark.asyncio
async def test_raises_on_http_error(monkeypatch):
    from app.tools import accounting_fx

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(accounting_fx.httpx, "AsyncClient", MagicMock(return_value=mock_client))

    with pytest.raises(RuntimeError, match="FX API unavailable"):
        await accounting_fx.to_ils(Decimal("100"), "USD", date.today())


@pytest.mark.asyncio
async def test_raises_on_api_success_false(monkeypatch):
    from app.tools import accounting_fx

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"success": False}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(accounting_fx.httpx, "AsyncClient", MagicMock(return_value=mock_client))

    with pytest.raises(RuntimeError, match="FX API returned error"):
        await accounting_fx.to_ils(Decimal("50"), "EUR", date.today())
