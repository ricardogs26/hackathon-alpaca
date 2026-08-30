"""
The cycle. Wires the pieces: read the chain, pre-build both candidate spreads,
ask the LLM only for a direction, size in code, run the risk gates, execute, and
record. Every dependency is injected via `Deps`, so the whole pipeline is
testable end-to-end with fakes — no Alpaca, no LLM, no Postgres.

Contracts are requested at a high ceiling and the max-loss + daily-budget gates
shrink to the right size: sizing emerges from the gates, not from the LLM.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from optionwright.agent.analyzer import Proposal, propose as _live_propose
from optionwright.options.models import Direction, Right, VerticalSpread
from optionwright.options.select import build_spread
from optionwright.policy.gates import PolicyState, RuleSet, evaluate

logger = logging.getLogger("optionwright.loop")

_SIZE_CEILING = 100  # gates shrink from here; never the source of sizing


@dataclass
class Deps:
    account: Callable[[], tuple[float, float]]                 # -> (equity, cash)
    nearest_expiry: Callable[[str], str | None]
    fetch_chain: Callable[[str, str, Right], list]             # (underlying, expiry, right) -> [OptionQuote]
    propose: Callable[[dict], Proposal]
    build_state: Callable[[str, float], PolicyState]           # (underlying, equity) -> PolicyState
    submit_spread: Callable[[VerticalSpread, int], dict]       # -> order json
    record_decision: Callable[..., None]
    record_position: Callable[[VerticalSpread, int, str | None], int]
    save_equity: Callable[[float, float], None]
    rules: RuleSet = None  # type: ignore[assignment]


def _candidate_summary(spread: VerticalSpread | None) -> dict | None:
    if spread is None:
        return None
    return {
        "short_strike": spread.short_leg.strike,
        "long_strike": spread.long_leg.strike,
        "short_delta": round(spread.short_leg.delta, 3),
        "credit": spread.credit,
        "max_loss": spread.max_loss,
        "reward_risk": spread.reward_risk,
    }


def run_cycle(underlying: str, deps: Deps) -> dict:
    rules = deps.rules or RuleSet()
    equity, cash = deps.account()
    deps.save_equity(equity, cash)

    expiry = deps.nearest_expiry(underlying)
    if not expiry:
        deps.record_decision(underlying, Direction.ABSTAIN, None, "no expiry available",
                             False, 0, "no expiry", None)
        return {"underlying": underlying, "action": "abstain", "reason": "no expiry"}

    puts = deps.fetch_chain(underlying, expiry, Right.PUT)
    calls = deps.fetch_chain(underlying, expiry, Right.CALL)
    bull_put = build_spread(puts, Direction.BULLISH, expiry)
    bear_call = build_spread(calls, Direction.BEARISH, expiry)

    context = {
        "underlying": underlying,
        "expiry": expiry,
        "bull_put_spread": _candidate_summary(bull_put),
        "bear_call_spread": _candidate_summary(bear_call),
    }
    proposal = deps.propose(context)

    chosen = bull_put if proposal.direction is Direction.BULLISH else bear_call if proposal.direction is Direction.BEARISH else None
    if proposal.direction is Direction.ABSTAIN or chosen is None:
        reason = "LLM abstained" if proposal.direction is Direction.ABSTAIN else f"no liquid {proposal.direction.value} spread"
        deps.record_decision(underlying, proposal.direction, proposal.confidence, proposal.rationale,
                             False, 0, reason, chosen)
        return {"underlying": underlying, "action": "abstain", "reason": reason}

    state = deps.build_state(underlying, equity)
    verdict = evaluate(chosen, _SIZE_CEILING, state, rules)
    if not verdict.approved:
        deps.record_decision(underlying, proposal.direction, proposal.confidence, proposal.rationale,
                             False, 0, verdict.reason, chosen)
        return {"underlying": underlying, "action": "vetoed", "reason": verdict.reason}

    order = deps.submit_spread(chosen, verdict.contracts)
    order_id = order.get("id") if isinstance(order, dict) else None
    pos_id = deps.record_position(chosen, verdict.contracts, order_id)
    deps.record_decision(underlying, proposal.direction, proposal.confidence, proposal.rationale,
                         True, verdict.contracts, verdict.reason, chosen, pos_id)
    logger.info("cycle %s -> %s x%d (%s)", underlying, proposal.direction.value, verdict.contracts, order_id)
    return {"underlying": underlying, "action": "opened", "contracts": verdict.contracts,
            "direction": proposal.direction.value, "order_id": order_id, "position_id": pos_id}
