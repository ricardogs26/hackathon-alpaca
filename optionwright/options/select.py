"""
Deterministic spread selection. Given a chain and a direction, this module picks
the strikes and builds the vertical — with zero LLM involvement. Every numeric
comparison the decision needs happens here, in code, so the model never has to
(and never gets to) do arithmetic.

Pure functions over the domain models in models.py: fully testable against
synthetic chains, no network, no Alpaca types.
"""
from __future__ import annotations

from dataclasses import dataclass

from optionwright.options.models import (
    Direction,
    OptionQuote,
    Right,
    VerticalSpread,
)

# ── Liquidity gate ────────────────────────────────────────────────────────────
# Open interest and a tight bid-ask are the hard signals: an illiquid leg means
# slippage we can't model and can't exit. Daily VOLUME is only a soft signal —
# it is 0 off-hours and near-0 in the first minutes after the open, so a contract
# with deep open interest (an established, actively-traded strike) clears the gate
# even before intraday volume builds. A thin contract still needs volume to prove
# it actually trades.
MIN_OPEN_INTEREST = 100
MIN_VOLUME = 10
OI_VOLUME_BYPASS = 500   # OI this deep proves liquidity without intraday volume
MAX_SPREAD_PCT = 0.15    # bid-ask no wider than 15% of mid


def is_liquid(q: OptionQuote, *, min_oi: int = MIN_OPEN_INTEREST, max_spread_pct: float = MAX_SPREAD_PCT) -> bool:
    if q.mid <= 0 or q.open_interest < min_oi or q.spread_pct > max_spread_pct:
        return False
    # volume is a soft signal; deep open interest bypasses it
    return q.volume >= MIN_VOLUME or q.open_interest >= OI_VOLUME_BYPASS


def liquid_contracts(chain: list[OptionQuote], right: Right, expiry: str, *,
                     min_oi: int = MIN_OPEN_INTEREST, max_spread_pct: float = MAX_SPREAD_PCT) -> list[OptionQuote]:
    return [q for q in chain if q.right is right and q.expiry == expiry
            and is_liquid(q, min_oi=min_oi, max_spread_pct=max_spread_pct)]


@dataclass(frozen=True)
class SelectParams:
    """Selection knobs, resolved from the rules table per underlying/group."""
    short_delta: float = 0.30
    width_pct: float = 0.0065          # of spot; SPY 770 -> 5.0, IWM 295 -> 2.0, AAPL 320 -> 2.0
    width_tolerance: float = 0.5       # long leg within ±50% of the target width, else no spread
    min_open_interest: int = MIN_OPEN_INTEREST
    max_quote_spread_pct: float = MAX_SPREAD_PCT

    @classmethod
    def from_params(cls, params, underlying: str | None = None, group: str | None = None) -> "SelectParams":
        g = lambda k: params.get(k, underlying, group)  # noqa: E731
        return cls(short_delta=g("short_delta"), width_pct=g("width_pct"), width_tolerance=g("width_tolerance"),
                   min_open_interest=g("min_open_interest"), max_quote_spread_pct=g("max_quote_spread_pct"))


def strike_step(contracts: list[OptionQuote]) -> float:
    """Smallest gap between listed strikes (1.0 for SPY, 0.5 for AAPL, 2.5 for NVDA…). 1.0 if unknown."""
    strikes = sorted({q.strike for q in contracts})
    gaps = [round(b - a, 4) for a, b in zip(strikes, strikes[1:]) if b > a]
    return min(gaps) if gaps else 1.0


def width_for(spot: float, width_pct: float, step: float, *, min_width: float = 0.5, max_width: float = 25.0) -> float:
    """Spread width proportional to spot, snapped to the strike step and clamped.
    A fixed 5-wide was 0.65% of SPY but 1.7% of IWM (reward/risk 0.11 on 293/287)."""
    raw = max(min_width, min(max_width, spot * width_pct))
    snapped = round(raw / step) * step if step > 0 else raw
    return round(max(step, snapped), 4)


def _nearest_by_delta(contracts: list[OptionQuote], target_abs_delta: float) -> OptionQuote | None:
    """Contract whose |delta| is closest to the target. None if list is empty."""
    if not contracts:
        return None
    return min(contracts, key=lambda q: abs(abs(q.delta) - target_abs_delta))


def build_spread(
    chain: list[OptionQuote],
    direction: Direction,
    expiry: str,
    *,
    short_delta: float = 0.30,
    width: float = 5.0,
    width_tolerance: float | None = None,
    min_oi: int = MIN_OPEN_INTEREST,
    max_spread_pct: float = MAX_SPREAD_PCT,
) -> VerticalSpread | None:
    """
    Build a defined-risk credit vertical for the given direction.

    - Bullish  → bull put spread : short put near `short_delta`, long put `width` lower.
    - Bearish  → bear call spread: short call near `short_delta`, long call `width` higher.

    The short leg is chosen by delta (a ~0.30-delta short ≈ ~70% chance of expiring
    worthless). The long leg is the liquid contract nearest `width` away in the
    protective direction — and, when `width_tolerance` is given, no farther than
    that fraction of the width from the target: the probe of 4-Sep-2026 showed
    the nearest liquid leg jumping to 10 wide on QQQ (715/705) and MSFT (510/520),
    doubling the risk of the structure without anyone asking for it. Returns
    None if either leg can't be found liquid — the agent then abstains rather
    than trading a broken structure.
    """
    if direction is Direction.ABSTAIN:
        return None

    right = Right.PUT if direction is Direction.BULLISH else Right.CALL
    contracts = liquid_contracts(chain, right, expiry, min_oi=min_oi, max_spread_pct=max_spread_pct)
    if not contracts:
        return None

    short_leg = _nearest_by_delta(contracts, short_delta)
    if short_leg is None:
        return None

    # Protective long leg: puts go DOWN (lower strike), calls go UP (higher strike).
    if right is Right.PUT:
        target = short_leg.strike - width
        candidates = [q for q in contracts if q.strike < short_leg.strike]
    else:
        target = short_leg.strike + width
        candidates = [q for q in contracts if q.strike > short_leg.strike]

    if not candidates:
        return None
    long_leg = min(candidates, key=lambda q: abs(q.strike - target))
    if width_tolerance is not None and abs(long_leg.strike - target) > width_tolerance * width:
        return None

    spread = VerticalSpread(
        underlying=short_leg.underlying,
        right=right,
        expiry=expiry,
        short_leg=short_leg,
        long_leg=long_leg,
        direction=direction,
    )
    # A credit spread must actually take in credit; a non-positive credit means
    # the chain is inverted or mispriced — refuse it.
    if spread.credit <= 0:
        return None
    return spread
