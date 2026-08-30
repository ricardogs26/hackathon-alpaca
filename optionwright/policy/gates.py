"""
Risk gates. An ordered list of checks that can only ever VETO or SHRINK a
proposed trade, never enlarge it. This is where the agent's discipline lives.

The gates are pure: they read a `PolicyState` snapshot passed in by the caller
(the loop builds it from Postgres/Redis and the clock) and a `RuleSet` of
tunable limits. No I/O here, so every gate is unit-tested against hand-built
state. The order matters and is fixed.
"""
from __future__ import annotations

from dataclasses import dataclass

from optionwright.options.models import VerticalSpread


@dataclass(frozen=True)
class RuleSet:
    max_loss_pct: float = 0.01          # max loss per position as a fraction of equity
    max_open_positions: int = 3
    daily_budget_pct: float = 0.05      # capital-at-risk deployable per day
    cooldown_seconds: float = 3600.0    # per-underlying re-entry cooldown
    max_consecutive_losses: int = 3
    opening_blackout_minutes: float = 30.0
    macro_blackout_minutes: float = 60.0


@dataclass(frozen=True)
class PolicyState:
    equity: float
    open_positions: int
    consecutive_losses: int
    premium_at_risk_today: float                 # sum of max-loss already deployed today
    seconds_since_symbol_trade: float | None = None   # None = never traded this symbol
    minutes_since_open: float | None = None           # None = unknown (gate skipped)
    minutes_to_macro: float | None = None             # None = no upcoming macro event


@dataclass(frozen=True)
class Verdict:
    approved: bool
    contracts: int          # 0 when vetoed; may be shrunk below the request
    reason: str             # logged with every decision


def _max_contracts_by_loss(spread: VerticalSpread, budget: float) -> int:
    """How many contracts fit under a dollar budget of max loss."""
    if spread.max_loss <= 0:
        return 0
    return int(budget // spread.max_loss)


def evaluate(
    spread: VerticalSpread,
    requested_contracts: int,
    state: PolicyState,
    rules: RuleSet | None = None,
) -> Verdict:
    rules = rules or RuleSet()
    if requested_contracts < 1:
        return Verdict(False, 0, "requested_contracts < 1")

    n = requested_contracts

    # 1 — Consecutive-loss breaker (hard stop, first so nothing slips through).
    if state.consecutive_losses >= rules.max_consecutive_losses:
        return Verdict(False, 0, f"circuit breaker: {state.consecutive_losses} consecutive losses")

    # 2 — Open positions cap.
    if state.open_positions >= rules.max_open_positions:
        return Verdict(False, 0, f"open positions cap: {state.open_positions}/{rules.max_open_positions}")

    # 3 — Per-underlying cooldown.
    if state.seconds_since_symbol_trade is not None and state.seconds_since_symbol_trade < rules.cooldown_seconds:
        return Verdict(False, 0, f"cooldown: {state.seconds_since_symbol_trade:.0f}s < {rules.cooldown_seconds:.0f}s")

    # 4 — Opening blackout.
    if state.minutes_since_open is not None and state.minutes_since_open < rules.opening_blackout_minutes:
        return Verdict(False, 0, f"opening blackout: {state.minutes_since_open:.0f}min")

    # 5 — Macro blackout.
    if state.minutes_to_macro is not None and state.minutes_to_macro < rules.macro_blackout_minutes:
        return Verdict(False, 0, f"macro blackout: event in {state.minutes_to_macro:.0f}min")

    # 6 — Max loss per position (shrinks n to fit the per-position budget).
    per_pos_budget = state.equity * rules.max_loss_pct
    n_by_loss = _max_contracts_by_loss(spread, per_pos_budget)
    if n_by_loss < 1:
        return Verdict(False, 0, f"one contract (${spread.max_loss}) exceeds max-loss budget (${per_pos_budget:.0f})")
    n = min(n, n_by_loss)

    # 7 — Daily premium budget (shrinks n to what's left in today's risk budget).
    daily_budget = state.equity * rules.daily_budget_pct
    remaining = daily_budget - state.premium_at_risk_today
    n_by_daily = _max_contracts_by_loss(spread, remaining)
    if n_by_daily < 1:
        return Verdict(False, 0, f"daily budget exhausted (${state.premium_at_risk_today:.0f}/${daily_budget:.0f})")
    n = min(n, n_by_daily)

    reason = f"approved {n}x (max loss ${spread.max_loss * n:.0f})"
    if n < requested_contracts:
        reason += f", shrunk from {requested_contracts}"
    return Verdict(True, n, reason)
