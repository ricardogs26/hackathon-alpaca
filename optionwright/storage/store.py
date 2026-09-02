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


def update_peak_captured(position_id: int, captured: float) -> None:
    """Raise the position's high-water mark (never lowers it)."""
    with _conn() as c:
        c.execute(
            "UPDATE positions SET peak_captured = GREATEST(coalesce(peak_captured,0), %s) WHERE id=%s",
            (captured, position_id),
        )


def save_equity(equity: float, cash: float | None) -> None:
    from optionwright import metrics

    with _conn() as c:
        c.execute("INSERT INTO equity_curve (equity,cash) VALUES (%s,%s)", (equity, cash))
    metrics.set_equity(equity)


def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_equity_curve(limit: int = 500) -> list[dict]:
    with _conn() as c:
        cur = c.execute(
            "SELECT ts, equity, cash FROM equity_curve ORDER BY ts DESC LIMIT %s", (limit,)
        )
        rows = _rows(cur)
    rows.reverse()  # chronological for charting
    return [{"ts": r["ts"].isoformat(), "equity": r["equity"], "cash": r["cash"]} for r in rows]


def get_equity_daily(limit: int = 120) -> list[dict]:
    """One row per calendar day: the last equity recorded that day (chronological).
    The intraday curve has hundreds of points/day; the chart needs one per day."""
    with _conn() as c:
        cur = c.execute(
            "SELECT DISTINCT ON (ts::date) ts::date AS d, equity"
            " FROM equity_curve ORDER BY ts::date DESC, ts DESC LIMIT %s", (limit,)
        )
        rows = _rows(cur)
    rows.reverse()  # chronological for charting
    return [{"date": str(r["d"]), "equity": r["equity"]} for r in rows]


def get_positions(limit: int = 50) -> list[dict]:
    with _conn() as c:
        # LEFT JOIN LATERAL pulls the confidence/rationale of the decision that
        # opened each position, so the dashboard can build "open" events from
        # positions (full history) instead of from the 100-row decision log,
        # where ~98% abstentions push any open out of view within ~2 hours.
        cur = c.execute(
            "SELECT p.id, p.ts_open, p.ts_close, p.underlying, p.option_right, p.expiry,"
            " p.short_symbol, p.long_symbol, p.contracts, p.credit, p.max_loss, p.status,"
            " p.realized_pnl, p.exit_reason, coalesce(p.peak_captured,0) AS peak_captured,"
            " d.confidence AS open_confidence, d.rationale AS open_rationale"
            " FROM positions p"
            " LEFT JOIN LATERAL (SELECT confidence, rationale FROM decisions"
            "   WHERE position_id = p.id AND approved ORDER BY ts DESC LIMIT 1) d ON true"
            " ORDER BY p.ts_open DESC LIMIT %s", (limit,)
        )
        rows = _rows(cur)
    for r in rows:
        r["ts_open"] = r["ts_open"].isoformat()
        r["ts_close"] = r["ts_close"].isoformat() if r.get("ts_close") else None
        r["expiry"] = str(r["expiry"])
    return rows


def get_decisions(limit: int = 30) -> list[dict]:
    with _conn() as c:
        cur = c.execute(
            "SELECT ts, underlying, direction, confidence, rationale, approved, contracts, reason"
            " FROM decisions ORDER BY ts DESC LIMIT %s", (limit,)
        )
        rows = _rows(cur)
    return [{**r, "ts": r["ts"].isoformat()} for r in rows]


def last_opened_confidence() -> float | None:
    """Confidence of the most recent decision that actually opened a position.
    Seeds the confidence-on-trades gauge at startup so it survives redeploys."""
    with _conn() as c:
        row = c.execute(
            "SELECT confidence FROM decisions WHERE approved=true AND confidence IS NOT NULL"
            " ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    return float(row[0]) if row else None


def _consecutive_losses(realized_pnls_desc: list[float]) -> int:
    """Leading run of losing trades (pnl < 0) from most-recent backwards."""
    n = 0
    for pnl in realized_pnls_desc:
        if pnl is not None and pnl < 0:
            n += 1
        else:
            break
    return n


def _summarize_outcomes(rows: list[dict]) -> dict:
    """Resumen de trades cerrados por dirección (call=bajista, put=alcista)."""
    closed = [r for r in rows if r.get("realized_pnl") is not None]

    def won(r):
        return r["realized_pnl"] > 0

    return {
        "cerradas": len(closed),
        "ganadas_bajista": sum(1 for r in closed if r["option_right"] == "call" and won(r)),
        "ganadas_alcista": sum(1 for r in closed if r["option_right"] == "put" and won(r)),
        "perdidas": sum(1 for r in closed if not won(r)),
    }


def _summarize_book(open_rows: list[dict], pnl_dia: float, consec_losses: int) -> dict:
    """Resumen legible del libro abierto para el contexto del LLM."""
    from collections import Counter

    by_u = Counter(r["underlying"] for r in open_rows)
    by_dir = Counter(
        "bajista" if r["option_right"] == "call" else "alcista" for r in open_rows
    )
    conc = by_u.most_common(1)[0][0] if by_u else None
    return {
        "abiertas": len(open_rows),
        "por_subyacente": dict(by_u),
        "por_direccion": dict(by_dir),
        "concentracion": conc,
        "pnl_dia": round(pnl_dia, 2),
        "perdidas_consecutivas": consec_losses,
    }


def recent_outcomes(underlying: str, limit: int = 5) -> dict:
    """Resumen de los últimos `limit` trades cerrados del subyacente."""
    with _conn() as c:
        cur = c.execute(
            "SELECT underlying, option_right, realized_pnl FROM positions"
            " WHERE status='closed' AND underlying=%s ORDER BY ts_close DESC LIMIT %s",
            (underlying, limit),
        )
        rows = _rows(cur)
    return _summarize_outcomes(rows)


def book_summary() -> dict:
    """Resumen del libro abierto + P&L del día + racha de pérdidas."""
    with _conn() as c:
        open_cur = c.execute(
            "SELECT underlying, option_right FROM positions WHERE status='open'"
        )
        open_rows = _rows(open_cur)
        pnl_dia = c.execute(
            "SELECT coalesce(sum(realized_pnl),0) FROM positions"
            " WHERE status='closed' AND ts_close::date = now()::date"
        ).fetchone()[0]
        pnls = c.execute(
            "SELECT realized_pnl FROM positions WHERE status='closed'"
            " ORDER BY ts_close DESC LIMIT 20"
        ).fetchall()
    consec = _consecutive_losses([p[0] for p in pnls])
    return _summarize_book(open_rows, float(pnl_dia), consec)


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
        open_underlying = c.execute(
            "SELECT count(*) FROM positions WHERE status='open' AND underlying=%s", (underlying,)
        ).fetchone()[0]
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
        sig_rows = c.execute(
            "SELECT short_symbol, long_symbol FROM positions WHERE status='open'"
        ).fetchall()
    open_signatures = frozenset(f"{r[0]}|{r[1]}" for r in sig_rows)
    return PolicyState(
        equity=equity,
        open_positions=int(open_positions),
        consecutive_losses=_consecutive_losses([p[0] for p in pnls]),
        premium_at_risk_today=float(at_risk),
        open_positions_underlying=int(open_underlying),
        seconds_since_symbol_trade=float(last[0]) if last else None,
        minutes_since_open=minutes_since_open,
        minutes_to_macro=minutes_to_macro,
        open_signatures=open_signatures,
    )
