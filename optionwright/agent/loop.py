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

from optionwright.agent.analyzer import Proposal
from optionwright.options.models import Direction, Right, VerticalSpread
from optionwright.options.select import SelectParams, build_spread, strike_step, width_for
from optionwright.policy.gates import PolicyState, RuleSet, evaluate

logger = logging.getLogger("optionwright.loop")

_SIZE_CEILING = 100  # gates shrink from here; never the source of sizing


def _safe(fn) -> dict:
    """Ejecuta un proveedor de contexto; degrada a {} ante cualquier fallo."""
    try:
        out = fn()
        return out if isinstance(out, dict) else {}
    except Exception as exc:  # el contexto enriquecido nunca rompe el ciclo
        logger.warning("rich context provider failed: %s", exc)
        return {}


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
    select: SelectParams = None  # type: ignore[assignment]
    spot: Callable[[str], float] = None              # (underlying) -> last price; None = fixed 5-wide
    signals: Callable[[str, str], dict] = None       # (underlying, expiry) -> señales
    memory: Callable[[str], dict] = None             # (underlying) -> resultados recientes
    book: Callable[[], dict] = None                  # -> resumen de portafolio
    rich_context: bool = False


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


def _width(deps: Deps, underlying: str, contracts: list, sel: SelectParams) -> float:
    """Spread width ∝ spot, snapped to the chain's strike step. Without a spot
    read (or on failure) the legacy fixed 5.0 applies — never a crash."""
    if deps.spot is None:
        return 5.0
    try:
        spot = float(deps.spot(underlying))
    except Exception as exc:  # a quote hiccup must not cost the cycle
        logger.warning("spot for %s unavailable (%s); using fixed width", underlying, exc)
        return 5.0
    return width_for(spot, sel.width_pct, strike_step(contracts))


def _open_condor(underlying: str, deps: Deps, rules: RuleSet, equity: float, proposal: Proposal,
                 bull_put: VerticalSpread | None, bear_call: VerticalSpread | None) -> dict:
    """
    Iron condor = the bull put AND the bear call, each gated and recorded as its
    own position (the exits manage each wing: the winning wing closes on its
    take-profit, the other on its own rule). Both wings must exist and both must
    pass the gates, else nothing is opened — half a condor is a directional bet
    nobody asked for. Sizing is per wing (1% each), conservative: only one wing
    can lose at expiry, so the structure's true max loss is the larger wing.
    """
    if bull_put is None or bear_call is None:
        reason = "neutral needs both wings; missing " + ("bull put" if bull_put is None else "bear call")
        deps.record_decision(underlying, proposal.direction, proposal.confidence, proposal.rationale, False, 0, reason, None)
        return {"underlying": underlying, "action": "abstain", "reason": reason}
    state = deps.build_state(underlying, equity)
    verdicts = [(w, evaluate(w, _SIZE_CEILING, state, rules, confidence=proposal.confidence)) for w in (bull_put, bear_call)]
    failed = [(w, v) for w, v in verdicts if not v.approved]
    if failed:
        w, v = failed[0]
        reason = f"condor {w.direction.value} wing: {v.reason}"
        deps.record_decision(underlying, proposal.direction, proposal.confidence, proposal.rationale, False, 0, reason, w)
        return {"underlying": underlying, "action": "vetoed", "reason": reason}
    n = min(v.contracts for _, v in verdicts)   # same size on both wings
    ids = []
    for wing, v in verdicts:
        order = deps.submit_spread(wing, n)
        order_id = order.get("id") if isinstance(order, dict) else None
        pos_id = deps.record_position(wing, n, order_id)
        deps.record_decision(underlying, proposal.direction, proposal.confidence, proposal.rationale,
                             True, n, f"condor {wing.direction.value} wing: {v.reason}", wing, pos_id)
        ids.append(pos_id)
    logger.info("cycle %s -> iron condor x%d (%s)", underlying, n, ids)
    return {"underlying": underlying, "action": "opened", "contracts": n, "direction": "neutral",
            "structure": "iron_condor", "confidence": proposal.confidence, "position_ids": ids}


