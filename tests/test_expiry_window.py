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
