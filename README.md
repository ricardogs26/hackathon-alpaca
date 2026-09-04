<div align="center">

# optionwright

**An autonomous options-trading agent for Alpaca where the LLM proposes and deterministic code decides.**

[![License: MIT](https://img.shields.io/badge/License-MIT-0b7a55.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-218%20passing-3dba8c.svg)](tests/)
[![Trading](https://img.shields.io/badge/trading-paper%20only-a4671a.svg)](#safety)

[Live dashboard](https://optionwright.richardx.dev) · [Rules](docs/RULES.md) · [Strategy write-up](docs/writeup.md) · [Metrics](https://optionwright.richardx.dev/metrics)

</div>

---

The model picks a direction (bullish, bearish, or abstain) and nothing else. Every
strike, contract count, position size, and exit is computed and vetoed in code.
The agent trades defined-risk vertical spreads only, so the maximum loss of any
position is fixed the moment it opens. **Nothing the model returns can increase risk.**

Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)
(lablab.ai × Alpaca, 2026).

## Contents

- [Why split the model from the code](#why-split-the-model-from-the-code)
- [Strategy](#strategy)
- [Risk gates](#risk-gates)
- [Exit management](#exit-management)
- [The model](#the-model)
- [Alpaca integration](#alpaca-integration)
- [Quickstart](#quickstart)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Observability](#observability)
- [Tests](#tests)
- [Safety](#safety)

## Why split the model from the code

An LLM reads context well and handles arithmetic and discipline badly, so the
agent takes those two jobs away from it. Each cycle, deterministic code reads the
option chain, filters for liquidity, and pre-builds the candidate spreads with
every number already computed. The model sees that pre-digested context and
returns a direction with a confidence. Code picks the strikes, sizes the position
to a max-loss budget, runs it through the risk gates, and executes. The model
gets to have an opinion; it never decides how much money is on the line.

```
                        every cycle (market hours)
  manage open positions  ->  read chain  ->  build candidate spreads
          |                                          |
  take-profit / stop / expiry            LLM: direction or abstain
          |                                          |
     Alpaca CLI  <-  risk gates  <-  size in code  <-+
          |
  Postgres (orders, equity, decisions)  .  Prometheus  .  live dashboard
```

## Strategy

Defined-risk credit vertical spreads on the most liquid weekly options.

- A **bull put spread** when the model reads bullish, a **bear call spread** when
  bearish, both together (an **iron condor**) when it reads neutral. All are
  credit spreads with a capped max loss of `width − credit`.
- In a **volatile regime** (daily or intraday realized vol above a threshold)
  directional entries are off: only condors, with the short legs farther from
  the money.
- Underlyings in **correlation groups** (`UNDERLYING_GROUPS`): index ETFs
  (SPY, QQQ, IWM) and megacaps (AAPL, NVDA, AMZN, TSLA). Caps and cooldowns
  count per group, because three spreads on SPY, QQQ and IWM are one bet. The
  universe is what the options market actually supports at 2-3 sessions:
  sector ETFs, TLT and GLD were probed and have no tradable spreads there.
- Strikes chosen by delta; width proportional to spot (0.65 %, snapped to the
  strike step); expiries 2 to 3 trading sessions out (weekends and exchange
  holidays skipped). A chain with no tradable spread on either side is skipped
  without calling the model.

## Risk gates

Eighteen checks run in order before any order (`policy/gates.py`; the full list
with parameters is in [`docs/RULES.md`](docs/RULES.md)). Each can veto or shrink
a trade, none can enlarge one. Position size is not a fixed constant or a model
output: it emerges from the gates. The request enters at a ceiling and the
max-loss, daily-budget, direction-share and net-delta gates shrink it to fit.

| Gate | Rule |
|------|------|
| Confidence | A direction below `min_confidence` is vetoed |
| Consecutive-loss breaker | N losing trades in a row pause trading |
| Daily-loss pause | A realized loss of 2 % of equity today pauses new entries |
| Open-positions cap | No more than N concurrent spreads |
| Per-underlying cap | No more than N concurrent spreads on one symbol |
| Duplicate-spread guard | Never reopen the exact same spread while one is open |
| Per-group cap | No more than N concurrent spreads across a correlation group |
| Same short strike | Never a second spread short the same strike on one symbol |
| Cooldown | No re-entry on a symbol, nor on its group, within a window |
| Opening / closing blackout | No entries in the first 30 min or the last 60 min of the session |
| Macro blackout | No new trades near FOMC / CPI / NFP prints |
| Reward/risk floor | A spread paying less than 20 % of its max loss is not opened |
| Max loss per position | At most 1 % of equity |
| Daily budget | Capital-at-risk per day bounded at 5 % of equity |
| Direction share | No side may hold more than 60 % of the open risk |
| Net delta cap | The book's directional exposure stays under 3 % of equity |

All thresholds live in the `rules` table (scopes global / group / underlying,
with history) and can be changed at runtime through `PATCH /api/rules`; the
environment only seeds them.

## Exit management

Open positions are checked every 60 s and closed on a rule, never left to
chance. The thresholds are functions of the position's **state** — the short
leg's delta, time to expiry, time to the close — not fixed multiples of the
credit.

- **Stop by delta**: closes when the short leg's |delta| reaches 0.45, the
  thesis is dead whatever the P&L; a **credit stop** at 1.0× caps the loss and
  is the only stop when the delta is unknown.
- **Take-profit by time**: 50 % of the credit with more than a day to expiry,
  25 % in the last hours (the theta is collected; the gamma left isn't worth it).
- **Trailing take-profit**: arms at 30 % captured and closes below the peak by
  a give-back that scales with the day's realized volatility (7 points on a
  normal day), so a runner that reverses still exits in the green.
- **Overnight rule**: in `flat` mode every position that would sleep is closed
  30 min before the close; in `delta` mode a position may sleep only if it is
  small and the book is balanced.
- **Forced close on the expiration day** to avoid assignment and pin risk.

`python -m optionwright.replay` runs these rules over the recorded ticks and
shows where a candidate set of parameters would have closed each real position.

## Learning

A nightly job measures every closed position from its ticks (favourable and
adverse excursions, the highest short delta, the outcome) and aggregates by
correlation group, regime and days to expiry. With a real sample it proposes a
bounded parameter change with its evidence; the proposal goes to WhatsApp and
is applied only when a person approves it through the API with the rules
token. Details in [`docs/RULES.md`](docs/RULES.md#learning--the-nightly-memory-agentlearningpy).

## The model

- The analyzer's only output is `{direction, confidence, rationale}`, with
  direction one of bullish, bearish, neutral or abstain. It never does
  arithmetic and never sizes a trade.
- **What it sees** (`AGENT_RICH_CONTEXT`): besides the two pre-built spreads, the
  model gets market signals computed in code as categorical flags (5-day trend,
  momentum vs moving average, the 30-minute intraday trend, VWAP position,
  daily and intraday realized-volatility regime), its recent outcomes on
  that underlying (wins by direction), and a summary of the open book (open
  count, today's P&L, losing streak). It reasons over all of that, but every
  number is resolved in code; the model never compares raw numbers. Direction
  concentration is deliberately NOT shown to the model: the gates enforce it.
- A **confidence gate**: a direction below `min_confidence` is vetoed before
  sizing.
- It talks to any **OpenAI-compatible** endpoint. The deployed agent runs
  **Qwen 72B via Featherless** as the primary model, with a **local Ollama as an
  automatic fallback**: if the primary fails or returns empty, the agent decides
  with the local model instead of standing down.
- **Fail-closed**: a timeout, a malformed response, an unknown direction, or an
  out-of-range confidence all collapse to *abstain*, never a fabricated trade.

## Alpaca integration

- **Trading API + `alpaca-py`** for the option chain, quotes, greeks, and account.
- **Alpaca CLI** for execution: each spread is one `mleg` order, the short leg
  `sell_to_open` and the long leg `buy_to_open`; closes reverse it. Every order
  is followed to its end (filled, cancelled, or retried wider), realized P&L
  comes from the fills, and each exits pass reconciles the book in Postgres
  against the broker's positions — a mismatch blocks new entries and alerts.
- That is how the project meets the hackathon's **"MCP or CLI"** core requirement:
  execution goes through Alpaca's official CLI, which Alpaca positions for
  long-running agents, cron jobs and CI where MCP is heavier than needed. This
  agent is exactly that shape: a scheduled loop with no interactive assistant in
  the order path.
- Paper trading only. The agent refuses to start against a live account.

## Quickstart

Judges need no Kubernetes. Docker Compose brings up the agent with its own
Postgres:

```bash
cp .env.example .env     # fill in Alpaca paper keys + an LLM endpoint
docker compose up        # agent + Postgres
# dashboard on http://localhost:8080
```

## Configuration

Set these in `.env` (see [`.env.example`](.env.example)):

| Variable | What it does |
|----------|--------------|
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | Paper account keys (a new $100k account) |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | Primary LLM (Featherless, OpenAI, a local Ollama) |
| `LLM_NATIVE_OLLAMA` | `true` for the native Ollama API, `false` for OpenAI-compatible |
| `FALLBACK_LLM_BASE_URL` / `FALLBACK_LLM_MODEL` | Optional local fallback model |
| `UNDERLYING_GROUPS` | Correlation groups, `name:SYM,SYM;name:SYM` (default index + megacap); `UNDERLYINGS` is the flat fallback |
| `CYCLE_SECONDS` | How often the agent evaluates the market |
| Rule parameters (`MAX_LOSS_PCT`, `STOP_DELTA`, `OVERNIGHT_MODE`, …) | **Seeds** for the `rules` table on first start; afterwards edit via `PATCH /api/rules`. Full list: [`docs/RULES.md`](docs/RULES.md) |
| `RULES_TOKEN` | Bearer token for `PATCH /api/rules` and proposal decisions; empty disables edits |
| `LEARNING_CRON_UTC` / `WHATSAPP_SEND_URL` / `WHATSAPP_TO` | Nightly memory schedule and where its summary is sent (WhatsApp off when empty) |
| `AGENT_RICH_CONTEXT` | Feed the model market signals, recent outcomes and the open book |
| `EXPIRY_MIN_DAYS` / `EXPIRY_MAX_DAYS` | Target expiration window, in trading sessions from today |

## Architecture

```
optionwright/
  options/    chain models + deterministic spread selection   (pure, tested)
  policy/     the risk gates + the rule-parameter registry     (pure, tested)
  agent/      the cycle loop, the LLM analyzer, market perception, exits, the runner
  broker/     Alpaca chain data + multi-leg execution via CLI
  storage/    Postgres schema + reads (orders, equity, decisions)
  api/        FastAPI: read-only endpoints, the rules API + the live dashboard
  replay.py   the exit rules over recorded ticks, simulated vs actual
tests/        218 tests over the deterministic core
scripts/      account, chain, and dry-run probes
k8s/          deployment, ingress, ServiceMonitor, Grafana dashboard
```

## Observability

- **Prometheus** at `/metrics`: cycle outcomes, decisions by direction, LLM
  confidence and latency, open positions, realized P&L, equity, and a live
  per-position evaluation table.
- **Position ticks** in Postgres (`position_ticks`): every ~60s, per open
  position, the short leg's delta and IV, the distance to the short strike in
  expected-move units, time to expiry and to the close, whether it sleeps
  overnight, captured fraction and P&L, next to the decision taken. The
  dataset the next generation of exit rules is designed on.
- **Dashboard** at `/`: equity curve, open positions with their live state
  (delta, σ distance, time left, what the rules say), open and closed spreads, and the decision log (the reason and gate verdict behind
  every cycle).
- **Grafana** dashboard under [`k8s/grafana`](k8s/grafana).

## Tests

```bash
pip install -r requirements.txt
pytest -q          # 218 tests, no network or account needed
```

The deterministic core (spread selection, the risk gates, exit decisions, market
perception,
the multi-leg order builders, the LLM response parser, and the full pipeline) is
tested against synthetic data with no live services.

## Safety

Educational hackathon project. Not investment advice. Paper trading only: the
agent refuses to start against a live-money account.

## License

MIT, see [LICENSE](LICENSE).
