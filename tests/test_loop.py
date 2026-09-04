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
