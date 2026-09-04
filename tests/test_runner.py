"""
Tests for the runner: the seam between the pure pipeline and live services, and
the ONLY code that closes positions with money. Everything external (Alpaca,
Postgres, the LLM, the scheduler) is a fake via monkeypatch; no network, no DB.
"""
from __future__ import annotations

import threading
import time
from datetime import date
from types import SimpleNamespace

import pytest

from optionwright import metrics
from optionwright.agent import runner
from optionwright.policy.params import GLOBAL, Params


# ── fakes ─────────────────────────────────────────────────────────────────────
class _Clock:
    def __init__(self, is_open, next_close=None):
        self.is_open = is_open
        self.next_close = next_close


class _TradingClient:
    def __init__(self, is_open, next_close=None):
        self.calls = 0
        self._open = is_open
        self._next_close = next_close

    def get_clock(self):
        self.calls += 1
        return _Clock(self._open, self._next_close)


def _use_clock(monkeypatch, is_open: bool, next_close=None) -> _TradingClient:
    tc = _TradingClient(is_open, next_close)
    monkeypatch.setattr(runner.alpaca, "_trading_client", lambda: tc)
    return tc


def _settings(**over):
    from optionwright.universe import flat_universe

    base = dict(underlyings_list=["SPY", "QQQ", "IWM"], universe=flat_universe(["SPY", "QQQ", "IWM"]),
                chain_prefetch_workers=3, cycle_seconds=180, exit_check_seconds=60,
                trend_flat_pct=1.0, vol_high_pct=1.2)
    base.update(over)
    return SimpleNamespace(**base)


def _pos(pid=1, credit=1.0, contracts=2, expiry="2099-01-01", peak=0.0, status="open"):
    return {"id": pid, "status": status, "underlying": "SPY", "short_symbol": f"S{pid}",
            "long_symbol": f"L{pid}", "credit": credit, "contracts": contracts,
            "expiry": expiry, "peak_captured": peak, "realized_pnl": None}


def _val(counter, **labels):
    return counter.labels(**labels)._value.get()


@pytest.fixture(autouse=True)
def _fresh_runner_state(monkeypatch):
    """Each test starts with an empty market-clock cache and a free exit lock."""
    monkeypatch.setattr(runner, "_clock_cache", None)
    monkeypatch.setattr(runner, "_exit_lock", threading.Lock())
    monkeypatch.setattr(runner, "get_settings", lambda: _settings())
    monkeypatch.setattr(runner, "_params_cache", None)
    monkeypatch.setattr(runner, "current_params", lambda: Params())
    yield


# ── market clock cache ────────────────────────────────────────────────────────
def test_market_open_is_cached_within_ttl(monkeypatch):
    tc = _use_clock(monkeypatch, True)
    assert runner._market_open() and runner._market_open() and runner._market_open()
    assert tc.calls == 1  # three checks, one broker call


def test_market_open_cache_expires(monkeypatch):
    tc = _use_clock(monkeypatch, False)
    monkeypatch.setattr(runner, "_CLOCK_TTL", 0.0)
    assert runner._market_open() is False
    assert runner._market_open() is False
    assert tc.calls == 2


# ── run_exits ─────────────────────────────────────────────────────────────────
def test_run_exits_does_nothing_when_market_closed(monkeypatch):
    _use_clock(monkeypatch, False)

    def boom():
        raise AssertionError("manage_positions must not run when the market is closed")

    monkeypatch.setattr(runner, "manage_positions", boom)
    assert runner.run_exits() == []


def test_run_exits_runs_manage_and_counts_closes(monkeypatch):
    _use_clock(monkeypatch, True)
    monkeypatch.setattr(runner, "manage_positions",
                        lambda: [{"action": "closed", "position_id": 1}, {"action": "closed", "position_id": 2}])
    before = _val(metrics.CYCLES, result="closed")
    out = runner.run_exits()
    assert [o["action"] for o in out] == ["closed", "closed"]
    assert _val(metrics.CYCLES, result="closed") == before + 2


