# Technical debt — full review after 0.8.1 (2026-09-04)

Reviewed on the code as deployed (phases 0-4 done in one day). Criticality:
**C** can lose money or leave the book unmanaged · **A** high · **M** medium ·
**L** low. "Resolves" says what goes wrong today.

## 1 · Execution and reconciliation — **1.1, 1.2, 1.3 done in 0.9.0 (2026-09-04)**; 1.4 partly (limit widens by attempts, still in cents)
| # | Activity | Crit | Resolves |
|---|----------|------|----------|
| 1.1 | **Order lifecycle**: after `submit_spread`/`close_spread` poll the order (id from the CLI JSON) until filled / cancelled / expired; on a close that is not filled keep the position OPEN and retry with a wider limit; record `fill_price` | C | Both functions assume a fill. A limit close that never fills leaves the DB saying "closed" while Alpaca still holds the spread: an **unmanaged position** with no rule watching it |
| 1.2 | **Reconciliation** with Alpaca every exits pass (`get_all_positions`): DB open ⇄ broker legs; any drift → alert + refuse new entries until resolved | C | Nothing checks that the book in Postgres is the book at the broker (a missed fill, a manual close, an assignment) |
| 1.3 | Realized P&L from **fills**, not from the mid at decision time (`account activities`); label it "estimated" until then | A | Every P&L figure in the dashboard, the memory buckets and the breaker is an estimate; the breaker and the daily-loss pause act on it |
| 1.4 | Close limit `price + 0.05` → proportional to the quote width (and to the leg price for megacaps) | M | 5 cents is fine for SPY, meaningless for a $3 NVDA spread, too wide for a $0.30 IWM one |

## 2 · Data, retention, observability — **2.1 done in 0.10.0 (2026-09-04); retention of decisions and ticks too (part of 2.2)**
| # | Activity | Crit | Resolves |
|---|----------|------|----------|
| 2.1 | Store the **LLM context** (signals, spreads, book) on each decision (JSONB) | A | The 2-Sep A/B had to reconstruct contexts (80 % fidelity); replaying the model or auditing a decision is impossible today |
| 2.2 | `save_equity` once per pass, not once per underlying (7× today); retention for `equity_curve` (keep 1/min for 30 d, then 1/day) and `position_ticks` (90 d) | M | 1,914 equity rows in 5 days at 3 symbols → ~900/day at 7; ticks will add ~400/day per open position; no purge anywhere |
| 2.3 | Decisions table: index on `(ts)` and `(position_id)`; `get_decisions` used by the dashboard scans 1,894 rows already | L | Dashboard latency grows with the log |
| 2.4 | Tick vector: add `regime`, `vol_intradia`, `net_delta_usd` at the time of the tick | M | The nightly memory buckets by regime *at open*; the trail scales by intraday vol that is not stored, so the replay cannot reproduce it |
| 2.5 | Grafana dashboard `k8s/grafana` not updated since 0.3.0 (no ticks, rules, proposals, condors, groups) | M | The panels no longer describe the agent |

## 3 · Model — **3.1 done in 0.10.0 (2026-09-04)**
| # | Activity | Crit | Resolves |
|---|----------|------|----------|
| 3.1 | Featherless: detect empty `choices`, log "primary returned empty", **retry the primary once** before falling back; expose `enable_thinking` in the client (needed for Qwen3.x) | A | ~1 empty completion every 2-3 market hours falls straight to the 9B, which the A/B showed trades without an edge |
| 3.2 | Prompt: "if the spread of your direction is null, abstain"; the fallback path has never fired in a test | M | The 72B's one synthetic failure; a code path with money on it and no test |
| 3.3 | Long A/B (replayed day) of Qwen3.8-27B vs 72B, once contexts are stored (2.1) | M | The 27B was one answer behind on synthetic cases only |
| 3.4 | Neutral proposals: measure how often the model says `neutral` and what condors return; the prompt hint ("only sideways + calm") is untested in production | M | New behaviour since 0.7.0 |

