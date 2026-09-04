"""
Runner: wires the real broker / analyzer / storage into Deps and runs the agent.
Two entry points on two clocks (see api/main.build_scheduler):

  run_exits()   every EXIT_CHECK_SECONDS (60s)  - manage open positions only:
                take-profit, trailing, stop, expiry. Cheap (one quote per open
                position, no chain, no LLM), so it can run often and the trailing
                give-back stays close to what is configured.
  run_entries() every CYCLE_SECONDS (180s)      - the full decision pass: chains,
                LLM, gates, execution.

run_once() keeps the old single-pass shape (exits then entries) for dry runs.
This module is the seam between the pure pipeline (loop.run_cycle) and live
services.
"""
from __future__ import annotations

import logging
import threading
import time

from optionwright.agent import perception
from optionwright.agent.analyzer import propose
from optionwright.agent.exits import ExitParams, decide_exit
from optionwright.agent.loop import Deps, run_cycle
from optionwright.agent.state import compute_tick, position_clock
from optionwright.broker import alpaca
from optionwright.policy.gates import RuleSet
from optionwright.policy.params import Params
from optionwright.settings import get_settings
from optionwright.storage import store

logger = logging.getLogger("optionwright.runner")


def _account() -> tuple[float, float]:
    acct = alpaca._trading_client().get_account()
    return float(acct.equity), float(acct.cash)


_CLOCK_TTL = 15.0                       # seconds; two jobs share one clock read
_clock_cache: tuple[float, bool, object] | None = None   # (monotonic, is_open, next_close)
_exit_lock = threading.Lock()           # exits passes never overlap each other


def _clock() -> tuple[bool, object]:
    """Alpaca's clock (is_open, next_close), cached _CLOCK_TTL so the 60s exits
    job and the 180s entries job don't each hit the broker for the same answer."""
    global _clock_cache
    now = time.monotonic()
    if _clock_cache is not None and now - _clock_cache[0] < _CLOCK_TTL:
        return _clock_cache[1], _clock_cache[2]
    clk = alpaca._trading_client().get_clock()
    is_open = bool(clk.is_open)
    next_close = getattr(clk, "next_close", None)
    _clock_cache = (now, is_open, next_close)
    return is_open, next_close


def _market_open() -> bool:
    return _clock()[0]


def _minutes_to_close() -> float | None:
    from datetime import datetime, timezone

    _, next_close = _clock()
    if next_close is None:
        return None
    return max(0.0, (next_close - datetime.now(timezone.utc)).total_seconds() / 60.0)


_PARAMS_TTL = 60.0                      # seconds; the rules table is re-read at most this often
_params_cache: tuple[float, Params] | None = None


def current_params() -> Params:
    """The rule parameters (policy/params.py) as stored in Postgres, cached 60s.
    If the table can't be read the last good snapshot is kept — never the
    registry defaults while a better value existed."""
    global _params_cache
    now = time.monotonic()
    if _params_cache is not None and now - _params_cache[0] < _PARAMS_TTL:
        return _params_cache[1]
    try:
        params = Params(store.load_rules())
    except Exception as exc:
        logger.warning("rules table unavailable (%s); keeping %s", exc,
                       "the last values" if _params_cache else "registry defaults")
        params = _params_cache[1] if _params_cache else Params()
    _params_cache = (now, params)
    return params


def _minutes_since_open() -> float | None:
    """
    Minutes since today's session open, or None if the market is closed or the
    calendar can't be read. None leaves the opening-blackout gate inert (safe
    degradation), so a calendar hiccup never blocks or forces a trade.
    """
    try:
        from datetime import date

        from alpaca.trading.requests import GetCalendarRequest

        tc = alpaca._trading_client()
        clock = tc.get_clock()
        if not clock.is_open:
            return None
        today = date.today()
        cal = tc.get_calendar(GetCalendarRequest(start=today, end=today))
        if not cal:
            return None
        open_dt = cal[0].open   # session open (may be naive ET or a date+time)
        now = clock.timestamp    # tz-aware current market time
        if open_dt.tzinfo is None:
            import pytz

            open_dt = pytz.timezone("America/New_York").localize(open_dt)
        return max(0.0, (now - open_dt).total_seconds() / 60.0)
    except Exception as exc:  # never let the clock/calendar break a cycle
        logger.warning("minutes_since_open unavailable: %s", exc)
        return None


