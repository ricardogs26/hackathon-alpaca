"""
Risk gates. An ordered list of checks that can only ever VETO or SHRINK a
proposed trade, never enlarge it. This is where the agent's discipline lives.

The gates are pure: they read a `PolicyState` snapshot passed in by the caller
(the runner builds it from Postgres and the clock) and a `RuleSet` of limits
resolved from the parameter table (policy/params.py). No I/O here, so every
gate is unit-tested against hand-built state. The order matters and is fixed.

Phase 1 (post-mortem of the 31-Aug → 3-Sep week) added the gates that would
have stopped that week's loss: the confidence gate moved here from the loop,
a daily-loss pause, no entries in the last hour, a minimum reward/risk, a cap
on the share of risk on one side and a cap on the book's net delta. Every
input that depends on the network (net delta, minutes to close) is optional:
unknown means the gate is skipped, never that it invents a number.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from optionwright.options.models import Direction, Right, VerticalSpread


@dataclass(frozen=True)
class RuleSet:
    max_loss_pct: float = 0.01
    max_open_positions: int = 6
    max_per_underlying: int = 2
    daily_budget_pct: float = 0.05
    cooldown_seconds: float = 2700.0
    max_consecutive_losses: int = 3
    opening_blackout_minutes: float = 30.0
    macro_blackout_minutes: float = 60.0
    min_confidence: float = 0.6
    max_direction_share: float = 0.60
    max_net_delta_pct: float = 0.03
    min_reward_risk: float = 0.20
    max_daily_loss_pct: float = 0.02
    no_entry_minutes_before_close: float = 60.0
    max_per_group: int = 2
    group_cooldown_seconds: float = 1800.0
    breaker_lookback_hours: float = 24.0

    @classmethod
    def from_params(cls, params, underlying: str | None = None, group: str | None = None) -> "RuleSet":
        g = lambda k: params.get(k, underlying, group)  # noqa: E731
        return cls(
            max_loss_pct=g("max_loss_pct"), max_open_positions=g("max_open_positions"),
            max_per_underlying=g("max_per_underlying"), daily_budget_pct=g("daily_budget_pct"),
            cooldown_seconds=g("cooldown_seconds"), max_consecutive_losses=g("max_consecutive_losses"),
            opening_blackout_minutes=g("opening_blackout_minutes"), macro_blackout_minutes=g("macro_blackout_minutes"),
            min_confidence=g("min_confidence"), max_direction_share=g("max_direction_share"),
            max_net_delta_pct=g("max_net_delta_pct"), min_reward_risk=g("min_reward_risk"),
            max_daily_loss_pct=g("max_daily_loss_pct"), no_entry_minutes_before_close=g("no_entry_minutes_before_close"),
            max_per_group=g("max_per_group"), group_cooldown_seconds=g("group_cooldown_seconds"),
            breaker_lookback_hours=g("breaker_lookback_hours"),
        )


@dataclass(frozen=True)
class PolicyState:
    equity: float
    open_positions: int
    consecutive_losses: int
    premium_at_risk_today: float                 # sum of max-loss already deployed today
    open_positions_underlying: int = 0           # open positions on THIS spread's underlying
    seconds_since_symbol_trade: float | None = None   # None = never traded this symbol
    minutes_since_open: float | None = None           # None = unknown (gate skipped)
    minutes_to_macro: float | None = None             # None = no upcoming macro event
    open_signatures: frozenset = frozenset()          # "short_symbol|long_symbol" of open spreads
    # phase 1
    risk_by_direction: dict = field(default_factory=dict)   # {"bullish": $max_loss, "bearish": $max_loss} of open spreads
    net_delta_usd: float | None = None                 # signed $ delta of the book (None = no ticks yet -> gate skipped)
    minutes_to_close: float | None = None              # None = unknown (gate skipped)
    realized_pnl_today: float = 0.0
    # phase 2: correlation groups
    open_positions_group: int = 0                      # open spreads on any symbol of THIS spread's group
    seconds_since_group_trade: float | None = None     # None = the group never traded
    open_short_strikes: frozenset = frozenset()        # "SPY|C|769.0" of open spreads


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


def _delta_sign(direction: Direction) -> int:
    # A short put spread is long delta; a short call spread is short delta.
    return 1 if direction is Direction.BULLISH else -1


def evaluate(
    spread: VerticalSpread,
    requested_contracts: int,
    state: PolicyState,
    rules: RuleSet | None = None,
    confidence: float | None = None,
) -> Verdict:
    rules = rules or RuleSet()
    if requested_contracts < 1:
        return Verdict(False, 0, "requested_contracts < 1")

    n = requested_contracts

    # 0 — Confidence gate: the direction alone is not enough to put money on the line.
    if confidence is not None and confidence < rules.min_confidence:
        return Verdict(False, 0, f"low confidence {confidence:.2f} < {rules.min_confidence:.2f}")

    # 1 — Consecutive-loss breaker (hard stop, first so nothing slips through).
    if state.consecutive_losses >= rules.max_consecutive_losses:
        return Verdict(False, 0, f"circuit breaker: {state.consecutive_losses} consecutive losses")

    # 1b — Daily loss pause: after losing this much today, no new entries (3-Sep: −8% in a day).
    daily_loss_cap = state.equity * rules.max_daily_loss_pct
    if state.realized_pnl_today <= -daily_loss_cap:
        return Verdict(False, 0, f"daily loss pause: ${-state.realized_pnl_today:.0f} >= ${daily_loss_cap:.0f}")

    # 2 — Open positions cap (global).
    if state.open_positions >= rules.max_open_positions:
        return Verdict(False, 0, f"open positions cap: {state.open_positions}/{rules.max_open_positions}")

    # 2b — Per-underlying cap (anti-concentration: don't pile N bets on one symbol).
    if state.open_positions_underlying >= rules.max_per_underlying:
        return Verdict(False, 0,
                       f"per-underlying cap: {state.open_positions_underlying}/{rules.max_per_underlying} on {spread.underlying}")

    # 2c — Duplicate-spread guard: never reopen the exact same spread (same legs)
    # while an identical one is already open. The cooldown is time-based and let
    # this slip through — on 1-sep it opened SPY 767/772 twice, doubling one bet.
    sig = f"{spread.short_leg.symbol}|{spread.long_leg.symbol}"
    if sig in state.open_signatures:
        return Verdict(False, 0, f"duplicate of an open spread on {spread.underlying}")

    # 2d — Per-group cap: SPY, QQQ and IWM move together; three spreads on them are one bet.
    if state.open_positions_group >= rules.max_per_group:
        return Verdict(False, 0, f"per-group cap: {state.open_positions_group}/{rules.max_per_group} in {spread.underlying}'s group")

    # 2e — Same short strike guard: a different long leg or expiry on the same
    # short strike is still the same bet (1-2 Sep: three SPY spreads short the 767 call).
    strike_key = f"{spread.underlying}|{'C' if spread.right is Right.CALL else 'P'}|{float(spread.short_leg.strike)}"
    if strike_key in state.open_short_strikes:
        return Verdict(False, 0, f"same short strike already open: {spread.underlying} {spread.short_leg.strike}")

    # 3 — Per-underlying cooldown.
    if state.seconds_since_symbol_trade is not None and state.seconds_since_symbol_trade < rules.cooldown_seconds:
        return Verdict(False, 0, f"cooldown: {state.seconds_since_symbol_trade:.0f}s < {rules.cooldown_seconds:.0f}s")

    # 3b — Group cooldown: after any trade in the group, the whole group waits.
    if state.seconds_since_group_trade is not None and state.seconds_since_group_trade < rules.group_cooldown_seconds:
        return Verdict(False, 0, f"group cooldown: {state.seconds_since_group_trade:.0f}s < {rules.group_cooldown_seconds:.0f}s")

    # 4 — Opening blackout.
    if state.minutes_since_open is not None and state.minutes_since_open < rules.opening_blackout_minutes:
        return Verdict(False, 0, f"opening blackout: {state.minutes_since_open:.0f}min")

    # 4b — Closing blackout: no entries in the last hour (#15/#17 opened at 15:03/15:56 ET for $0.56).
    if state.minutes_to_close is not None and state.minutes_to_close <= rules.no_entry_minutes_before_close:
        return Verdict(False, 0, f"closing blackout: {state.minutes_to_close:.0f}min to close")

    # 5 — Macro blackout.
    if state.minutes_to_macro is not None and state.minutes_to_macro < rules.macro_blackout_minutes:
        return Verdict(False, 0, f"macro blackout: event in {state.minutes_to_macro:.0f}min")

    # 5b — Reward/risk floor: a spread that pays less than the probability it assumes is not opened.
    if spread.max_loss > 0 and spread.max_profit / spread.max_loss < rules.min_reward_risk:
        return Verdict(False, 0, f"reward/risk {spread.max_profit / spread.max_loss:.2f} < {rules.min_reward_risk:.2f}")

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

    # 8 — Direction share: no side may hold more than max_direction_share of the
    # open risk once the book has both sides' worth of positions. The first
    # position is exempt (100% of nothing). 1-3 Sep: six bear calls, one bet.
    side = spread.direction.value
    same = float(state.risk_by_direction.get(side, 0.0))
    other = sum(float(v) for k, v in state.risk_by_direction.items() if k != side)
    if same > 0 or other > 0:
        s = rules.max_direction_share
        # (same + m·L) / (same + other + m·L) <= s  ->  m <= (s·other − (1−s)·same) / (L·(1−s))
        room = (s * other - (1.0 - s) * same) / (spread.max_loss * (1.0 - s)) if s < 1.0 else float("inf")
        m = int(room) if room != float("inf") else n
        if m < 1:
            return Verdict(False, 0, f"direction share: {side} already {same / (same + other):.0%} of open risk (max {s:.0%})")
        n = min(n, m)

    # 9 — Net delta cap: the book's directional exposure in $ stays under a
    # fraction of equity. Skipped while no tick has measured the book yet.
    if state.net_delta_usd is not None:
        spot_proxy = spread.short_leg.strike           # within ~1% of spot for a 0.30-delta short
        per_contract = abs(spread.short_leg.delta) * 100.0 * spot_proxy
        if per_contract > 0:
            cap = state.equity * rules.max_net_delta_pct
            sign = _delta_sign(spread.direction)
            # allowed m: |net + sign·m·per| <= cap
            lo = (-cap - state.net_delta_usd) / (sign * per_contract)
            hi = (cap - state.net_delta_usd) / (sign * per_contract)
            m_max = int(max(lo, hi))
            if m_max < 1:
                return Verdict(False, 0, f"net delta cap: book ${state.net_delta_usd:+.0f} of ±${cap:.0f}, {side} adds more")
            n = min(n, m_max)

    reason = f"approved {n}x (max loss ${spread.max_loss * n:.0f})"
    if n < requested_contracts:
        reason += f", shrunk from {requested_contracts}"
    return Verdict(True, n, reason)
