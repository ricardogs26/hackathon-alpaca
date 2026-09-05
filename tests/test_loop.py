"""
End-to-end pipeline tests with fully faked dependencies. Proves the wiring:
LLM proposes a direction, code sizes and gates it, and only an approved trade
reaches the (fake) broker. No Alpaca, no LLM, no Postgres.
"""
from __future__ import annotations

from optionwright.agent.analyzer import Proposal
from optionwright.agent.loop import Deps, run_cycle
from optionwright.options.models import Direction, OptionQuote, Right
from optionwright.policy.gates import PolicyState


def _put_chain():
    def q(strike, delta, bid, ask):
        return OptionQuote(f"P{strike}", "SPY", Right.PUT, strike, "2026-09-04", bid, ask, delta, 5000, 500)
    return [q(645, -0.45, 6.0, 6.1), q(640, -0.35, 4.0, 4.1), q(635, -0.28, 2.5, 2.6),
            q(630, -0.20, 1.5, 1.6), q(625, -0.12, 0.8, 0.9)]


def _call_chain():
    def q(strike, delta, bid, ask):
        return OptionQuote(f"C{strike}", "SPY", Right.CALL, strike, "2026-09-04", bid, ask, delta, 5000, 500)
    return [q(645, 0.30, 2.4, 2.5), q(650, 0.22, 1.4, 1.5), q(655, 0.14, 0.7, 0.8)]


class _Recorder:
    def __init__(self):
        self.decisions = []
        self.positions = []
        self.submitted = []

    def deps(self, proposal, state=None):
        state = state or PolicyState(equity=100_000, open_positions=0, consecutive_losses=0, premium_at_risk_today=0.0)
        return Deps(
            account=lambda: (100_000.0, 100_000.0),
            nearest_expiry=lambda u: "2026-09-04",
            fetch_chain=lambda u, e, r: _put_chain() if r is Right.PUT else _call_chain(),
            propose=lambda ctx: proposal,
            build_state=lambda u, eq: state,
            submit_spread=lambda sp, n: self.submitted.append((sp, n)) or {"id": "order-123"},
            record_decision=lambda *a, **k: self.decisions.append((a, k)),
            record_position=lambda sp, n, oid: self.positions.append((sp, n, oid)) or 77,
            save_equity=lambda eq, cash: None,
        )


def test_low_confidence_is_vetoed():
    rec = _Recorder()
    res = run_cycle("SPY", rec.deps(Proposal(Direction.BULLISH, 0.4, "weak edge")))
    assert res["action"] == "vetoed"
    assert "confidence" in res["reason"].lower()
    assert rec.submitted == []


def test_opened_result_carries_confidence():
    rec = _Recorder()
    res = run_cycle("SPY", rec.deps(Proposal(Direction.BULLISH, 0.7, "uptrend")))
    assert res["action"] == "opened"
    assert res["confidence"] == 0.7


def test_bullish_opens_a_position():
    rec = _Recorder()
    res = run_cycle("SPY", rec.deps(Proposal(Direction.BULLISH, 0.7, "uptrend")))
    assert res["action"] == "opened"
    assert res["direction"] == "bullish"
    assert res["order_id"] == "order-123"
    assert res["position_id"] == 77
    assert len(rec.submitted) == 1
    spread, n = rec.submitted[0]
    assert spread.right is Right.PUT and n >= 1


def test_abstain_opens_nothing():
    rec = _Recorder()
    res = run_cycle("SPY", rec.deps(Proposal(Direction.ABSTAIN, 0.0, "chop")))
    assert res["action"] == "abstain"
    assert rec.submitted == []
    assert rec.positions == []


def test_gate_veto_blocks_trade():
    rec = _Recorder()
    # circuit breaker tripped -> gates veto regardless of a bullish proposal
    tripped = PolicyState(equity=100_000, open_positions=0, consecutive_losses=3, premium_at_risk_today=0.0)
    res = run_cycle("SPY", rec.deps(Proposal(Direction.BULLISH, 0.9, "strong"), state=tripped))
    assert res["action"] == "vetoed"
    assert "breaker" in res["reason"]
    assert rec.submitted == []


