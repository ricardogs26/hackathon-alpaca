"""
Tests for the pure state-derivation in storage (no DB). The consecutive-loss
counter drives the circuit breaker, so its edge cases are worth pinning.
"""
from __future__ import annotations

from optionwright.storage.store import _consecutive_losses, _summarize_outcomes, _summarize_book


def test_no_trades():
    assert _consecutive_losses([]) == 0


def test_recent_win_resets():
    # most recent first: a win at the head means zero consecutive losses
    assert _consecutive_losses([50, -30, -40]) == 0


def test_leading_losses_counted():
    assert _consecutive_losses([-10, -20, 30, -5]) == 2


def test_all_losses():
    assert _consecutive_losses([-1, -2, -3]) == 3


def test_none_pnl_breaks_run():
    # an unknown (None) P&L is not a loss; it stops the run
    assert _consecutive_losses([-10, None, -20]) == 1


def test_zero_is_not_a_loss():
    assert _consecutive_losses([0, -10]) == 0


def test_summarize_outcomes_counts_by_direction():
    rows = [
        {"underlying": "SPY", "option_right": "call", "realized_pnl": 200.0},  # bajista win
        {"underlying": "SPY", "option_right": "call", "realized_pnl": 150.0},  # bajista win
        {"underlying": "SPY", "option_right": "put", "realized_pnl": -80.0},   # alcista loss
        {"underlying": "SPY", "option_right": "call", "realized_pnl": None},   # sin cerrar -> ignora
    ]
    s = _summarize_outcomes(rows)
    assert s["cerradas"] == 3
    assert s["ganadas_bajista"] == 2
    assert s["ganadas_alcista"] == 0
    assert s["perdidas"] == 1


def test_summarize_book_groups_and_flags_concentration():
    rows = [
        {"underlying": "SPY", "option_right": "call"},
        {"underlying": "SPY", "option_right": "call"},
        {"underlying": "QQQ", "option_right": "put"},
    ]
    b = _summarize_book(rows, pnl_dia=1098.5, consec_losses=0)
    assert b["abiertas"] == 3
    assert b["por_subyacente"]["SPY"] == 2
    assert b["por_direccion"]["bajista"] == 2
    assert b["por_direccion"]["alcista"] == 1
    assert b["concentracion"] == "SPY"
    assert b["pnl_dia"] == 1098.5


def test_summarize_book_empty():
    b = _summarize_book([], pnl_dia=0.0, consec_losses=0)
    assert b["abiertas"] == 0
    assert b["concentracion"] is None