## 4 · Risk and strategy
| # | Activity | Crit | Resolves |
|---|----------|------|----------|
| 4.1 | **Condor sizing** as a structure: max loss = larger wing, not the sum; `PolicyState` is not refreshed between the two wings of one cycle | A | Each wing takes 1 % → the condor takes 2 % of equity for a structure whose true max loss is 1 %; the second wing is gated on a stale book |
| 4.2 | Validate Alpaca's indicative IV (0.068 for SPY on 4-Sep is not credible) before any sigma-based width or signal | M | Phase 2/3 deferred sigma width for this reason |
| 4.3 | Net delta and direction share use `short_leg.strike` as a spot proxy and 100× delta; fine for a 0.30 short, coarse for the 0.20 volatile legs | L | Small bias in the caps |
| 4.4 | Macro calendar never wired (`minutes_to_macro` is always None) | M | The macro blackout gate is inert; FOMC/CPI/NFP prints hit short gamma hardest |
| 4.5 | Bull puts on indexes fail `min_reward_risk` (SPY 0.16, IWM 0.17 on 4-Sep) while bear calls pass: the book will tilt bearish by construction | M | A structural bias that the direction-share gate only partly corrects; consider R/R per right or per group |
| 4.6 | Nightly memory has two proposal rules; group vol thresholds (3.5 % megacap) were set by hand from one day | M | Calibration by eye until the job learns it |

## 5 · Code and tests — **5.1 and 5.2 done in 0.9.2 (2026-09-04)**
| # | Activity | Crit | Resolves |
|---|----------|------|----------|
| 5.1 | **Postgres in CI** (service container) to test `store.py` (568 lines, zero tests: rules, proposals, ticks, policy state SQL) | A | Every SQL change is verified only by hand in the pod |
| 5.2 | **Dashboard JS in CI**: the `node -e new Function(js)` syntax check that caught 0.5.1 belongs in `make lint`/CI; move the 430-line HTML/JS out of the Python string into `static/` | A | 0.5.1 shipped a page that showed "connecting…" forever |
| 5.3 | 20 broad `except Exception` (10 in runner): each is deliberate degradation, but none is narrowed or asserted by a test that the degradation happens | M | A typo inside a guarded block becomes a silent warning |
| 5.4 | Settings duplicates the registry (26 seed fields): add a test that every registry key has a seed and vice-versa, or seed from the registry defaults and drop the fields | M | Two lists to keep in sync by hand |
| 5.5 | `runner.py` (391 lines) mixes clock cache, params cache, exits pass, deps wiring, perception; split (`clock.py`, `params_cache`, `exits_pass.py`) | M | Hard to test in isolation; module globals patched in tests |
| 5.6 | `alpaca.py` caches as module globals (`_chain_cache`, `_bars_cache`, `_intraday_cache`, `_clock`) → a `BrokerCache` object | L | Test isolation via monkeypatch of globals |
| 5.7 | `logging.basicConfig` at import in `api/main.py`; Spanish/English mix in identifiers and docstrings; stale `scripts/` (SPY-only probes) | L | Hygiene |
| 5.8 | `CHANGELOG` grew 9 versions in one day; tag `v0.2.1`…`v0.8.1` and `main` not pushed | L | Remote does not have the week's work |

## 6 · Operations — **6.1 and 6.3 done in 0.9.1 (2026-09-04)**
| # | Activity | Crit | Resolves |
|---|----------|------|----------|
| 6.1 | `RULES_TOKEN` missing from the Secret: no proposal can be approved, no rule edited over the API | A | The human gate of phase 4 is closed |
| 6.2 | `/ow approve N` route in the Amael WhatsApp bridge (other repo) | M | Approval by API only |
| 6.3 | Alerting when the agent stops cycling (the Amael watchdog checks replicas, not cycles): a Prometheus rule on `optionwright_cycles_total` flat for 15 min in market hours | A | A hung scheduler looks healthy |
| 6.4 | Featherless latency 8 s in market hours × 7 symbols = up to 60 s of a 180 s cycle; parallelise the LLM calls or raise `CYCLE_SECONDS` for 7+ symbols | M | Skips would return with 2-3 more symbols |
| 6.5 | Swagger `/docs` public on the demo URL; decide | L | Read-only, but decide on purpose |
| 6.6 | `docker compose` path not re-verified since 0.2.0 (new env, rules seed, groups) | M | The "judges can run it" promise may be stale |

## Order of attack
1. **1.1 + 1.2** (order lifecycle + reconciliation) — the one debt that can leave money unmanaged.
2. **6.1, 6.3** (token, cycle alert) — cheap, unblock phase 4 and catch a hung agent.
3. **5.1, 5.2** (Postgres in CI, JS check in CI) — every later change is safer.
4. **2.1, 4.1, 3.1** (store contexts, condor sizing, Featherless retry).
5. Everything else between phases of the evaluation (phase 5).
