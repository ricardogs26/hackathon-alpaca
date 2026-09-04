"""Replay harness: the exit rules over recorded ticks, no network."""
from optionwright.agent.exits import ExitParams
from optionwright.replay import replay_all, replay_position


def _tick(i, price, delta=0.30, hte=40.0, htc=5.0, sleeps=True, ts="2026-09-08T14:00:00+00:00"):
    return {"position_id": 7, "ts": ts, "price": price, "short_delta": delta, "hours_to_expiry": hte,
            "hours_to_close": htc, "sleeps_tonight": sleeps}


def test_replay_closes_at_the_delta_stop_and_reports_that_tick():
    ticks = [_tick(0, 1.00), _tick(1, 1.20, delta=0.40), _tick(2, 1.30, delta=0.46), _tick(3, 2.50, delta=0.60)]
    r = replay_position(ticks, ExitParams(), credit=1.0, contracts=2)
    assert r.closed and r.tick_index == 2 and "short delta 0.46" in r.reason
    assert r.pnl == -60.0                      # (1.0 - 1.3) * 100 * 2, not the -300 of the last tick


def test_replay_take_profit_and_trailing_use_the_rederived_peak():
    ticks = [_tick(0, 0.90), _tick(1, 0.62), _tick(2, 0.75)]     # 10% -> 38% -> 25%: trailing from the peak
    r = replay_position(ticks, ExitParams(), credit=1.0, contracts=1)
    assert r.closed and r.tick_index == 2 and "trailing" in r.reason


def test_replay_flat_mode_closes_at_the_last_half_hour():
    ticks = [_tick(0, 0.95, htc=2.0), _tick(1, 0.95, htc=0.4)]
    r = replay_position(ticks, ExitParams(), credit=1.0, contracts=1)
    assert r.closed and r.tick_index == 1 and "overnight flatten" in r.reason


def test_replay_stays_open_when_no_rule_fires():
    r = replay_position([_tick(0, 0.95, htc=3.0)], ExitParams(), credit=1.0, contracts=1)
    assert not r.closed and r.tick_index is None and r.pnl == 5.0


def test_replay_all_compares_with_the_actual_outcome():
    positions = [{"id": 7, "underlying": "SPY", "credit": 1.0, "contracts": 2, "expiry": "2026-09-10",
                  "status": "closed", "exit_reason": "stop-loss (2.0x credit)", "realized_pnl": -400.0},
                 {"id": 8, "underlying": "QQQ", "credit": 1.0, "contracts": 1, "status": "open"}]
    ticks = {7: [_tick(0, 1.0), _tick(1, 1.3, delta=0.5)]}
    rows = replay_all(ExitParams(), positions, ticks)
    assert len(rows) == 1 and rows[0]["position_id"] == 7
    assert rows[0]["sim_pnl"] == -60.0 and rows[0]["actual_pnl"] == -400.0
