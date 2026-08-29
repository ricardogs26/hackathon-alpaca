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
# A contract must clear all three to be tradable. These are the first line of
# defense: an illiquid leg means slippage we can't model and can't exit.
MIN_OPEN_INTEREST = 100
MIN_VOLUME = 10
MAX_SPREAD_PCT = 0.15   # bid-ask no wider than 15% of mid


def is_liquid(q: OptionQuote) -> bool:
    return (
        q.open_interest >= MIN_OPEN_INTEREST
        and q.volume >= MIN_VOLUME
        and q.mid > 0
        and q.spread_pct <= MAX_SPREAD_PCT
    )


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
