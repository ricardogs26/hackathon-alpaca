"""
Risk gates. An ordered list of checks that can only ever VETO or SHRINK a
proposed trade — never enlarge it. This is where the agent's discipline lives:
the LLM proposes a direction, select.py builds a spread, and the trade only
reaches the broker if it clears every gate here.

Scaffold: gate signatures and ordering are fixed; each gate's body is filled
with tests before market open (plan: Sunday).
"""
from __future__ import annotations

from dataclasses import dataclass

from optionwright.options.models import VerticalSpread


@dataclass(frozen=True)
class Verdict:
    approved: bool
    contracts: int          # 0 when vetoed; may be shrunk below the request
    reason: str             # human-readable, logged with every decision


# Ordered gates. TODO(Sun): implement each against Postgres/Redis state + equity.
#   1. max_loss_per_position  — spread.max_loss × contracts ≤ pct of equity
#   2. open_positions_cap     — count of live spreads < N
#   3. daily_premium_budget   — capital-at-risk deployed today is bounded
#   4. per_underlying_cooldown— no re-entry within a window
#   5. consecutive_loss_breaker — N losses in a row → pause
#   6. opening_blackout       — no trades in first minutes after the open
#   7. macro_blackout         — no new trades near FOMC/CPI/NFP
def evaluate(spread: VerticalSpread, requested_contracts: int) -> Verdict:
    raise NotImplementedError("policy.gates.evaluate — implemented Sunday with tests")
