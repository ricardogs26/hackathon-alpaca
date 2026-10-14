# Retrospective — optionwright (31-Aug → 13-Oct-2026)

An autonomous options agent for the Alpaca AI Trading Agents Hackathon
(lablab.ai × Alpaca, 28-Aug → 4-Sep-2026), left running on paper for five more
weeks. This is the close-out: what happened, what held, what did not, and what
the next agent should do differently. Numbers come from the Postgres store
(dumped on 2026-10-13), not from memory.

## Timeline

| Date | Milestone |
|---|---|
| 29-Aug | Plan, public repo, agent deployed on Kubernetes |
| 31-Aug → 2-Sep | Judged week: 0.1.x → 0.3.0 (~35 releases in 3 days); 14/14 wins on 1-Sep; peak equity $102,328 |
| 3-Sep | Judged close: −$8,290 in one day, $93,070 (−6.9 %); six bear calls slept through a rebound |
| 4-Sep | Hackathon closes (421 projects; no prize). Same day: phases 0–4 and the tech-debt list, 0.4.0 → 0.11.1, 79 → 270 tests |
| 8–10-Sep | First "real" days: 0.12.0 → 0.15.1 — net-delta gate, mleg orders over plain REST, signed entry limits, entry ladder, HTTP timeouts, expiry fallback |
| 10-Sep → 12-Oct | No code changes. The agent ran alone |
| 13-Oct | Teardown. Final equity $89,796 (−10.2 %) |

## The numbers

| | Judged week | After | Total |
|---|---|---|---|
| Equity | 100,000 → 92,568 | 92,568 → 89,796 | **89,796 (−10.2 %)** |
| Closed | 23 | 149 | 172 |
| Win rate | 74 % | 41 % | 45 % |
| Realized | −$7,059 | −$2,717 | −$9,776 |

After the hackathon the weekly realized P&L was −276, −528, −674, −584, −658:
a steady bleed of ~$550 a week with no catastrophic day. Average win $23,
average loss $47, average credit $128, average max loss $466.

Exits after the hackathon (149 closed):

| Exit | n | wins | P&L | avg hold |
|---|---|---|---|---|
| overnight flatten (15:30 ET) | 71 | 13 | −$1,645 | 3.5 h |
| trailing take-profit | 45 | 45 | +$1,063 | 2.2 h |
| stop on short delta ≥ 0.45 | 22 | 0 | −$1,841 | 2.3 h |
| hard take-profit | 3 | 3 | +$192 | 9.4 h |
| stop on credit multiple | 4 | 0 | −$339 | 6.3 h |
| reconciled (close found at broker) | 4 | 0 | −$147 | 2.3 h |

Entries in the first hour (10:00 ET) lost −$1,650 over 76 positions; entries
after noon were roughly flat.

Decisions: ~22,200 LLM calls over six weeks (Qwen2.5-72B-Instruct on
Featherless, 5.7 s average; the local qwen3.5:9b fallback answered 36 times).
Dominant vetoes: condor wing blocked by the per-group cap (~5,000), model
abstained (2,456), condor missing one wing (1,645), illiquid chain (631).
Roughly 170 of 22,000 proposals were approved (0.8 %).

