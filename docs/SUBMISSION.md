# Submission — lablab.ai form (copy-paste ready)

Deadline: **Fri 4 Sep, 9:00 CST**. Submit Thursday night.

## Basic information

**Project title**
> optionwright

**Short description**
> An autonomous options-trading agent on Alpaca where an LLM proposes a direction
> and deterministic code decides every strike, size, and exit. Defined-risk
> spreads only, so max loss is fixed before the order fills.

**Long description**
> optionwright is an autonomous AI options agent built on one rule: the LLM
> proposes, the code decides. Each cycle, deterministic code reads the SPY/QQQ/IWM
> option chain, filters for liquidity, and pre-builds two defined-risk credit
> spreads (a bull put and a bear call) with every number already computed. The
> LLM sees that pre-digested context and returns only a direction — bullish,
> bearish, or abstain — with a confidence. Code then picks the strikes, sizes the
> position, and runs it through eighteen ordered risk checks that can only veto or
> shrink a trade, never enlarge it. Sizing emerges from the gates, not from the
> model. Execution goes through the Alpaca CLI as a single multi-leg order (the
> MCP-or-CLI core requirement, met via the CLI).
>
> Because it only trades vertical credit spreads, the maximum loss of every
> position is fixed the moment it opens. The model is fail-closed: a timeout or a
> malformed response collapses to abstain, never a fabricated trade. It runs Qwen 72B on Featherless through an OpenAI-compatible
> interface with an automatic local Ollama fallback (portable to any endpoint).
> The model also sees market signals, its recent outcomes and the open book, all
> resolved in code, and a confidence gate vetoes low-conviction calls. It persists every decision to Postgres, and exposes
> Prometheus metrics scraped into Grafana. Judges can run the whole stack with a
> single `docker compose up`.

**Technology tags**
> Alpaca, Options Trading, AI Agent, LLM, Python, FastAPI, PostgreSQL, Prometheus,
> Featherless, Ollama, Docker, Kubernetes

**Category tags**
> Autonomous Agents, Fintech, Algorithmic Trading

## Cover image and presentation

- [x] **Cover image** — uploaded
- [ ] **Video presentation** (3–5 min): problem → architecture → agent deciding live → P&L
- [x] **Slide presentation** — 6 slides, uploaded

## App hosting and repository

**Public GitHub repository**
> https://github.com/ricardogs26/hackathon-alpaca

**Demo application platform / Application URL**
> https://optionwright.richardx.dev

**Alpaca paper trading account ID** (required for judging)
> PA31YQGU372M

## One-page write-up
> See `docs/writeup.md` — AI logic, risk gates, Alpaca infrastructure. Fill the
> Results section Thursday with the week's P&L.

## Social engagement (up to 5 links)

Tag @AlpacaHQ and @lablabai (X) / Alpaca and lablab.ai (LinkedIn).

1. [x] Day 1 — architecture + first commit (LinkedIn)
2. [ ] Mon — first live trade
3. [ ] Tue — dashboard / demo
4. [ ] Wed — what we learned / risk gates in action
5. [ ] Thu — final results

## Checklist

- [x] Public repo (MIT)
- [x] Demo URL live (HTTPS)
- [x] Paper account ID
- [x] Strategy write-up (results pending Thursday)
- [x] Original + MIT-compliant
- [ ] Video (cover + slides uploaded; screen recording done, voiceover pending)
- [ ] New account with $100k start — **confirmed: `PA31YQGU372M`, equity $100k, options level 3**