def manage_positions() -> list[dict]:
    """
    Check every open spread and close it on take-profit, trailing, stop-loss, or
    expiration day. Guarded by a non-blocking lock: if a previous exits pass is
    still running (slow broker), this tick is skipped instead of overlapping it —
    two passes must never act on the same position at once.
    """
    if not _exit_lock.acquire(blocking=False):
        logger.warning("exits pass already running; skipping this tick")
        return []
    try:
        return _manage_positions()
    finally:
        _exit_lock.release()


def _manage_positions() -> list[dict]:
    """The exits pass proper. Errors on one position never stop the others."""
    from datetime import date, datetime, timezone

    from optionwright import metrics

    params = current_params()
    _, next_close = _clock()
    now = datetime.now(timezone.utc)
    today = date.today().isoformat()
    results = []
    spots: dict[str, float] = {}   # one spot read per underlying per pass, for the ticks
    book_delta_pct: float | None = None   # computed once per pass, only if a rule needs it

    all_positions = store.get_positions(200)
    metrics.set_position_gauges(all_positions)
    metrics.clear_position_info()  # re-populated below for currently-open positions
    for pos in all_positions:
        if pos["status"] != "open":
            continue
        try:
            price = alpaca.current_spread_price(pos["short_symbol"], pos["long_symbol"])
            if price is None:
                continue
            credit = float(pos["credit"])
            is_expiry_day = str(pos["expiry"]) <= today
            captured = (credit - price) / credit if credit > 0 else 0.0
            # Update and read the high-water mark before deciding on the trail.
            peak = max(float(pos.get("peak_captured") or 0.0), captured)
            if peak > float(pos.get("peak_captured") or 0.0):
                store.update_peak_captured(pos["id"], peak)

            # State inputs for the rules: one greeks snapshot (best effort) and the clock.
            xp = ExitParams.from_params(params, pos["underlying"])
            snap = _snapshot_safe(pos)
            short_delta = None if snap.get("short_delta") is None else abs(float(snap["short_delta"]))
            hours_to_expiry, hours_to_close, sleeps = _clock_inputs_safe(pos, now, next_close)
            if xp.overnight_mode == "delta" and sleeps and book_delta_pct is None:
                book_delta_pct = _book_delta_pct()
            decision = decide_exit(
                credit, price, is_expiry_day, peak_captured=peak, params=xp,
                short_delta=short_delta, hours_to_expiry=hours_to_expiry, hours_to_close=hours_to_close,
                sleeps_tonight=sleeps, book_net_delta_pct=book_delta_pct,
            )
            captured_pct = captured * 100
            pnl_now = round((credit - price) * 100 * pos["contracts"], 2)
            # Surface the live evaluation as a Grafana table row.
            metrics.set_position_info(
                pos["id"], pos["underlying"], credit, price, captured_pct,
                "close" if decision.close else "hold", pnl_now,
            )
            if decision.close:
                # Cross the spread by a few cents so the close actually fills (SPY/QQQ
                # options are penny-wide). We estimate realized P&L from the mid.
                close_limit = round(price + 0.05, 2)
                alpaca.close_spread(pos["short_symbol"], pos["long_symbol"], pos["contracts"], close_limit)
                store.close_position(pos["id"], pnl_now, decision.reason)
                metrics.record_realized_pnl(pnl_now)
                logger.info("closed position %s: %s, P&L %.2f", pos["id"], decision.reason, pnl_now)
                results.append({"position_id": pos["id"], "action": "closed",
                                "reason": decision.reason, "realized_pnl": pnl_now})
            # Instrumentation AFTER the money decision: a tick that fails never
            # delays or blocks a close.
            _record_tick(pos, price, peak, decision, spots, snap, now, next_close)
        except Exception as exc:
            logger.error("manage position %s failed: %s", pos.get("id"), exc, exc_info=True)
            metrics.ERRORS.labels(where="manage").inc()
    return results


def _clock_inputs_safe(pos: dict, now, next_close) -> tuple[float | None, float | None, bool | None]:
    """Time inputs for the rules, or all-unknown if the symbol can't be parsed —
    the credit-based rules still protect the position."""
    try:
        return position_clock(pos["short_symbol"], now, next_close)
    except Exception as exc:
        logger.warning("clock inputs for position %s unavailable: %s", pos.get("id"), exc)
        return None, None, None


def _snapshot_safe(pos: dict) -> dict:
    """The short leg's delta/IV, or {} — the credit stop still protects without it."""
    from optionwright import metrics

    try:
        return alpaca.spread_snapshot(pos["short_symbol"], pos["long_symbol"]) or {}
    except Exception as exc:
        logger.warning("snapshot for position %s unavailable: %s", pos.get("id"), exc)
        metrics.ERRORS.labels(where="snapshot").inc()
        return {}


