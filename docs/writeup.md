# optionwright — one-page write-up

*(Submission write-up. Skeleton now; filled with the week's real numbers and the
gates that actually vetoed trades before submission on Thursday.)*

## AI logic
- The LLM's only job is to read pre-digested market context and return a
  **direction or an abstention** with a confidence. It never does arithmetic and
  never sizes a trade.
- OpenAI-compatible backend (local Ollama / Featherless), `format=json`, low
  temperature, bounded timeout. A malformed or degenerate response is treated as
  an abstention — the agent never fabricates a trade.
- Every numeric comparison the decision depends on is computed in code and handed
  to the model already resolved; the prompt forbids recomputing them.

## Risk gates
- Trades are **defined-risk vertical spreads only** — max loss is fixed at open.
- An ordered set of gates can only veto or shrink: max-loss-per-position, open-
  positions cap, daily premium budget, per-underlying cooldown, consecutive-loss
  breaker, opening blackout, macro blackout.
- *(Table of gates that actually fired this week goes here.)*

## Alpaca infrastructure
- **Trading API / market data** via alpaca-py for the option chain and quotes.
- **Execution via the Alpaca CLI** — multi-leg spread orders as structured JSON.
- **MCP server** connected to Claude for the conversational demo.
- Paper trading only; dedicated hackathon account, $100,000 starting balance.
- Account ID: *(paste before submission)*

## Results
- *(Equity curve, realized P&L, win rate, and the decisions log go here Thursday.)*
