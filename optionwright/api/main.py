"""
FastAPI app: read-only status endpoints + the dashboard (the demo URL for the
submission). No control endpoints are exposed publicly — the agent runs its own
loop; this surface only reports what it did.

Scaffold: /health is live now so the container has a real readiness signal;
data endpoints and the dashboard land once storage is wired.
"""
from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="optionwright", docs_url="/docs")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "optionwright"}


# TODO: GET /api/status, /api/positions, /api/decisions, /api/equity + dashboard at /
