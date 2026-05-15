"""Currency conversion to ILS.

Logic:
  - original == ILS → no conversion needed
  - original ↔ ILS, one side is ILS → Bank of Israel Public API
  - foreign-to-foreign (e.g. USD → EUR, edge case) → yfinance

Rates are fetched at ingestion time and stored in both the invoice row
and the exchange_rate_cache table for reproducibility.

Cache hit: same currency pair + same date → reuse stored rate.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from decimal import Decimal

import httpx
import yfinance as yf

from app.db.models import ExchangeRateCache
from app.db.session import SessionLocal
from app.utils.retry import retry

logger = logging.getLogger(__name__)

_BOI_URL = "https://www.boi.org.il/PublicApi/GetExchangeRates"


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_lookup(currency_from: str, currency_to: str, rate_date: date) -> Decimal | None:
    with SessionLocal() as db:
        row = (
            db.query(ExchangeRateCache)
            .filter(
                ExchangeRateCache.currency_from == currency_from,
                ExchangeRateCache.currency_to == currency_to,
                ExchangeRateCache.rate_date == rate_date,
            )
            .first()
        )
        return Decimal(str(row.rate)) if row else None


def _cache_store(
    currency_from: str,
    currency_to: str,
    rate: Decimal,
    rate_date: date,
    source: str,
) -> None:
    with SessionLocal() as db:
        existing = (
            db.query(ExchangeRateCache)
            .filter(
                ExchangeRateCache.currency_from == currency_from,
                ExchangeRateCache.currency_to == currency_to,
                ExchangeRateCache.rate_date == rate_date,
            )
            .first()
        )
        if existing:
            existing.rate = rate
            existing.source = source
        else:
            db.add(ExchangeRateCache(
                currency_from=currency_from,
                currency_to=currency_to,
                rate=rate,
                rate_date=rate_date,
                source=source,
            ))
        db.commit()


# ── Bank of Israel ─────────────────────────────────────────────────────────────

async def _boi_http_get(currency: str, target_date: date) -> dict:
    """Single HTTP call to BoI historical API — separated so retry can wrap it."""
    date_str = target_date.isoformat()
    params = {
        "currencyCode": currency,
        "startDate": date_str,
        "endDate": date_str,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(_BOI_URL, params=params)
        resp.raise_for_status()
        return resp.json()


async def _fetch_boi_rate(currency: str, target_date: date) -> tuple[Decimal, date] | None:
    """Fetch the historical rate for `currency` → ILS from the Bank of Israel API.

    BoI publishes daily rates for USD, EUR, GBP, JPY, CHF, etc.
    For weekends / holidays, the API returns the last available rate for the range.
    Returns (rate, effective_date) or None if the currency is not found.
    """
    try:
        data = await retry(_boi_http_get, currency, target_date, retries=3, base_delay=1.0)
    except Exception as exc:
        logger.error("BoI API request failed after retries: %s", exc)
        return None

    # Response shape: {"ExchangeRates": [{"Key": "USD", "CurrentExchangeRate": 3.67, "Date": "..."}]}
    rates = data.get("ExchangeRates") or []
    if not rates:
        logger.warning("BoI returned no rates for currency %s on %s", currency, target_date)
        return None

    entry = rates[0]
    raw_rate = entry.get("CurrentExchangeRate")
    if raw_rate is None:
        return None

    # BoI rates are expressed as: 1 unit of foreign currency = X ILS
    rate = Decimal(str(raw_rate))

    # Parse the effective date returned by the API (may differ for weekends/holidays)
    raw_date = entry.get("Date")
    try:
        effective_date = date.fromisoformat(raw_date[:10]) if raw_date else target_date
    except (ValueError, TypeError):
        effective_date = target_date

    return rate, effective_date


async def _get_boi_rate(currency: str, target_date: date) -> tuple[Decimal, date, str] | None:
    """Return (rate, rate_date, source) for currency → ILS via BoI, with cache."""
    # Try cache first
    cached = _cache_lookup(currency, "ILS", target_date)
    if cached:
        return cached, target_date, "boi"

    # Also try yesterday (weekend/holiday fallback)
    yesterday = target_date - timedelta(days=1)
    cached_yesterday = _cache_lookup(currency, "ILS", yesterday)
    if cached_yesterday:
        return cached_yesterday, yesterday, "boi"

    result = await _fetch_boi_rate(currency, target_date)
    if result is None:
        return None

    rate, effective_date = result
    _cache_store(currency, "ILS", rate, effective_date, "boi")
    return rate, effective_date, "boi"


# ── yfinance ─────────────────────────────────────────────────────────────────

def _fetch_yfinance_rate_sync(currency_from: str, currency_to: str, target_date: date) -> Decimal | None:
    """Sync fetch of exchange rate via yfinance. Call via asyncio.to_thread from async context."""
    ticker = f"{currency_from}{currency_to}=X"
    try:
        # Download last 5 days to handle weekends/holidays
        df = yf.download(ticker, period="5d", progress=False, auto_adjust=True)
        if df.empty:
            logger.warning("yfinance returned no data for %s", ticker)
            return None
        close = df["Close"].iloc[-1]
        # Handle multi-level columns (yfinance sometimes wraps in a DataFrame)
        if hasattr(close, "iloc"):
            close = close.iloc[0]
        return Decimal(str(float(close)))
    except Exception as exc:
        logger.error("yfinance fetch failed for %s: %s", ticker, exc)
        return None


async def _get_yfinance_rate(
    currency_from: str, currency_to: str, target_date: date
) -> tuple[Decimal, date, str] | None:
    """Return (rate, rate_date, source) for a foreign pair via yfinance, with cache."""
    cached = _cache_lookup(currency_from, currency_to, target_date)
    if cached:
        return cached, target_date, "yfinance"

    rate = await asyncio.to_thread(_fetch_yfinance_rate_sync, currency_from, currency_to, target_date)
    if rate is None:
        return None

    _cache_store(currency_from, currency_to, rate, target_date, "yfinance")
    return rate, target_date, "yfinance"


# ── Public API ────────────────────────────────────────────────────────────────

class ConversionResult:
    __slots__ = ("amount_ils", "exchange_rate", "rate_source", "rate_date", "error")

    def __init__(
        self,
        amount_ils: Decimal | None = None,
        exchange_rate: Decimal | None = None,
        rate_source: str | None = None,
        rate_date: date | None = None,
        error: str | None = None,
    ):
        self.amount_ils    = amount_ils
        self.exchange_rate = exchange_rate
        self.rate_source   = rate_source
        self.rate_date     = rate_date
        self.error         = error


async def convert_to_ils(
    amount: Decimal,
    currency: str,
    invoice_date: date | None = None,
) -> ConversionResult:
    """Convert `amount` in `currency` to ILS.

    Args:
        amount: The original invoice amount.
        currency: ISO 4217 code of the original currency.
        invoice_date: Date of the invoice (used as target rate date). Defaults to today.

    Returns a ConversionResult with all fields populated, or .error set on failure.
    """
    target_date = invoice_date or date.today()
    currency = currency.upper()

    # No conversion needed
    if currency == "ILS":
        return ConversionResult(
            amount_ils=amount,
            exchange_rate=Decimal("1"),
            rate_source="none",
            rate_date=target_date,
        )

    # One side is ILS — use Bank of Israel
    result = await _get_boi_rate(currency, target_date)
    if result:
        rate, rate_date, source = result
        return ConversionResult(
            amount_ils=round(amount * rate, 4),
            exchange_rate=rate,
            rate_source=source,
            rate_date=rate_date,
        )

    # BoI doesn't know this currency — try yfinance (currency → ILS directly)
    logger.warning("BoI has no rate for %s, falling back to yfinance", currency)
    result = await _get_yfinance_rate(currency, "ILS", target_date)
    if result:
        rate, rate_date, source = result
        return ConversionResult(
            amount_ils=round(amount * rate, 4),
            exchange_rate=rate,
            rate_source=source,
            rate_date=rate_date,
        )

    return ConversionResult(
        error=f"Could not find exchange rate for {currency} → ILS on {target_date}. Invoice saved without conversion."
    )
