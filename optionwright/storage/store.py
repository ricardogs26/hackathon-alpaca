"""
Postgres persistence: decisions, positions, equity. Also builds the PolicyState
the gates read. Uses psycopg3. The pure state-derivation logic (consecutive
losses) lives in `_consecutive_losses` so it can be unit-tested without a DB.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager

from optionwright.options.models import Direction, VerticalSpread
from optionwright.policy.gates import PolicyState
from optionwright.settings import get_settings
from optionwright.storage.schema import SCHEMA

logger = logging.getLogger("optionwright.storage")


@contextmanager
def _conn():
    import psycopg

    c = psycopg.connect(get_settings().postgres_dsn)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_schema() -> None:
    with _conn() as c:
        c.execute(SCHEMA)
    logger.info("schema ready")


def record_decision(
    underlying: str,
    direction: Direction,
    confidence: float | None,
    rationale: str | None,
    approved: bool,
    contracts: int,
    reason: str,
    spread: VerticalSpread | None,
    position_id: int | None = None,
) -> None:
    spread_json = None
    if spread is not None:
        spread_json = json.dumps({
            "short_symbol": spread.short_leg.symbol,
            "long_symbol": spread.long_leg.symbol,
            "short_strike": spread.short_leg.strike,
            "long_strike": spread.long_leg.strike,
            "expiry": spread.expiry,
            "credit": spread.credit,
            "max_loss": spread.max_loss,
        })
    with _conn() as c:
        c.execute(
            "INSERT INTO decisions (underlying,direction,confidence,rationale,approved,contracts,reason,spread,position_id)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (underlying, direction.value, confidence, rationale, approved, contracts, reason, spread_json, position_id),
        )


def record_position(spread: VerticalSpread, contracts: int, order_id: str | None) -> int:
    with _conn() as c:
        row = c.execute(
            "INSERT INTO positions (underlying,expiry,option_right,short_symbol,long_symbol,contracts,credit,max_loss,order_id)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (spread.underlying, spread.expiry, spread.right.value, spread.short_leg.symbol,
             spread.long_leg.symbol, contracts, spread.credit, spread.max_loss * contracts, order_id),
        ).fetchone()
        return row[0]


def close_position(position_id: int, realized_pnl: float, exit_reason: str) -> None:
    from optionwright import metrics

    with _conn() as c:
        c.execute(
            "UPDATE positions SET status='closed', ts_close=now(), realized_pnl=%s, exit_reason=%s WHERE id=%s",
            (realized_pnl, exit_reason, position_id),
        )
    metrics.record_realized_pnl(realized_pnl)


def save_equity(equity: float, cash: float | None) -> None:
    from optionwright import metrics

    with _conn() as c:
        c.execute("INSERT INTO equity_curve (equity,cash) VALUES (%s,%s)", (equity, cash))
    metrics.set_equity(equity)


def _consecutive_losses(realized_pnls_desc: list[float]) -> int:
    """Leading run of losing trades (pnl < 0) from most-recent backwards."""
    n = 0
    for pnl in realized_pnls_desc:
        if pnl is not None and pnl < 0:
            n += 1
        else:
            break
    return n


def build_policy_state(
    underlying: str,
    equity: float,
    *,
    minutes_since_open: float | None = None,
    minutes_to_macro: float | None = None,
) -> PolicyState:
    """Read the live risk state for one underlying from Postgres."""
    with _conn() as c:
        open_positions = c.execute("SELECT count(*) FROM positions WHERE status='open'").fetchone()[0]
        at_risk = c.execute(
            "SELECT coalesce(sum(max_loss),0) FROM positions WHERE status='open' AND ts_open::date = now()::date"
        ).fetchone()[0]
        last = c.execute(
            "SELECT extract(epoch FROM now() - ts_open) FROM positions WHERE underlying=%s ORDER BY ts_open DESC LIMIT 1",
            (underlying,),
        ).fetchone()
        pnls = c.execute(
            "SELECT realized_pnl FROM positions WHERE status='closed' ORDER BY ts_close DESC LIMIT 20"
        ).fetchall()
    return PolicyState(
        equity=equity,
        open_positions=int(open_positions),
        consecutive_losses=_consecutive_losses([p[0] for p in pnls]),
        premium_at_risk_today=float(at_risk),
        seconds_since_symbol_trade=float(last[0]) if last else None,
        minutes_since_open=minutes_since_open,
        minutes_to_macro=minutes_to_macro,
    )