def test_bearish_uses_call_spread():
    rec = _Recorder()
    res = run_cycle("SPY", rec.deps(Proposal(Direction.BEARISH, 0.6, "rolling over")))
    assert res["action"] == "opened"
    spread, n = rec.submitted[0]
    assert spread.right is Right.CALL


def test_rich_context_injects_signals_memory_book():
    rec = _Recorder()
    captured = {}
    deps = rec.deps(Proposal(Direction.ABSTAIN, 0.5, "x"))
    deps.propose = lambda ctx: captured.update(ctx) or Proposal(Direction.ABSTAIN, 0.5, "x")
    deps.signals = lambda u, e: {"tendencia_5d": "baja"}
    deps.memory = lambda u: {"cerradas": 3}
    deps.book = lambda: {"abiertas": 2, "concentracion": "SPY"}
    deps.rich_context = True
    run_cycle("SPY", deps)
    assert captured["signals"] == {"tendencia_5d": "baja"}
    assert captured["memoria"] == {"cerradas": 3}
    assert captured["portafolio"]["concentracion"] == "SPY"


def test_rich_context_off_adds_no_keys():
    rec = _Recorder()
    captured = {}
    deps = rec.deps(Proposal(Direction.ABSTAIN, 0.5, "x"))
    deps.propose = lambda ctx: captured.update(ctx) or Proposal(Direction.ABSTAIN, 0.5, "x")
    run_cycle("SPY", deps)
    assert "signals" not in captured


def test_rich_context_degrades_on_error():
    rec = _Recorder()
    captured = {}
    deps = rec.deps(Proposal(Direction.ABSTAIN, 0.5, "x"))
    deps.propose = lambda ctx: captured.update(ctx) or Proposal(Direction.ABSTAIN, 0.5, "x")
    def boom(*a):
        raise RuntimeError("alpaca down")
    deps.signals = boom
    deps.memory = lambda u: {"cerradas": 0}
    deps.book = lambda: {"abiertas": 0}
    deps.rich_context = True
    run_cycle("SPY", deps)
    assert captured["signals"] == {}          # degradó, no rompió
    assert captured["memoria"] == {"cerradas": 0}


# ── phase 2: liquidity screen and width from spot ────────────────────────────
from optionwright.options.select import SelectParams  # noqa: E402


def test_illiquid_chain_abstains_without_calling_the_llm():
    rec = _Recorder()
    calls = {"llm": 0}

    def propose(ctx):
        calls["llm"] += 1
        return Proposal(Direction.BULLISH, 0.9, "x")

    deps = rec.deps(None)
    deps.propose = propose
    deps.fetch_chain = lambda u, e, r: []          # nothing liquid on either side
    res = run_cycle("XLF", deps)
    assert res["action"] == "abstain" and res["reason"] == "illiquid chain"
    assert calls["llm"] == 0
    assert rec.decisions[-1][0][6] == "illiquid chain"


def test_width_comes_from_spot_and_the_strike_step():
    rec = _Recorder()
    deps = rec.deps(Proposal(Direction.BULLISH, 0.9, "up"))
    deps.spot = lambda u: 640.0                    # 0.0065 * 640 = 4.2 -> 5 on the fixture's $5 strikes
    deps.select = SelectParams(width_pct=0.0065)
    res = run_cycle("SPY", deps)
    assert res["action"] == "opened"
    assert rec.positions[0][0].width == 5.0
    deps2 = rec.deps(Proposal(Direction.BULLISH, 0.9, "up"))
    deps2.spot = lambda u: 640.0
    deps2.select = SelectParams(width_pct=0.015)   # 9.6 -> 10 wide (635/625)
    run_cycle("SPY", deps2)
    assert rec.positions[-1][0].width == 10.0


def test_spot_failure_falls_back_to_the_fixed_width():
    rec = _Recorder()
    deps = rec.deps(Proposal(Direction.BULLISH, 0.9, "up"))
    deps.spot = lambda u: (_ for _ in ()).throw(RuntimeError("quote down"))
    res = run_cycle("SPY", deps)
    assert res["action"] == "opened" and rec.positions[0][0].width == 5.0


