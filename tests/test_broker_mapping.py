"""
Tests for the pure chain->OptionQuote mapping. Duck-typed fakes stand in for
Alpaca's contract and snapshot objects, so no network or account is touched.
"""
from __future__ import annotations

from types import SimpleNamespace

from optionwright.broker.alpaca import _to_quote
from optionwright.options.models import Right


def _contract(strike=765.0, ctype="ContractType.PUT", oi=8451):
    return SimpleNamespace(
        symbol="SPY260831P00765000",
        type=ctype,
        strike_price=strike,
        expiration_date="2026-08-31",
        open_interest=oi,
    )


def _snapshot(bid=0.53, ask=0.54, delta=-0.189, volume=1200):
    return SimpleNamespace(
        latest_quote=SimpleNamespace(bid_price=bid, ask_price=ask),
        greeks=SimpleNamespace(delta=delta),
        daily_bar=SimpleNamespace(volume=volume),
    )


def test_maps_put_contract_and_snapshot():
    q = _to_quote(_contract(), _snapshot(), "SPY")
    assert q is not None
    assert q.right is Right.PUT
    assert q.strike == 765.0
    assert q.expiry == "2026-08-31"
    assert q.bid == 0.53 and q.ask == 0.54
    assert q.delta == -0.189
    assert q.open_interest == 8451
    assert q.volume == 1200
    assert q.mid == 0.535


def test_maps_call_contract():
    q = _to_quote(_contract(ctype="ContractType.CALL"), _snapshot(delta=0.30), "SPY")
    assert q.right is Right.CALL
    assert q.delta == 0.30


def test_missing_quote_returns_none():
    snap = _snapshot()
    snap.latest_quote = None
    assert _to_quote(_contract(), snap, "SPY") is None


def test_missing_greeks_returns_none():
    snap = _snapshot()
    snap.greeks = None
    assert _to_quote(_contract(), snap, "SPY") is None


def test_missing_daily_bar_defaults_volume_zero():
    snap = _snapshot()
    snap.daily_bar = None
    q = _to_quote(_contract(), snap, "SPY")
    assert q.volume == 0


def test_none_open_interest_becomes_zero():
    q = _to_quote(_contract(oi=None), _snapshot(), "SPY")
    assert q.open_interest == 0
