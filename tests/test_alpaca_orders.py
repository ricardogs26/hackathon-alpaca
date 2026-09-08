"""Order reads go through plain REST: the typed alpaca-py Order model cannot
parse Alpaca's multi-leg responses (8-Sep-2026)."""
from __future__ import annotations

from optionwright.broker import alpaca

# Verbatim shape returned by /v2/orders on 8-Sep-2026 for the AMZN bull put.
MLEG = {
    "id": "221e9569-0000-0000-0000-000000000000", "order_class": "mleg", "asset_id": "", "side": "",
    "qty": "4", "filled_qty": "4", "status": "filled", "filled_avg_price": "-0.54",
    "submitted_at": "2026-09-08T15:38:46.12Z",
    "legs": [
        {"symbol": "AMZN260911P00252500", "side": "sell", "status": "filled", "filled_qty": "4",
         "filled_avg_price": "1.37", "order_class": "mleg", "asset_id": ""},
        {"symbol": "AMZN260911P00250000", "side": "buy", "status": "filled", "filled_qty": "4",
         "filled_avg_price": "0.83", "order_class": "mleg", "asset_id": ""},
    ],
}


def test_order_status_parses_the_real_mleg_payload(monkeypatch):
    monkeypatch.setattr(alpaca, "_rest_get", lambda path, params=None: dict(MLEG) if path.endswith(MLEG["id"]) else None)
    st = alpaca.order_status(MLEG["id"])
    assert st["status"] == "filled" and st["filled_qty"] == 4
    assert st["filled_avg_price"] == 0.54                       # credit magnitude, sign dropped
    assert [(leg["symbol"], leg["side"], leg["filled_qty"]) for leg in st["legs"]] == [
        ("AMZN260911P00252500", "sell", 4), ("AMZN260911P00250000", "buy", 4)]


def test_orders_for_symbols_dedups_and_passes_symbols(monkeypatch):
    calls = []

    def fake(path, params=None):
        calls.append((path, params))
        return [MLEG, MLEG, {**MLEG, "id": "other", "status": "canceled", "filled_avg_price": None, "legs": []}]

    monkeypatch.setattr(alpaca, "_rest_get", fake)
    out = alpaca.orders_for_symbols(["AMZN260911P00250000", "AMZN260911P00252500", "AMZN260911P00250000"])
    assert [o["id"] for o in out] == [MLEG["id"], "other"]
    assert out[1]["filled_avg_price"] is None and out[1]["status"] == "canceled"
    assert calls[0][0] == "/v2/orders" and calls[0][1]["symbols"] == "AMZN260911P00250000,AMZN260911P00252500"
    assert alpaca.orders_for_symbols([]) == []