# ── phase 3: neutral (iron condor) and the volatile regime ──────────────────
def _rich(deps, regime="tranquilo"):
    deps.rich_context = True
    deps.signals = lambda u, e: {"tendencia_5d": "lateral", "regimen": regime}
    deps.memory = lambda u: {"cerradas": 0}
    deps.book = lambda: {"abiertas": 0}
    return deps


def test_neutral_opens_both_wings_with_the_same_size():
    rec = _Recorder()
    res = run_cycle("SPY", _rich(rec.deps(Proposal(Direction.NEUTRAL, 0.8, "rango"))))
    assert res["action"] == "opened" and res["structure"] == "iron_condor" and len(res["position_ids"]) == 2
    assert {sp.direction for sp, n in rec.submitted} == {Direction.BULLISH, Direction.BEARISH}
    assert len({n for sp, n in rec.submitted}) == 1
    assert sum(1 for a, k in rec.decisions if a[4]) == 2          # two approved decisions, one per wing


def test_neutral_with_a_missing_wing_opens_nothing():
    rec = _Recorder()
    deps = _rich(rec.deps(Proposal(Direction.NEUTRAL, 0.8, "rango")))
    deps.fetch_chain = lambda u, e, r: _put_chain() if r is Right.PUT else []     # no calls: no bear wing
    res = run_cycle("SPY", deps)
    assert res["action"] == "abstain" and "missing bear call" in res["reason"] and rec.submitted == []


def test_neutral_vetoed_if_either_wing_fails_a_gate():
    rec = _Recorder()
    deps = _rich(rec.deps(Proposal(Direction.NEUTRAL, 0.5, "rango")))       # below min_confidence
    res = run_cycle("SPY", deps)
    assert res["action"] == "vetoed" and "condor bullish wing: low confidence" in res["reason"] and rec.submitted == []


def test_volatile_regime_neutral_mode_refuses_directional_and_sells_farther():
    rec = _Recorder()
    deps = _rich(rec.deps(Proposal(Direction.BEARISH, 0.9, "baja")), regime="volatil")
    deps.select = SelectParams(short_delta=0.30, short_delta_volatile=0.20, volatile_mode="neutral")
    res = run_cycle("SPY", deps)
    assert res["action"] == "vetoed" and "volatile regime" in res["reason"] and rec.submitted == []
    rec2 = _Recorder()
    deps2 = _rich(rec2.deps(Proposal(Direction.NEUTRAL, 0.9, "rango")), regime="volatil")
    deps2.select = SelectParams(short_delta=0.30, short_delta_volatile=0.20, volatile_mode="neutral")
    run_cycle("SPY", deps2)
    assert all(abs(sp.short_leg.delta) == 0.20 for sp, n in rec2.submitted)   # the 0.20-delta legs of the fixture


def test_volatile_regime_none_mode_abstains_without_the_llm():
    rec = _Recorder()
    calls = {"n": 0}
    deps = _rich(rec.deps(None), regime="volatil")
    deps.propose = lambda ctx: calls.__setitem__("n", calls["n"] + 1) or Proposal(Direction.NEUTRAL, 0.9, "x")
    deps.select = SelectParams(volatile_mode="none")
    res = run_cycle("SPY", deps)
    assert res["action"] == "abstain" and res["reason"] == "volatile regime" and calls["n"] == 0


def test_volatile_regime_directional_mode_trades_normally():
    from optionwright.policy.gates import RuleSet

    rec = _Recorder()
    deps = _rich(rec.deps(Proposal(Direction.BULLISH, 0.9, "up")), regime="volatil")
    deps.select = SelectParams(volatile_mode="directional")
    deps.rules = RuleSet(min_reward_risk=0.1)     # the 0.20-delta wing of the fixture pays 0.16
    assert run_cycle("SPY", deps)["action"] == "opened"


# ── phase 4: the regime at open is recorded ──────────────────────────────────
def test_regime_recorded_for_directional_and_both_condor_wings():
    rec = _Recorder()
    noted = []
    deps = _rich(rec.deps(Proposal(Direction.BULLISH, 0.9, "up")))
    deps.note_regime = lambda pid, regime: noted.append((pid, regime))
    run_cycle("SPY", deps)
    assert noted == [(77, "tranquilo")]
    rec2 = _Recorder()
    deps2 = _rich(rec2.deps(Proposal(Direction.NEUTRAL, 0.9, "rango")))
    deps2.note_regime = lambda pid, regime: noted.append((pid, regime))
    run_cycle("SPY", deps2)
    assert len(noted) == 3


