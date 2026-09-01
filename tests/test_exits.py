"""
Tests for the exit decision (trailing take-profit + stop + hard cap + expiry).
Credit-spread P&L is (credit - current_price) / credit = captured fraction.
"""
from __future__ import annotations

from optionwright.agent.exits import ExitParams, decide_exit

CREDIT = 1.00  # $1.00 per share


def test_expiration_forces_close_even_when_profitable():
    d = decide_exit(CREDIT, current_price=0.10, is_expiry_day=True)
    assert d.close and "expiration" in d.reason


def test_stop_loss_at_two_x_credit():
    # current 3.00 -> loss 2.00 = 2x credit -> close
    d = decide_exit(CREDIT, current_price=3.00, is_expiry_day=False)
    assert d.close and "stop-loss" in d.reason


def test_hard_take_profit_ceiling_at_60():
    # captured 60% (buy back at 0.40) -> hard cap closes it
    d = decide_exit(CREDIT, current_price=0.40, is_expiry_day=False, peak_captured=0.60)
    assert d.close and "take-profit" in d.reason


def test_trailing_closes_on_pullback_from_peak():
    # peaked at 30%, now back to 19% (30% - 10% floor = 20%; 19% < 20%) -> close
    d = decide_exit(CREDIT, current_price=0.81, is_expiry_day=False, peak_captured=0.30)
    assert d.close and "trailing" in d.reason


def test_trailing_holds_above_the_floor():
    # peaked 30%, now 22% (still above the 20% floor) -> hold
    d = decide_exit(CREDIT, current_price=0.78, is_expiry_day=False, peak_captured=0.30)
    assert not d.close


def test_trailing_not_armed_below_activation():
    # peaked only 15% (below 20% activation), fell to a loss -> trailing inactive, hold
    d = decide_exit(CREDIT, current_price=1.10, is_expiry_day=False, peak_captured=0.15)
    assert not d.close


def test_todays_scenario_peak_27_reverses_exits_green():
    # peaked 27%, reverses toward a loss: floor = 27% - 10% = 17%; at 16% -> close in the green
    d = decide_exit(CREDIT, current_price=0.84, is_expiry_day=False, peak_captured=0.27)
    assert d.close and "trailing" in d.reason


def test_fresh_winner_still_climbing_holds():
    # captured 40%, peak 40% (no pullback) -> below hard cap, no pullback -> hold and let it run
    d = decide_exit(CREDIT, current_price=0.60, is_expiry_day=False, peak_captured=0.40)
    assert not d.close


def test_custom_params():
    p = ExitParams(trail_activation=0.15, trail_giveback=0.05, hard_take_profit=0.5)
    # peaked 20%, now 14% (floor 15%) -> close under stricter trail
    assert decide_exit(CREDIT, 0.86, False, peak_captured=0.20, params=p).close
