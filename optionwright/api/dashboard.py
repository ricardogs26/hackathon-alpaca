"""The demo dashboard: a single self-contained HTML page served at /.

Vanilla JS + inline SVG, no external libraries. It polls the read-only /api/*
endpoints and renders the account equity, open positions with their state, the
positions table and the decision stream — the *why* behind every cycle.

The page lives in `static/dashboard.html` (tech-debt 5.2): editing HTML/JS
inside a Python string hid a stray fragment that broke the whole script in
0.5.1. `scripts/check_dashboard_js.py` parses the script with node in `make lint`.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"
DASHBOARD_FILE = STATIC_DIR / "dashboard.html"


@lru_cache(maxsize=1)
def dashboard_html() -> str:
    return DASHBOARD_FILE.read_text(encoding="utf-8")