def test_regime_bookkeeping_failure_never_breaks_the_cycle():
    rec = _Recorder()
    deps = _rich(rec.deps(Proposal(Direction.BULLISH, 0.9, "up")))
    deps.note_regime = lambda pid, regime: (_ for _ in ()).throw(RuntimeError("db"))
    assert run_cycle("SPY", deps)["action"] == "opened"


# ── tech-debt 1.1: the loop learns whether the entry filled ──────────────────
def _with_fill(rec, proposal, status, fap=1.03):
    deps = _rich(rec.deps(proposal))
    deps.wait_fill = lambda oid: {"id": oid, "status": status, "filled_avg_price": fap}
    recorded = []
    deps.record_position = lambda sp, n, oid, status="open", fill_credit=None: recorded.append((sp.direction, status, fill_credit)) or (100 + len(recorded))
    return deps, recorded


def test_filled_entry_records_the_fill_credit():
    rec = _Recorder()
    deps, recorded = _with_fill(rec, Proposal(Direction.BULLISH, 0.9, "up"), "filled", 1.03)
    res = run_cycle("SPY", deps)
    assert res["action"] == "opened" and res["fill"] == "filled" and recorded == [(Direction.BULLISH, "open", 1.03)]


def test_unfilled_entry_records_no_position_and_an_honest_decision():
    rec = _Recorder()
    deps, recorded = _with_fill(rec, Proposal(Direction.BULLISH, 0.9, "up"), "canceled")
    res = run_cycle("SPY", deps)
    assert res["action"] == "unfilled" and "canceled" in res["reason"] and recorded == []
    assert rec.decisions[-1][0][4] is False


def test_still_working_entry_is_recorded_pending():
    rec = _Recorder()
    deps, recorded = _with_fill(rec, Proposal(Direction.BULLISH, 0.9, "up"), "new")
    res = run_cycle("SPY", deps)
    assert res["action"] == "opened" and res["fill"] == "pending" and recorded == [(Direction.BULLISH, "pending", None)]
    assert "(pending fill)" in rec.decisions[-1][0][6]


def test_condor_places_the_second_wing_only_after_the_first_fills():
    rec = _Recorder()
    deps, recorded = _with_fill(rec, Proposal(Direction.NEUTRAL, 0.9, "rango"), "new")
    canceled = []
    deps.cancel_order = lambda oid: canceled.append(oid) or True
    res = run_cycle("SPY", deps)
    assert res["action"] == "unfilled" and len(rec.submitted) == 1 and canceled == ["order-123"]
    rec2 = _Recorder()
    deps2, recorded2 = _with_fill(rec2, Proposal(Direction.NEUTRAL, 0.9, "rango"), "filled")
    res2 = run_cycle("SPY", deps2)
    assert res2["structure"] == "iron_condor" and len(recorded2) == 2


# ── tech-debt 2.1: every decision after the model carries what it saw ────────
def test_decisions_carry_context_model_and_latency():
    rec = _Recorder()
    deps = _rich(rec.deps(Proposal(Direction.BULLISH, 0.9, "up", model="Qwen/72B", latency_ms=1234)))
    run_cycle("SPY", deps)
    a, k = rec.decisions[-1]
    assert k["model"] == "Qwen/72B" and k["latency_ms"] == 1234
    assert k["context"]["underlying"] == "SPY" and "signals" in k["context"] and "bull_put_spread" in k["context"]
    rec2 = _Recorder()
    run_cycle("SPY", _rich(rec2.deps(Proposal(Direction.ABSTAIN, 0.3, "nada", model="Qwen/72B", latency_ms=900))))
    assert rec2.decisions[-1][1]["context"]["underlying"] == "SPY"


def test_pre_model_decisions_carry_no_context():
    rec = _Recorder()
    deps = _rich(rec.deps(Proposal(Direction.BULLISH, 0.9, "up")))
    deps.fetch_chain = lambda u, e, r: []
    run_cycle("SPY", deps)
    assert "context" not in rec.decisions[-1][1]
