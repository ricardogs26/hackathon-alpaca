# Post #5 — final results (LinkedIn, draft 2026-09-05)

The Alpaca hackathon closed yesterday. My agent finished the week at $92,568. Down 7.4%.

On Tuesday night it was up 1.5% with fifteen wins in a row. I wrote then that the streak wouldn't hold. It didn't. Thursday took $8,290 in one session.

Here is what the post-mortem found, in order of weight.

The exits were asymmetric by design. Take profit at 40% of the credit, stop at 200%. That math needs an 83% hit rate to break even. The agent hit 74%. The losses averaged about $1,500 against wins of $174.

Six bear call spreads on SPY and QQQ, all short the same side, added up to 18.7% of the account riding on one idea: the index doesn't climb. On Thursday it climbed 1%.

Every losing position slept overnight. The eleven trades that opened and closed the same day went 11 for 11, +$1,667. I replayed the week from Prometheus: closing everything at 15:30 ET each day would have ended +$1,300 instead of -$6,518.

The LLM was never the problem. The 72B model said bullish when the signals said bullish. The signals ran on daily bars and saw the pullback, not the turn.

So on Friday I rebuilt the risk layer around those findings. Positions now carry a state vector on every tick: short-strike delta, distance in sigmas, hours to close, whether it sleeps. Stops fire on delta, not on a multiple of the credit. Take profit steps down with time. A nightly rule flattens or trims the book before the close. Direction and correlation group caps live in code, outside the prompt. Signals run on 30-minute bars. Orders have a real lifecycle with fills and a reconciler against the broker. A nightly job measures excursions and proposes parameter changes I approve by hand. 79 tests became 270.

None of it existed a week ago. I built the first version to compete. I built the second because the first lost money on paper and told me why in numbers.

Paper only, still. Repo (MIT) and dashboard in the comments.

Built with Alpaca, lablab.ai, and Featherless AI

#AITradingAgents #AlpacaMarkets #OptionsTrading #BuildInPublic #AlgoTrading #LLM

---

Comment to post right after:
Repo: https://github.com/ricardogs26/hackathon-alpaca
Dashboard: https://optionwright.richardx.dev
Post-mortem numbers came from the agent's own Postgres and Prometheus.

---

# LinkedIn Experience entry (draft)

Title: Participant, Alpaca AI Trading Agents Hackathon
Organization: lablab.ai
Employment type: Self-employed / Freelance (or leave blank)
Dates: Aug 2026 – Sep 2026
Location: Remote
Skills: Python, FastAPI, PostgreSQL, Kubernetes, Prometheus, LLM Agents, Options Trading

Description:
Built OptionWright, an autonomous options trading agent on Alpaca paper trading, solo, in one week (track: Options Alpha Agents, 421 projects).

Design: the LLM proposes a direction (bullish, bearish, neutral, abstain); code chooses strikes, sizes the position to a fixed max loss, applies risk gates and sends multi-leg orders through the Alpaca CLI. Only defined-risk vertical spreads and iron condors. Qwen 72B on Featherless as primary model, local 9B on my own GPU as fallback.

Results: 23 positions, 74% hit rate, finished the week at -7.4%. The post-mortem showed asymmetric exits, same-side concentration and overnight gap risk. Rebuilt the risk engine from that data: per-tick position state, delta-based stops, time-tiered take profit, nightly flattening rule, direction and correlation caps, order lifecycle with broker reconciliation, nightly statistical proposals with human approval. Test suite grew from 79 to 270.

Runs on my home Kubernetes cluster with Grafana/Prometheus observability and WhatsApp alerts.

Repo (MIT): https://github.com/ricardogs26/hackathon-alpaca
Live dashboard: https://optionwright.richardx.dev
