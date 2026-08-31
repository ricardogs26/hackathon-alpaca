"""
FastAPI service: starts the trading loop on a scheduler and exposes read-only
status + the data the dashboard (the demo URL) renders. No control endpoints are
public — the agent runs itself; this surface only reports what it did.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from optionwright import metrics  # noqa: F401 — import so metrics register at startup
from optionwright.settings import get_settings

logger = logging.getLogger("optionwright.api")
logging.basicConfig(level=get_settings().log_level)

_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    from apscheduler.schedulers.background import BackgroundScheduler

    from optionwright.agent.runner import run_once
    from optionwright.storage import store

    s = get_settings()
    try:
        store.init_schema()
    except Exception as exc:  # DB may still be starting; scheduler retries anyway
        logger.warning("init_schema deferred: %s", exc)

    global _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(run_once, "interval", seconds=s.cycle_seconds, id="cycle",
                       max_instances=1, coalesce=True)
    _scheduler.start()
    logger.info("scheduler started: every %ds over %s", s.cycle_seconds, s.underlyings_list)
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="optionwright", docs_url="/docs", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "optionwright"}


@app.get("/metrics")
def metrics_endpoint():
    from fastapi import Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/status")
def status() -> dict:
    s = get_settings()
    running = bool(_scheduler and _scheduler.running)
    return {
        "service": "optionwright",
        "scheduler_running": running,
        "cycle_seconds": s.cycle_seconds,
        "underlyings": s.underlyings_list,
        "model": s.llm_model,
        "paper": s.alpaca_paper,
    }


# Short TTL cache so a flood of dashboard requests hits memory, not the shared
# Postgres. The dashboard polls every 15s, so 10s of staleness is invisible, and
# a burst of thousands of requests still touches the DB at most once per window.
import time as _time

_cache: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 10.0


def _cached(key: str, fn):
    now = _time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


@app.get("/api/equity")
def equity(limit: int = 500) -> list[dict]:
    from optionwright.storage import store

    limit = max(1, min(limit, 1000))
    return _cached(f"equity:{limit}", lambda: store.get_equity_curve(limit))


@app.get("/api/positions")
def positions(limit: int = 50) -> list[dict]:
    from optionwright.storage import store

    limit = max(1, min(limit, 200))
    return _cached(f"positions:{limit}", lambda: store.get_positions(limit))


@app.get("/api/decisions")
def decisions(limit: int = 30) -> list[dict]:
    from optionwright.storage import store

    limit = max(1, min(limit, 100))
    return _cached(f"decisions:{limit}", lambda: store.get_decisions(limit))


@app.get("/", response_class=None)
def dashboard():
    from fastapi.responses import HTMLResponse

    from optionwright.api.dashboard import DASHBOARD_HTML

    return HTMLResponse(DASHBOARD_HTML)
