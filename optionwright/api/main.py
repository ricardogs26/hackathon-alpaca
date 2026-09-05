"""
FastAPI service: starts the trading loop on a scheduler and exposes read-only
status + the data the dashboard (the demo URL) renders. No control endpoints are
public — the agent runs itself; this surface only reports what it did.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException

from optionwright import __version__, metrics
from optionwright.policy.params import REGISTRY, Params, seed_from_settings  # noqa: F401 — metrics registers at import
from optionwright.settings import get_settings

logger = logging.getLogger("optionwright.api")
logging.basicConfig(level=get_settings().log_level)

_scheduler = None


def build_scheduler(s, run_exits, run_entries, run_learning=None):
    """Two jobs on two clocks: exits every EXIT_CHECK_SECONDS (cheap: one quote
    per open position) and entries every CYCLE_SECONDS (chains + LLM + gates).
    max_instances=1 per job so a slow pass never overlaps itself; the exits
    pass additionally holds a lock (runner) so two never act on one position.
    Built here, unstarted, so the wiring is unit-testable without threads."""
    from apscheduler.schedulers.background import BackgroundScheduler

    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(run_entries, "interval", seconds=s.cycle_seconds, id="entries",
                  max_instances=1, coalesce=True)
    sched.add_job(run_exits, "interval", seconds=s.exit_check_seconds, id="exits",
                  max_instances=1, coalesce=True)
    cron = getattr(s, "learning_cron_utc", "") or ""
    if run_learning is not None and cron.strip():
        from apscheduler.triggers.cron import CronTrigger

        sched.add_job(run_learning, CronTrigger.from_crontab(cron, timezone="UTC"), id="learning",
                      max_instances=1, coalesce=True)
    return sched


@asynccontextmanager
async def lifespan(app: FastAPI):
    from optionwright.agent.learning import run_nightly
    from optionwright.agent.runner import run_entries, run_exits
    from optionwright.storage import store

    s = get_settings()
    try:
        store.init_schema()
        store.seed_rules(seed_from_settings(s))   # env seeds the global scope once; the table rules after
        metrics.set_position_gauges(store.get_positions(200))  # correct at rest, before any cycle
        curve = store.get_equity_curve(1)  # seed equity so it doesn't flash $0 after a restart
        if curve:
            metrics.set_equity(curve[-1]["equity"])
        conf = store.last_opened_confidence()  # so the panel isn't 0.00 until the next trade
        if conf is not None:
            metrics.CONFIDENCE_OPENED.set(conf)
    except Exception as exc:  # DB may still be starting; scheduler retries anyway
        logger.warning("startup DB step deferred: %s", exc)

    global _scheduler
    _scheduler = build_scheduler(s, run_exits, run_entries, lambda: run_nightly())
    _scheduler.start()
    logger.info("scheduler started: entries every %ds, exits every %ds, over %s",
                s.cycle_seconds, s.exit_check_seconds, s.underlyings_list)
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="optionwright", docs_url="/docs", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "optionwright", "version": __version__}


@app.get("/metrics")
def metrics_endpoint():
    from fastapi import Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _market_clock() -> dict:
    """Alpaca's market clock for the header badge. Cached by the caller; any
    failure degrades to None so the status endpoint never depends on Alpaca."""
    try:
        from optionwright.broker import alpaca

        c = alpaca._trading_client().get_clock()
        return {"is_open": bool(c.is_open), "next_open": c.next_open.isoformat(),
                "next_close": c.next_close.isoformat()}
    except Exception as exc:  # keep /api/status up even if the broker is not
        logger.warning("market clock unavailable: %s", exc)
        return {"is_open": None, "next_open": None, "next_close": None}


@app.get("/api/status")
def status() -> dict:
    s = get_settings()
    running = bool(_scheduler and _scheduler.running)
    clock = _cached("clock", _market_clock)
    return {
        "service": "optionwright",
        "version": __version__,
        "scheduler_running": running,
        "market_open": clock["is_open"],
        "next_open": clock["next_open"],
        "next_close": clock["next_close"],
        "cycle_seconds": s.cycle_seconds,
        "exit_check_seconds": s.exit_check_seconds,
        "reconciled": _reconciled(),
        "underlyings": s.underlyings_list,
        "model": s.llm_model,
        "paper": s.alpaca_paper,
    }


# Short TTL cache so a flood of dashboard requests hits memory, not the shared
# Postgres. The dashboard polls every 15s, so 10s of staleness is invisible, and
# a burst of thousands of requests still touches the DB at most once per window.
_cache: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 10.0


def _cached(key: str, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


@app.get("/api/equity")
def equity(limit: int = 500) -> list[dict]:
    from optionwright.storage import store

    limit = max(1, min(limit, 5000))
    return _cached(f"equity:{limit}", lambda: store.get_equity_curve(limit))


@app.get("/api/equity/daily")
def equity_daily(limit: int = 120) -> list[dict]:
    """One equity point per calendar day (the day's last value) — for the chart."""
    from optionwright.storage import store

    limit = max(1, min(limit, 400))
    return _cached(f"equity_daily:{limit}", lambda: store.get_equity_daily(limit))


@app.get("/api/positions/state")
def positions_state() -> list[dict]:
    """Open positions with their latest state tick (delta, sigma distance,
    time left, sleeps, and what the exit rules decided)."""
    from optionwright.storage import store

    return _cached("positions_state", store.open_position_states)


@app.get("/api/rules")
def rules(underlying: str | None = None, group: str | None = None) -> dict:
    """Effective rule parameters (resolved through underlying > group > global >
    default) with the scope each value came from, plus the registry."""
    from optionwright.storage import store

    params = Params(_cached("rules_raw", store.load_rules))
    return {
        "underlying": underlying, "group": group,
        "effective": params.effective(underlying, group),
        "source": {k: params.source(k, underlying, group) for k in REGISTRY},
        "registry": {k: {"type": sp.type, "default": sp.default, "lo": sp.lo, "hi": sp.hi,
                         "choices": list(sp.choices), "section": sp.section, "description": sp.description}
                     for k, sp in REGISTRY.items()},
    }


@app.get("/api/rules/history")
def rules_history(limit: int = 50) -> list[dict]:
    from optionwright.storage import store

    return _cached(f"rules_history:{limit}", lambda: store.rules_history(limit))


@app.patch("/api/rules")
def patch_rule(body: dict, authorization: str | None = Header(default=None)) -> dict:
    """Change one parameter: {scope, key, value, reason, changed_by?}. Requires
    `Authorization: Bearer <RULES_TOKEN>`; with no token configured the endpoint
    is disabled. Every change lands in rules_history."""
    from optionwright.storage import store

    token = get_settings().rules_token
    if not token:
        raise HTTPException(status_code=403, detail="rule edits are disabled (RULES_TOKEN not set)")
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="bad token")
    try:
        out = store.set_rule(
            str(body.get("scope", "global")), str(body.get("key", "")), body.get("value"),
            str(body.get("changed_by") or "api"), str(body.get("reason") or ""),
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    _cache.pop("rules_raw", None)
    return out


def _reconciled() -> bool:
    try:
        from optionwright.agent import runner

        return runner.reconciled()
    except Exception:
        return True


@app.get("/api/reconcile")
def reconcile_status() -> dict:
    """The DB book against the broker's, leg by leg, as the exits pass sees it.
    `mismatches` empty = entries allowed. Read-only; nothing here fixes anything."""
    from optionwright import reconcile
    from optionwright.broker import alpaca
    from optionwright.storage import store

    def compute():
        expected = reconcile.expected_legs(store.live_legs_rows())
        try:
            actual = alpaca.broker_option_positions()
        except Exception as exc:
            return {"ok": _reconciled(), "error": f"broker unreachable: {str(exc)[:120]}", "expected": expected}
        mism = reconcile.diff(expected, actual)
        return {"ok": not mism, "entries_blocked": not _reconciled(), "expected": expected, "broker": actual,
                "mismatches": [{"symbol": m.symbol, "db": m.expected, "broker": m.actual} for m in mism],
                "how_to_clear": "fix the cause at the broker or in the DB; the next exits pass (<=60s) unblocks entries when both books agree"}

    return _cached("reconcile", compute)


def _require_token(authorization: str | None) -> None:
    token = get_settings().rules_token
    if not token:
        raise HTTPException(status_code=403, detail="rule edits are disabled (RULES_TOKEN not set)")
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="bad token")


