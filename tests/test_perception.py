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


# ── phase 3: intraday ────────────────────────────────────────────────────────
from optionwright.agent.perception import compute_intraday, merge_signals  # noqa: E402


def _bars(closes, vol=100.0):
    return [{"open": c, "high": c + 0.2, "low": c - 0.2, "close": c, "volume": vol} for c in closes]


def test_intraday_vwap_trend_and_regime():
    up = _bars([100 + i * 0.02 for i in range(60)])          # +1.2% over 60 bars, +0.6% over the last 30
    sig = compute_intraday(up, spot=101.2, trend_pct=0.25, vol_high_pct=1.2)
    assert sig["tendencia_30m"] == "alza" and sig["sobre_vwap"] is True and sig["vs_vwap_pct"] > 0
    assert sig["regimen_intradia"] == "tranquilo" and sig["rango_dia_pct"] > 0
    flat = compute_intraday(_bars([100.0] * 40), spot=100.0)
    assert flat["tendencia_30m"] == "lateral" and flat["vol_intradia_pct"] == 0.0


def test_intraday_volatile_regime_from_choppy_bars():
    chop = _bars([100 + (0.6 if i % 2 else -0.6) for i in range(40)])     # ±0.6% every minute
    sig = compute_intraday(chop, spot=100.0, vol_high_pct=1.2)
    assert sig["regimen_intradia"] == "volatil" and sig["vol_intradia_pct"] > 1.2


def test_intraday_needs_bars_and_a_spot():
    assert compute_intraday([], 100.0) == {}
    assert compute_intraday(_bars([100.0] * 3), 100.0) == {}
    assert compute_intraday(_bars([100.0] * 10), 0.0) == {}


def test_merge_signals_regime_is_volatile_if_either_is():
    daily = {"tendencia_5d": "baja", "regimen": "tranquilo"}
    assert merge_signals(daily, {"regimen_intradia": "volatil", "vwap": 1.0})["regimen"] == "volatil"
    assert merge_signals(daily, {"regimen_intradia": "tranquilo"})["regimen"] == "tranquilo"
    assert merge_signals({"regimen": "volatil"}, {"regimen_intradia": "tranquilo"})["regimen"] == "volatil"
    assert merge_signals(daily, {}) == daily        # intraday unavailable: the daily view survives untouched
