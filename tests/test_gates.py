"""
Tests for the risk gates. Each gate is exercised against hand-built state, and
the core invariant is checked: a gate can veto or shrink, never enlarge.
"""
from __future__ import annotations

from optionwright.options.models import Direction, OptionQuote, Right, VerticalSpread
from optionwright.policy.gates import PolicyState, RuleSet, evaluate


def _spread(max_loss_target=400.0):
    # width 5, credit 1.0 -> max_loss = (5-1)*100 = 400
    short = OptionQuote("S", "SPY", Right.PUT, 767, "2026-08-31", 1.20, 1.20, -0.30, 5000, 500)
    long = OptionQuote("L", "SPY", Right.PUT, 762, "2026-08-31", 0.20, 0.20, -0.09, 5000, 500)
    return VerticalSpread("SPY", Right.PUT, "2026-08-31", short, long, Direction.BULLISH)


def _state(**kw):
    base = dict(equity=100_000.0, open_positions=0, consecutive_losses=0, premium_at_risk_today=0.0)
    base.update(kw)
    return PolicyState(**base)


def test_clean_state_approves():
    v = evaluate(_spread(), 1, _state())
    assert v.approved and v.contracts == 1


def test_consecutive_loss_breaker_vetoes():
    v = evaluate(_spread(), 1, _state(consecutive_losses=3))
    assert not v.approved and v.contracts == 0
    assert "breaker" in v.reason


def test_open_positions_cap_vetoes():
    v = evaluate(_spread(), 1, _state(open_positions=3))
    assert not v.approved and "cap" in v.reason


def test_cooldown_vetoes_within_window():
    v = evaluate(_spread(), 1, _state(seconds_since_symbol_trade=600))
    assert not v.approved and "cooldown" in v.reason


def test_cooldown_passes_after_window():
    v = evaluate(_spread(), 1, _state(seconds_since_symbol_trade=4000))
    assert v.approved


def test_duplicate_open_spread_vetoes():
    # the spread's legs are "S" and "L" -> signature "S|L"
    v = evaluate(_spread(), 1, _state(open_signatures=frozenset({"S|L"})))
    assert not v.approved and "duplicate" in v.reason


def test_non_duplicate_signature_passes():
    v = evaluate(_spread(), 1, _state(open_signatures=frozenset({"OTHER|LEGS"})))
    assert v.approved


def test_opening_blackout_vetoes():
    v = evaluate(_spread(), 1, _state(minutes_since_open=10))
    assert not v.approved and "opening" in v.reason


def test_macro_blackout_vetoes():
    v = evaluate(_spread(), 1, _state(minutes_to_macro=30))
    assert not v.approved and "macro" in v.reason


def test_max_loss_shrinks_contracts():
    # equity 100k, max_loss_pct 1% -> $1000 budget; max_loss 400 -> 2 contracts fit
    v = evaluate(_spread(), 5, _state())
    assert v.approved and v.contracts == 2
    assert "shrunk" in v.reason


def test_one_contract_over_budget_vetoes():
    # tiny equity so even one 400-loss contract exceeds 1%
    v = evaluate(_spread(), 1, _state(equity=10_000))  # 1% = $100 < $400
    assert not v.approved and "exceeds max-loss" in v.reason


def test_daily_budget_shrinks():
    # daily budget 5% of 100k = $5000; already 4600 at risk -> $400 left -> 1 contract
    v = evaluate(_spread(), 5, _state(premium_at_risk_today=4600))
    assert v.approved and v.contracts == 1


def test_daily_budget_exhausted_vetoes():
    v = evaluate(_spread(), 1, _state(premium_at_risk_today=5000))
    assert not v.approved and "daily budget" in v.reason


def test_never_enlarges_beyond_request():
    # request 1 even though budget allows more -> stays 1
    v = evaluate(_spread(), 1, _state())
    assert v.contracts == 1


def test_custom_ruleset_respected():
    rules = RuleSet(max_open_positions=1)
    assert evaluate(_spread(), 1, _state(open_positions=1), rules).approved is False


def test_per_underlying_cap_vetoes():
    # global cap not hit (2 of 5), but 2 already on this symbol -> veto
    v = evaluate(_spread(), 1, _state(open_positions=2, open_positions_underlying=2))
    assert not v.approved and "per-underlying" in v.reason


def test_per_underlying_below_cap_ok():
    v = evaluate(_spread(), 1, _state(open_positions=2, open_positions_underlying=1))
    assert v.approved
