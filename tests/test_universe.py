"""Correlation groups (phase 2)."""
import pytest

from optionwright.settings import Settings
from optionwright.universe import flat_universe, parse_groups


def test_parse_groups_symbols_and_membership():
    u = parse_groups("index:SPY,QQQ,IWM;megacap:AAPL,NVDA")
    assert u.symbols == ["SPY", "QQQ", "IWM", "AAPL", "NVDA"]
    assert u.group_of("qqq") == "index" and u.group_of("NVDA") == "megacap" and u.group_of("TLT") is None
    assert u.peers("IWM") == ("SPY", "QQQ", "IWM") and u.peers("TLT") == ("TLT",)


def test_parse_groups_rejects_duplicates_and_bad_shapes():
    with pytest.raises(ValueError):
        parse_groups("index:SPY;other:SPY")
    with pytest.raises(ValueError):
        parse_groups("SPY,QQQ")
    with pytest.raises(ValueError):
        parse_groups("index:")


def test_flat_universe_makes_each_symbol_its_own_group():
    u = flat_universe(["SPY", "QQQ"])
    assert u.group_of("SPY") == "spy" and u.peers("SPY") == ("SPY",)


def test_settings_universe_prefers_groups_and_falls_back_to_underlyings(monkeypatch):
    s = Settings(UNDERLYING_GROUPS="index:SPY,QQQ;megacap:AAPL", UNDERLYINGS="IWM", _env_file=None)
    assert s.underlyings_list == ["SPY", "QQQ", "AAPL"] and s.universe.group_of("AAPL") == "megacap"
    s2 = Settings(UNDERLYING_GROUPS="", UNDERLYINGS="IWM,GLD", _env_file=None)
    assert s2.underlyings_list == ["IWM", "GLD"] and s2.universe.group_of("GLD") == "gld"
