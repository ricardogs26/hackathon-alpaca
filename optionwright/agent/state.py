"""
Position state vector — phase 0 of the post-hackathon plan (instrumentation).

Every exits tick we record, for each open position, what an experienced premium
seller looks at before deciding: how far the short strike sits in units of the
underlying's expected move, the short leg's delta (≈ probability of expiring in
the money), how much life and how much session are left, whether the position
will sleep through a close, and how much of the credit is captured.

Pure functions only. The runner feeds them and the store keeps the rows. Nothing
here changes what the agent does: the state-based rules engine (phase 1+) will
be designed on these real ticks instead of on fixed multiples of the credit.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

# OCC option symbol: ROOT + YYMMDD + C|P + strike*1000 (8 digits). Parsed from
# the end because the root has variable length (SPY, QQQ, BRKB, ...).
_OCC = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<right>[CP])(?P<strike>\d{8})$")

HOURS_PER_YEAR = 365.0 * 24.0
# Options expire at the 16:00 ET close. We approximate that as 20:00 UTC (EDT);
# in winter it is 21:00 UTC — a one-hour error on a horizon of days, documented
# rather than hidden behind a timezone dependency.
EXPIRY_HOUR_UTC = 20


def parse_occ(symbol: str) -> tuple[str, str, str, float]:
    """(root, expiry ISO, right 'C'|'P', strike) from an OCC symbol."""
    m = _OCC.match(symbol or "")
    if not m:
        raise ValueError(f"not an OCC option symbol: {symbol!r}")
    expiry = f"20{m['yy']}-{m['mm']}-{m['dd']}"
    return m["root"], expiry, m["right"], int(m["strike"]) / 1000.0


def expiry_moment(expiry: str) -> datetime:
    """The UTC instant the contract stops trading (see EXPIRY_HOUR_UTC)."""
    d = date.fromisoformat(str(expiry))
    return datetime(d.year, d.month, d.day, EXPIRY_HOUR_UTC, tzinfo=timezone.utc)


def sigma_distance(spot: float, strike: float, right: str, iv: float | None, hours_to_expiry: float) -> float | None:
    """
    Distance from spot to the short strike in units of the expected move over
    the remaining life: (K − S) / (S · σ · √T) for calls, (S − K) / … for puts.
    Positive = the strike is still on the safe side; 0 = at the money; negative
    = in the money. None when there is no IV or no time left to measure it.
    """
    if not iv or iv <= 0 or spot <= 0 or hours_to_expiry <= 0:
        return None
    expected_move = spot * iv * math.sqrt(hours_to_expiry / HOURS_PER_YEAR)
    if expected_move <= 0:
        return None
    dist = (strike - spot) if right == "C" else (spot - strike)
    return round(dist / expected_move, 3)


def position_clock(short_symbol: str, now: datetime, next_close: datetime | None) -> tuple[float, float | None, bool | None]:
    """(hours_to_expiry, hours_to_close, sleeps_tonight) for a position — the
    time inputs the exit rules and the tick share."""
    _, expiry, _, _ = parse_occ(short_symbol)
    hours_to_expiry = max(0.0, (expiry_moment(expiry) - now).total_seconds() / 3600.0)
    if next_close is None:
        return hours_to_expiry, None, None
    hours_to_close = max(0.0, (next_close - now).total_seconds() / 3600.0)
    sleeps = date.fromisoformat(expiry) > next_close.astimezone(timezone.utc).date()
    return hours_to_expiry, hours_to_close, sleeps


@dataclass(frozen=True)
class PositionTick:
    position_id: int
    ts: datetime
    underlying: str
    spot: float | None
    credit: float
    price: float                 # debit to close now
    captured: float              # fraction of the credit captured (negative = losing)
    peak_captured: float
    pnl_now: float
    short_strike: float
    option_right: str            # 'C' | 'P'
    short_delta: float | None    # |delta| of the short leg ≈ P(ITM at expiry)
    short_iv: float | None
    sigma_dist: float | None
    hours_to_expiry: float
    hours_to_close: float | None
    sleeps_tonight: bool | None  # True = expiry is after today's session close
    decision: str                # hold | close
    reason: str

    def as_row(self) -> dict:
        return asdict(self)


def compute_tick(
    *,
    pos: dict,
    price: float,
    peak_captured: float,
    decision: str,
    reason: str,
    spot: float | None,
    short_delta: float | None,
    short_iv: float | None,
    now: datetime,
    next_close: datetime | None,
) -> PositionTick:
    """Build the tick from what the exits pass already knows plus one snapshot."""
    _, _, right, strike = parse_occ(pos["short_symbol"])
    credit = float(pos["credit"])
    captured = (credit - price) / credit if credit > 0 else 0.0
    pnl_now = round((credit - price) * 100 * int(pos["contracts"]), 2)
    hours_to_expiry, hours_to_close, sleeps = position_clock(pos["short_symbol"], now, next_close)
    return PositionTick(
        position_id=int(pos["id"]),
        ts=now,
        underlying=pos["underlying"],
        spot=spot,
        credit=credit,
        price=price,
        captured=round(captured, 4),
        peak_captured=round(float(peak_captured), 4),
        pnl_now=pnl_now,
        short_strike=strike,
        option_right=right,
        short_delta=None if short_delta is None else round(abs(float(short_delta)), 4),
        short_iv=None if short_iv is None else round(float(short_iv), 4),
        sigma_dist=sigma_distance(spot or 0.0, strike, right, short_iv, hours_to_expiry),
        hours_to_expiry=round(hours_to_expiry, 2),
        hours_to_close=None if hours_to_close is None else round(hours_to_close, 2),
        sleeps_tonight=sleeps,
        decision=decision,
        reason=reason,
    )
