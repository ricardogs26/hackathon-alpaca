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
from optionwright.policy.params import GLOBAL, REGISTRY, validate_scope
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


_TICK_COLS = (
    "ts", "position_id", "underlying", "spot", "credit", "price", "captured", "peak_captured",
    "pnl_now", "short_strike", "option_right", "short_delta", "short_iv", "sigma_dist",
    "hours_to_expiry", "hours_to_close", "sleeps_tonight", "decision", "reason",
)


def record_tick(tick) -> None:
    """Persist one PositionTick (see agent/state.py). Phase 0 instrumentation."""
    row = tick.as_row()
    with _conn() as c:
        c.execute(
            f"INSERT INTO position_ticks ({','.join(_TICK_COLS)}) VALUES ({','.join(['%s'] * len(_TICK_COLS))})",
            tuple(row[k] for k in _TICK_COLS),
        )


def get_ticks(position_id: int, limit: int = 2000) -> list[dict]:
    """Chronological ticks of one position, for replays and the dashboard."""
    with _conn() as c:
        cur = c.execute(
            "SELECT * FROM position_ticks WHERE position_id=%s ORDER BY ts LIMIT %s", (position_id, limit)
        )
        rows = _rows(cur)
    return [{**r, "ts": r["ts"].isoformat()} for r in rows]


# ── rule parameters (phase 1) ─────────────────────────────────────────────────
def seed_rules(seed: dict[str, object]) -> int:
    """Insert global values that don't exist yet. Idempotent: the environment
    is a seed, editing it later does nothing once the row exists."""
    n = 0
    with _conn() as c:
        for key, value in seed.items():
            if key not in REGISTRY:
                continue
            cur = c.execute(
                "INSERT INTO rules (scope, key, value) VALUES (%s, %s, %s) ON CONFLICT (scope, key) DO NOTHING",
                (GLOBAL, key, str(value)),
            )
            n += cur.rowcount
    if n:
        logger.info("seeded %d rule parameter(s) from the environment", n)
    return n


def load_rules() -> dict[str, dict[str, str]]:
    """{scope: {key: raw value}} — the Params constructor's input."""
    out: dict[str, dict[str, str]] = {}
    with _conn() as c:
        for scope, key, value in c.execute("SELECT scope, key, value FROM rules").fetchall():
            out.setdefault(scope, {})[key] = value
    return out


def set_rule(scope: str, key: str, value, changed_by: str, reason: str) -> dict:
    """Validate against the registry, upsert, and write the history row."""
    scope = validate_scope(scope)
    if key not in REGISTRY:
        raise ValueError(f"unknown rule {key!r}")
    if not (reason or "").strip():
        raise ValueError("a reason is required")
    new = REGISTRY[key].coerce(value)
    with _conn() as c:
        row = c.execute("SELECT value FROM rules WHERE scope=%s AND key=%s", (scope, key)).fetchone()
        old = row[0] if row else None
        c.execute(
            "INSERT INTO rules (scope, key, value, updated_at) VALUES (%s, %s, %s, now())"
            " ON CONFLICT (scope, key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
            (scope, key, str(new)),
        )
        c.execute(
            "INSERT INTO rules_history (scope, key, old_value, new_value, changed_by, reason) VALUES (%s,%s,%s,%s,%s,%s)",
            (scope, key, old, str(new), changed_by, reason.strip()),
        )
    logger.info("rule %s@%s: %s -> %s by %s (%s)", key, scope, old, new, changed_by, reason)
    return {"scope": scope, "key": key, "old": old, "new": new}


def rules_history(limit: int = 50) -> list[dict]:
    with _conn() as c:
        cur = c.execute(
            "SELECT ts, scope, key, old_value, new_value, changed_by, reason FROM rules_history ORDER BY ts DESC LIMIT %s",
            (limit,),
        )
        rows = _rows(cur)
    return [{**r, "ts": r["ts"].isoformat()} for r in rows]


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


def _recent_pnls(rows_desc: list[tuple], now, lookback_hours: float) -> list[float]:
    """P&Ls of the trades closed within the lookback (rows are (pnl, ts_close),
    most recent first). Discovered 4-Sep-2026 after 0.6.0: with no window the
    breaker read 4 straight losses from the 3-Sep and vetoed everything, and
    since nothing could open, no win could ever end the streak."""
    from datetime import timedelta

    cutoff = now - timedelta(hours=lookback_hours)
    return [float(pnl) for pnl, ts in rows_desc if ts is not None and ts >= cutoff]


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


def llm_book_view(book: dict) -> dict:
    """What the LLM sees of the book. Concentration and direction are OUT since
    phase 1: they are enforced by the gates (direction share, net delta), and a
    model asked to weigh them sat on a knife edge (abstain 0.40 / bearish 0.80
    on the same context). The model keeps size, today's P&L and the streak."""
    return {k: v for k, v in book.items() if k not in ("por_direccion", "concentracion")}


def _net_delta_usd(open_rows: list[dict], ticks: dict[int, dict]) -> float | None:
    """
    Signed $ delta of the book from the latest tick of each open position:
    short call spread -> −|delta|·100·contracts·spot, short put spread -> +.
    None if any open position has no measured delta/spot yet: a partial number
    would be a lie the gates would act on.
    """
    total = 0.0
    for r in open_rows:
        t = ticks.get(int(r["id"]))
        if not t or t.get("short_delta") is None or t.get("spot") is None:
            return None
        sign = -1.0 if r["option_right"] == "call" else 1.0
        total += sign * float(t["short_delta"]) * 100.0 * int(r["contracts"]) * float(t["spot"])
    return round(total, 2)


