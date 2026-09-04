# Changelog

Versions follow semver with meaning: a minor bump is a change in what the agent
*does*, a patch is a fix or tuning. The version lives in
`optionwright/__init__.py`; `make release` uses it as the image tag and the git
tag is `v<version>`.

## 0.6.1 — 2026-09-04

- **The loss breaker gets a window** (`breaker_lookback_hours`, 24). The first
  0.6.0 pass vetoed all seven symbols with "circuit breaker: 4 consecutive
  losses" — the streak from 3-Sep — and since nothing could open, no win could
  ever end it: a permanent pause by construction. The streak now counts only
  trades closed within the window, so a bad day pauses the rest of that day.

## 0.6.0 — 2026-09-04 · phase 2: universe and correlation groups

- **Correlation groups.** `UNDERLYING_GROUPS="index:SPY,QQQ,IWM;megacap:AAPL,NVDA,AMZN,TSLA"`
  replaces the flat list (`UNDERLYINGS` still works as a fallback). Caps and
  cooldowns count per group — `max_per_group` (2), `group_cooldown_seconds`
  (1800) — and every rule parameter may carry a `group:<name>` value. New gate:
  **same short strike** (a different long leg or expiry on the same short strike
  is still the same bet; 1-2 Sep had three SPY spreads short the 767 call).
- **The universe, decided by data, not by wish.** The 4-Sep liquidity probe:
  TLT, GLD, XLE and XLF have no tradable spread at 2-3 sessions and none at
  5-8 either (OI 20-144, bid-ask 27-86 %, credits $0.05-0.44); DIA lists no
  expiry in the window. Megacaps do: AAPL (R/R 0.35, OI 2.5k), NVDA (0.42,
  OI 5.3k), AMZN, TSLA. MSFT and META are too thin. So: two groups, seven
  symbols — not the five or six groups the plan hoped for. The screen below
  re-evaluates every symbol every cycle, so a group can be added when its
  options are real.
- **Liquidity screen.** With no tradable spread on either side the cycle
  abstains with reason `illiquid chain` and never calls the LLM. IWM was
  liquid all week: it never traded because the model abstained "on
  concentration" (out of the prompt since 0.5.0).
- **Width ∝ spot.** `width_pct` (0.65 % of spot, snapped to the chain's strike
  step): SPY 770 → 5, IWM 295 → 2, AAPL 320 → 2.5. The fixed 5 was 1.7 % of
  IWM and left its bull put at reward/risk 0.11. And **`width_tolerance`**
  (0.5): the long leg must sit within half a width of the target, otherwise no
  spread — the probe caught the selector jumping to 10 wide on QQQ (715/705)
  and MSFT (510/520) when the target strike was thin.
- Selection knobs (`short_delta`, `width_pct`, `width_tolerance`,
  `min_open_interest`, `max_quote_spread_pct`) join the rules table
  (section `selection`), resolvable per group.
- **Parallel chain prefetch** (`CHAIN_PREFETCH_WORKERS`, 3): seven chains
  fetched serially would not fit the 180 s cycle.
- Dashboard: symbol filters come from the running universe.
- 18 new tests (171 total).

## 0.5.2 — 2026-09-04

- Dashboard: fix a stray fragment left by the 0.5.1 panel removal that broke
  the page script (everything showed "connecting…").

## 0.5.1 — 2026-09-04

- Dashboard: the "Rules" panel is removed — a wall of parameters on the
  trading page had no reader. The values stay available at `GET /api/rules`.

## 0.5.0 — 2026-09-04 · phase 1: risk math and the state-based rules engine

The judged week (31-Aug → 3-Sep) ended at −6.9 %: 17 wins averaging $174
against 5 losses averaging $1,510, every loss a position that slept overnight,
six bear calls on SPY+QQQ as one $18.7k bet. This release changes the rules
that lost that money. Full rule list and order: `docs/RULES.md`.

- **Rule parameters in Postgres** (`rules`, `rules_history`), declared once in
  `policy/params.py` with type, bounds and meaning, resolved with precedence
  underlying > group > global > default. The environment only seeds the global
  scope on first start. `GET /api/rules[?underlying=]`, `GET /api/rules/history`,
  `PATCH /api/rules` (Bearer `RULES_TOKEN`; disabled when unset; reason
  mandatory). `RuleSet` and `ExitParams` are built from the table — the
  strategy is no longer defined in three places.
