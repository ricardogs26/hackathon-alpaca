"""
Tests for the exit decision. Credit-spread P&L is credit - current_price, so the
take-profit and stop thresholds are checked at their exact boundaries.
"""
from __future__ import annotations

from optionwright.agent.exits import ExitParams, decide_exit

CREDIT = 1.00  # $1.00 per share


def test_expiration_forces_close_even_when_profitable():
    d = decide_exit(CREDIT, current_price=0.10, is_expiry_day=True)
    assert d.close and "expiration" in d.reason


def test_take_profit_at_half_credit():
    # buy back for 0.50 -> captured 0.50 = 50% of credit -> close
    d = decide_exit(CREDIT, current_price=0.50, is_expiry_day=False)
    assert d.close and "take-profit" in d.reason


def test_just_below_take_profit_holds():
    # buy back for 0.51 -> captured 0.49 < 50% -> hold
    d = decide_exit(CREDIT, current_price=0.51, is_expiry_day=False)
    assert not d.close and d.reason == "hold"


def test_stop_loss_at_two_x_credit():
    # current price 3.00 -> loss = 3.00 - 1.00 = 2.00 = 2x credit -> close
    d = decide_exit(CREDIT, current_price=3.00, is_expiry_day=False)
    assert d.close and "stop-loss" in d.reason


def test_just_below_stop_holds():
    # current price 2.99 -> loss 1.99 < 2x -> hold
    d = decide_exit(CREDIT, current_price=2.99, is_expiry_day=False)
    assert not d.close


def test_mid_range_holds_to_let_theta_work():
    # small unrealized loss, not at stop, not at target -> hold
    d = decide_exit(CREDIT, current_price=1.20, is_expiry_day=False)
    assert not d.close


def test_custom_params():
    p = ExitParams(take_profit_pct=0.75, stop_mult=1.5)
    # 0.50 buyback -> captured 50% < 75% -> hold under stricter target
    assert not decide_exit(CREDIT, 0.50, False, p).close
    # loss 1.5x -> current 2.50 -> close under looser stop
    assert decide_exit(CREDIT, 2.50, False, p).close


def test_zero_credit_never_take_profits():
    d = decide_exit(0.0, current_price=0.0, is_expiry_day=False)
    assert not d.close
