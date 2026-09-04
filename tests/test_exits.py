"""Exit rules by state (phase 1). Pure: no network, no DB."""
from __future__ import annotations

from optionwright.agent.exits import ExitParams, decide_exit, take_profit_threshold
from optionwright.policy.params import GLOBAL, Params

P = ExitParams()   # registry defaults: stop delta 0.45 / 1.0x, TP 50%/25% @24h, trail 30/7, flat @30min


# ── 1 expiry ─────────────────────────────────────────────────────────────────
def test_expiration_forces_close_even_when_profitable():
    d = decide_exit(1.0, 0.2, True, params=P)
    assert d.close and "expiration" in d.reason


# ── 2/3 stops ────────────────────────────────────────────────────────────────
def test_stop_by_delta_fires_before_any_credit_multiple():
    # price barely above credit (loss 0.1x) but the short leg is at 0.47 delta
    d = decide_exit(1.0, 1.10, False, params=P, short_delta=0.47)
    assert d.close and "short delta 0.47" in d.reason


def test_stop_by_delta_needs_the_snapshot_else_credit_stop_protects():
    assert not decide_exit(1.0, 1.50, False, params=P, short_delta=None).close     # 0.5x, no delta: hold
    d = decide_exit(1.0, 2.00, False, params=P, short_delta=None)                   # 1.0x: credit stop
    assert d.close and "stop-loss (1.0x credit)" in d.reason


def test_credit_stop_caps_the_loss_even_with_a_calm_delta():
    d = decide_exit(1.0, 2.05, False, params=P, short_delta=0.30)
    assert d.close and "stop-loss" in d.reason


def test_thursday_positions_would_have_stopped_at_the_delta_not_at_2x():
    # 3-Sep 08:18: #18 QQQ credit 1.125, price 1.725 (0.53x loss), short 714 with spot 712.5 (~0.45 delta)
    d = decide_exit(1.125, 1.725, False, params=P, short_delta=0.45)
    assert d.close and "short delta" in d.reason


# ── 4 take-profit by time ────────────────────────────────────────────────────
def test_take_profit_threshold_steps_at_24h():
    assert take_profit_threshold(P, None) == 0.50
    assert take_profit_threshold(P, 30.0) == 0.50
    assert take_profit_threshold(P, 24.0) == 0.25
    assert take_profit_threshold(P, 3.0) == 0.25


def test_take_profit_far_holds_at_40_closes_at_50():
    assert not decide_exit(1.0, 0.60, False, params=P, hours_to_expiry=48).close
    d = decide_exit(1.0, 0.50, False, params=P, hours_to_expiry=48)
    assert d.close and "threshold 50%" in d.reason


def test_take_profit_near_banks_25_in_the_last_hours():
    d = decide_exit(1.0, 0.74, False, params=P, hours_to_expiry=5)
    assert d.close and "threshold 25%" in d.reason


# ── 5 trailing ───────────────────────────────────────────────────────────────
def test_trailing_closes_on_pullback_from_peak():
    d = decide_exit(1.0, 0.78, False, peak_captured=0.34, params=P, hours_to_expiry=48)   # 22% now, peak 34
    assert d.close and "trailing" in d.reason


def test_trailing_holds_above_the_floor_and_not_armed_below_activation():
    assert not decide_exit(1.0, 0.72, False, peak_captured=0.34, params=P, hours_to_expiry=48).close  # 28% > 27
    assert not decide_exit(1.0, 0.85, False, peak_captured=0.25, params=P, hours_to_expiry=48).close  # not armed


# ── 6 overnight ──────────────────────────────────────────────────────────────
def test_flat_mode_closes_a_sleeper_in_the_last_30_minutes():
    d = decide_exit(1.0, 0.95, False, params=P, hours_to_close=0.4, sleeps_tonight=True, short_delta=0.20)
    assert d.close and "overnight flatten" in d.reason


