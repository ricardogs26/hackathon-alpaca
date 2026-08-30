"""
Alpaca integration. Two responsibilities kept apart:

  - Market data / chain: read the option chain and quotes via alpaca-py.
  - Execution: place multi-leg spread orders through the **Alpaca CLI**
    (subprocess + structured JSON), which satisfies the hackathon's MCP-or-CLI
    requirement and is built for long-running agent sessions.

Paper only: the base URL is always paper-api.alpaca.markets and settings refuse
ALPACA_PAPER=false.

The chain->OptionQuote mapping lives in `_to_quote`, a pure function unit-tested
without any network. `fetch_chain` is the thin networked wrapper around it.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from functools import lru_cache

from optionwright.options.models import OptionQuote, Right
from optionwright.settings import get_settings

logger = logging.getLogger("optionwright.broker")


# ── Clients (lazy, cached) ────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _trading_client():
    from alpaca.trading.client import TradingClient

    s = get_settings()
    return TradingClient(s.alpaca_api_key, s.alpaca_secret_key, paper=True)


@lru_cache(maxsize=1)
def _option_data_client():
    from alpaca.data.historical.option import OptionHistoricalDataClient

    s = get_settings()
    return OptionHistoricalDataClient(s.alpaca_api_key, s.alpaca_secret_key)


@lru_cache(maxsize=1)
def _stock_data_client():
    from alpaca.data.historical.stock import StockHistoricalDataClient

    s = get_settings()
    return StockHistoricalDataClient(s.alpaca_api_key, s.alpaca_secret_key)


# ── Pure mapping (unit-tested, no network) ────────────────────────────────────
def _to_quote(contract, snapshot, underlying: str) -> OptionQuote | None:
    """
    Merge an Alpaca OptionContract (strike, expiry, OI) with its chain snapshot
    (quote + greeks) into our OptionQuote. Returns None if the snapshot has no
    tradable quote, so callers can skip it rather than trade on a phantom.

    `contract` and `snapshot` are duck-typed: any object exposing the same
    attributes works, which is what the unit tests rely on.
    """
    quote = getattr(snapshot, "latest_quote", None)
    if quote is None or quote.bid_price is None or quote.ask_price is None:
        return None
    greeks = getattr(snapshot, "greeks", None)
    delta = getattr(greeks, "delta", None) if greeks else None
    if delta is None:
        return None

    # Daily volume when the market has traded; 0 off-hours. Open interest is the
    # stable liquidity signal and always comes from the contract.
    daily = getattr(snapshot, "daily_bar", None)
    volume = int(getattr(daily, "volume", 0) or 0)

    right = Right.PUT if str(contract.type).lower().endswith("put") else Right.CALL
    return OptionQuote(
        symbol=contract.symbol,
        underlying=underlying,
        right=right,
        strike=float(contract.strike_price),
        expiry=str(contract.expiration_date),
        bid=float(quote.bid_price),
        ask=float(quote.ask_price),
        delta=float(delta),
        open_interest=int(contract.open_interest or 0),
        volume=volume,
    )


# ── Networked reads ───────────────────────────────────────────────────────────
def get_spot(underlying: str) -> float:
    from alpaca.data.requests import StockLatestTradeRequest

    resp = _stock_data_client().get_stock_latest_trade(
        StockLatestTradeRequest(symbol_or_symbols=underlying)
    )
    return float(resp[underlying].price)


def nearest_expiry(underlying: str, min_days: int = 1, max_days: int = 10) -> str | None:
    """Nearest listed expiration within [min_days, max_days] from today."""
    from alpaca.trading.enums import AssetStatus
    from alpaca.trading.requests import GetOptionContractsRequest

    today = date.today()
    req = GetOptionContractsRequest(
        underlying_symbols=[underlying],
        status=AssetStatus.ACTIVE,
        expiration_date_gte=today + timedelta(days=min_days),
        expiration_date_lte=today + timedelta(days=max_days),
        limit=1,
    )
    contracts = _trading_client().get_option_contracts(req).option_contracts
    return str(contracts[0].expiration_date) if contracts else None


def fetch_chain(underlying: str, expiry: str, right: Right, *, strike_span: float = 25.0) -> list[OptionQuote]:
    """
    Read the option chain for one underlying/expiry/right and return liquid-shaped
    OptionQuotes near the spot. Contracts without a tradable quote are dropped.
    """
    from alpaca.data.requests import OptionChainRequest
    from alpaca.trading.enums import AssetStatus, ContractType
    from alpaca.trading.requests import GetOptionContractsRequest

    spot = get_spot(underlying)
    ctype = ContractType.PUT if right is Right.PUT else ContractType.CALL
    req = GetOptionContractsRequest(
        underlying_symbols=[underlying],
        status=AssetStatus.ACTIVE,
        type=ctype,
        expiration_date_gte=expiry,
        expiration_date_lte=expiry,
        strike_price_gte=str(spot - strike_span),
        strike_price_lte=str(spot + strike_span),
        limit=200,
    )
    contracts = _trading_client().get_option_contracts(req).option_contracts

    chain = _option_data_client().get_option_chain(
        OptionChainRequest(underlying_symbol=underlying, feed="indicative")
    )

    quotes: list[OptionQuote] = []
    for c in contracts:
        snap = chain.get(c.symbol)
        if snap is None:
            continue
        q = _to_quote(c, snap, underlying)
        if q is not None:
            quotes.append(q)
    logger.info("fetch_chain %s %s %s -> %d quotes", underlying, expiry, right.value, len(quotes))
    return quotes