Nightly learning: five proposals, all the same (`stop_delta` 0.45 → 0.40, "the
stop fires late, losses average 92–133 % of the credit", n = 20–34). Four
expired undecided, one still pending. The loop never closed because no human
answered it.

Operations: 7 pod restarts in 32 days (lab network), 8 book/broker mismatches
fixed automatically by the reconciler, zero manual interventions, heartbeat
green. Entries approved seconds after the closing bell (found 15-Sep) were
never fixed: 18 `unfilled` orders between 9-Sep and 12-Oct.

## What held

1. **"The LLM proposes, the code decides" survived contact.** Across ~22k
   decisions the model never increased risk: sizing, strikes, stops and exits
   were always code. No loss traces back to a hallucination. That was the
   thesis, and it was demonstrated.
2. **Exits in code work.** 48/48 take-profits won; 26/26 stops cut the loss
   where designed (capped at 1.0× credit from 0.5.0). The judged week lost on
   strategy design, not on execution.
3. **Broker reconciliation** (0.9.0 / 0.11.0): DB ⇄ Alpaca every minute,
   bounded auto-fixes with a log; 8 drifts resolved alone, no orphan positions.
4. **Measure before optimizing.** The 72B-vs-9B A/B (10 cases × 2 runs) and the
   replay over stored contexts settled the model choice with evidence; the chain
   cache was validated with a real download counter; the bottleneck was the
   chains, not the LLM.
5. **Speed with a net.** Sixty releases in eleven days through `make release`
   (lint + tests + build + push + manifest bump + deploy), CI with a real
   Postgres and a syntax check of the dashboard script. CI caught two real bugs.
6. **Diagnosis without tools.** The 10-Sep hang was found by reading
   `/proc/1/net/tcp` inside the pod (ptrace denied). Rule kept: no HTTP client
   without a default timeout.
7. **Honest history.** The 3-Sep post-mortem was written the same day with the
   raw numbers; so is this.

## What did not

1. **The strategy was never tested before going live.** No backtest, no replay
   before the judged day (the replay harness arrived in 0.10.0, after). A
   two-week simulation would have shown the −$9,474 hole from six sleeping
   positions.
2. **The post-hackathon configuration was incoherent** — noted on 9-Sep, never
   decided: `overnight_mode=flat` at 15:30 ET + 2–3 DTE + 50 % take-profit means
   a 48–72 h option held for 3.5 h, which collects no theta. 71 of 149 exits
   were the flatten, 58 of them small losses. The agent ran five weeks on a rule
   that guaranteed a slow bleed.
3. **Over-engineering after the judges.** Phases 0–4 (rules engine, correlation
   groups, intraday perception, condors, nightly memory) shipped in one day.
   Impressive, but none was validated against data before production, and the
   condor became the main source of vetoes.
4. **Deploying with the market open.** Four releases in ten minutes on 8-Sep
   meant four scheduler restarts and ten minutes without cycles. On 10-Sep the
   pod lost 2 h 13 min hung on a read with no timeout.
5. **Unpublished work.** Twenty commits and seven tags (v0.11.2 → v0.15.1) sat
   unpushed; the public repo stayed at 0.11.1 for five weeks and a scheduled
   "PM" routine reported "no commits since 4-Sep" twice a day while pushing
   result branches computed on a stale main.
6. **A routine without an end date.** The PM routine's prompt said "deadline
   3-Sep"; it kept running twice a day until 12-Oct (~75 e-mails), and another
   assistant turned those e-mails into calendar blocks with their own
   notifications. Noise generated by two of my own agents talking to each other.
7. **The human gate had no channel.** Approval was an API token; the alert went
   out over WhatsApp with no way to reply. Five identical, reasonable proposals
   died unanswered.
8. **Money paths left untested.** Entries after the bell, the entry limit sent
   with the wrong sign (21 market-like fills from 1 to 9-Sep), P&L from the mid
   until 0.14.1. Same shape every time: a path that touches money with no test
   against a real order.

## What to change next time

- **Backtest or replay before the first paper dollar**, then a fixed
  measurement window (two weeks) before any strategy change. No new phase
  without a metric asking for it.
- **Design the clocks together**: DTE, maximum hold and take-profit are one
  decision. Flatten every afternoon → trade 0–1 DTE; trade 2–3 DTE → sleep with
  a bounded net delta.
- **Change freeze while the market is open**; batch releases before 9:30 or
  after 16:00 ET.
- **Every scheduled routine gets an end date**, or is disabled in the same
  session its milestone ends. One agent does not consume another agent's
  e-mails unfiltered.
- **Push at the end of every session.** The public repo is the evidence.
- **A human gate ships only with its reply channel.** Alert over WhatsApp →
  approve over WhatsApp. No route, no gate.
- **A money path is tested with a real order and the market open** (rule since
  0.12.1) — including the small findings like the closing blackout.

## Teardown (13-Oct-2026)

Database dumped (`data/hackathon-db-2026-10-13.sql.gz`, local only: 8 tables,
190 positions, 23,163 decisions, ticks). The Kubernetes namespace, the Grafana
dashboard, the public hostname and the scheduled routine were retired. The paper
account held no positions. The code stays as it was at 0.15.1.