# ── run_entries ───────────────────────────────────────────────────────────────
def test_run_entries_skips_when_market_closed(monkeypatch):
    _use_clock(monkeypatch, False)
    monkeypatch.setattr(runner, "run_cycle", lambda u, d: (_ for _ in ()).throw(AssertionError("no entries when closed")))
    before = _val(metrics.CYCLES, result="skipped")
    out = runner.run_entries()
    assert out == [{"action": "skipped", "reason": "market closed"}]
    assert _val(metrics.CYCLES, result="skipped") == before + 1


def test_run_entries_refreshes_chains_runs_each_underlying_and_isolates_failures(monkeypatch):
    _use_clock(monkeypatch, True)
    calls = {"new_cycle": 0}
    monkeypatch.setattr(runner.alpaca, "new_cycle", lambda: calls.__setitem__("new_cycle", calls["new_cycle"] + 1))
    monkeypatch.setattr(runner.alpaca, "prefetch_chains", lambda syms, workers: {u: None for u in syms})
    monkeypatch.setattr(runner, "_build_deps", lambda params, u: object())

    def fake_cycle(u, deps):
        if u == "QQQ":
            raise RuntimeError("chain unavailable")
        return {"underlying": u, "action": "abstain"}

    monkeypatch.setattr(runner, "run_cycle", fake_cycle)
    errs_before = _val(metrics.ERRORS, where="cycle")
    out = runner.run_entries()
    assert calls["new_cycle"] == 1                       # chains refreshed once per pass
    assert [o["action"] for o in out] == ["abstain", "error", "abstain"]  # QQQ failed, others ran
    assert _val(metrics.ERRORS, where="cycle") == errs_before + 1


def test_run_once_is_exits_then_entries(monkeypatch):
    _use_clock(monkeypatch, True)
    order = []
    monkeypatch.setattr(runner, "run_exits", lambda: order.append("exits") or [{"action": "closed"}])
    monkeypatch.setattr(runner, "run_entries", lambda: order.append("entries") or [{"action": "abstain"}])
    out = runner.run_once()
    assert order == ["exits", "entries"]
    assert [o["action"] for o in out] == ["closed", "abstain"]


# ── manage_positions: the money path ──────────────────────────────────────────
def _wire_store(monkeypatch, positions, price, raise_for=()):
    """positions: rows; price: dict pos_id->price or float; raise_for: ids whose price call raises."""
    closes, peaks, orders = [], [], []
    monkeypatch.setattr(runner.store, "get_positions", lambda n=200: positions)
    monkeypatch.setattr(runner.store, "update_peak_captured", lambda pid, v: peaks.append((pid, v)))
    monkeypatch.setattr(runner.store, "close_position", lambda pid, pnl, why: closes.append((pid, pnl, why)))

    def spread_price(short, long):
        pid = int(short[1:]) if short[1:].isdigit() else None   # "S1" fakes; OCC symbols carry no id
        if pid in raise_for:
            raise RuntimeError("quote unavailable")
        return price[pid] if isinstance(price, dict) else price

    monkeypatch.setattr(runner.alpaca, "current_spread_price", spread_price)
    monkeypatch.setattr(runner.alpaca, "close_spread", lambda s, lng, n, lim: orders.append((s, lng, n, lim)))
    monkeypatch.setattr(runner.alpaca, "spread_snapshot", lambda s, lng: None)   # no greeks unless a test says so
    monkeypatch.setattr(runner.alpaca, "intraday_bars", lambda u: [])            # no intraday vol unless a test says so
    monkeypatch.setattr(runner, "_record_tick", lambda *a, **k: None)          # instrumentation off here
    return closes, peaks, orders


def _wire_ticks(monkeypatch, snapshot=None, snapshot_raises=False, record_raises=False):
    """Re-enable the real _record_tick with fake broker reads; returns the recorded ticks."""
    ticks = []
    monkeypatch.setattr(runner, "_record_tick", runner.__dict__["_record_tick_impl"])
    monkeypatch.setattr(runner.alpaca, "get_spot", lambda u: 768.34)

    def snap(short, long):
        if snapshot_raises:
            raise RuntimeError("snapshot unavailable")
        return snapshot

    def rec(t):
        if record_raises:
            raise RuntimeError("db down")
        ticks.append(t)

    monkeypatch.setattr(runner.alpaca, "spread_snapshot", snap)
    monkeypatch.setattr(runner.store, "record_tick", rec)
    return ticks


