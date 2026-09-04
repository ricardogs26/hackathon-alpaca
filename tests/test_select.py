"""
Tests for the deterministic spread selection. A synthetic SPY chain lets us
assert the geometry of the chosen spread without any network or Alpaca account.
"""
from __future__ import annotations

from optionwright.options.models import Direction, OptionQuote, Right, VerticalSpread
from optionwright.options.select import build_spread, is_liquid

EXPIRY = "2026-09-04"


def _q(right: Right, strike: float, delta: float, *, bid: float, ask: float,
       oi: int = 5000, vol: int = 500, underlying: str = "SPY") -> OptionQuote:
    return OptionQuote(
        symbol=f"{underlying}{strike:.0f}{right.value[0].upper()}",
        underlying=underlying, right=right, strike=strike, expiry=EXPIRY,
        bid=bid, ask=ask, delta=delta, open_interest=oi, volume=vol,
    )


def _put_chain() -> list[OptionQuote]:
    # Puts around a 640 spot: deeper OTM (lower strike) → smaller |delta|, cheaper.
    return [
        _q(Right.PUT, 645, -0.45, bid=6.0, ask=6.1),
        _q(Right.PUT, 640, -0.35, bid=4.0, ask=4.1),
        _q(Right.PUT, 635, -0.28, bid=2.5, ask=2.6),   # ~0.30 delta short target
        _q(Right.PUT, 630, -0.20, bid=1.5, ask=1.6),
        _q(Right.PUT, 625, -0.12, bid=0.8, ask=0.9),   # ~5 wide protective long
    ]


def _call_chain() -> list[OptionQuote]:
    return [
        _q(Right.CALL, 645, 0.30, bid=2.4, ask=2.5),   # ~0.30 delta short target
        _q(Right.CALL, 650, 0.22, bid=1.4, ask=1.5),
        _q(Right.CALL, 655, 0.14, bid=0.7, ask=0.8),   # protective long ~5 up
        _q(Right.CALL, 640, 0.40, bid=3.6, ask=3.7),
    ]


def test_liquidity_gate_rejects_thin_and_wide():
    good = _q(Right.PUT, 635, -0.28, bid=2.5, ask=2.6, oi=5000, vol=500)
    thin_oi = _q(Right.PUT, 635, -0.28, bid=2.5, ask=2.6, oi=10, vol=500)
    # low OI (below bypass) AND low volume -> genuinely illiquid, rejected
    thin_both = _q(Right.PUT, 635, -0.28, bid=2.5, ask=2.6, oi=300, vol=1)
    wide = _q(Right.PUT, 635, -0.28, bid=1.0, ask=3.0, oi=5000, vol=500)
    assert is_liquid(good)
    assert not is_liquid(thin_oi)
    assert not is_liquid(thin_both)
    assert not is_liquid(wide)


def test_deep_oi_bypasses_zero_volume():
    # off-hours / at the open: deep open interest, zero intraday volume -> liquid
    q = _q(Right.PUT, 635, -0.28, bid=2.5, ask=2.6, oi=5000, vol=0)
    assert is_liquid(q)


def test_low_oi_with_volume_is_liquid():
    # a thin strike that is actually trading today still qualifies
    q = _q(Right.PUT, 635, -0.28, bid=2.5, ask=2.6, oi=200, vol=50)
    assert is_liquid(q)


def test_bullish_builds_bull_put_spread():
    s = build_spread(_put_chain(), Direction.BULLISH, EXPIRY, short_delta=0.30, width=5.0)
    assert isinstance(s, VerticalSpread)
    assert s.right is Right.PUT
    # short ~0.30 delta → 635; protective long 5 lower → 630
    assert s.short_leg.strike == 635
    assert s.long_leg.strike == 630
    assert s.long_leg.strike < s.short_leg.strike       # long is protective (lower)
    assert s.credit > 0
    assert s.width == 5.0


def test_bearish_builds_bear_call_spread():
    s = build_spread(_call_chain(), Direction.BEARISH, EXPIRY, short_delta=0.30, width=5.0)
    assert s is not None
    assert s.right is Right.CALL
    assert s.short_leg.strike == 645                    # nearest 0.30 delta
    assert s.long_leg.strike == 650                     # 5 higher, protective
    assert s.long_leg.strike > s.short_leg.strike
    assert s.credit > 0


