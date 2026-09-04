# Roadmap and improvement backlog

Consolidated on 2026-09-04 after the hackathon week (judged close: equity
$93,070, −6.9 %). Merges the 2026-09-01 code-review backlog with the findings of
the week and the post-mortem plan. Criticality: **C** loses money or blocks
the plan · **A** high · **M** medium · **L** low.

## Done during the week
0.2.0 versioning/CI/docs · 0.2.x dashboard events, market badge, favicon ·
0.3.0 exits on their own 60s job + 17 runner tests · 0.4.0 `position_ticks`
state vector + expiry window in trading sessions · duplicate-spread guard ·
`EXPIRY_MIN_DAYS` weekend/holiday fix.

## Phase 1 · risk math and the state-based rules engine (3-4 days)
| # | Activity | Crit | Addresses |
|---|---|---|---|
| 1.1 | Parameter table with precedence global → group → underlying, with history (the `trader_rules` pattern) | C | env constants; foundation of the engine |
| 1.2 | `RuleSet`/`ExitParams` built from settings/table; drop divergent defaults | C | strategy defined in three places |
| 1.3 | Stop by **short-leg delta ≥ 0.45**, capped at 1.0× credit | C | five $1-2k losses at 2.0× |
| 1.4 | Take-profit stepped by time (50 % with >1 day left, 25 % in the last hours) | C | avg win $174 vs avg loss $1,510 |
| 1.5 | Overnight rule: flat at 15:30 ET or net delta ≤ 2-3 % of equity | C | every loss slept; simulated +$1,300 vs −$6,518 |
| 1.6 | Cap per **direction** in code (≤ 60 % of risk one side); concentration out of the prompt | C | 6/6 bearish; 72B on a knife edge |
| 1.7 | Sizing: 1 % per position, 5 % per day, 6 open with the real cap on net delta | C | $18.7k = 18.7 % of equity in one bet |
| 1.8 | No entries in the last hour; reward/risk ≥ 0.2 | A | #15/#17 at 15:03/15:56 ET, credit $0.56 |
| 1.9 | Pause on daily loss > 2 % (besides 3 consecutive losses) | A | −8 % in one day |
| 1.10 | Confidence gate inside `policy/gates.py`, not `loop.py` | M | gates in one place |
| 1.11 | Replay harness over `position_ticks` to validate each rule before it touches an order | C | phase 1 is blind without it |
| 1.12 | Dashboard panel: state per position (delta, sigmas, sleeps) | M | see what the engine sees |

## Phase 2 · universe and correlation groups (2-3 days)
| # | Activity | Crit | Addresses |
|---|---|---|---|
| 2.1 | Correlation groups; caps and cooldown per group (SPY/QQQ/DIA = one) | C | false diversification |
| 2.2 | 6-8 underlyings in 5-6 groups (IWM, TLT, GLD, XLE, XLF…) with an automatic liquidity screen | A | three symbols, two of them the same bet |
| 2.3 | Spread width ∝ spot × sigma | A | fixed 5-wide; #16 with 22 contracts |
| 2.4 | Duplicate guard also on the same short strike within a group | M | three SPY 767 calls |
| 2.5 | Diagnose why IWM never had a liquid spread | M | zero entries all week |

## Phase 3 · perception (3-5 days)
| # | Activity | Crit | Addresses |
|---|---|---|---|
| 3.1 | Intraday signals: VWAP, 30-min trend, intraday realized vol, IV | C | sold calls at the bottom; 0 bullish until Thursday |
| 3.2 | Volatile regime → neutral or nothing; short delta 0.20 in volatile | A | Thursday +1 % with short gamma |
| 3.3 | Iron condor when the signal is sideways | A | sideways days idle |
| 3.4 | Trailing give-back in sigmas, not points | M | 7 points for everything |

## Phase 4 · statistical memory (3-4 days)
| # | Activity | Crit | Addresses |
|---|---|---|---|
| 4.1 | Nightly job: excursions per group × regime × DTE; threshold proposals with min sample and hard bounds | A | the seller's "experience" |
| 4.2 | Human approval over WhatsApp (Raphael's skill-gate pattern) | A | nothing changes unsigned |

## Phase 5 · evaluation and model (2 weeks of paper)
| # | Activity | Crit | Addresses |
|---|---|---|---|
| 5.1 | Daily KPI e-mail: expectancy, intraday vs overnight, per group, MAE/MFE | A | knowing whether it works |
| 5.2 | Featherless: detect empty `choices`, retry primary once, honest log | A | one failure every 2-3 h; 8 s latency in market hours |
| 5.3 | `enable_thinking` flag in the client; long A/B Qwen3.8-27B vs 72B on a replayed day | M | 27B one answer behind |
| 5.4 | Prompt: "if your direction's spread is null, abstain" | M | the 72B's only synthetic failure |
| 5.5 | Realized P&L reconciled against Alpaca fills (or labelled "estimated") | M | computed from the mid today |

## Technical debt (between phases)
| # | Activity | Crit |
|---|---|---|
| D.1 | Tests: chain/bars caches, API endpoints and `_cached` TTL, forced Ollama fallback | M |
| D.2 | Dashboard to a static file; `ChainCache` class | L |
| D.3 | One language for identifiers and docs | L |
| D.4 | `logging.basicConfig` out of import time; justify broad excepts; refresh `scripts/` | L |
| D.5 | Decide whether Swagger stays public | L |

Order is not negotiable: 1 → 2 → 3 → 4 → 5. Universe before risk repeats the
judged week in six places.