def test_manage_closes_on_take_profit(monkeypatch):
    closes, peaks, orders = _wire_store(monkeypatch, [_pos(1, credit=1.0, contracts=2)], price=0.50)
    out = runner.manage_positions()
    # captured 50% >= far take-profit 50% -> close at price + 0.05, P&L (1.0-0.5)*100*2
    assert orders == [("S1", "L1", 2, 0.55)]
    assert closes[0][0] == 1 and closes[0][1] == 100.0 and "take-profit" in closes[0][2]
    assert out[0]["action"] == "closed" and out[0]["realized_pnl"] == 100.0


def test_manage_holds_below_thresholds(monkeypatch):
    closes, peaks, orders = _wire_store(monkeypatch, [_pos(1)], price=0.90)  # captured 10%, trail not armed
    assert runner.manage_positions() == []
    assert orders == [] and closes == []


def test_manage_updates_high_water_mark(monkeypatch):
    closes, peaks, orders = _wire_store(monkeypatch, [_pos(1, peak=0.10)], price=0.70)  # captured 30% > peak 10%
    runner.manage_positions()
    assert peaks == [(1, pytest.approx(0.30))]


def test_manage_trailing_closes_on_pullback_from_peak(monkeypatch):
    # peak 38% captured, now 26%: gave back 12 > 7 -> trailing close
    closes, peaks, orders = _wire_store(monkeypatch, [_pos(1, peak=0.38)], price=0.74)
    out = runner.manage_positions()
    assert len(out) == 1 and "trailing" in out[0]["reason"]


def test_manage_forces_close_on_expiry_day(monkeypatch):
    today = date.today().isoformat()
    closes, peaks, orders = _wire_store(monkeypatch, [_pos(1, expiry=today)], price=1.30)  # losing, still closes
    out = runner.manage_positions()
    assert len(out) == 1 and "expiration" in out[0]["reason"]
    assert closes[0][1] == pytest.approx((1.0 - 1.30) * 100 * 2)


def test_manage_skips_position_without_price(monkeypatch):
    closes, peaks, orders = _wire_store(monkeypatch, [_pos(1)], price=None)
    assert runner.manage_positions() == []
    assert orders == []


def test_manage_isolates_one_failing_position(monkeypatch):
    closes, peaks, orders = _wire_store(monkeypatch, [_pos(1), _pos(2, credit=1.0, contracts=1)],
                                        price={1: 0.9, 2: 0.5}, raise_for=(1,))
    errs_before = _val(metrics.ERRORS, where="manage")
    out = runner.manage_positions()
    assert [o["position_id"] for o in out] == [2]          # #1 failed, #2 still closed
    assert _val(metrics.ERRORS, where="manage") == errs_before + 1


def test_manage_ignores_closed_rows(monkeypatch):
    closes, peaks, orders = _wire_store(monkeypatch, [_pos(1, status="closed")], price=0.1)
    assert runner.manage_positions() == []
    assert orders == []


def test_manage_lock_skips_overlapping_run(monkeypatch):
    """A second exits pass that starts while one is still running must skip,
    never run concurrently over the same positions."""
    calls = {"get_positions": 0}

    def slow_positions(n=200):
        calls["get_positions"] += 1
        time.sleep(0.4)
        return []

    monkeypatch.setattr(runner.store, "get_positions", slow_positions)
    t = threading.Thread(target=runner.manage_positions)
    t.start()
    time.sleep(0.1)
    assert runner.manage_positions() == []   # returned immediately, did not enter
    t.join()
    assert calls["get_positions"] == 1       # only the first pass touched the store


