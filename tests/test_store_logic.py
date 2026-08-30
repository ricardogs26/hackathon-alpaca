"""
Tests for the pure state-derivation in storage (no DB). The consecutive-loss
counter drives the circuit breaker, so its edge cases are worth pinning.
"""
from __future__ import annotations

from optionwright.storage.store import _consecutive_losses


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