def run_cycle(underlying: str, deps: Deps) -> dict:
    rules = deps.rules or RuleSet()
    equity, cash = deps.account()
    deps.save_equity(equity, cash)

    expiry = deps.nearest_expiry(underlying)
    if not expiry:
        deps.record_decision(underlying, Direction.ABSTAIN, None, "no expiry available",
                             False, 0, "no expiry", None)
        return {"underlying": underlying, "action": "abstain", "reason": "no expiry"}

    # Perception first: the regime decides how far from the money we sell.
    signals = _safe(lambda: deps.signals(underlying, expiry)) if (deps.rich_context and deps.signals) else {}
    regime = signals.get("regimen")

    puts = deps.fetch_chain(underlying, expiry, Right.PUT)
    calls = deps.fetch_chain(underlying, expiry, Right.CALL)
    sel = deps.select or SelectParams()
    width = _width(deps, underlying, puts + calls, sel)
    kw = dict(short_delta=sel.delta_for(regime), width=width, width_tolerance=sel.width_tolerance,
              min_oi=sel.min_open_interest, max_spread_pct=sel.max_quote_spread_pct)
    bull_put = build_spread(puts, Direction.BULLISH, expiry, **kw)
    bear_call = build_spread(calls, Direction.BEARISH, expiry, **kw)

    # Liquidity screen: with no tradable spread on either side there is nothing
    # to decide — abstain without spending an LLM call (TLT/GLD/XLE/XLF at 2-3
    # sessions: OI 20-140, bid-ask 27-86%).
    if bull_put is None and bear_call is None:
        deps.record_decision(underlying, Direction.ABSTAIN, None, "no liquid spread on either side",
                             False, 0, "illiquid chain", None)
        return {"underlying": underlying, "action": "abstain", "reason": "illiquid chain"}

    # Volatile regime, mode "none": short gamma has no business here today.
    if regime == "volatil" and sel.volatile_mode == "none":
        deps.record_decision(underlying, Direction.ABSTAIN, None, "volatile regime: entries disabled",
                             False, 0, "volatile regime", None)
        return {"underlying": underlying, "action": "abstain", "reason": "volatile regime"}

    context = {
        "underlying": underlying,
        "expiry": expiry,
        "bull_put_spread": _candidate_summary(bull_put),
        "bear_call_spread": _candidate_summary(bear_call),
    }
    if deps.rich_context and deps.signals and deps.memory and deps.book:
        context["signals"] = signals
        context["memoria"] = _safe(lambda: deps.memory(underlying))
        context["portafolio"] = _safe(deps.book)
    proposal = deps.propose(context)

    # Volatile regime, mode "neutral": a directional proposal is not taken; only
    # a condor (both sides, farther from the money) may be opened.
    if regime == "volatil" and sel.volatile_mode == "neutral" and proposal.direction in (Direction.BULLISH, Direction.BEARISH):
        reason = f"volatile regime: directional {proposal.direction.value} not taken"
        deps.record_decision(underlying, proposal.direction, proposal.confidence, proposal.rationale, False, 0, reason, None)
        return {"underlying": underlying, "action": "vetoed", "reason": reason}

    if proposal.direction is Direction.NEUTRAL:
        return _open_condor(underlying, deps, rules, equity, proposal, bull_put, bear_call)

    chosen = bull_put if proposal.direction is Direction.BULLISH else bear_call if proposal.direction is Direction.BEARISH else None
    if proposal.direction is Direction.ABSTAIN or chosen is None:
        reason = "LLM abstained" if proposal.direction is Direction.ABSTAIN else f"no liquid {proposal.direction.value} spread"
        deps.record_decision(underlying, proposal.direction, proposal.confidence, proposal.rationale,
                             False, 0, reason, chosen)
        return {"underlying": underlying, "action": "abstain", "reason": reason}

    # Every gate, the confidence one included, lives in policy/gates.py.
    state = deps.build_state(underlying, equity)
    verdict = evaluate(chosen, _SIZE_CEILING, state, rules, confidence=proposal.confidence)
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
            "direction": proposal.direction.value, "confidence": proposal.confidence,
            "order_id": order_id, "position_id": pos_id}
