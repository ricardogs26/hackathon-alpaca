<div align="center">

# optionwright

**An autonomous options-trading agent for Alpaca where the LLM proposes and deterministic code decides.**

[![License: MIT](https://img.shields.io/badge/License-MIT-0b7a55.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-85%20passing-3dba8c.svg)](tests/)
[![Trading](https://img.shields.io/badge/trading-paper%20only-a4671a.svg)](#safety)

[Live dashboard](https://optionwright.richardx.dev) · [Strategy write-up](docs/writeup.md) · [Metrics](https://optionwright.richardx.dev/metrics)

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
  bearish. Both are credit spreads with a capped max loss of `width − credit`.
- Underlyings limited to a short whitelist (SPY, QQQ, IWM). That whitelist is a
  risk gate, not a limitation.
- Strikes chosen by delta, expiries near-weekly (2 to 3 DTE).

## Risk gates

Ten risk checks run in order before any order. Each can veto or shrink a trade, none
can enlarge one. Position size is not a fixed constant or a model output: it
emerges from the gates. The request enters at a ceiling and the max-loss and
daily-budget gates shrink it to fit.

| Gate | Rule |
|------|------|
| Consecutive-loss breaker | N losing trades in a row pauses trading |
| Open-positions cap | No more than N concurrent spreads |
| Per-underlying cap | No more than N concurrent spreads on one symbol (anti-concentration) |
| Duplicate-spread guard | Never reopen the exact same spread (same legs) while one is already open |
| Per-underlying cooldown | No re-entry on a symbol within a window |
| Opening blackout | No trades in the first minutes after the open |
| Macro blackout | No new trades near FOMC / CPI / NFP prints |
| Max loss per position | At most a fixed % of equity |
| Daily premium budget | Capital-at-risk per day is bounded |
| Confidence gate | A direction below `MIN_CONFIDENCE` is vetoed before sizing |

## Exit management

Open positions are checked every cycle and closed on a rule, never left to chance.

- **Trailing take-profit**: arms once a set fraction of the credit is captured
  and closes if the captured fraction falls a set number of points below its
  peak, so a runner that reverses still exits in the green.
- **Hard take-profit** ceiling: bank automatically at a set fraction of the credit.
- **Stop-loss** once the loss reaches a multiple of the credit.
- **Forced close on the expiration day** to avoid assignment and pin risk.

Between those bounds the agent holds, so time decay works in its favor.

## The model

- The analyzer's only output is `{direction, confidence, rationale}`. It never
  does arithmetic and never sizes a trade.
- **What it sees** (`AGENT_RICH_CONTEXT`): besides the two pre-built spreads, the
  model gets market signals computed in code as categorical flags (5-day trend,
  momentum vs moving average, realized-volatility regime), its recent outcomes on
  that underlying (wins by direction), and a summary of the open book (positions
  per underlying and direction, concentration, today's P&L). It reasons over all
  of that, but every number is resolved in code; the model never compares raw
  numbers.
- A **confidence gate**: a direction below `MIN_CONFIDENCE` is vetoed before
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
  `sell_to_open` and the long leg `buy_to_open`; closes reverse it.
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
| `UNDERLYINGS` | Comma-separated tickers (default `SPY,QQQ,IWM`) |
| `CYCLE_SECONDS` | How often the agent evaluates the market |
| `MAX_OPEN_POSITIONS` / `MAX_PER_UNDERLYING` | Position caps (global and per symbol) |
| `MAX_LOSS_PCT` / `DAILY_BUDGET_PCT` | Capital-at-risk limits |
| `HARD_TAKE_PROFIT` / `TRAIL_ACTIVATION` / `TRAIL_GIVEBACK` / `STOP_LOSS_MULT` | Exit thresholds (see Exit management) |
| `MIN_CONFIDENCE` | Minimum model confidence to open a trade |
| `AGENT_RICH_CONTEXT` | Feed the model market signals, recent outcomes and the open book |
| `EXPIRY_MIN_DAYS` / `EXPIRY_MAX_DAYS` | Target days-to-expiration window |

## Architecture

```
optionwright/
  options/    chain models + deterministic spread selection   (pure, tested)
  policy/     the risk gates                                   (pure, tested)
  agent/      the cycle loop, the LLM analyzer, market perception, exits, the runner
  broker/     Alpaca chain data + multi-leg execution via CLI
  storage/    Postgres schema + reads (orders, equity, decisions)
  api/        FastAPI: read-only endpoints + the live dashboard
tests/        85 tests over the deterministic core
scripts/      account, chain, and dry-run probes
k8s/          deployment, ingress, ServiceMonitor, Grafana dashboard
```

## Observability

- **Prometheus** at `/metrics`: cycle outcomes, decisions by direction, LLM
  confidence and latency, open positions, realized P&L, equity, and a live
  per-position evaluation table.
- **Dashboard** at `/`: equity curve, open and closed spreads, and the decision
  log (the reason and gate verdict behind every cycle).
- **Grafana** dashboard under [`k8s/grafana`](k8s/grafana).

## Tests

```bash
pip install -r requirements.txt
pytest -q          # 85 tests, no network or account needed
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