@app.get("/api/rules/proposals")
def rule_proposals(limit: int = 50) -> list[dict]:
    """What the nightly memory proposed, newest first, with status."""
    from optionwright.storage import store

    return _cached(f"proposals:{limit}", lambda: store.list_proposals(limit))


@app.post("/api/rules/proposals/{proposal_id}/{decision}")
def decide_rule_proposal(proposal_id: int, decision: str, body: dict | None = None,
                         authorization: str | None = Header(default=None)) -> dict:
    """approve = apply the proposed value through the rules table (history says
    'proposal #N approved'); reject = mark it. Token required."""
    from optionwright.storage import store

    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=404, detail="use approve or reject")
    _require_token(authorization)
    try:
        out = store.decide_proposal(proposal_id, decision == "approve", str((body or {}).get("decided_by") or "api"))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    _cache.pop("rules_raw", None)
    _cache.pop("proposals:50", None)
    return out


@app.get("/api/positions")
def positions(limit: int = 50) -> list[dict]:
    from optionwright.storage import store

    limit = max(1, min(limit, 200))
    return _cached(f"positions:{limit}", lambda: store.get_positions(limit))


@app.get("/api/decisions")
def decisions(limit: int = 30, with_context: bool = False) -> list[dict]:
    from optionwright.storage import store

    limit = max(1, min(limit, 100))
    return _cached(f"decisions:{limit}:{with_context}", lambda: store.get_decisions(limit, with_context))


@app.get("/", response_class=None)
def dashboard():
    from fastapi.responses import HTMLResponse

    from optionwright.api.dashboard import dashboard_html

    return HTMLResponse(dashboard_html())
