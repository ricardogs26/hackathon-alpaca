"""Position state vector (phase 0): pure functions, no network."""
from datetime import datetime, timezone

import pytest

from optionwright.agent.state import compute_tick, expiry_moment, parse_occ, sigma_distance

NOW = datetime(2026, 9, 2, 15, 0, tzinfo=timezone.utc)          # Wed 11:00 ET
CLOSE = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)        # same day 16:00 ET


def _pos(short="SPY260904C00769000", credit=1.07, contracts=8, pid=22):
    return {"id": pid, "underlying": "SPY", "short_symbol": short, "long_symbol": "SPY260904C00774000",
            "credit": credit, "contracts": contracts, "expiry": "2026-09-04"}


# ── OCC parsing ──────────────────────────────────────────────────────────────
def test_parse_occ_call_and_fractional_strike():
    assert parse_occ("SPY260904C00769000") == ("SPY", "2026-09-04", "C", 769.0)
    assert parse_occ("QQQ260904C00712500") == ("QQQ", "2026-09-04", "C", 712.5)
    assert parse_occ("SPY260904P00766000") == ("SPY", "2026-09-04", "P", 766.0)


def test_parse_occ_rejects_garbage():
    with pytest.raises(ValueError):
        parse_occ("S1")


def test_expiry_moment_is_close_of_expiry_day():
    assert expiry_moment("2026-09-04") == datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)


# ── sigma distance ───────────────────────────────────────────────────────────
def test_sigma_positive_when_short_strike_is_safe_for_both_rights():
    # 24h left, IV 20%: expected move = 700 * 0.20 * sqrt(24/8760) ≈ 7.33
    assert sigma_distance(700.0, 707.33, "C", 0.20, 24.0) == pytest.approx(1.0, abs=0.01)
    assert sigma_distance(700.0, 692.67, "P", 0.20, 24.0) == pytest.approx(1.0, abs=0.01)


def test_sigma_negative_when_in_the_money():
    assert sigma_distance(710.0, 700.0, "C", 0.20, 24.0) < 0
    assert sigma_distance(690.0, 700.0, "P", 0.20, 24.0) < 0


def test_sigma_none_without_iv_or_time():
    assert sigma_distance(700.0, 705.0, "C", None, 24.0) is None
    assert sigma_distance(700.0, 705.0, "C", 0.0, 24.0) is None
    assert sigma_distance(700.0, 705.0, "C", 0.2, 0.0) is None


# ── the tick ─────────────────────────────────────────────────────────────────
def test_tick_captures_pnl_time_and_sleep_flag():
    t = compute_tick(pos=_pos(), price=1.625, peak_captured=0.1682, decision="hold", reason="hold",
                     spot=768.34, short_delta=0.48, short_iv=0.19, now=NOW, next_close=CLOSE)
    assert t.position_id == 22 and t.option_right == "C" and t.short_strike == 769.0
    assert t.captured == pytest.approx((1.07 - 1.625) / 1.07, abs=1e-4)
    assert t.pnl_now == pytest.approx((1.07 - 1.625) * 100 * 8, abs=0.01)
    assert t.hours_to_expiry == pytest.approx(53.0)           # Wed 15:00Z -> Fri 20:00Z
    assert t.hours_to_close == pytest.approx(5.0)
    assert t.sleeps_tonight is True                           # expires Friday, today is Wednesday
    assert t.short_delta == 0.48 and t.short_iv == 0.19
    assert t.sigma_dist is not None and t.sigma_dist > 0     # 769 above 768.34: still safe, barely


def test_tick_does_not_sleep_on_expiry_day_and_tolerates_missing_snapshot():
    now = datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc)
    close = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)
    t = compute_tick(pos=_pos(), price=0.5, peak_captured=0.0, decision="close", reason="expiration force-close",
                     spot=None, short_delta=None, short_iv=None, now=now, next_close=close)
    assert t.sleeps_tonight is False
    assert t.short_delta is None and t.short_iv is None and t.sigma_dist is None
    assert t.decision == "close"


def test_tick_without_clock_leaves_session_fields_unknown():
    t = compute_tick(pos=_pos(), price=1.0, peak_captured=0.0, decision="hold", reason="hold",
                     spot=768.0, short_delta=0.3, short_iv=0.2, now=NOW, next_close=None)
    assert t.hours_to_close is None and t.sleeps_tonight is None
    assert set(t.as_row()) >= {"position_id", "ts", "sigma_dist", "sleeps_tonight", "decision"}
