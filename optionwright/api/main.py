"""
FastAPI service: starts the trading loop on a scheduler and exposes read-only
status + the data the dashboard (the demo URL) renders. No control endpoints are
public — the agent runs itself; this surface only reports what it did.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

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
