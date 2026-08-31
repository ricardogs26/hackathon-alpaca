"""
Runner: wires the real broker / analyzer / storage into Deps and runs one cycle
per underlying. The scheduler (in service.py) calls run_once on an interval; this
module is the seam between the pure pipeline (loop.run_cycle) and live services.
"""
from __future__ import annotations

import logging

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


def _build_deps() -> Deps:
    return Deps(
        account=_account,
        nearest_expiry=lambda u: alpaca.nearest_expiry(u, min_days=1, max_days=10),
        fetch_chain=alpaca.fetch_chain,
        propose=propose,
        build_state=lambda u, eq: store.build_policy_state(u, eq, minutes_since_open=_minutes_since_open()),
        submit_spread=alpaca.submit_spread,
        record_decision=store.record_decision,
        record_position=store.record_position,
        save_equity=store.save_equity,
        rules=RuleSet(),
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

    deps = _build_deps()
    results = []
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
