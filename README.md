# optionwright

**An autonomous options-trading agent for Alpaca where an LLM proposes and
deterministic code decides.** The model may pick a direction — bullish, bearish,
or *abstain* — but every strike, contract count, position size, and exit is
computed and vetoed in code. Losses are bounded by construction: the agent only
trades **defined-risk vertical spreads**, so the maximum loss of any position is
fixed the moment it opens.

Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)
(lablab.ai × Alpaca, Aug–Sep 2026). Runs entirely against Alpaca's **paper
trading** environment — no real capital.

> This is a hackathon project for educational purposes. It is not investment
> advice and must not be pointed at a live-money account.

---

## The idea in one paragraph

An LLM is good at reading context and bad at arithmetic and discipline. So the
agent splits the two. Each cycle, deterministic code reads the option chain,
filters for liquidity, and pre-computes the candidate spreads and every
comparison the decision depends on. The LLM sees that pre-digested context and
returns only a **direction or an abstention** with a confidence. Code then picks
the strikes, sizes the position to a fixed max-loss budget, runs it through an
ordered set of **risk gates** that can only ever shrink or veto the trade, and
executes multi-leg orders through the **Alpaca CLI**. Nothing the LLM says can
increase risk.

## Strategy: defined-risk vertical spreads

- **Bull put spread** when the model is bullish, **bear call spread** when
  bearish — both credit spreads with a capped max loss (`width − credit`).
- Underlyings limited to the most liquid weekly options (SPY, QQQ) — the short
  whitelist *is* a risk gate, not a limitation.
- Strikes chosen by delta; expiries near-weekly; positions closed at a
  take-profit fraction of the credit, a stop multiple, or forced flat before
  expiration.

## Risk gates (code, not prompt)

Evaluated in order; any gate can veto or shrink, none can enlarge:

| Gate | Rule |
|------|------|
| Max loss per position | ≤ a fixed % of equity |
| Open positions cap | No more than N concurrent spreads |
| Daily premium budget | Capital-at-risk deployed per day is bounded |
| Per-underlying cooldown | No re-entry on the same symbol within a window |
| Consecutive-loss breaker | N losing trades in a row → pause |
| Opening blackout | No trades in the first minutes after the open |
| Macro blackout | No new trades near FOMC / CPI / NFP prints |

## Architecture

```
              ┌──────────── every cycle (market hours) ────────────┐
   chain  →   filter liquidity  →  build candidate spreads  →  LLM: direction/abstain
              │                                                     │
              └──── code: pick strikes → size to max-loss → risk gates → CLI execute
                                                                     │
                     Postgres (orders/legs, equity, decisions)  ·  Prometheus  ·  dashboard
```

- **LLM backend is OpenAI-compatible** — point `LLM_BASE_URL` at any endpoint
  (a local Ollama, Featherless, etc.). The model never does math.
- **Execution via the Alpaca CLI** (structured JSON, built for long-running
  agents). An MCP server integration is available for conversational demos.

## Quickstart (judges: no Kubernetes required)

```bash
cp .env.example .env        # fill in your Alpaca paper keys + an LLM endpoint
docker compose up           # brings up the agent + Postgres + Redis
# dashboard on http://localhost:8080
```

See [`docs/writeup.md`](docs/writeup.md) for the one-page write-up (AI logic,
risk gates, Alpaca infrastructure).

## Layout

```
optionwright/
  options/    chain reading, liquidity filter, spread construction  (pure, tested)
  policy/     risk gates + tunable rules registry
  agent/      the cycle loop + the LLM analyzer (proposes only)
  broker/     Alpaca chain data + multi-leg execution via CLI
  storage/    Postgres schema: orders, legs, equity curve, decisions
  api/        FastAPI: read-only status + the dashboard (the demo URL)
tests/        deterministic tests for the options + policy layers
```

## Status

Scaffold. Options-selection and policy logic land with tests before market open;
the agent goes live on the first trading day of the hackathon.
