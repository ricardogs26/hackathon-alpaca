"""
Deterministic spread selection. Given a chain and a direction, this module picks
the strikes and builds the vertical — with zero LLM involvement. Every numeric
comparison the decision needs happens here, in code, so the model never has to
(and never gets to) do arithmetic.

Pure functions over the domain models in models.py: fully testable against
synthetic chains, no network, no Alpaca types.
"""
from __future__ import annotations

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


def is_liquid(q: OptionQuote) -> bool:
    if q.mid <= 0 or q.open_interest < MIN_OPEN_INTEREST or q.spread_pct > MAX_SPREAD_PCT:
        return False
    # volume is a soft signal; deep open interest bypasses it
    return q.volume >= MIN_VOLUME or q.open_interest >= OI_VOLUME_BYPASS


def liquid_contracts(chain: list[OptionQuote], right: Right, expiry: str) -> list[OptionQuote]:
    return [q for q in chain if q.right is right and q.expiry == expiry and is_liquid(q)]


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
) -> VerticalSpread | None:
    """
    Build a defined-risk credit vertical for the given direction.

    - Bullish  → bull put spread : short put near `short_delta`, long put `width` lower.
    - Bearish  → bear call spread: short call near `short_delta`, long call `width` higher.

    The short leg is chosen by delta (a ~0.30-delta short ≈ ~70% chance of expiring
    worthless). The long leg is the liquid contract nearest `width` away in the
    protective direction. Returns None if either leg can't be found liquid — the
    agent then abstains rather than trading a broken structure.
    """
    if direction is Direction.ABSTAIN:
        return None

    right = Right.PUT if direction is Direction.BULLISH else Right.CALL
    contracts = liquid_contracts(chain, right, expiry)
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
