"""Currency → ILS conversion via api.exchangerate.host."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.exchangerate.host/convert"


async def to_ils(amount: Decimal, currency: str, on_date: date) -> Decimal:
    """Convert amount in currency to ILS at the exchange rate for on_date.

    Args:
        amount: Amount to convert.
        currency: ISO 4217 source currency code (e.g. "USD", "EUR").
        on_date: Date for which to fetch the rate.

    Returns:
        Equivalent amount in ILS.

    Raises:
        RuntimeError: If the API is unavailable or returns an error — caller
            should surface this to the user and ask them to provide ILS amount manually.
    """
    if currency.upper() == "ILS":
        return amount

    params = {
        "from": currency.upper(),
        "to": "ILS",
        "date": on_date.isoformat(),
        "amount": str(amount),
    }

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(_BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(
                f"FX API unavailable ({exc}). Please provide the ILS amount manually."
            ) from exc

    if not data.get("success"):
        raise RuntimeError(
            f"FX API returned error for {currency}→ILS on {on_date}. "
            "Please provide the ILS amount manually."
        )

    return Decimal(str(data["result"]))
