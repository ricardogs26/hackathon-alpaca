# optionwright

**An autonomous options-trading agent for Alpaca where the LLM proposes and
deterministic code decides.** The model picks a direction (bullish, bearish, or
abstain) and nothing else. Every strike, contract count, position size, and exit
is computed and vetoed in code. The agent trades defined-risk vertical spreads
only, so the maximum loss of any position is fixed the moment it opens. Nothing
the model returns can increase risk.

Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)
(lablab.ai × Alpaca, 2026). Paper trading only, no real capital.

| | |
|---|---|
| **Live dashboard** | https://optionwright.richardx.dev |
| **Strategy write-up** | [`docs/writeup.md`](docs/writeup.md) |
| **Metrics** | `/metrics` (Prometheus) |
| **License** | MIT |

> Educational hackathon project. Not investment advice. Must not be pointed at a
> live-money account.

---

## Why split the model from the code

An LLM reads context well and handles arithmetic and discipline badly, so the
agent takes those two jobs away from it. Each cycle, deterministic code reads the
option chain, filters for liquidity, and pre-builds the candidate spreads with
every number already computed. The model sees that pre-digested context and
returns a direction with a confidence. Code picks the strikes, sizes the position
to a max-loss budget, runs it through the risk gates, and executes. The model
gets to have an opinion; it never decides how much money is on the line.

## Strategy: defined-risk vertical spreads

- A **bull put spread** when the model reads bullish, a **bear call spread** when
  bearish. Both are credit spreads with a capped max loss of `width − credit`.
- Underlyings are limited to the most liquid weekly options (SPY, QQQ). That
  short whitelist is a risk gate, not a limitation.
- Strikes are chosen by delta, expiries near-weekly. Positions close at a
  take-profit fraction of the credit, a stop multiple, or flat before expiration.

## Risk gates

Seven gates run in order before any order. Each can veto or shrink a trade, none
can enlarge one. Position size is not a fixed constant or a model output: it
emerges from the gates. The request enters at a ceiling and the max-loss and
daily-budget gates shrink it to fit.

| Gate | Rule |
|------|------|
| Consecutive-loss breaker | N losing trades in a row pauses trading |
| Open-positions cap | No more than N concurrent spreads |
| Per-underlying cooldown | No re-entry on a symbol within a window |
| Opening blackout | No trades in the first minutes after the open |
| Macro blackout | No new trades near FOMC / CPI / NFP prints |
| Max loss per position | At most a fixed % of equity |
| Daily premium budget | Capital-at-risk per day is bounded |

## Architecture

```
              every cycle (market hours)
  chain  ->  filter liquidity  ->  build candidate spreads  ->  LLM: direction / abstain
             |                                                   |
             +---- code: pick strikes -> size to max-loss -> risk gates -> Alpaca CLI
                                                                 |
              Postgres (orders/legs, equity, decisions)  .  Prometheus  .  dashboard
```

## The model

- The analyzer's only output is `{direction, confidence, rationale}`. It never
  does arithmetic and never sizes a trade.
- It talks to any **OpenAI-compatible** endpoint. The deployed agent runs
  **Qwen 72B via Featherless** as the primary model, with a **local Ollama
  (qwen3.5:9b) as an automatic fallback**: if the primary fails or returns empty,
  the agent decides with the local model instead of standing down.
- **Fail-closed**: a timeout, a malformed response, an unknown direction, or an
  out-of-range confidence all collapse to *abstain*, never a fabricated trade.

## Alpaca integration

- **Trading API + `alpaca-py`** for the option chain, quotes, greeks, and account.
- **Alpaca CLI** for execution: each spread is one `mleg` order, the short leg
  `sell_to_open` and the long leg `buy_to_open`.
- **MCP server** for the conversational demo.
- Paper trading only. The agent refuses to start against a live account.

## Quickstart (judges: no Kubernetes required)

```bash
cp .env.example .env     # fill in Alpaca paper keys + an LLM endpoint
docker compose up        # agent + Postgres + Redis
# dashboard on http://localhost:8080
```

## Configuration

Set these in `.env` (see [`.env.example`](.env.example)):

| Variable | What it does |
|----------|--------------|
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | Paper account keys (a new $100k account) |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | Primary LLM (e.g. Featherless, OpenAI, a local Ollama) |
| `LLM_NATIVE_OLLAMA` | `true` to use the native Ollama API, `false` for OpenAI-compatible |
| `FALLBACK_LLM_BASE_URL` / `FALLBACK_LLM_MODEL` | Optional local fallback model |
| `UNDERLYINGS` | Comma-separated tickers (default `SPY,QQQ`) |
| `CYCLE_SECONDS` | How often the agent evaluates the market |

## Project layout

```
optionwright/
  options/    chain models + deterministic spread selection   (pure, tested)
  policy/     the seven risk gates                             (pure, tested)
  agent/      the cycle loop, the LLM analyzer, the runner
  broker/     Alpaca chain data + multi-leg execution via CLI
  storage/    Postgres schema + reads (orders, equity, decisions)
  api/        FastAPI: read-only endpoints + the dashboard
tests/        54 tests over the deterministic core
scripts/      account, chain, and dry-run probes
k8s/          deployment, ingress, ServiceMonitor, Grafana dashboard
```

## Observability

- **Prometheus** metrics at `/metrics`: cycle outcomes, decisions by direction,
  LLM confidence and latency, positions opened, realized P&L, equity.
- **Dashboard** at `/`: equity curve, open and closed spreads, and the decision
  log (the reason and gate verdict behind every cycle).
- **Grafana** dashboard under [`k8s/grafana`](k8s/grafana).

## Tests

```bash
pip install -r requirements.txt
pytest -q          # 54 tests, no network or account needed
```

The deterministic core (spread selection, risk gates, the multi-leg order
builder, the LLM response parser, and the full pipeline) is tested against
synthetic data with no live services.

## License

MIT, see [LICENSE](LICENSE).
