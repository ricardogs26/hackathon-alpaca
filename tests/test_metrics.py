"""
Tests for the metrics helpers. The one with real logic is the signed-P&L split
into gain/loss counters (a Prometheus Counter can't go negative).
"""
from __future__ import annotations

from optionwright import metrics


def _val(counter, **labels):
    return counter.labels(**labels)._value.get()


def test_gain_increments_gain_counter():
    before = _val(metrics.REALIZED_PNL, result="gain")
    metrics.record_realized_pnl(120.0)
    assert _val(metrics.REALIZED_PNL, result="gain") == before + 120.0


def test_loss_increments_loss_counter_as_positive():
    before = _val(metrics.REALIZED_PNL, result="loss")
    metrics.record_realized_pnl(-45.0)
    # stored as a positive magnitude on the loss counter
    assert _val(metrics.REALIZED_PNL, result="loss") == before + 45.0


def test_record_cycle_counts_opened_and_position():
    b_cyc = _val(metrics.CYCLES, result="opened")
    b_pos = _val(metrics.POSITIONS_OPENED, underlying="SPY", direction="bullish")
    metrics.record_cycle({"action": "opened", "underlying": "SPY", "direction": "bullish"})
    assert _val(metrics.CYCLES, result="opened") == b_cyc + 1
    assert _val(metrics.POSITIONS_OPENED, underlying="SPY", direction="bullish") == b_pos + 1


def test_record_cycle_abstain_no_position():
    b_pos = _val(metrics.POSITIONS_OPENED, underlying="SPY", direction="bullish")
    metrics.record_cycle({"action": "abstain", "underlying": "SPY"})
    assert _val(metrics.POSITIONS_OPENED, underlying="SPY", direction="bullish") == b_pos