def test_flat_mode_leaves_it_alone_earlier_in_the_day_or_if_it_expires_today():
    assert not decide_exit(1.0, 0.95, False, params=P, hours_to_close=2.0, sleeps_tonight=True).close
    assert not decide_exit(1.0, 0.95, False, params=P, hours_to_close=0.4, sleeps_tonight=False).close
    assert not decide_exit(1.0, 0.95, False, params=P, hours_to_close=None, sleeps_tonight=True).close


def test_delta_mode_lets_a_small_leg_in_a_balanced_book_sleep():
    p = ExitParams(overnight_mode="delta")
    ok = decide_exit(1.0, 0.95, False, params=p, hours_to_close=0.4, sleeps_tonight=True,
                     short_delta=0.20, book_net_delta_pct=0.01)
    assert not ok.close


def test_delta_mode_closes_when_the_leg_is_big_the_book_is_tilted_or_either_is_unknown():
    p = ExitParams(overnight_mode="delta")
    big = decide_exit(1.0, 0.95, False, params=p, hours_to_close=0.4, sleeps_tonight=True, short_delta=0.40, book_net_delta_pct=0.01)
    tilted = decide_exit(1.0, 0.95, False, params=p, hours_to_close=0.4, sleeps_tonight=True, short_delta=0.20, book_net_delta_pct=0.05)
    unknown = decide_exit(1.0, 0.95, False, params=p, hours_to_close=0.4, sleeps_tonight=True, short_delta=None, book_net_delta_pct=0.01)
    assert big.close and "short delta 0.40" in big.reason
    assert tilted.close and "net delta 5.0%" in tilted.reason
    assert unknown.close and "unknown" in unknown.reason


def test_winners_close_with_their_own_reason_before_the_overnight_rule():
    d = decide_exit(1.0, 0.45, False, params=P, hours_to_expiry=48, hours_to_close=0.3, sleeps_tonight=True)
    assert "take-profit" in d.reason


# ── params from the table ────────────────────────────────────────────────────
def test_exit_params_from_params_respects_scopes():
    prm = Params({GLOBAL: {"stop_delta": 0.40, "overnight_mode": "delta"}, "underlying:QQQ": {"stop_delta": 0.50}})
    assert ExitParams.from_params(prm).stop_delta == 0.40
    assert ExitParams.from_params(prm, underlying="QQQ").stop_delta == 0.50
    assert ExitParams.from_params(prm).overnight_mode == "delta"
    assert ExitParams.from_params(prm).take_profit_far == 0.50    # default flows through


# ── phase 3: trailing in sigma terms ─────────────────────────────────────────
from optionwright.agent.exits import trail_giveback_for  # noqa: E402


def test_trail_giveback_scales_with_intraday_vol_and_clamps():
    p = ExitParams(trail_giveback=0.07, trail_vol_ref_pct=0.8)
    assert trail_giveback_for(p, 0.8) == 0.07
    assert trail_giveback_for(p, 1.6) == 0.14          # twice the movement, twice the room
    assert trail_giveback_for(p, 0.2) == 0.035         # clamped at 0.5x
    assert trail_giveback_for(p, 5.0) == 0.14          # clamped at 2x
    assert trail_giveback_for(p, None) == 0.07         # unknown: fixed
    assert trail_giveback_for(ExitParams(trail_vol_ref_pct=0.0), 3.0) == 0.07   # feature off


def test_trailing_uses_the_scaled_giveback():
    p = ExitParams(trail_giveback=0.07, trail_vol_ref_pct=0.8)
    # peak 34%, now 24%: a 10-pt pull-back closes on a calm day (7) but holds on a volatile one (14)
    calm = decide_exit(1.0, 0.76, False, peak_captured=0.34, params=p, hours_to_expiry=48, vol_intradia_pct=0.8)
    wild = decide_exit(1.0, 0.76, False, peak_captured=0.34, params=p, hours_to_expiry=48, vol_intradia_pct=1.6)
    assert calm.close and "give-back 7%" in calm.reason
    assert not wild.close
