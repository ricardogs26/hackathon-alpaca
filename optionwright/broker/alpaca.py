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

import json
import logging
import os
import subprocess
from datetime import date, timedelta
from functools import lru_cache

from optionwright.options.models import OptionQuote, Right, VerticalSpread
from optionwright.settings import get_settings

logger = logging.getLogger("optionwright.broker")

ALPACA_CLI = os.environ.get("ALPACA_CLI_BIN", "alpaca")


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
# Daily bars only change once a day, but the agent asks for them every cycle
# (every ~2 min). Cache them per underlying so we don't re-fetch 30 days of bars
# ~30×/hour — that fetch was a big chunk of each cycle's wall time.
_BARS_TTL_SECONDS = 1800  # 30 min
_bars_cache: dict[str, tuple[float, list[float]]] = {}  # underlying -> (expires_monotonic, closes)


def recent_bars(underlying: str, days: int = 30) -> list[float]:
    """Cierres diarios cronológicos (viejo→nuevo) de las últimas ~`days` sesiones.
    Cacheado 30 min: las barras diarias no cambian intradía."""
    import time
    from datetime import datetime, timedelta, timezone

    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    now = time.monotonic()
    hit = _bars_cache.get(underlying)
    if hit and hit[0] > now:
        return hit[1]

    start = datetime.now(timezone.utc) - timedelta(days=days * 2)  # holgura fines de semana
    req = StockBarsRequest(symbol_or_symbols=underlying, timeframe=TimeFrame.Day, start=start)
    resp = _stock_data_client().get_stock_bars(req)
    bars = resp.data.get(underlying, []) if hasattr(resp, "data") else []
    closes = [float(b.close) for b in bars]
    if closes:  # never cache an empty result (Alpaca hiccup) — retry next cycle
        _bars_cache[underlying] = (now + _BARS_TTL_SECONDS, closes)
    return closes


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


# The whole-chain fetch below is the slowest call in a cycle (10-30s) and it's
# the SAME for puts and calls of one underlying. Cache it per underlying so all
# reads in ONE cycle share a single network fetch. TTL must be LONGER than a full
# cycle pass (~127s, since the 6 chain fetches are slow and sequential) so the
# cache survives the whole pass, but SHORTER than the scheduler interval (180s)
# so the next cycle re-fetches fresh prices. 150s sits in that window.
_CHAIN_TTL_SECONDS = 150
_chain_cache: dict[str, tuple[float, object]] = {}  # underlying -> (expires_monotonic, chain)
chain_net_fetches = 0  # counter of REAL network fetches (for tests/observability)


def _get_chain(underlying: str):
    """The underlying's full option chain, cached _CHAIN_TTL_SECONDS. Shared by
    the puts and calls reads of the same cycle so we hit the network once."""
    global chain_net_fetches
    import time

    from alpaca.data.requests import OptionChainRequest

    now = time.monotonic()
    hit = _chain_cache.get(underlying)
    if hit and hit[0] > now:
        return hit[1]
    chain = _option_data_client().get_option_chain(
        OptionChainRequest(underlying_symbol=underlying, feed="indicative")
    )
    chain_net_fetches += 1
    _chain_cache[underlying] = (now + _CHAIN_TTL_SECONDS, chain)
    return chain


def fetch_chain(underlying: str, expiry: str, right: Right, *, strike_span: float = 25.0) -> list[OptionQuote]:
    """
    Read the option chain for one underlying/expiry/right and return liquid-shaped
    OptionQuotes near the spot. Contracts without a tradable quote are dropped.
    """
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

    chain = _get_chain(underlying)  # cached per underlying — puts+calls share one fetch

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