def latest_ticks(position_ids: list[int]) -> dict[int, dict]:
    """Latest tick per position id."""
    if not position_ids:
        return {}
    with _conn() as c:
        cur = c.execute(
            "SELECT DISTINCT ON (position_id) * FROM position_ticks WHERE position_id = ANY(%s)"
            " ORDER BY position_id, ts DESC", (list(position_ids),)
        )
        rows = _rows(cur)
    return {int(r["position_id"]): r for r in rows}


def book_net_delta_usd() -> float | None:
    with _conn() as c:
        open_rows = _rows(c.execute("SELECT id, option_right, contracts FROM positions WHERE status='open'"))
    return _net_delta_usd(open_rows, latest_ticks([r["id"] for r in open_rows]))


def open_position_states() -> list[dict]:
    """Open positions with their latest tick (the dashboard's state panel)."""
    with _conn() as c:
        open_rows = _rows(c.execute(
            "SELECT id, underlying, option_right, short_symbol, long_symbol, contracts, credit, expiry, ts_open"
            " FROM positions WHERE status='open' ORDER BY id"))
    ticks = latest_ticks([r["id"] for r in open_rows])
    out = []
    for r in open_rows:
        t = ticks.get(int(r["id"]), {})
        out.append({
            **r, "ts_open": r["ts_open"].isoformat(), "expiry": str(r["expiry"]),
            "tick_ts": t["ts"].isoformat() if t.get("ts") else None,
            "spot": t.get("spot"), "price": t.get("price"), "captured": t.get("captured"),
            "peak_captured": t.get("peak_captured"), "pnl_now": t.get("pnl_now"),
            "short_delta": t.get("short_delta"), "short_iv": t.get("short_iv"), "sigma_dist": t.get("sigma_dist"),
            "hours_to_expiry": t.get("hours_to_expiry"), "hours_to_close": t.get("hours_to_close"),
            "sleeps_tonight": t.get("sleeps_tonight"), "decision": t.get("decision"), "reason": t.get("reason"),
        })
    return out


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


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def book_summary(lookback_hours: float = 24.0) -> dict:
    """Resumen del libro abierto + P&L del día + racha de pérdidas (dentro de la ventana)."""
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
            "SELECT realized_pnl, ts_close FROM positions WHERE status='closed'"
            " ORDER BY ts_close DESC LIMIT 20"
        ).fetchall()
    consec = _consecutive_losses(_recent_pnls(pnls, _now(), lookback_hours))
    return _summarize_book(open_rows, float(pnl_dia), consec)


def build_policy_state(
    underlying: str,
    equity: float,
    *,
    minutes_since_open: float | None = None,
    minutes_to_macro: float | None = None,
    minutes_to_close: float | None = None,
    group_symbols: list[str] | None = None,
    lookback_hours: float = 24.0,
) -> PolicyState:
    """Read the live risk state for one underlying from Postgres. `group_symbols`
    are the peers in its correlation group (the symbol included)."""
    peers = [s.upper() for s in (group_symbols or [underlying])]
    with _conn() as c:
        open_rows = _rows(c.execute(
            "SELECT id, underlying, option_right, contracts, max_loss, short_symbol FROM positions WHERE status='open'"))
        last_group = c.execute(
            "SELECT extract(epoch FROM now() - max(ts_open)) FROM positions WHERE underlying = ANY(%s)", (peers,)
        ).fetchone()
        realized_today = c.execute(
            "SELECT coalesce(sum(realized_pnl),0) FROM positions WHERE status='closed' AND ts_close::date = now()::date"
        ).fetchone()[0]
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
            "SELECT realized_pnl, ts_close FROM positions WHERE status='closed' ORDER BY ts_close DESC LIMIT 20"
        ).fetchall()
        sig_rows = c.execute(
            "SELECT short_symbol, long_symbol FROM positions WHERE status='open'"
        ).fetchall()
    open_signatures = frozenset(f"{r[0]}|{r[1]}" for r in sig_rows)
    risk_by_direction: dict[str, float] = {}
    for r in open_rows:
        side = "bearish" if r["option_right"] == "call" else "bullish"
        risk_by_direction[side] = risk_by_direction.get(side, 0.0) + float(r["max_loss"] or 0.0)
    net_delta = _net_delta_usd(open_rows, latest_ticks([r["id"] for r in open_rows]))
    open_in_group = sum(1 for r in open_rows if r["underlying"].upper() in peers)
    short_strikes = frozenset(_short_strike_key(r["underlying"], r["short_symbol"]) for r in open_rows)
    return PolicyState(
        equity=equity,
        open_positions=int(open_positions),
        consecutive_losses=_consecutive_losses(_recent_pnls(pnls, _now(), lookback_hours)),
        premium_at_risk_today=float(at_risk),
        open_positions_underlying=int(open_underlying),
        seconds_since_symbol_trade=float(last[0]) if last else None,
        minutes_since_open=minutes_since_open,
        minutes_to_macro=minutes_to_macro,
        open_signatures=open_signatures,
        risk_by_direction=risk_by_direction,
        net_delta_usd=net_delta,
        minutes_to_close=minutes_to_close,
        realized_pnl_today=float(realized_today),
        open_positions_group=open_in_group,
        seconds_since_group_trade=float(last_group[0]) if last_group and last_group[0] is not None else None,
        open_short_strikes=short_strikes,
    )


def _short_strike_key(underlying: str, short_symbol: str) -> str:
    """'SPY|C|769.0' from the short leg's OCC symbol (the gates' same-strike guard)."""
    from optionwright.agent.state import parse_occ

    try:
        _, _, right, strike = parse_occ(short_symbol)
        return f"{underlying.upper()}|{right}|{strike}"
    except ValueError:
        return f"{underlying.upper()}|?|{short_symbol}"
