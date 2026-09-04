"""
Domain models for the options layer. Plain dataclasses — no Alpaca types leak in
here, so the selection logic stays pure and testable against synthetic chains.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Right(str, Enum):
    CALL = "call"
    PUT = "put"


class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"      # iron condor: bull put + bear call, the price stays in the range
    ABSTAIN = "abstain"


@dataclass(frozen=True)
class OptionQuote:
    """A single option contract as the agent sees it after normalization."""
    symbol: str          # OCC symbol, e.g. SPY260904P00640000
    underlying: str
    right: Right
    strike: float
    expiry: str          # ISO date, e.g. "2026-09-04"
    bid: float
    ask: float
    delta: float         # signed: calls positive, puts negative
    open_interest: int
    volume: int

    @property
    def mid(self) -> float:
        return round((self.bid + self.ask) / 2, 4)

    @property
    def spread_pct(self) -> float:
        """Bid-ask spread as a fraction of the mid. A liquidity signal."""
        if self.mid <= 0:
            return 1.0
        return (self.ask - self.bid) / self.mid


@dataclass(frozen=True)
class VerticalSpread:
    """
    A defined-risk credit vertical. `short_leg` is sold, `long_leg` is bought;
    both share right and expiry. Max loss is fixed at construction.
    """
    underlying: str
    right: Right
    expiry: str
    short_leg: OptionQuote
    long_leg: OptionQuote
    direction: Direction

    @property
    def width(self) -> float:
        return round(abs(self.short_leg.strike - self.long_leg.strike), 4)

    @property
    def credit(self) -> float:
        """Net credit received per share (short mid − long mid)."""
        return round(self.short_leg.mid - self.long_leg.mid, 4)

    @property
    def max_loss(self) -> float:
        """Per contract (×100 shares). Fixed the moment the spread opens."""
        return round((self.width - self.credit) * 100, 2)

    @property
    def max_profit(self) -> float:
        """Per contract. The credit is the most you can keep."""
        return round(self.credit * 100, 2)

    @property
    def reward_risk(self) -> float:
        if self.max_loss <= 0:
            return 0.0
        return round(self.max_profit / self.max_loss, 4)