# ── scheduler wiring ──────────────────────────────────────────────────────────
def test_build_scheduler_registers_exits_and_entries_jobs():
    from optionwright.api.main import build_scheduler

    sched = build_scheduler(_settings(), run_exits=lambda: None, run_entries=lambda: None)
    jobs = {j.id: j for j in sched.get_jobs()}
    assert set(jobs) == {"entries", "exits"}
    assert jobs["entries"].trigger.interval.total_seconds() == 180
    assert jobs["exits"].trigger.interval.total_seconds() == 60
    assert jobs["exits"].max_instances == 1 and jobs["entries"].max_instances == 1
    assert not sched.running   # built, not started (no threads in tests)


# ── phase 0: position ticks ───────────────────────────────────────────────────
def _occ_pos(pid=22, credit=1.07, contracts=8, expiry="2099-01-01"):
    p = _pos(pid, credit=credit, contracts=contracts, expiry=expiry)
    p["short_symbol"], p["long_symbol"] = "SPY990101C00769000", "SPY990101C00774000"
    return p


def test_tick_recorded_with_state_on_hold(monkeypatch):
    _use_clock(monkeypatch, True)
    _wire_store(monkeypatch, [_occ_pos()], price=1.10)          # captured -3%: hold
    ticks = _wire_ticks(monkeypatch, snapshot={"short_delta": 0.41, "short_iv": 0.19})
    runner.manage_positions()
    assert len(ticks) == 1
    t = ticks[0]
    assert t.position_id == 22 and t.decision == "hold" and t.spot == 768.34
    assert t.short_delta == 0.41 and t.short_iv == 0.19 and t.short_strike == 769.0
    assert t.sigma_dist is not None and t.hours_to_expiry > 0


def test_tick_recorded_after_close_and_marks_decision(monkeypatch):
    _use_clock(monkeypatch, True)
    closes, _, orders = _wire_store(monkeypatch, [_occ_pos()], price=0.50)   # take-profit
    ticks = _wire_ticks(monkeypatch, snapshot={"short_delta": 0.12, "short_iv": 0.15})
    runner.manage_positions()
    assert len(orders) == 1 and len(closes) == 1
    assert ticks[0].decision == "close" and "take-profit" in ticks[0].reason


def test_tick_failure_never_blocks_the_close(monkeypatch):
    _use_clock(monkeypatch, True)
    closes, _, orders = _wire_store(monkeypatch, [_occ_pos()], price=0.50)
    ticks = _wire_ticks(monkeypatch, record_raises=True)
    before = _val(metrics.ERRORS, where="tick")
    out = runner.manage_positions()
    assert len(orders) == 1 and len(closes) == 1 and out[0]["action"] == "closed"
    assert ticks == []
    assert _val(metrics.ERRORS, where="tick") == before + 1


def test_snapshot_failure_keeps_the_credit_stop_and_counts_it(monkeypatch):
    _use_clock(monkeypatch, True)
    closes, _, orders = _wire_store(monkeypatch, [_occ_pos(credit=1.0)], price=2.10)   # 1.1x loss: credit stop
    _wire_ticks(monkeypatch, snapshot_raises=True)
    before = _val(metrics.ERRORS, where="snapshot")
    runner.manage_positions()
    assert len(orders) == 1 and "stop-loss" in closes[0][2]
    assert _val(metrics.ERRORS, where="snapshot") == before + 1


def test_tick_tolerates_missing_snapshot(monkeypatch):
    _use_clock(monkeypatch, True)
    _wire_store(monkeypatch, [_occ_pos()], price=1.10)
    ticks = _wire_ticks(monkeypatch, snapshot=None)
    runner.manage_positions()
    assert ticks[0].short_delta is None and ticks[0].sigma_dist is None


def test_spot_read_once_per_underlying_per_pass(monkeypatch):
    _use_clock(monkeypatch, True)
    _wire_store(monkeypatch, [_occ_pos(1), _occ_pos(2)], price=1.10)
    ticks = _wire_ticks(monkeypatch, snapshot=None)
    reads = []
    monkeypatch.setattr(runner.alpaca, "get_spot", lambda u: reads.append(u) or 768.0)
    runner.manage_positions()
    assert len(ticks) == 2 and reads == ["SPY"]