def test_max_loss_is_bounded_and_correct():
    s = build_spread(_put_chain(), Direction.BULLISH, EXPIRY, short_delta=0.30, width=5.0)
    # credit = short mid 2.55 − long mid 1.55 = 1.00 → max loss = (5 − 1)×100 = 400
    assert s.credit == 1.0
    assert s.max_loss == 400.0
    assert s.max_profit == 100.0
    assert s.max_loss > 0                                # never unbounded


def test_abstain_returns_none():
    assert build_spread(_put_chain(), Direction.ABSTAIN, EXPIRY) is None


def test_no_liquid_leg_returns_none_not_exception():
    illiquid = [_q(Right.PUT, 635, -0.30, bid=2.5, ask=2.6, oi=1, vol=1)]
    assert build_spread(illiquid, Direction.BULLISH, EXPIRY) is None


def test_inverted_credit_is_refused():
    # A chain where the "short" leg is cheaper than the long → non-positive credit.
    bad = [
        _q(Right.PUT, 635, -0.30, bid=1.0, ask=1.1),
        _q(Right.PUT, 630, -0.20, bid=3.0, ask=3.1),
    ]
    assert build_spread(bad, Direction.BULLISH, EXPIRY, width=5.0) is None


# ── phase 2: width from spot, tolerance, params ──────────────────────────────
from optionwright.options.select import SelectParams, strike_step, width_for  # noqa: E402
from optionwright.policy.params import GLOBAL, Params  # noqa: E402


def test_width_for_scales_with_spot_and_snaps_to_the_step():
    assert width_for(770.0, 0.0065, 1.0) == 5.0      # SPY
    assert width_for(295.0, 0.0065, 1.0) == 2.0      # IWM: 1.9 -> 2
    assert width_for(320.0, 0.0065, 2.5) == 2.5      # AAPL: 2.08 -> 2.5
    assert width_for(232.0, 0.0065, 2.5) == 2.5      # NVDA
    assert width_for(58.0, 0.0065, 0.5) == 0.5       # tiny spot: never below one step
    assert width_for(5000.0, 0.0065, 1.0) == 25.0    # clamped


def test_strike_step_from_the_chain():
    assert strike_step(_put_chain()) == 5.0          # the fixture lists strikes every $5
    assert strike_step([]) == 1.0


def test_long_leg_beyond_the_tolerance_yields_no_spread():
    # Short 635, target long 630. Remove 630: the nearest liquid leg is 625, 5 away = 10 wide.
    chain = [q for q in _put_chain() if q.strike != 630]
    legacy = build_spread(chain, Direction.BULLISH, EXPIRY, width=5.0)
    assert legacy is not None and legacy.width == 10.0                                              # the 4-Sep probe's QQQ 715/705
    assert build_spread(chain, Direction.BULLISH, EXPIRY, width=5.0, width_tolerance=0.5) is None    # |625-630|=5 > 2.5


def test_select_params_from_the_table_with_scopes():
    prm = Params({GLOBAL: {"width_pct": 0.01}, "group:megacap": {"short_delta": 0.25}})
    assert SelectParams.from_params(prm).width_pct == 0.01 and SelectParams.from_params(prm).short_delta == 0.30
    assert SelectParams.from_params(prm, group="megacap").short_delta == 0.25
    assert SelectParams.from_params(prm).min_open_interest == 100


def test_delta_for_regime_and_volatile_params_from_table():
    sel = SelectParams(short_delta=0.30, short_delta_volatile=0.20)
    assert sel.delta_for("volatil") == 0.20 and sel.delta_for("tranquilo") == 0.30 and sel.delta_for(None) == 0.30
    prm = Params({GLOBAL: {"volatile_mode": "none", "short_delta_volatile": 0.15}})
    assert SelectParams.from_params(prm).volatile_mode == "none" and SelectParams.from_params(prm).short_delta_volatile == 0.15