- **Exits by state** (`agent/exits.py`): stop when the short leg's |delta|
  reaches `stop_delta` (0.45) with the credit stop as a cap at 1.0× (was 2.0×);
  take-profit stepped by time (50 % with >24 h to expiry, 25 % after); the
  trailing unchanged; and the **overnight rule**: `flat` mode closes every
  position that would sleep 30 min before the close (the post-mortem replay:
  +$1,300 vs −$6,518), `delta` mode lets one sleep only if its short delta ≤
  0.35 and the book's net delta ≤ 3 % of equity. Every network input is
  optional: without a greeks snapshot the credit stop still protects.
- **Entries** (`policy/gates.py`): the confidence gate moves into the engine;
  new daily-loss pause (2 %), closing blackout (no entries in the last 60 min),
  reward/risk floor (0.20), **direction share** (no side above 60 % of open
  risk; first position exempt; shrinks before it vetoes) and **net delta cap**
  (3 % of equity, from the latest ticks; skipped while unmeasured). Sizing seeds:
  1 % per position, 5 % per day, 6 open, 2 per underlying.
- **Concentration leaves the prompt.** The model no longer sees direction or
  concentration of the book (it sat on a knife edge: abstain 0.40 / bearish 0.80
  on the same context); the gates enforce it.
- **Replay harness** `python -m optionwright.replay [--stop-delta …]`: the exit
  rules over recorded ticks, simulated vs actual, so a rule is validated on the
  ticks the agent really saw before it touches an order.
- Dashboard: "Open positions · state" (delta, σ, time left, sleeps, what the
  rules say) and "Rules" (effective parameters with their scope).
- 32 new tests (153 total).

## 0.4.0 — 2026-09-04 · phase 0: instrumentation

- **Position ticks.** Every exits pass (~60s) records, per open position, the
  state a premium seller reads before deciding: short-leg delta and IV (one
  snapshot per position), distance to the short strike in units of the expected
  move (`sigma_dist`), hours to expiry and to the close, whether the position
  sleeps through tonight, captured fraction, peak and P&L — next to the
  decision the current rules took. Table `position_ticks`, pure builder in
  `agent/state.py`. Recorded AFTER the money decision and best-effort: a tick
  failure is counted (`optionwright_errors_total{where="tick"}`) and never
  delays a close. This is the ground truth the state-based rules engine
  (phase 1+) will be designed on; nothing in the agent's behaviour changes.
- **Expiry window in trading sessions.** `EXPIRY_MIN_DAYS`/`MAX_DAYS` now count
  exchange sessions (Alpaca calendar), not calendar days. On Thu 3-Sep a 2-3 day
  window landed on Sat/Sun and the agent logged "no expiry" all morning; and
  1 day after Fri 4-Sep is Tue 8-Sep, because Mon 7-Sep is Labor Day. Falls
  back to calendar days if the calendar can't be read. `EXPIRY_MIN_DAYS` goes
  back to 2 in the manifest.
- 19 new tests (121 total): OCC parsing, sigma distance, the tick, the session
  window across a weekend and a holiday, and the runner's tick path.

## 0.3.0 — 2026-09-02 · exits on their own clock

- **Exits every 60s, entries every 180s.** `manage_positions` (take-profit,
  trailing, stop, expiry) moves to its own scheduler job, `EXIT_CHECK_SECONDS`.
  On 1-2 Sep the trailing gave back 10-12 pts against a configured 7 because
  positions were only checked every 180s; checking every 60s keeps it near 7
  and reacts to stops 3x faster. The exits pass is cheap (one quote per open
  position, no chain, no LLM).
- Safety: `max_instances=1` per job, a non-blocking lock so two exits passes
  never overlap, the market clock cached 15s so both jobs share one read.
  `run_once()` keeps the old exits-then-entries shape for dry runs.
- First tests for the runner (16): the money path, the lock, and the
  scheduler wiring.

## 0.2.3 — 2026-09-02 · the stream knows when the market is closed

- The stream's LIVE chip, title badge and footer follow the market clock:
  **LIVE · 180s** with a pulsing dot while the market is open, **PAUSED ·
  market closed** in grey (no pulse, footer says idle) when it is not. The
  chip also shows the real `cycle_seconds` instead of a hard-coded 120s that
  had been stale since the interval moved to 180s.

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