# ── phase 1: the exits read the state ─────────────────────────────────────────
def test_delta_stop_closes_from_the_snapshot_before_the_credit_stop(monkeypatch):
    _use_clock(monkeypatch, True)
    closes, _, orders = _wire_store(monkeypatch, [_occ_pos(credit=1.0)], price=1.15)   # only 0.15x loss
    monkeypatch.setattr(runner.alpaca, "spread_snapshot", lambda s, lng: {"short_delta": 0.47, "short_iv": 0.2})
    runner.manage_positions()
    assert len(orders) == 1 and "short delta 0.47" in closes[0][2]


def test_flat_mode_closes_sleepers_in_the_last_half_hour(monkeypatch):
    from datetime import datetime, timedelta, timezone

    _use_clock(monkeypatch, True, next_close=datetime.now(timezone.utc) + timedelta(minutes=20))
    closes, _, orders = _wire_store(monkeypatch, [_occ_pos(credit=1.0)], price=0.95)   # nothing else fires
    runner.manage_positions()
    assert len(orders) == 1 and "overnight flatten" in closes[0][2]


def test_flat_mode_holds_earlier_in_the_session(monkeypatch):
    from datetime import datetime, timedelta, timezone

    _use_clock(monkeypatch, True, next_close=datetime.now(timezone.utc) + timedelta(hours=3))
    _, _, orders = _wire_store(monkeypatch, [_occ_pos(credit=1.0)], price=0.95)
    runner.manage_positions()
    assert orders == []


def test_exit_params_resolve_per_underlying_from_the_table(monkeypatch):
    _use_clock(monkeypatch, True)
    monkeypatch.setattr(runner, "current_params", lambda: Params({GLOBAL: {"stop_delta": 0.60}, "underlying:SPY": {"stop_delta": 0.40}}))
    closes, _, orders = _wire_store(monkeypatch, [_occ_pos(credit=1.0)], price=1.05)
    monkeypatch.setattr(runner.alpaca, "spread_snapshot", lambda s, lng: {"short_delta": 0.45, "short_iv": 0.2})
    runner.manage_positions()
    assert len(orders) == 1 and "short delta 0.45 >= 0.40" in closes[0][2]      # SPY scope, not global 0.60


def test_current_params_caches_and_keeps_last_good_values_when_the_table_fails(monkeypatch):
    monkeypatch.setattr(runner, "current_params", runner.__dict__["_current_params_impl"])
    calls = {"n": 0}

    def load():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("db down")
        return {GLOBAL: {"stop_delta": 0.5}}

    monkeypatch.setattr(runner.store, "load_rules", load)
    assert runner.current_params().get("stop_delta") == 0.5
    assert runner.current_params().get("stop_delta") == 0.5 and calls["n"] == 1     # cached
    monkeypatch.setattr(runner, "_PARAMS_TTL", 0.0)
    assert runner.current_params().get("stop_delta") == 0.5 and calls["n"] == 2     # failed load -> last good


# ── phase 2: groups and prefetch ──────────────────────────────────────────────
def test_run_entries_prefetches_every_chain_before_the_cycles(monkeypatch):
    from types import SimpleNamespace
    from optionwright.universe import parse_groups

    _use_clock(monkeypatch, True)
    uni = parse_groups("index:SPY,QQQ;megacap:AAPL")
    monkeypatch.setattr(runner, "get_settings", lambda: SimpleNamespace(
        underlyings_list=uni.symbols, universe=uni, chain_prefetch_workers=2, cycle_seconds=180, exit_check_seconds=60))
    seen = {}
    monkeypatch.setattr(runner.alpaca, "new_cycle", lambda: None)
    monkeypatch.setattr(runner.alpaca, "prefetch_chains", lambda syms, workers: seen.update({"syms": syms, "workers": workers}) or {s: None for s in syms})
    monkeypatch.setattr(runner, "_build_deps", lambda params, u: u)
    monkeypatch.setattr(runner, "run_cycle", lambda u, deps: {"underlying": u, "action": "abstain"})
    out = runner.run_entries()
    assert seen == {"syms": ["SPY", "QQQ", "AAPL"], "workers": 2}
    assert [o["underlying"] for o in out] == ["SPY", "QQQ", "AAPL"]


