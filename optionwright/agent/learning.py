"""
Statistical memory — phase 4. The seller's "experience", made explicit.

Every night the job reads the closed positions and their ticks, measures for
each one what an experienced seller remembers (how far it went in our favour
before it turned — MFE —, how far against — MAE —, how close the short strike
got — max delta —, how it ended), aggregates by bucket (correlation group ×
regime at open × days to expiry) and, only with a real sample, PROPOSES a
bounded parameter change with its evidence. Nothing is applied here: a
proposal waits for a human (API with the rules token; WhatsApp carries the
summary). The pure parts (stats, buckets, proposal rules) are unit-tested; the
job wires them to the store and the notifier.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

from optionwright.policy.params import REGISTRY

logger = logging.getLogger("optionwright.learning")

MIN_SAMPLE = 20            # a rule of thumb from fewer trades is superstition
MAX_STEP = 0.25            # a proposal moves a value at most 25% (and stays inside the registry bounds)
PROPOSAL_TTL_DAYS = 3


@dataclass(frozen=True)
class PositionStats:
    position_id: int
    underlying: str
    group: str
    regime: str
    dte_bucket: str
    result: str              # win | loss | flat
    pnl: float
    credit: float
    mfe: float               # max captured fraction seen in the ticks (>= 0)
    mae: float               # min captured fraction seen (<= 0 when it was ever losing)
    max_short_delta: float | None
    exit_reason: str
    hold_hours: float


def dte_bucket(ts_open: datetime, expiry: str) -> str:
    days = (date.fromisoformat(str(expiry)[:10]) - ts_open.date()).days
    if days <= 1:
        return "0-1"
    if days <= 3:
        return "2-3"
    return "4+"


def position_stats(pos: dict, ticks: list[dict], group: str) -> PositionStats | None:
    """None when the position has no ticks (nothing was observed)."""
    if not ticks:
        return None
    caps = [float(t["captured"]) for t in ticks]
    deltas = [float(t["short_delta"]) for t in ticks if t.get("short_delta") is not None]
    pnl = float(pos.get("realized_pnl") or 0.0)
    ts_open, ts_close = pos["ts_open"], pos.get("ts_close")
    if isinstance(ts_open, str):
        ts_open = datetime.fromisoformat(ts_open)
    if isinstance(ts_close, str):
        ts_close = datetime.fromisoformat(ts_close)
    hold = ((ts_close or datetime.now(timezone.utc)) - ts_open).total_seconds() / 3600.0
    return PositionStats(
        position_id=int(pos["id"]), underlying=pos["underlying"], group=group,
        regime=str(pos.get("regime") or "desconocido"), dte_bucket=dte_bucket(ts_open, pos["expiry"]),
        result="win" if pnl > 0 else "loss" if pnl < 0 else "flat", pnl=pnl, credit=float(pos["credit"]),
        mfe=round(max(0.0, max(caps)), 4), mae=round(min(0.0, min(caps)), 4),
        max_short_delta=round(max(deltas), 4) if deltas else None,
        exit_reason=str(pos.get("exit_reason") or ""), hold_hours=round(hold, 2),
    )


def bucket_key(s: PositionStats) -> tuple[str, str, str]:
    return (s.group, s.regime, s.dte_bucket)


def aggregate(stats: list[PositionStats]) -> dict[tuple, dict]:
    """Per bucket: the numbers a seller keeps in his head."""
    out: dict[tuple, dict] = {}
    by: dict[tuple, list[PositionStats]] = {}
    for s in stats:
        by.setdefault(bucket_key(s), []).append(s)
    for key, rows in by.items():
        wins = [r for r in rows if r.result == "win"]
        losses = [r for r in rows if r.result == "loss"]
        out[key] = {
            "n": len(rows), "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(rows), 3) if rows else None,
            "avg_win": round(statistics.mean(r.pnl for r in wins), 2) if wins else None,
            "avg_loss": round(statistics.mean(r.pnl for r in losses), 2) if losses else None,
            "median_mfe_losers": round(statistics.median(r.mfe for r in losses), 3) if losses else None,
            "median_mfe_winners": round(statistics.median(r.mfe for r in wins), 3) if wins else None,
            "median_mae_winners": round(statistics.median(r.mae for r in wins), 3) if wins else None,
            "delta_stops": sum(1 for r in losses if r.exit_reason.startswith("stop (short delta")),
            "avg_loss_vs_credit": round(statistics.mean(-r.pnl / (r.credit * 100) for r in losses if r.credit > 0) / 1.0, 3) if losses else None,
            "overnight_losses": sum(1 for r in losses if r.hold_hours > 8),
        }
    return out


@dataclass(frozen=True)
class Proposal:
    scope: str
    key: str
    current: float
    proposed: float
    sample_n: int
    evidence: str

    def as_row(self) -> dict:
        return asdict(self)


def _bounded(key: str, current: float, target: float) -> float:
    """Never more than MAX_STEP away from the current value, never outside the registry."""
    spec = REGISTRY[key]
    lo = max(spec.lo if spec.lo is not None else -1e9, current * (1 - MAX_STEP))
    hi = min(spec.hi if spec.hi is not None else 1e9, current * (1 + MAX_STEP))
    return round(max(lo, min(hi, target)), 4)


def propose(agg: dict[tuple, dict], current: dict[str, float], scope_of_group) -> list[Proposal]:
    """
    Two rules of thumb, each only with MIN_SAMPLE trades in the bucket:

    A. take_profit_far — if half of the LOSERS had already captured a good part
       of the credit before turning (median MFE of losers ≥ 0.30) while the
       winners' median peak is not far above the current target, the target is
       too greedy for this bucket: propose it at the losers' median MFE.
    B. stop_delta — if the delta stop is firing late (losses average ≥ 0.8x
       the credit, i.e. near the credit cap) propose a tighter delta stop.

    `current` = the effective values for the bucket's scope; `scope_of_group`
    maps a group name to its rules scope.
    """
    out: list[Proposal] = []
    for (group, regime, dte), a in agg.items():
        if a["n"] < MIN_SAMPLE:
            continue
        scope = scope_of_group(group)
        tp = float(current["take_profit_far"])
        if a["losses"] >= 5 and a["median_mfe_losers"] is not None and a["median_mfe_losers"] >= 0.30 \
                and (a["median_mfe_winners"] is None or a["median_mfe_winners"] <= tp + 0.05):
            target = _bounded("take_profit_far", tp, a["median_mfe_losers"])
            if target < tp:
                out.append(Proposal(scope, "take_profit_far", tp, target, a["n"],
                                    f"{group}/{regime}/{dte} DTE: of {a['losses']} losers half had captured "
                                    f"≥{a['median_mfe_losers']:.0%} before turning; winners' median peak {a['median_mfe_winners']}"))
        sd = float(current["stop_delta"])
        if a["losses"] >= 5 and a["delta_stops"] >= 3 and a["avg_loss_vs_credit"] is not None and a["avg_loss_vs_credit"] >= 0.8:
            target = _bounded("stop_delta", sd, sd - 0.05)
            if target < sd:
                out.append(Proposal(scope, "stop_delta", sd, target, a["n"],
                                    f"{group}/{regime}/{dte} DTE: {a['delta_stops']} delta stops, losses average "
                                    f"{a['avg_loss_vs_credit']:.0%} of the credit (the stop fires late)"))
    return out


def summary_text(agg: dict[tuple, dict], proposals: list[Proposal], pending_ids: list[int], today: date) -> str:
    """The WhatsApp message: what was measured, what is proposed, how to answer."""
    lines = [f"📈 optionwright · memoria nocturna {today.isoformat()}"]
    if not agg:
        lines.append("Sin posiciones cerradas con ticks todavía.")
    for (g, r, d), a in sorted(agg.items()):
        wr = f"{a['win_rate']:.0%}" if a["win_rate"] is not None else "—"
        lines.append(f"• {g}/{r}/{d} DTE: n={a['n']} acierto {wr}, gana {a['avg_win']} / pierde {a['avg_loss']}, "
                     f"MFE perdedores {a['median_mfe_losers']}, stops por delta {a['delta_stops']}, pérdidas nocturnas {a['overnight_losses']}")
    if proposals:
        lines.append("Propuestas (nada se aplica sin tu firma):")
        for pid, p in zip(pending_ids, proposals):
            lines.append(f"  #{pid} {p.key}@{p.scope}: {p.current} → {p.proposed} (n={p.sample_n}). {p.evidence}")
        lines.append("Aprobar: POST /api/rules/proposals/<id>/approve con el token · rechazar: /reject")
    else:
        lines.append("Sin propuestas: ningún bucket alcanza la muestra mínima o los números no lo justifican.")
    return "\n".join(lines)


def run_nightly(*, dry_run: bool = False, today: date | None = None) -> dict:
    """Wire: store → stats → proposals → store → WhatsApp. Returns the report."""
    from optionwright.agent import notify
    from optionwright.policy.params import Params, group_scope
    from optionwright.settings import get_settings
    from optionwright.storage import store

    s = get_settings()
    today = today or datetime.now(timezone.utc).date()
    uni = s.universe
    rows = store.closed_positions(days=45)
    stats = []
    for pos in rows:
        st = position_stats(pos, store.get_ticks(int(pos["id"])), uni.group_of(pos["underlying"]) or pos["underlying"].lower())
        if st:
            stats.append(st)
    agg = aggregate(stats)
    params = Params(store.load_rules())
    proposals: list[Proposal] = []
    for (group, regime, dte), a in agg.items():
        sub = propose({(group, regime, dte): a}, params.effective(group=group), group_scope)
        proposals.extend(sub)
    # one open proposal per (scope, key); expire stale ones first
    ids: list[int] = []
    if not dry_run:
        store.expire_proposals(PROPOSAL_TTL_DAYS)
        pending = {(p["scope"], p["key"]) for p in store.pending_proposals()}
        kept = []
        for p in proposals:
            if (p.scope, p.key) in pending:
                continue
            ids.append(store.add_proposal(p))
            kept.append(p)
        proposals = kept
    text = summary_text(agg, proposals, ids or [0] * len(proposals), today)
    if not dry_run:
        notify.send_whatsapp(text)
        try:
            store.purge(s.decisions_retention_days, s.ticks_retention_days)
        except Exception as exc:  # housekeeping never breaks the report
            logger.warning("purge failed: %s", exc)
    logger.info("nightly learning: %d positions, %d buckets, %d proposals", len(stats), len(agg), len(proposals))
    return {"positions": len(stats), "buckets": {"/".join(k): v for k, v in agg.items()},
            "proposals": [p.as_row() | {"id": i} for p, i in zip(proposals, ids or [None] * len(proposals))], "text": text}


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="nightly statistical memory: measure, propose, notify")
    ap.add_argument("--dry-run", action="store_true", help="measure and print; store nothing, send nothing")
    args = ap.parse_args()
    rep = run_nightly(dry_run=args.dry_run)
    print(rep["text"])
    print(json.dumps({k: v for k, v in rep.items() if k != "text"}, indent=1, default=str))
