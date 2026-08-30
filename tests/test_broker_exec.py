"""
Tests for the multi-leg CLI argument construction. Pure: builds argv and the
legs JSON without ever invoking the CLI or touching the network.
"""
from __future__ import annotations

import json

from optionwright.broker.alpaca import _build_mleg_args
from optionwright.options.models import Direction, OptionQuote, Right, VerticalSpread


def _leg(strike, delta, bid, ask, symbol):
    return OptionQuote(
        symbol=symbol, underlying="SPY", right=Right.PUT, strike=strike,
        expiry="2026-08-31", bid=bid, ask=ask, delta=delta,
        open_interest=5000, volume=500,
    )


def _bull_put():
    return VerticalSpread(
        underlying="SPY", right=Right.PUT, expiry="2026-08-31",
        short_leg=_leg(767, -0.30, 0.97, 0.99, "SPY260831P00767000"),
        long_leg=_leg(762, -0.09, 0.25, 0.29, "SPY260831P00762000"),
        direction=Direction.BULLISH,
    )


def test_argv_has_mleg_class_and_limit():
    argv = _build_mleg_args(_bull_put(), contracts=2, limit_price=0.70)
    assert "mleg" in argv
    assert argv[argv.index("--qty") + 1] == "2"
    assert argv[argv.index("--type") + 1] == "limit"
    assert argv[argv.index("--limit-price") + 1] == "0.70"
    assert argv[argv.index("--time-in-force") + 1] == "day"


def test_legs_json_shape():
    argv = _build_mleg_args(_bull_put(), contracts=1, limit_price=0.70)
    legs = json.loads(argv[argv.index("--legs") + 1])
    assert len(legs) == 2
    short, long = legs
    assert short["symbol"] == "SPY260831P00767000"
    assert short["side"] == "sell" and short["position_intent"] == "sell_to_open"
    assert long["symbol"] == "SPY260831P00762000"
    assert long["side"] == "buy" and long["position_intent"] == "buy_to_open"
    assert short["ratio_qty"] == "1" and long["ratio_qty"] == "1"


def test_rejects_zero_contracts():
    import pytest

    with pytest.raises(ValueError):
        _build_mleg_args(_bull_put(), contracts=0, limit_price=0.70)
