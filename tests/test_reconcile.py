from optionwright.reconcile import Mismatch, diff, expected_legs


def test_expected_legs_from_live_rows_only():
    rows = [{"status": "open", "contracts": 2, "short_symbol": "S1", "long_symbol": "L1"},
            {"status": "closing", "contracts": 1, "short_symbol": "S1", "long_symbol": "L2"},
            {"status": "pending", "contracts": 5, "short_symbol": "S9", "long_symbol": "L9"},
            {"status": "closed", "contracts": 5, "short_symbol": "S8", "long_symbol": "L8"}]
    assert expected_legs(rows) == {"S1": -3, "L1": 2, "L2": 1}


def test_diff_reports_every_disagreement_and_nothing_else():
    assert diff({"S1": -3, "L1": 3}, {"S1": -3, "L1": 3}) == []
    d = diff({"S1": -3, "L1": 3}, {"S1": -3, "X": 1})
    assert d == [Mismatch("L1", 3, 0), Mismatch("X", 0, 1)]
    assert str(d[0]) == "L1: db +3 vs broker +0"
