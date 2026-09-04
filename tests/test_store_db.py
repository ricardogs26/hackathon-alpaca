"""
store.py against a real Postgres (tech-debt 5.1). Needs a database at
OPTIONWRIGHT_TEST_DSN (CI provides one; locally `make db-up`); skipped otherwise.
Every test starts from a clean schema.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from optionwright.agent.learning import Proposal
from optionwright.agent.state import PositionTick
from optionwright.options.models import Direction, OptionQuote, Right, VerticalSpread
from optionwright.storage import store

DSN = os.environ.get("OPTIONWRIGHT_TEST_DSN", "postgresql://ow:ow@localhost:55432/ow_test")


@pytest.fixture
def db(monkeypatch):
    import psycopg

    try:
        psycopg.connect(DSN, connect_timeout=2).close()
    except Exception as exc:  # no database around: skip, don't fail
        pytest.skip(f"no test Postgres at {DSN}: {exc}")
    monkeypatch.setattr(store, "get_settings", lambda: SimpleNamespace(postgres_dsn=DSN))
    with psycopg.connect(DSN) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    store.init_schema()
    return store


def _spread(underlying="SPY", right=Right.CALL, short=769.0, long=774.0, credit=1.07, expiry="2099-01-09"):
    yy = expiry[2:4] + expiry[5:7] + expiry[8:10]
    r = "C" if right is Right.CALL else "P"
    sq = OptionQuote(f"{underlying}{yy}{r}{int(short*1000):08d}", underlying, right, short, expiry, credit + 0.5, credit + 0.5, 0.30 if r == "C" else -0.30, 1000, 100)
    lq = OptionQuote(f"{underlying}{yy}{r}{int(long*1000):08d}", underlying, right, long, expiry, 0.5, 0.5, 0.12 if r == "C" else -0.12, 1000, 100)
    return VerticalSpread(underlying, right, expiry, sq, lq, Direction.BEARISH if r == "C" else Direction.BULLISH)


# ── schema ───────────────────────────────────────────────────────────────────
def test_schema_is_idempotent(db):
    db.init_schema()
    db.init_schema()


# ── positions: the order lifecycle state machine ─────────────────────────────
def test_open_close_with_fills(db):
    pid = db.record_position(_spread(), 8, "ord-1", status="open", fill_credit=1.10)
    row = db.get_positions()[0]
    assert row["status"] == "open" and row["fill_credit"] == 1.10 and row["max_loss"] == pytest.approx(3.93 * 100 * 8)
    db.close_position(pid, -100.0, "stop (short delta 0.46 >= 0.45)", fill_exit_price=1.225)
    row = db.get_positions()[0]
    assert row["status"] == "closed" and row["fill_exit_price"] == 1.225 and row["realized_pnl"] == -100.0
    assert db.closed_positions(days=1)[0]["id"] == pid


def test_pending_entry_confirmed_or_unfilled(db):
    a = db.record_position(_spread(), 2, "ord-a", status="pending")
    b = db.record_position(_spread(underlying="QQQ"), 2, "ord-b", status="pending")
    rows = db.pending_rows()
    assert [r["id"] for r in rows] == [a, b] and rows[0]["pending_order_id"] == "ord-a" and rows[0]["pending_age_s"] >= 0
    db.confirm_fill(a, 1.02)
    db.mark_unfilled(b, "entry canceled")
    by_id = {r["id"]: r for r in db.get_positions()}
    assert by_id[a]["status"] == "open" and by_id[a]["fill_credit"] == 1.02 and by_id[a]["pending_order_id"] is None
    assert by_id[b]["status"] == "unfilled" and by_id[b]["exit_reason"] == "entry canceled"
    assert db.pending_rows() == []


def test_closing_then_revert_counts_attempts(db):
    pid = db.record_position(_spread(), 3, "ord-1")
    db.mark_closing(pid, "close-1", "take-profit")
    row = db.pending_rows()[0]
    assert row["status"] == "closing" and row["exit_reason"] == "take-profit"
    db.revert_closing(pid)
    row = db.get_positions()[0]
    assert row["status"] == "open" and row["close_attempts"] == 1 and row["pending_order_id"] is None
    db.mark_closing(pid, "close-2", "take-profit")
    db.close_position(pid, 50.0, "take-profit", fill_exit_price=0.57)
    assert db.get_positions()[0]["status"] == "closed" and db.pending_rows() == []


def test_live_statuses_count_as_exposure_and_unfilled_does_not(db):
    db.record_position(_spread(), 2, "o1", status="open")
    db.record_position(_spread(underlying="QQQ", short=720.0, long=725.0), 2, "o2", status="pending")
    p3 = db.record_position(_spread(underlying="IWM", short=300.0, long=302.0), 2, "o3", status="pending")
    db.mark_unfilled(p3, "x")
    p4 = db.record_position(_spread(short=771.0, long=776.0), 1, "o4")
    db.mark_closing(p4, "c4", "stop")
    st = db.build_policy_state("SPY", 100_000.0, group_symbols=["SPY", "QQQ", "IWM"])
    assert st.open_positions == 3 and st.open_positions_underlying == 2 and st.open_positions_group == 3
    assert st.risk_by_direction == {"bearish": pytest.approx(sum(r["max_loss"] for r in db.get_positions() if r["status"] in ("open", "pending", "closing")))}
    assert "SPY|C|769.0" in st.open_short_strikes and st.net_delta_usd is None       # no ticks yet
    assert len(db.live_legs_rows()) == 2                                             # open + closing, not pending/unfilled


def test_regime_peak_and_position_states(db):
    pid = db.record_position(_spread(), 8, "o1")
    db.note_regime(pid, "tranquilo")
    db.update_peak_captured(pid, 0.2)
    db.update_peak_captured(pid, 0.1)       # never lowers
    assert db.get_positions()[0]["peak_captured"] == 0.2
    assert db.closed_positions(days=1) == []
    st = db.open_position_states()
    assert len(st) == 1 and st[0]["id"] == pid and st[0]["short_delta"] is None


# ── ticks and net delta ──────────────────────────────────────────────────────
def _tick(pid, ts, spot=768.0, delta=0.3, price=1.0, captured=0.1):
    return PositionTick(pid, ts, "SPY", spot, 1.07, price, captured, 0.2, 50.0, 769.0, "C", delta, 0.2, 0.5, 40.0, 5.0, True, "hold", "hold")


def test_ticks_latest_and_book_net_delta(db):
    pid = db.record_position(_spread(), 8, "o1")
    t0 = datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc)
    db.record_tick(_tick(pid, t0, delta=0.30))
    db.record_tick(_tick(pid, t0.replace(minute=1), delta=0.35, spot=770.0))
    ticks = db.get_ticks(pid)
    assert len(ticks) == 2 and ticks[-1]["short_delta"] == 0.35
    assert db.latest_ticks([pid])[pid]["spot"] == 770.0
    assert db.book_net_delta_usd() == pytest.approx(-0.35 * 100 * 8 * 770.0)     # short call spread: negative
    st = db.build_policy_state("SPY", 100_000.0)
    assert st.net_delta_usd == pytest.approx(-0.35 * 100 * 8 * 770.0)
    assert db.open_position_states()[0]["short_delta"] == 0.35


# ── decisions ────────────────────────────────────────────────────────────────
def test_decisions_and_open_confidence_join(db):
    sp = _spread()
    pid = db.record_position(sp, 2, "o1")
    db.record_decision("SPY", Direction.BEARISH, 0.8, "baja", True, 2, "approved 2x", sp, pid)
    db.record_decision("SPY", Direction.ABSTAIN, 0.3, "nada", False, 0, "LLM abstained", None)
    decs = db.get_decisions()
    assert decs[0]["direction"] == "abstain" and decs[1]["approved"] is True
    assert db.get_positions()[0]["open_confidence"] == 0.8
    assert db.last_opened_confidence() == 0.8


# ── equity ───────────────────────────────────────────────────────────────────
def test_equity_curve_and_daily(db):
    db.save_equity(100_000.0, 90_000.0)
    db.save_equity(100_500.0, 90_000.0)
    curve = db.get_equity_curve()
    assert [c["equity"] for c in curve] == [100_000.0, 100_500.0]
    assert db.get_equity_daily()[-1]["equity"] == 100_500.0


# ── rules ────────────────────────────────────────────────────────────────────
def test_rules_seed_set_history_and_precedence(db):
    assert db.seed_rules({"stop_delta": 0.45, "max_open_positions": 6, "not_a_rule": 1}) == 2
    assert db.seed_rules({"stop_delta": 0.99}) == 0                        # seed never overwrites
    out = db.set_rule("group:megacap", "stop_delta", "0.40", "ricardo", "test")
    assert out["old"] is None and out["new"] == 0.40
    out2 = db.set_rule("group:megacap", "stop_delta", 0.42, "ricardo", "tighter")
    assert out2["old"] == "0.4" and out2["new"] == 0.42
    rules = db.load_rules()
    assert rules["global"]["stop_delta"] == "0.45" and rules["group:megacap"]["stop_delta"] == "0.42"
    hist = db.rules_history()
    assert len(hist) == 2 and hist[0]["new_value"] == "0.42" and hist[0]["reason"] == "tighter"
    with pytest.raises(ValueError):
        db.set_rule("global", "stop_delta", 0.42, "x", "")             # reason required
    with pytest.raises(ValueError):
        db.set_rule("global", "stop_delta", 9.0, "x", "out of bounds")
    with pytest.raises(ValueError):
        db.set_rule("global", "no_such", 1, "x", "y")
    with pytest.raises(ValueError):
        db.set_rule("team:x", "stop_delta", 0.4, "x", "y")


# ── proposals ────────────────────────────────────────────────────────────────
def test_proposals_lifecycle(db):
    db.seed_rules({"take_profit_far": 0.5})
    pid = db.add_proposal(Proposal("group:index", "take_profit_far", 0.5, 0.38, 23, "losers captured 38%"))
    pid2 = db.add_proposal(Proposal("global", "stop_delta", 0.45, 0.40, 21, "late stops"))
    assert [p["id"] for p in db.pending_proposals()] == [pid, pid2]
    out = db.decide_proposal(pid, True, "ricardo")
    assert out["status"] == "approved" and out["value"] == "0.38"
    assert db.load_rules()["group:index"]["take_profit_far"] == "0.38"
    assert db.rules_history()[0]["reason"].startswith(f"proposal #{pid} approved")
    with pytest.raises(ValueError):
        db.decide_proposal(pid, True, "ricardo")                        # already decided
    with pytest.raises(KeyError):
        db.decide_proposal(9999, True, "ricardo")
    assert db.decide_proposal(pid2, False, "ricardo")["status"] == "rejected"
    assert db.load_rules().get("global", {}).get("stop_delta") is None   # a rejection changes nothing
    lst = db.list_proposals()
    assert {p["status"] for p in lst} == {"approved", "rejected"} and lst[0]["decided_at"] is not None


def test_proposals_expire_by_age(db):
    import psycopg

    pid = db.add_proposal(Proposal("global", "stop_delta", 0.45, 0.40, 21, "x"))
    assert db.expire_proposals(3) == 0
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE rule_proposals SET ts = now() - interval '4 days' WHERE id=%s", (pid,))
    assert db.expire_proposals(3) == 1 and db.pending_proposals() == []


# ── book summary, outcomes, breaker window ───────────────────────────────────
def test_book_summary_outcomes_and_lookback(db):
    import psycopg

    a = db.record_position(_spread(), 2, "o1")
    b = db.record_position(_spread(underlying="QQQ", short=720.0, long=725.0), 2, "o2")
    db.close_position(a, -100.0, "stop")
    db.close_position(b, -50.0, "stop")
    book = db.book_summary(lookback_hours=24.0)
    assert book["abiertas"] == 0 and book["pnl_dia"] == -150.0 and book["perdidas_consecutivas"] == 2
    assert db.llm_book_view(book).keys() >= {"abiertas", "pnl_dia", "perdidas_consecutivas"}
    assert "por_direccion" not in db.llm_book_view(book)
    assert db.recent_outcomes("SPY") == {"cerradas": 1, "ganadas_bajista": 0, "ganadas_alcista": 0, "perdidas": 1}
    with psycopg.connect(DSN) as c:
        c.execute("UPDATE positions SET ts_close = now() - interval '30 hours'")
    assert db.book_summary(lookback_hours=24.0)["perdidas_consecutivas"] == 0     # out of the window
    assert db.build_policy_state("SPY", 100_000.0, lookback_hours=24.0).consecutive_losses == 0
    assert db.build_policy_state("SPY", 100_000.0, lookback_hours=48.0).consecutive_losses == 2