def test_build_deps_resolves_rules_and_selection_by_group(monkeypatch):
    from types import SimpleNamespace
    from optionwright.universe import parse_groups

    uni = parse_groups("index:SPY,QQQ,IWM;megacap:AAPL")
    monkeypatch.setattr(runner, "get_settings", lambda: SimpleNamespace(
        universe=uni, expiry_min_days=2, expiry_max_days=3, trend_flat_pct=1.0,
        vol_high_pct=1.2, agent_rich_context=True))
    prm = Params({"group:megacap": {"max_per_group": 1, "short_delta": 0.25}})
    d_spy, d_aapl = runner._build_deps(prm, "SPY"), runner._build_deps(prm, "AAPL")
    assert d_spy.rules.max_per_group == 2 and d_aapl.rules.max_per_group == 1
    assert d_spy.select.short_delta == 0.30 and d_aapl.select.short_delta == 0.25
    assert d_spy.spot is runner.alpaca.get_spot


# ── phase 3: intraday perception feeds the trail and the signals ─────────────
def test_exits_pass_scales_the_trail_with_todays_vol(monkeypatch):
    _use_clock(monkeypatch, True)
    closes, _, orders = _wire_store(monkeypatch, [_occ_pos(credit=1.0, expiry="2099-01-01")], price=0.76)  # peak 34 -> now 24
    positions = [_occ_pos(credit=1.0)]
    positions[0]["peak_captured"] = 0.34
    monkeypatch.setattr(runner.store, "get_positions", lambda n=200: positions)
    chop = [{"open": 100, "high": 100.7, "low": 99.3, "close": 100 + (0.6 if i % 2 else -0.6), "volume": 1} for i in range(40)]
    monkeypatch.setattr(runner.alpaca, "intraday_bars", lambda u: chop)         # volatile day -> 14-pt give-back
    runner.manage_positions()
    assert orders == []                                                             # a 10-pt pull-back holds
    monkeypatch.setattr(runner.alpaca, "intraday_bars", lambda u: [])           # unknown vol -> fixed 7
    runner.manage_positions()
    assert len(orders) == 1 and "trailing" in closes[0][2]


def test_signals_merge_daily_and_intraday_and_survive_an_intraday_failure(monkeypatch):
    monkeypatch.setattr(runner.alpaca, "get_spot", lambda u: 101.0)
    monkeypatch.setattr(runner.alpaca, "recent_bars", lambda u: [100.0 + i * 0.1 for i in range(30)])
    bars = [{"open": 100, "high": 100.2, "low": 99.8, "close": 100 + i * 0.02, "volume": 10} for i in range(60)]
    monkeypatch.setattr(runner.alpaca, "intraday_bars", lambda u: bars)
    sig = runner._signals("SPY", Params(), "index")
    assert "tendencia_5d" in sig and "tendencia_30m" in sig and "vwap" in sig
    monkeypatch.setattr(runner.alpaca, "intraday_bars", lambda u: (_ for _ in ()).throw(RuntimeError("feed down")))
    sig2 = runner._signals("SPY", Params(), "index")
    assert "tendencia_5d" in sig2 and "vwap" not in sig2


def test_signals_thresholds_come_from_the_table_per_group(monkeypatch):
    monkeypatch.setattr(runner.alpaca, "get_spot", lambda u: 100.0)
    monkeypatch.setattr(runner.alpaca, "recent_bars", lambda u: [100 + (1.0 if i % 2 else -1.0) for i in range(30)])  # ~2% daily vol
    monkeypatch.setattr(runner.alpaca, "intraday_bars", lambda u: [])
    prm = Params({"group:megacap": {"vol_high_pct": 3.5}})
    assert runner._signals("SPY", prm, "index")["regimen"] == "volatil"       # global 1.2
    assert runner._signals("NVDA", prm, "megacap")["regimen"] == "tranquilo"  # megacap 3.5
