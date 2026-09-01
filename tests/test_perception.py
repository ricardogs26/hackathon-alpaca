from __future__ import annotations

from optionwright.agent.perception import compute_signals


def _rising(n=25, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def test_uptrend_flags_alza_and_positive_momentum():
    closes = _rising()
    s = compute_signals(closes, spot=closes[-1] + 1)
    assert s["tendencia_5d"] == "alza"
    assert s["momentum_positivo"] is True
    assert s["pct_5d"] > 0


def test_downtrend_flags_baja():
    closes = list(reversed(_rising()))
    s = compute_signals(closes, spot=closes[-1] - 1)
    assert s["tendencia_5d"] == "baja"
    assert s["momentum_positivo"] is False


def test_flat_market_is_lateral():
    closes = [100.0] * 25
    s = compute_signals(closes, spot=100.2)  # +0.2% < 1.0% umbral
    assert s["tendencia_5d"] == "lateral"


def test_insufficient_bars_returns_empty():
    assert compute_signals([100.0, 101.0], spot=101.0) == {}
    assert compute_signals([], spot=100.0) == {}


def test_high_variance_is_volatile():
    closes = [100.0, 110.0, 95.0, 115.0, 90.0, 120.0, 88.0]
    s = compute_signals(closes, spot=100.0, vol_high_pct=1.2)
    assert s["regimen"] == "volatil"
