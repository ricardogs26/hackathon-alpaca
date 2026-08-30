"""
Prometheus metrics for optionwright. Import this module and call the record_*
helpers; the FastAPI app exposes them at /metrics. Kept in one place so the
loop/runner/storage stay readable and the metric names are consistent.

Counters reset on pod restart (that's expected and fine); the equity/confidence
gauges hold the latest value.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ── Cycle outcomes ────────────────────────────────────────────────────────────
CYCLES = Counter(
    "optionwright_cycles_total",
    "Cycle passes by outcome",
    ["result"],  # opened | abstain | vetoed | skipped | error
)
DECISIONS = Counter(
    "optionwright_decisions_total",
    "LLM direction proposals",
    ["direction"],  # bullish | bearish | abstain
)

# ── LLM ───────────────────────────────────────────────────────────────────────
LLM_LATENCY = Histogram(
    "optionwright_llm_latency_seconds",
    "Analyzer LLM call latency",
    buckets=(0.5, 1, 2, 3, 5, 10, 20, 45, 60),
)
LLM_CONFIDENCE = Gauge(
    "optionwright_llm_confidence",
    "Confidence of the most recent proposal",
)

# ── Trading ───────────────────────────────────────────────────────────────────
POSITIONS_OPENED = Counter(
    "optionwright_positions_opened_total",
    "Spreads opened",
    ["underlying", "direction"],
)
REALIZED_PNL = Counter(
    "optionwright_realized_pnl_usd_total",
    "Realized P&L in USD (a Counter can't go negative; net = gain - loss)",
    ["result"],  # gain | loss
)
EQUITY = Gauge(
    "optionwright_equity_usd",
    "Account equity at the last cycle",
)
ERRORS = Counter(
    "optionwright_errors_total",
    "Errors during a cycle",
    ["where"],
)


def record_cycle(result: dict) -> None:
    """Emit cycle + decision metrics from a run_cycle result dict."""
    action = result.get("action", "error")
    CYCLES.labels(result=action).inc()
    direction = result.get("direction")
    if action == "opened" and direction:
        POSITIONS_OPENED.labels(
            underlying=result.get("underlying", "?"), direction=direction
        ).inc()


def record_decision(direction: str) -> None:
    DECISIONS.labels(direction=direction).inc()


def record_llm(latency_s: float, confidence: float) -> None:
    LLM_LATENCY.observe(latency_s)
    LLM_CONFIDENCE.set(confidence)


def record_realized_pnl(net_usd: float) -> None:
    """Split the signed net into gain/loss counters (Counters are non-negative)."""
    if net_usd >= 0:
        REALIZED_PNL.labels(result="gain").inc(net_usd)
    else:
        REALIZED_PNL.labels(result="loss").inc(-net_usd)


def set_equity(equity: float) -> None:
    EQUITY.set(equity)
