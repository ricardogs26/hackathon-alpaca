"""
Replay harness (phase 1.11): run the exit rules over the recorded ticks of a
position, tick by tick, and report where THESE parameters would have closed it
versus where the agent actually did. This is how a rule is validated before it
touches an order: on the ticks the agent really saw, with no network.

    python -m optionwright.replay                      # every position with ticks, current table params
    python -m optionwright.replay --stop-delta 0.40 --overnight-mode delta

Ticks only exist since 0.4.0, so the history grows one session at a time.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

from optionwright.agent.exits import ExitParams, decide_exit


@dataclass(frozen=True)
class ReplayResult:
    position_id: int
    closed: bool
    tick_index: int | None       # tick at which the rules closed (None = still open at the last tick)
    reason: str
    pnl: float                   # P&L at that tick (or at the last tick)
    ticks: int


def replay_position(ticks: list[dict], params: ExitParams, credit: float, contracts: int,
                    expiry: str | None = None) -> ReplayResult:
    """`ticks` chronological rows of position_ticks (dicts). Uses the ticks'
    own price/delta/clock fields; the peak is re-derived as the rules would."""
    if not ticks:
        raise ValueError("no ticks")
    peak = 0.0
    pid = int(ticks[0]["position_id"])
    for i, t in enumerate(ticks):
        price = float(t["price"])
        captured = (credit - price) / credit if credit > 0 else 0.0
        peak = max(peak, captured)
        ts = t["ts"]
        day = (ts if isinstance(ts, str) else ts.isoformat())[:10]
        is_expiry_day = bool(expiry) and day >= str(expiry)[:10]
        d = decide_exit(
            credit, price, is_expiry_day, peak_captured=peak, params=params,
            short_delta=t.get("short_delta"), hours_to_expiry=t.get("hours_to_expiry"),
            hours_to_close=t.get("hours_to_close"), sleeps_tonight=t.get("sleeps_tonight"),
            book_net_delta_pct=None,
        )
        if d.close:
            return ReplayResult(pid, True, i, d.reason, round((credit - price) * 100 * contracts, 2), len(ticks))
    last = float(ticks[-1]["price"])
    return ReplayResult(pid, False, None, "open at last tick", round((credit - last) * 100 * contracts, 2), len(ticks))


def replay_all(params: ExitParams, positions: list[dict], ticks_by_position: dict[int, list[dict]]) -> list[dict]:
    """One row per position that has ticks: simulated vs actual."""
    rows = []
    for pos in positions:
        ticks = ticks_by_position.get(int(pos["id"]))
        if not ticks:
            continue
        r = replay_position(ticks, params, float(pos["credit"]), int(pos["contracts"]), str(pos.get("expiry") or ""))
        rows.append({
            "position_id": r.position_id, "underlying": pos["underlying"], "ticks": r.ticks,
            "sim_closed": r.closed, "sim_tick": r.tick_index, "sim_reason": r.reason, "sim_pnl": r.pnl,
            "actual_status": pos.get("status"), "actual_reason": pos.get("exit_reason"),
            "actual_pnl": pos.get("realized_pnl"),
        })
    return rows


def _main() -> None:
    from optionwright.storage import store

    ap = argparse.ArgumentParser(description="replay the exit rules over recorded ticks")
    for k in ("stop_delta", "stop_mult", "take_profit_far", "take_profit_near", "take_profit_step_hours",
              "trail_activation", "trail_giveback", "flatten_minutes_before_close",
              "overnight_max_short_delta", "overnight_net_delta_pct"):
        ap.add_argument(f"--{k.replace('_', '-')}", type=float)
    ap.add_argument("--overnight-mode", choices=("flat", "delta"))
    args = vars(ap.parse_args())
    from optionwright.policy.params import Params
    base = ExitParams.from_params(Params(store.load_rules()))
    over = {k: v for k, v in args.items() if v is not None}
    params = ExitParams(**{**base.__dict__, **over})
    positions = store.get_positions(500)
    ticks = {int(p["id"]): store.get_ticks(int(p["id"])) for p in positions}
    rows = replay_all(params, positions, ticks)
    print(f"params: {params}")
    print(f"{'pos':>4} {'und':<4} {'ticks':>5} {'sim':<34} {'sim P&L':>9} | {'actual':<34} {'P&L':>9}")
    tot_sim = tot_act = 0.0
    for r in rows:
        tot_sim += r["sim_pnl"]
        tot_act += float(r["actual_pnl"] or 0.0)
        print(f"{r['position_id']:>4} {r['underlying']:<4} {r['ticks']:>5} {r['sim_reason'][:34]:<34} {r['sim_pnl']:>9.2f} | "
              f"{(r['actual_reason'] or r['actual_status'] or '')[:34]:<34} {float(r['actual_pnl'] or 0):>9.2f}")
    print(f"total simulated {tot_sim:+.2f} vs actual {tot_act:+.2f} over {len(rows)} positions")


if __name__ == "__main__":
    _main()
