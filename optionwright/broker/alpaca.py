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


_INTRADAY_TTL_SECONDS = 60
_intraday_cache: dict[str, tuple[float, list[dict]]] = {}


def intraday_bars(underlying: str) -> list[dict]:
    """Today's 1-minute bars (IEX feed — the free plan can't read recent SIP),
    cached 60s: the entries pass and the exits pass share one read."""
    import time
    from datetime import datetime, timedelta, timezone

    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    now = time.monotonic()
    hit = _intraday_cache.get(underlying)
    if hit and hit[0] > now:
        return hit[1]
    today = datetime.now(timezone.utc).date()
    start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=13)
    resp = _stock_data_client().get_stock_bars(StockBarsRequest(
        symbol_or_symbols=underlying, timeframe=TimeFrame.Minute, feed=DataFeed.IEX, start=start))
    bars = resp.data.get(underlying, []) if hasattr(resp, "data") else []
    out = [{"ts": b.timestamp, "open": float(b.open), "high": float(b.high), "low": float(b.low),
            "close": float(b.close), "volume": float(b.volume or 0)} for b in bars]
    if out:
        _intraday_cache[underlying] = (now + _INTRADAY_TTL_SECONDS, out)
    return out


def get_spot(underlying: str) -> float:
    from alpaca.data.requests import StockLatestTradeRequest

    resp = _stock_data_client().get_stock_latest_trade(
        StockLatestTradeRequest(symbol_or_symbols=underlying)
    )
    return float(resp[underlying].price)


def _session_window(today: date, sessions: list[date], min_days: int, max_days: int) -> tuple[date, date] | None:
    """
    [min_days, max_days] counted in TRADING SESSIONS after `today`, not calendar
    days. `sessions` are the exchange's upcoming session dates (sorted). Returns
    the (earliest, latest) dates the window covers, or None if the calendar
    doesn't reach min_days. Pure, so the weekend/holiday arithmetic is tested.

    Why: on Thu 3-Sep-2026 a calendar window of 2-3 days landed on Sat/Sun and
    the agent logged "no expiry" all morning. Counting sessions, 2 days after a
    Thursday is Monday — or Tuesday when Monday is a holiday.
    """
    future = sorted(d for d in sessions if d > today)
    if min_days < 1 or max_days < min_days or len(future) < min_days:
        return None
    return future[min_days - 1], future[min(max_days, len(future)) - 1]


def _upcoming_sessions(today: date, horizon_days: int = 21) -> list[date]:
    from alpaca.trading.requests import GetCalendarRequest

    cal = _trading_client().get_calendar(GetCalendarRequest(start=today, end=today + timedelta(days=horizon_days)))
    return [c.date for c in cal]


def nearest_expiry(underlying: str, min_days: int = 1, max_days: int = 10) -> str | None:
    """
    Nearest listed expiration within [min_days, max_days] trading sessions from
    today. If the calendar can't be read, degrades to calendar days (the old
    behaviour) rather than skipping the cycle.
    """
    from alpaca.trading.enums import AssetStatus
    from alpaca.trading.requests import GetOptionContractsRequest

    today = date.today()
    try:
        window = _session_window(today, _upcoming_sessions(today), min_days, max_days)
        if window is None:
            return None
        gte, lte = window
    except Exception as exc:  # calendar hiccup: never let it cost a cycle
        logger.warning("session calendar unavailable (%s); using calendar days", exc)
        gte, lte = today + timedelta(days=min_days), today + timedelta(days=max_days)
    req = GetOptionContractsRequest(
        underlying_symbols=[underlying],
        status=AssetStatus.ACTIVE,
        expiration_date_gte=gte,
        expiration_date_lte=lte,
        limit=1,
    )
    contracts = _trading_client().get_option_contracts(req).option_contracts
    return str(contracts[0].expiration_date) if contracts else None


# The whole-chain fetch below is the slowest call in a cycle (10-30s) and it's
# the SAME for puts and calls of one underlying. Cache it per underlying, scoped
# to ONE cycle: the runner calls new_cycle() at the start of each pass, so the
# first read of an underlying fetches fresh and the rest of the cycle reuses it.
# No TTL to tune — freshness is tied to the cycle boundary, driven by the single
# CYCLE_SECONDS knob instead of a magic number.
_chain_cache: dict[str, object] = {}  # underlying -> chain (valid for the current cycle)
chain_net_fetches = 0  # counter of REAL network fetches (for tests/observability)


def new_cycle() -> None:
    """Invalidate the option-chain cache. Called at the start of each cycle so the
    chain is fetched once per underlying per cycle (fresh), reused within it."""
    _chain_cache.clear()


def _get_chain(underlying: str):
    """The underlying's full option chain, cached for the current cycle. Shared by
    the puts and calls reads of the same cycle so we hit the network once."""
    global chain_net_fetches

    from alpaca.data.requests import OptionChainRequest

    cached = _chain_cache.get(underlying)
    if cached is not None:
        return cached
    chain = _option_data_client().get_option_chain(
        OptionChainRequest(underlying_symbol=underlying, feed="indicative")
    )
    chain_net_fetches += 1
    _chain_cache[underlying] = chain
    return chain


def prefetch_chains(underlyings: list[str], workers: int = 3) -> dict[str, str | None]:
    """Warm the per-cycle chain cache for several underlyings in parallel. Each
    chain read is network-bound (1-30s), so seven underlyings fetched serially
    would not fit a 180s cycle. Returns {underlying: error or None}; a failure
    here is not fatal — the cycle's own read retries."""
    from concurrent.futures import ThreadPoolExecutor

    def one(u: str) -> str | None:
        try:
            _get_chain(u)
            return None
        except Exception as exc:
            return str(exc)[:120]

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        return dict(zip(underlyings, ex.map(one, underlyings)))


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


def spread_snapshot(short_symbol: str, long_symbol: str) -> dict | None:
    """
    The short leg's greeks and IV for the position tick (phase 0
    instrumentation). Separate from `current_spread_price` on purpose: the money
    path keeps its own quote read; this one can fail without cost. Alpaca
    returns no greeks on the expiry day itself (verified 4-Sep-2026): the tick
    then carries None for delta/IV/sigma, which is honest, not an error.
    """
    from alpaca.data.requests import OptionSnapshotRequest

    resp = _option_data_client().get_option_snapshot(
        OptionSnapshotRequest(symbol_or_symbols=[short_symbol, long_symbol], feed="indicative")
    )
    snap = resp.get(short_symbol)
    if snap is None:
        return None
    greeks = getattr(snap, "greeks", None)
    return {
        "short_delta": getattr(greeks, "delta", None) if greeks else None,
        "short_iv": getattr(snap, "implied_volatility", None),
    }


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