def _book_delta_pct() -> float | None:
    """|net $ delta of the book| / equity, or None when unmeasured (delta mode then closes)."""
    try:
        net = store.book_net_delta_usd()
        if net is None:
            return None
        equity, _ = _account()
        return abs(net) / equity if equity > 0 else None
    except Exception as exc:
        logger.warning("book net delta unavailable: %s", exc)
        return None


def _record_tick(pos: dict, price: float, peak: float, decision, spots: dict[str, float],
                 snap: dict, now, next_close) -> None:
    """Phase 0: persist the position's state vector for this tick. Best effort —
    any failure is counted and logged, never raised into the exits pass."""
    from optionwright import metrics

    try:
        u = pos["underlying"]
        if u not in spots:
            spots[u] = alpaca.get_spot(u)
        tick = compute_tick(
            pos=pos, price=price, peak_captured=peak,
            decision="close" if decision.close else "hold", reason=decision.reason,
            spot=spots.get(u), short_delta=snap.get("short_delta"), short_iv=snap.get("short_iv"),
            now=now, next_close=next_close,
        )
        store.record_tick(tick)
    except Exception as exc:
        logger.warning("tick for position %s not recorded: %s", pos.get("id"), exc)
        metrics.ERRORS.labels(where="tick").inc()


_record_tick_impl = _record_tick         # tests patch `_record_tick`; this keeps the real one reachable
_current_params_impl = current_params    # same for `current_params`


def _build_deps(params: Params, underlying: str) -> Deps:
    """Dependencies for one underlying's cycle. Rules resolve per underlying
    (precedence underlying > group > global) from the parameter table."""
    s = get_settings()
    return Deps(
        account=_account,
        nearest_expiry=lambda u: alpaca.nearest_expiry(u, min_days=s.expiry_min_days, max_days=s.expiry_max_days),
        fetch_chain=alpaca.fetch_chain,
        propose=propose,
        build_state=lambda u, eq: store.build_policy_state(
            u, eq, minutes_since_open=_minutes_since_open(), minutes_to_close=_minutes_to_close()),
        submit_spread=alpaca.submit_spread,
        record_decision=store.record_decision,
        record_position=store.record_position,
        save_equity=store.save_equity,
        rules=RuleSet.from_params(params, underlying),
        signals=lambda u, e: perception.compute_signals(
            alpaca.recent_bars(u), alpaca.get_spot(u),
            trend_flat_pct=s.perception_trend_flat_pct,
            vol_high_pct=s.perception_vol_high_pct,
        ),
        memory=lambda u: store.recent_outcomes(u),
        book=lambda: store.llm_book_view(store.book_summary()),
        rich_context=s.agent_rich_context,
    )


def run_exits() -> list[dict]:
    """Exits job (every EXIT_CHECK_SECONDS): manage open positions. Quiet when the
    market is closed — it doesn't inflate the cycle counters every minute."""
    from optionwright import metrics

    if not _market_open():
        return []
    exits = manage_positions()
    for _ in exits:
        metrics.CYCLES.labels(result="closed").inc()
    return exits


def run_entries() -> list[dict]:
    """Entries job (every CYCLE_SECONDS): one decision pass over every underlying.
    Skips (and records it) when the market is closed."""
    from optionwright import metrics

    s = get_settings()
    if not _market_open():
        logger.info("market closed — skipping cycle")
        result = {"action": "skipped", "reason": "market closed"}
        metrics.record_cycle(result)
        return [result]

    # Fresh option chains for this pass; each underlying's chain is fetched once
    # and reused across its puts/calls reads (invalidated per pass, no TTL).
    alpaca.new_cycle()

    params = current_params()
    results: list[dict] = []
    for underlying in s.underlyings_list:
        try:
            result = run_cycle(underlying, _build_deps(params, underlying))
        except Exception as exc:  # one bad underlying never kills the whole pass
            logger.error("cycle failed for %s: %s", underlying, exc, exc_info=True)
            metrics.ERRORS.labels(where="cycle").inc()
            result = {"underlying": underlying, "action": "error", "reason": str(exc)[:200]}
        metrics.record_cycle(result)
        results.append(result)
    logger.info("cycle pass complete: %s", [r.get("action") for r in results])
    return results


def run_once() -> list[dict]:
    """Exits then entries in one pass. Kept for dry runs and as the reference
    shape of a full cycle; the scheduler runs the two jobs on their own clocks."""
    if not _market_open():
        return run_entries()  # records the skip once
    return run_exits() + run_entries()
