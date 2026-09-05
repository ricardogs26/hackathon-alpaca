"""The automated reconciler: every case, pure."""
from optionwright.reconciler import HUMAN, resolve

T0 = "2026-09-08T14:00:00+00:00"
S, L = "SPY260909C00769000", "SPY260909C00774000"


def _row(pid=1, n=8, credit=1.07, fill=1.10, s=S, lg=L):
    return {"id": pid, "short_symbol": s, "long_symbol": lg, "contracts": n, "credit": credit, "fill_credit": fill,
            "ts_open": T0, "underlying": "SPY", "expiry": "2026-09-09", "option_right": "call"}


def _order(oid, legs, price, status="filled", at="2026-09-08T15:00:00+00:00"):
    return {"id": oid, "status": status, "filled_avg_price": price, "submitted_at": at,
            "legs": [{"symbol": sym, "side": side, "filled_qty": 8} for sym, side in legs.items()]}


def test_matching_books_yield_nothing():
    assert resolve([_row()], {S: -8, L: 8}, [], []) == []


def test_both_legs_gone_with_a_filled_close_order_closes_at_the_fill():
    out = resolve([_row()], {}, [_order("c1", {S: "buy", L: "sell"}, 1.62)], [])
    assert len(out) == 1 and out[0].kind == "close_fill" and out[0].position_id == 1 and out[0].payload["fill_price"] == 1.62


def test_close_order_before_the_open_does_not_count():
    old = _order("c0", {S: "buy", L: "sell"}, 1.0, at="2026-09-07T15:00:00+00:00")
    out = resolve([_row()], {}, [old], [])
    assert out[0].kind == HUMAN and "no closing order" in out[0].evidence


def test_both_legs_expired_close_at_zero():
    acts = [{"type": "OPEXP", "symbol": S}, {"type": "OPEXP", "symbol": L}]
    out = resolve([_row()], {}, [], acts)
    assert out[0].kind == "close_expired" and out[0].payload["fill_price"] == 0.0


def test_assignment_or_exercise_is_urgent_human():
    out = resolve([_row()], {}, [], [{"type": "OPASN", "symbol": S}])
    assert out[0].kind == HUMAN and out[0].urgent and "assignment" in out[0].evidence


def test_partial_quantity_is_adjusted_to_the_broker():
    out = resolve([_row(n=8)], {S: -5, L: 5}, [], [])
    assert out[0].kind == "adjust_qty" and out[0].payload["contracts"] == 5


def test_single_leg_left_is_urgent_human():
    out = resolve([_row()], {L: 8}, [], [])
    assert out[0].kind == HUMAN and out[0].urgent and "single leg" in out[0].evidence


def test_unknown_spread_with_the_agents_filled_entry_is_adopted():
    out = resolve([], {S: -4, L: 4}, [_order("e1", {S: "sell", L: "buy"}, 1.05)], [])
    assert out[0].kind == "adopt" and out[0].payload["contracts"] == 4 and out[0].payload["fill_credit"] == 1.05
    assert out[0].payload["underlying"] == "SPY" and out[0].payload["option_right"] == "call" and out[0].payload["expiry"] == "2026-09-09"


def test_unknown_spread_matching_an_unfilled_row_is_reactivated():
    out = resolve([], {S: -4, L: 4}, [_order("e1", {S: "sell", L: "buy"}, 1.05)], [],
                  unfilled_rows=[{"id": 40, "short_symbol": S, "long_symbol": L, "contracts": 4}])
    assert out[0].kind == "reactivate" and out[0].position_id == 40


def test_unknown_spread_without_an_entry_order_goes_to_human():
    out = resolve([], {S: -4, L: 4}, [], [])
    assert out[0].kind == HUMAN and "unknown origin" in out[0].evidence and not out[0].urgent


def test_naked_short_at_the_broker_is_urgent_and_stray_long_is_not():
    out = resolve([], {S: -2, "SPY260909P00760000": 3}, [], [])
    kinds = {(r.symbols, r.urgent) for r in out}
    assert ((S,), True) in kinds and (("SPY260909P00760000",), False) in kinds


def test_put_spreads_pair_with_the_lower_long_strike():
    ps, pl = "SPY260909P00766000", "SPY260909P00761000"
    out = resolve([], {ps: -3, pl: 3}, [_order("e2", {ps: "sell", pl: "buy"}, 0.76)], [])
    assert out[0].kind == "adopt" and out[0].payload["option_right"] == "put"


def test_matched_positions_consume_their_legs_before_pairing_leftovers():
    # two identical spreads at the broker, one known: the other is adopted, not confused
    out = resolve([_row(n=4)], {S: -8, L: 8}, [_order("e9", {S: "sell", L: "buy"}, 1.0)], [])
    assert len(out) == 1 and out[0].kind == "adopt" and out[0].payload["contracts"] == 4
