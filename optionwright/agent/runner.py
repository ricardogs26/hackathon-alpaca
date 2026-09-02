"""
Runner: wires the real broker / analyzer / storage into Deps and runs one cycle
per underlying. The scheduler (in service.py) calls run_once on an interval; this
module is the seam between the pure pipeline (loop.run_cycle) and live services.
"""
from __future__ import annotations

import logging

from optionwright.agent import perception
from optionwright.agent.analyzer import propose
from optionwright.agent.loop import Deps, run_cycle
from optionwright.broker import alpaca
from optionwright.policy.gates import RuleSet
from optionwright.settings import get_settings
from optionwright.storage import store

logger = logging.getLogger("optionwright.runner")


def _account() -> tuple[float, float]:
    acct = alpaca._trading_client().get_account()
    return float(acct.equity), float(acct.cash)


def _market_open() -> bool:
    clock = alpaca._trading_client().get_clock()
    return bool(clock.is_open)


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
    Check every open spread and close it on take-profit, stop-loss, or expiration
    day. Runs before opening new positions each cycle. Errors on one position
    never stop the others.
    """
    from datetime import date

    from optionwright import metrics
    from optionwright.agent.exits import ExitParams, decide_exit
    from optionwright.storage import store

    s = get_settings()
    params = ExitParams(
        stop_mult=s.stop_loss_mult,
        hard_take_profit=s.hard_take_profit,
        trail_activation=s.trail_activation,
        trail_giveback=s.trail_giveback,
    )
    today = date.today().isoformat()
    results = []

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
            decision = decide_exit(credit, price, is_expiry_day, peak_captured=peak, params=params)
            captured_pct = captured * 100
            pnl_now = round((credit - price) * 100 * pos["contracts"], 2)
            # Surface the live evaluation as a Grafana table row.
            metrics.set_position_info(
                pos["id"], pos["underlying"], credit, price, captured_pct,
                "close" if decision.close else "hold", pnl_now,
            )
            if not decision.close:
                continue
            # Cross the spread by a few cents so the close actually fills (SPY/QQQ
            # options are penny-wide). We estimate realized P&L from the mid.
            close_limit = round(price + 0.05, 2)
            alpaca.close_spread(pos["short_symbol"], pos["long_symbol"], pos["contracts"], close_limit)
            store.close_position(pos["id"], pnl_now, decision.reason)
            metrics.record_realized_pnl(pnl_now)
            logger.info("closed position %s: %s, P&L %.2f", pos["id"], decision.reason, pnl_now)
            results.append({"position_id": pos["id"], "action": "closed",
                            "reason": decision.reason, "realized_pnl": pnl_now})
        except Exception as exc:
            logger.error("manage position %s failed: %s", pos.get("id"), exc, exc_info=True)
            metrics.ERRORS.labels(where="manage").inc()
    return results


def _build_deps() -> Deps:
    s = get_settings()
    return Deps(
        account=_account,
        nearest_expiry=lambda u: alpaca.nearest_expiry(u, min_days=s.expiry_min_days, max_days=s.expiry_max_days),
        fetch_chain=alpaca.fetch_chain,
        propose=propose,
        build_state=lambda u, eq: store.build_policy_state(u, eq, minutes_since_open=_minutes_since_open()),
        submit_spread=alpaca.submit_spread,
        record_decision=store.record_decision,
        record_position=store.record_position,
        save_equity=store.save_equity,
        rules=RuleSet(
            max_open_positions=s.max_open_positions,
            max_per_underlying=s.max_per_underlying,
            max_loss_pct=s.max_loss_pct,
            cooldown_seconds=s.cooldown_seconds,
            daily_budget_pct=s.daily_budget_pct,
            min_confidence=s.min_confidence,
        ),
        signals=lambda u, e: perception.compute_signals(
            alpaca.recent_bars(u), alpaca.get_spot(u),
            trend_flat_pct=s.perception_trend_flat_pct,
            vol_high_pct=s.perception_vol_high_pct,
        ),
        memory=lambda u: store.recent_outcomes(u),
        book=store.book_summary,
        rich_context=s.agent_rich_context,
    )


def run_once() -> list[dict]:
    """One pass over every configured underlying. Skips when the market is closed."""
    from optionwright import metrics

    s = get_settings()
    if not _market_open():
        logger.info("market closed — skipping cycle")
        result = {"action": "skipped", "reason": "market closed"}
        metrics.record_cycle(result)
        return [result]

    # Fresh option chains for this cycle; each underlying's chain is fetched once
    # and reused across its puts/calls reads (invalidated per cycle, no TTL).
    alpaca.new_cycle()

    # Manage exits first: take-profit, stop, or expiration-day close.
    exits = manage_positions()
    for e in exits:
        metrics.CYCLES.labels(result="closed").inc()

    deps = _build_deps()
    results = list(exits)
    for underlying in s.underlyings_list:
        try:
            result = run_cycle(underlying, deps)
        except Exception as exc:  # one bad underlying never kills the whole pass
            logger.error("cycle failed for %s: %s", underlying, exc, exc_info=True)
            metrics.ERRORS.labels(where="cycle").inc()
            result = {"underlying": underlying, "action": "error", "reason": str(exc)[:200]}
        metrics.record_cycle(result)
        results.append(result)
    logger.info("cycle pass complete: %s", [r.get("action") for r in results])
    return results