# ── Execution via the Alpaca CLI (multi-leg) ──────────────────────────────────
def _build_mleg_args(spread: VerticalSpread, contracts: int, limit_price: float) -> list[str]:
    """
    Build the `alpaca order submit` argv for a defined-risk credit vertical, as a
    pure function (unit-tested, never executes). The short leg is sold to open,
    the long leg bought to open; both carry ratio 1. order-class mleg means the
    top-level symbol is omitted and each leg names its own contract.
    """
    if contracts < 1:
        raise ValueError("contracts must be >= 1")
    legs = [
        {
            "symbol": spread.short_leg.symbol,
            "ratio_qty": "1",
            "side": "sell",
            "position_intent": "sell_to_open",
        },
        {
            "symbol": spread.long_leg.symbol,
            "ratio_qty": "1",
            "side": "buy",
            "position_intent": "buy_to_open",
        },
    ]
    return [
        ALPACA_CLI, "order", "submit",
        "--order-class", "mleg",
        "--qty", str(contracts),
        "--type", "limit",
        "--limit-price", f"{limit_price:.2f}",
        "--time-in-force", "day",
        "--legs", json.dumps(legs),
    ]


def current_spread_price(short_symbol: str, long_symbol: str) -> float | None:
    """
    Current debit to close the spread (short mid − long mid). This is what you'd
    pay to buy the credit spread back. None if a quote is missing.
    """
    from alpaca.data.requests import OptionLatestQuoteRequest

    resp = _option_data_client().get_option_latest_quote(
        OptionLatestQuoteRequest(symbol_or_symbols=[short_symbol, long_symbol], feed="indicative")
    )
    short_q, long_q = resp.get(short_symbol), resp.get(long_symbol)
    if not short_q or not long_q:
        return None

    def _mid(q):
        if q.bid_price is None or q.ask_price is None:
            return None
        return (q.bid_price + q.ask_price) / 2

    sm, lm = _mid(short_q), _mid(long_q)
    if sm is None or lm is None:
        return None
    return round(max(0.0, sm - lm), 4)


def _build_mleg_close_args(short_symbol: str, long_symbol: str, contracts: int, limit_price: float) -> list[str]:
    """Reverse of an open: buy back the short leg, sell the long leg."""
    if contracts < 1:
        raise ValueError("contracts must be >= 1")
    legs = [
        {"symbol": short_symbol, "ratio_qty": "1", "side": "buy", "position_intent": "buy_to_close"},
        {"symbol": long_symbol, "ratio_qty": "1", "side": "sell", "position_intent": "sell_to_close"},
    ]
    return [
        ALPACA_CLI, "order", "submit",
        "--order-class", "mleg",
        "--qty", str(contracts),
        "--type", "limit",
        "--limit-price", f"{max(0.01, limit_price):.2f}",
        "--time-in-force", "day",
        "--legs", json.dumps(legs),
    ]


def close_spread(short_symbol: str, long_symbol: str, contracts: int, limit_price: float) -> dict:
    """Close a spread by submitting the reverse multi-leg order via the CLI."""
    argv = _build_mleg_close_args(short_symbol, long_symbol, contracts, limit_price)
    logger.info("close_spread %s/%s x%d @ %.2f", short_symbol, long_symbol, contracts, limit_price)
    proc = subprocess.run(argv, env=_cli_env(), capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"alpaca CLI close failed ({proc.returncode}): {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout)


def _cli_env() -> dict:
    s = get_settings()
    env = dict(os.environ)
    # The CLI reads these for CI/automation instead of a stored profile.
    env["ALPACA_API_KEY"] = s.alpaca_api_key
    env["ALPACA_SECRET_KEY"] = s.alpaca_secret_key
    return env


def submit_spread(spread: VerticalSpread, contracts: int, limit_price: float | None = None) -> dict:
    """
    Place the two legs as one multi-leg order through the Alpaca CLI and return
    the parsed order JSON. Defaults the limit to the spread's net credit. Raises
    on non-zero exit so the loop records a failed decision rather than a phantom
    fill.
    """
    price = spread.credit if limit_price is None else limit_price
    argv = _build_mleg_args(spread, contracts, price)
    logger.info("submit_spread %s x%d @ %.2f", spread.underlying, contracts, price)
    proc = subprocess.run(argv, env=_cli_env(), capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"alpaca CLI failed ({proc.returncode}): {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout)
