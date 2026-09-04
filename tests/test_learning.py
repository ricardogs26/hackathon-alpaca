"""Phase 4: the nightly statistical memory — pure parts."""
from datetime import date, datetime, timezone

from optionwright.agent.learning import (MIN_SAMPLE, Proposal, aggregate, dte_bucket, position_stats, propose,
                                         summary_text)
from optionwright.policy.params import group_scope

T0 = datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc)


def _pos(pid, pnl, credit=1.0, hours=3.0, reason="take-profit", regime="tranquilo", expiry="2026-09-10", underlying="SPY"):
    return {"id": pid, "underlying": underlying, "expiry": expiry, "credit": credit, "contracts": 1, "ts_open": T0,
            "ts_close": T0.replace(hour=14 + int(hours)) if hours < 10 else T0.replace(day=9), "realized_pnl": pnl,
            "exit_reason": reason, "regime": regime}


def _ticks(caps, deltas=None):
    deltas = deltas or [0.3] * len(caps)
    return [{"captured": c, "short_delta": d} for c, d in zip(caps, deltas)]


def test_dte_bucket():
    assert dte_bucket(T0, "2026-09-08") == "0-1" and dte_bucket(T0, "2026-09-09") == "0-1"
    assert dte_bucket(T0, "2026-09-10") == "2-3" and dte_bucket(T0, "2026-09-15") == "4+"


def test_position_stats_measures_excursions_and_result():
    st = position_stats(_pos(1, -400.0, reason="stop (short delta 0.46 >= 0.45)"), _ticks([0.05, 0.35, 0.10, -0.6], [0.3, 0.2, 0.35, 0.46]), "index")
    assert st.result == "loss" and st.mfe == 0.35 and st.mae == -0.6 and st.max_short_delta == 0.46
    assert st.group == "index" and st.regime == "tranquilo" and st.dte_bucket == "2-3" and st.hold_hours == 3.0
    assert position_stats(_pos(2, 100.0), [], "index") is None
    assert position_stats(_pos(3, 50.0, regime=None), _ticks([0.1]), "index").regime == "desconocido"


def test_aggregate_per_bucket():
    stats = [position_stats(_pos(i, 100.0), _ticks([0.2, 0.5]), "index") for i in range(3)]
    stats += [position_stats(_pos(10 + i, -300.0, hours=20, reason="stop (short delta 0.47 >= 0.45)"), _ticks([0.35, -0.9]), "index") for i in range(2)]
    stats.append(position_stats(_pos(20, 80.0, underlying="NVDA"), _ticks([0.3]), "megacap"))
    agg = aggregate(stats)
    a = agg[("index", "tranquilo", "2-3")]
    assert a["n"] == 5 and a["wins"] == 3 and a["losses"] == 2 and a["win_rate"] == 0.6
    assert a["median_mfe_losers"] == 0.35 and a["delta_stops"] == 2 and a["overnight_losses"] == 2
    assert a["avg_loss_vs_credit"] == 3.0                      # -(-300)/(1.0*100)
    assert agg[("megacap", "tranquilo", "2-3")]["n"] == 1


def _agg(n_wins, n_losses, mfe_losers=0.35, mfe_winners=0.5, delta_stops=0, loss_vs_credit=0.5):
    return {("index", "tranquilo", "2-3"): {
        "n": n_wins + n_losses, "wins": n_wins, "losses": n_losses, "win_rate": None, "avg_win": None, "avg_loss": None,
        "median_mfe_losers": mfe_losers, "median_mfe_winners": mfe_winners, "median_mae_winners": None,
        "delta_stops": delta_stops, "avg_loss_vs_credit": loss_vs_credit, "overnight_losses": 0}}


CUR = {"take_profit_far": 0.5, "stop_delta": 0.45}


def test_no_proposal_below_the_minimum_sample():
    assert propose(_agg(10, 8), CUR, group_scope) == []
    assert MIN_SAMPLE == 20


def test_take_profit_proposal_when_losers_had_captured_a_lot():
    ps = propose(_agg(15, 8, mfe_losers=0.38, mfe_winners=0.52), CUR, group_scope)
    assert len(ps) == 1 and ps[0].key == "take_profit_far" and ps[0].scope == "group:index"
    assert ps[0].current == 0.5 and ps[0].proposed == 0.38 and ps[0].sample_n == 23
    # bounded: never more than 25% away from the current value
    ps2 = propose(_agg(15, 8, mfe_losers=0.30, mfe_winners=0.52), CUR, group_scope)
    assert ps2[0].proposed == 0.375


def test_no_take_profit_proposal_when_winners_run_well_past_the_target():
    assert propose(_agg(15, 8, mfe_losers=0.38, mfe_winners=0.70), CUR, group_scope) == []


def test_stop_delta_proposal_when_the_delta_stop_fires_late():
    ps = propose(_agg(14, 7, mfe_losers=0.1, delta_stops=4, loss_vs_credit=0.9), CUR, group_scope)
    assert [p.key for p in ps] == ["stop_delta"] and ps[0].proposed == 0.40
    assert propose(_agg(14, 7, mfe_losers=0.1, delta_stops=2, loss_vs_credit=0.9), CUR, group_scope) == []


def test_summary_text_lists_buckets_and_proposals():
    agg = _agg(15, 8)
    agg[("index", "tranquilo", "2-3")].update({"win_rate": 0.65, "avg_win": 120.0, "avg_loss": -300.0})
    txt = summary_text(agg, [Proposal("group:index", "take_profit_far", 0.5, 0.38, 23, "why")], [7], date(2026, 9, 8))
    assert "index/tranquilo/2-3" in txt and "#7 take_profit_far@group:index: 0.5 → 0.38" in txt and "approve" in txt
    assert "Sin propuestas" in summary_text(agg, [], [], date(2026, 9, 8))
    assert "Sin posiciones" in summary_text({}, [], [], date(2026, 9, 8))
