# Changelog

Versions follow semver with meaning: a minor bump is a change in what the agent
*does*, a patch is a fix or tuning. The version lives in
`optionwright/__init__.py`; `make release` uses it as the image tag and the git
tag is `v<version>`.

## 0.2.2 — 2026-09-02 · clearer stream filters, market badge

- Stream filters renamed **Entries / Exits** (they are events in time, not a
  status list — "Opened" read as "currently open"), plus a new **Open now**
  filter for the entries whose position is still open. Subtitle: "every action
  the agent took, in order".
- Header badge: **market open** (green dot) / **market closed** (grey dot) from
  Alpaca's clock via `/api/status` (cached 10s, degrades to no badge if the
  broker is unreachable); hover shows next open/close in ET.

## 0.2.1 — 2026-09-02 · the decision stream keeps every open

- **Fix:** the "Opened" filter came up empty. Open events were built from the
  100-row decision log, which is ~98% abstentions at 3 underlyings × every 3 min,
  so any open fell out of view within ~2 hours while closes (from positions) kept
  the whole history. Opens now come from positions too; the decision log only
  feeds vetoes and abstentions. `get_positions` joins the opening decision's
  confidence so the line still reads `bearish 0.75 · opened 22× spread`.
- Footer says "in view" instead of a misleading "today".
- Favicon: the credit-spread payoff curve (flat, rising, flat: capped loss,
  capped gain) inlined as an SVG data URI, no external asset.

## 0.2.0 — 2026-09-01 · the agent perceives, remembers, and only trades when convinced

- **Rich context for the model** (`AGENT_RICH_CONTEXT`): market signals computed
  in code as categorical flags (5-day trend, momentum, volatility regime), recent
  outcomes per underlying, and a summary of the open book. Fail-safe: any
  provider that errors degrades to `{}`, never breaks a cycle.
- **Confidence gate** (`MIN_CONFIDENCE`): a direction below the floor is vetoed
  before sizing. Confidence was previously logged but never acted on.
- **Duplicate-spread guard**: the exact same spread (same legs) cannot be
  reopened while one is open. The time-based cooldown had let SPY 767/772 open
  twice in 53 minutes.
- **Option-chain cache scoped to one cycle** (`new_cycle()` at the start of each
  pass): puts and calls of an underlying share a single fetch, cutting 6 chain
  downloads per cycle to 3. Daily bars cached 30 min. A full pass drops from
  ~127s to ~90s.
- **Cycle interval 120s → 180s**: a pass took longer than the interval, so the
  scheduler skipped every other tick.
- **Trailing take-profit retuned** (arms at 30%, gives back 7 pts; was 20/10):
  trailing exits were averaging $114 vs $212 for hard take-profit exits.
- **Dashboard**: terminal-style Live Decision Stream (opens, closes with P&L,
  vetoes, abstentions; filters incl. IWM); equity chart at 75% width with a
  5D / 30D / All selector, per-trading-day bands, intraday detail on the week
  view and one point per day beyond (`/api/equity/daily`); "Open now" and
  "confidence on trades" panels in Grafana.
- **Versioning introduced**: `__version__`, `/health` and `/api/status` report
  it, the dashboard footer shows it, `make release` builds/pushes/deploys from
  it, CI runs `ruff` + `pytest` on every push and PR.
- Removed: unused Redis wiring, an orphan `positions_opened_total` counter, dead
  dashboard code, and a README/write-up claim about an MCP server that never
  existed.

## 0.1.x — 2026-08-29 → 2026-09-01 · foundation and first live week

- 0.1.0 — option-chain reader, multi-leg execution via the Alpaca CLI, risk
  gates with injected state, Postgres storage, OpenAI-compatible analyzer,
  end-to-end pipeline with injected dependencies, scheduler + k8s manifests.
- 0.1.1–0.1.6 — Prometheus metrics, ServiceMonitor, Grafana dashboard, visual
  dashboard at `/`, pre-open calibration (volume as a soft gate, opening
  blackout), fixes: missing `pytz` in the slim image, naive calendar datetimes.
- 0.1.7–0.1.8 — Featherless Qwen 72B as primary with local Ollama fallback;
  endpoint TTL cache and ingress rate limits.
- 0.1.9–0.1.12 — exit management (take-profit, stop, forced close at expiry),
  position gauges sourced from Postgres, equity seeded at startup, cycle 300s → 120s.
- 0.1.13–0.1.17 — contest deployment tuning, realized P&L from Postgres (a
  counter had double-counted across a rolling update; strategy set to Recreate),
  per-underlying cap, trailing take-profit, IWM added, DTE 3–5.
- 0.1.18–0.1.30 — judged-week sprint tuning (DTE 2–3, take-profit 40%, 8 slots /
  3 per underlying), Live Decision Stream, and the agentic upgrade that became
  0.2.0.
