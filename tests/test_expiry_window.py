"""Expiry window counted in trading sessions (weekends and holidays skipped)."""
from datetime import date

from optionwright.broker.alpaca import _session_window

# Real calendar around the 2026 Labor Day: Fri 4-Sep, Mon 7-Sep holiday, Tue 8-Sep...
SESSIONS = [date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11)]


def test_thursday_2_to_3_sessions_lands_on_next_week_not_the_weekend():
    # The 3-Sep bug: calendar days 2-3 = Sat/Sun -> "no expiry". Sessions: Tue-Wed.
    assert _session_window(date(2026, 9, 3), SESSIONS, 2, 3) == (date(2026, 9, 8), date(2026, 9, 9))


def test_friday_skips_the_holiday_monday():
    assert _session_window(date(2026, 9, 4), SESSIONS, 1, 3) == (date(2026, 9, 8), date(2026, 9, 10))


def test_today_is_never_a_candidate():
    assert _session_window(date(2026, 9, 4), SESSIONS, 1, 1) == (date(2026, 9, 8), date(2026, 9, 8))


def test_window_is_capped_by_the_calendar_horizon():
    assert _session_window(date(2026, 9, 10), SESSIONS, 1, 5) == (date(2026, 9, 11), date(2026, 9, 11))


def test_none_when_calendar_is_too_short_or_window_invalid():
    assert _session_window(date(2026, 9, 11), SESSIONS, 1, 3) is None
    assert _session_window(date(2026, 9, 3), SESSIONS, 0, 3) is None
    assert _session_window(date(2026, 9, 3), SESSIONS, 3, 2) is None


def test_listed_expiries_are_sorted_unique_and_inside_the_window(monkeypatch):
    from datetime import date as _d
    from types import SimpleNamespace
    from optionwright.broker import alpaca

    seen = {}

    class _TC:
        def get_calendar(self, req):
            return [SimpleNamespace(date=d) for d in SESSIONS + [_d(2026, 9, 14), _d(2026, 9, 15), _d(2026, 9, 16), _d(2026, 9, 17), _d(2026, 9, 18)]]

        def get_option_contracts(self, req):
            seen["gte"], seen["lte"] = req.expiration_date_gte, req.expiration_date_lte
            return SimpleNamespace(option_contracts=[
                SimpleNamespace(expiration_date=_d(2026, 9, 18)),
                SimpleNamespace(expiration_date=_d(2026, 9, 15)),
                SimpleNamespace(expiration_date=_d(2026, 9, 18)),
            ])

    monkeypatch.setattr(alpaca, "_trading_client", lambda: _TC())
    monkeypatch.setattr(alpaca, "_today", lambda: _d(2026, 9, 10))
    assert alpaca.listed_expiries("AAPL", 4, 5) == ["2026-09-15", "2026-09-18"]
    assert (seen["gte"], seen["lte"]) == (_d(2026, 9, 16), _d(2026, 9, 17))   # sessions 4 and 5 after Thu 10
    assert alpaca.listed_expiries("AAPL", 8, 5) == []       # empty window, no call
