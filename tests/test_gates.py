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
    v = evaluate(_spread(), 1, _state(open_positions=6))
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


# ── phase 1 gates ─────────────────────────────────────────────────────────────
from optionwright.policy.params import GLOBAL, Params  # noqa: E402


def test_confidence_gate_lives_in_the_engine():
    v = evaluate(_spread(), 10, _state(), RuleSet(), confidence=0.55)
    assert not v.approved and "low confidence 0.55" in v.reason
    assert evaluate(_spread(), 10, _state(), RuleSet(), confidence=0.60).approved
    assert evaluate(_spread(), 10, _state(), RuleSet(), confidence=None).approved   # unknown = not gated here


def test_daily_loss_pause():
    v = evaluate(_spread(), 10, _state(realized_pnl_today=-2100.0), RuleSet(max_daily_loss_pct=0.02))  # equity 100k
    assert not v.approved and "daily loss pause" in v.reason
    assert evaluate(_spread(), 10, _state(realized_pnl_today=-1900.0), RuleSet(max_daily_loss_pct=0.02)).approved


def test_closing_blackout_blocks_the_last_hour_only():
    assert not evaluate(_spread(), 10, _state(minutes_to_close=45.0), RuleSet()).approved
    assert evaluate(_spread(), 10, _state(minutes_to_close=61.0), RuleSet()).approved
    assert evaluate(_spread(), 10, _state(minutes_to_close=None), RuleSet()).approved


def test_reward_risk_floor():
    thin = _spread(max_loss_target=400.0)      # credit 1.0 -> 100/400 = 0.25
    assert evaluate(thin, 10, _state(), RuleSet(min_reward_risk=0.20)).approved
    v = evaluate(thin, 10, _state(), RuleSet(min_reward_risk=0.30))
    assert not v.approved and "reward/risk 0.25" in v.reason


def test_direction_share_first_position_exempt_then_shrinks_then_vetoes():
    r = RuleSet(max_direction_share=0.60, max_loss_pct=1.0, daily_budget_pct=1.0)
    sp = _spread(max_loss_target=400.0)     # bullish, $400 per contract
    assert evaluate(sp, 10, _state(risk_by_direction={}), r).approved                      # empty book
    # $1000 bearish open, nothing bullish: bullish may take up to 60% -> m <= (0.6*1000 - 0)/(400*0.4) = 3.75 -> 3
    v = evaluate(sp, 10, _state(risk_by_direction={"bearish": 1000.0}), r)
    assert v.approved and v.contracts == 3 and "shrunk" in v.reason
    # $1000 bullish already and nothing on the other side: adding bullish is vetoed
    v = evaluate(sp, 10, _state(risk_by_direction={"bullish": 1000.0}), r)
    assert not v.approved and "direction share" in v.reason


def test_net_delta_cap_shrinks_and_vetoes_and_skips_when_unmeasured():
    # bullish spread, short delta -0.30 at strike 767 -> $23,010 of delta per contract
    r = RuleSet(max_net_delta_pct=0.50, max_loss_pct=1.0, daily_budget_pct=1.0)   # cap $50,000 on $100k
    sp = _spread(max_loss_target=400.0)
    assert evaluate(sp, 10, _state(net_delta_usd=None), r).approved                        # no ticks yet: skipped
    v = evaluate(sp, 10, _state(net_delta_usd=0.0), r)                                     # 50,000 / 23,010 -> 2
    assert v.approved and v.contracts == 2 and "shrunk" in v.reason
    v = evaluate(sp, 10, _state(net_delta_usd=40_000.0), r)                                # already long: no room
    assert not v.approved and "net delta cap" in v.reason
    v = evaluate(sp, 10, _state(net_delta_usd=-90_000.0), r)                               # short book: bullish balances it
    assert v.approved and v.contracts == 6


def test_ruleset_from_params_respects_scopes_and_defaults():
    prm = Params({GLOBAL: {"max_open_positions": 4}, "underlying:IWM": {"max_per_underlying": 1}})
    assert RuleSet.from_params(prm).max_open_positions == 4
    assert RuleSet.from_params(prm, underlying="IWM").max_per_underlying == 1
    assert RuleSet.from_params(prm).max_per_underlying == 2
    assert RuleSet.from_params(prm).max_direction_share == 0.60


# ── phase 2 gates ─────────────────────────────────────────────────────────────
def test_per_group_cap_vetoes():
    v = evaluate(_spread(), 10, _state(open_positions_group=2), RuleSet(max_per_group=2))
    assert not v.approved and "per-group cap" in v.reason
    assert evaluate(_spread(), 10, _state(open_positions_group=1), RuleSet(max_per_group=2)).approved


def test_group_cooldown_vetoes_within_window_only():
    assert not evaluate(_spread(), 10, _state(seconds_since_group_trade=600.0), RuleSet(group_cooldown_seconds=1800)).approved
    assert evaluate(_spread(), 10, _state(seconds_since_group_trade=1900.0), RuleSet(group_cooldown_seconds=1800)).approved
    assert evaluate(_spread(), 10, _state(seconds_since_group_trade=None), RuleSet(group_cooldown_seconds=1800)).approved


def test_same_short_strike_guard_ignores_the_long_leg():
    v = evaluate(_spread(), 10, _state(open_short_strikes=frozenset({"SPY|P|767.0"})), RuleSet())
    assert not v.approved and "same short strike" in v.reason
    assert evaluate(_spread(), 10, _state(open_short_strikes=frozenset({"SPY|C|767.0", "QQQ|P|767.0"})), RuleSet()).approved


def test_ruleset_from_params_carries_group_rules():
    prm = Params({"group:index": {"max_per_group": 1}})
    assert RuleSet.from_params(prm, underlying="SPY", group="index").max_per_group == 1
    assert RuleSet.from_params(prm).max_per_group == 2


def test_ruleset_carries_the_breaker_window():
    assert RuleSet.from_params(Params({GLOBAL: {"breaker_lookback_hours": 6}})).breaker_lookback_hours == 6.0
    assert RuleSet().breaker_lookback_hours == 24.0
