# optionwright — Strategy Write-up

**Alpaca AI Trading Agents Hackathon · Paper account `PA31YQGU372M` · $100,000 start**

An autonomous options agent built on one rule: **the LLM proposes a direction,
deterministic code decides everything else.** The model reads the market and
picks bullish, bearish, or abstain. Code owns every strike, contract count,
position size, and exit. Nothing the model returns can increase risk.

## Strategy: defined-risk vertical spreads

The agent trades **credit vertical spreads only** on SPY and QQQ (the most liquid
weekly options). A bullish read opens a **bull put spread**; a bearish read opens
a **bear call spread**. In both, the maximum loss is fixed the moment the order
fills: `(width − credit) × 100` per contract. There is no path to an unbounded
loss, and the short-strike whitelist of two underlyings is itself a risk gate.

Each cycle, code reads the option chain, filters for liquidity (open interest,
volume, bid-ask width), selects the short leg near a target delta (~0.30) and a
protective long leg a fixed width away, and computes credit and max loss. The LLM
sees this pre-digested context with every number already resolved, and returns
only a direction and a confidence. Positions are closed at a take-profit fraction
of the credit, a stop multiple, or forced flat before expiration.

## AI logic

- The analyzer's sole output is `{direction, confidence, rationale}`. It never
  does arithmetic and never sizes a trade. The prompt forbids recomputing any
  number it is given.
- The model runs behind an **OpenAI-compatible interface**, so the same code
  drives a local Ollama (qwen3.5:9b) or a hosted endpoint. For Ollama the agent
  calls the native API with reasoning disabled, which cuts latency from ~22s to
  ~0.8s per decision.
- **Fail-closed by design**: a timeout, a malformed response, an unknown
  direction, or an out-of-range confidence all collapse to *abstain*. The agent
  never fabricates a trade from a bad model response. This is verified live: when
  a call exceeded its timeout, the agent abstained rather than trading blind.

## Risk gates

Seven gates run in order before any order. Each can veto or shrink a trade; none
can enlarge one. Position size is not chosen by the model or a fixed constant, it
**emerges from the gates**: the request enters at a ceiling and the max-loss and
daily-budget gates shrink it to fit.

| Gate | Rule |
|------|------|
| Consecutive-loss breaker | N losses in a row → pause |
| Open-positions cap | No more than N concurrent spreads |
| Per-underlying cooldown | No re-entry on a symbol within a window |
| Opening blackout | No trades in the first minutes after the open |
| Macro blackout | No new trades near FOMC / CPI / NFP |
| Max loss per position | ≤ a fixed % of equity |
| Daily premium budget | Capital-at-risk per day is bounded |

## Alpaca infrastructure

- **Trading API + `alpaca-py`** to read the option chain, quotes, and greeks
  (indicative feed) and to read the account.
- **Alpaca CLI** for execution: each spread is placed as a single `mleg`
  (multi-leg) order — the short leg `sell_to_open`, the long leg `buy_to_open`.
- **MCP server** connected to Claude for the conversational demo.
- Paper trading only. The agent refuses to start against a live account.

Every decision, position (with legs, credit, and max loss), and equity snapshot
is persisted to Postgres; the agent exposes Prometheus metrics (cycle outcomes,
LLM confidence and latency, realized P&L, equity) scraped into Grafana.

## How it runs

- **Judges**: `docker compose up` brings up the agent with its own Postgres and
  Redis. Fill `.env` with Alpaca paper keys and an LLM endpoint.
- **Live**: deployed on Kubernetes, dashboard at `optionwright.richardx.dev`,
  metrics in Prometheus/Grafana, an external watchdog alerting on failure.

Test coverage: 56 tests over the deterministic core (spread selection, risk
gates, the multi-leg order builder, the LLM parser, and the full pipeline).

## Results

*(Filled Thursday with the week's real numbers: equity curve, realized P&L, win
rate, and the count of trades each risk gate vetoed.)*
