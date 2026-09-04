# Rules — what the agent does, in order

Every rule the agent applies to money, in the order it applies them, with the
parameter that governs it. Parameters live in the `rules` table in Postgres and
resolve with precedence **underlying:SYM > group:NAME > global > registry
default** (`optionwright/policy/params.py`). The environment seeds the global
scope on first start only. Change a value with

```
PATCH /api/rules   Authorization: Bearer <RULES_TOKEN>
{"scope": "global" | "group:index" | "underlying:QQQ", "key": "stop_delta",
 "value": 0.40, "reason": "why", "changed_by": "ricardo"}
```

Every change is written to `rules_history` (old, new, who, why). `GET /api/rules?underlying=QQQ`
shows the effective values and which scope supplied each one.

## Entries — the gates (`policy/gates.py`)

Run in this order on every candidate spread. A gate can **veto** or **shrink**
the contract count; none can enlarge it. Sizing starts at a ceiling of 100
contracts and the shrinking gates bring it down.

| # | Gate | Parameter (default) | Vetoes / shrinks when |
|---|------|---------------------|-----------------------|
| 0 | Confidence | `min_confidence` (0.60) | the LLM's confidence is below the floor |
| 1 | Consecutive-loss breaker | `max_consecutive_losses` (3) / `breaker_lookback_hours` (24) | this many losing trades in a row among those closed in the last 24 h (without the window a streak could never end: nothing opens, so nothing can win) |
| 1b | Daily-loss pause | `max_daily_loss_pct` (0.02) | realized loss today ≥ 2 % of equity |
| 2 | Open-positions cap | `max_open_positions` (6) | that many spreads already open |
| 2b | Per-underlying cap | `max_per_underlying` (2) | that many open on this symbol |
| 2c | Duplicate-spread guard | — | the exact same legs are already open |
| 2d | Per-group cap | `max_per_group` (2) | that many open across the symbol's correlation group (SPY+QQQ+IWM count together) |
| 2e | Same short strike | — | a spread short the same strike (any long leg / expiry) is already open on this symbol |
| 3 | Cooldown | `cooldown_seconds` (2700) | this symbol traded less than 45 min ago |
| 3b | Group cooldown | `group_cooldown_seconds` (1800) | any symbol of the group traded less than 30 min ago |
| 4 | Opening blackout | `opening_blackout_minutes` (30) | too soon after the open |
| 4b | Closing blackout | `no_entry_minutes_before_close` (60) | too close to the close |
| 5 | Macro blackout | `macro_blackout_minutes` (60) | a macro print is near (when a calendar is wired) |
| 5b | Reward/risk floor | `min_reward_risk` (0.20) | credit / max loss of the spread is below the floor |
| 6 | Max loss per position | `max_loss_pct` (0.01) | **shrinks** to 1 % of equity of max loss |
| 7 | Daily budget | `daily_budget_pct` (0.05) | **shrinks** to what is left of 5 % of equity today |
| 8 | Direction share | `max_direction_share` (0.60) | **shrinks/vetoes** so no side holds > 60 % of open risk (first position exempt) |
| 9 | Net delta cap | `max_net_delta_pct` (0.03) | **shrinks/vetoes** so \|net $ delta of the book\| ≤ 3 % of equity (skipped until the ticks have measured the book) |

Inputs that come from the network (minutes to close, net delta) are optional:
unknown means the gate is skipped, never that it invents a number.

## Selection — before the gates (`options/select.py`)

| Parameter (default) | What it does |
|---------------------|--------------|
| `short_delta` (0.30) / `short_delta_volatile` (0.20) | target \|delta\| of the short leg; farther from the money when the regime is volatile |
| `volatile_mode` (neutral) | in a volatile regime: `neutral` = only iron condors, `none` = no entries, `directional` = no restriction |
| `intraday_trend_pct` (0.25) / `intraday_vol_high_pct` (1.2) | perception: the 30-minute move that reads as a trend, and the intraday realized vol (daily-equivalent %) above which the regime is volatile |
| `width_pct` (0.0065) | spread width as a fraction of spot, snapped to the chain's strike step: SPY 770 → 5, IWM 295 → 2, AAPL 320 → 2.5 |
| `width_tolerance` (0.5) | the long leg must sit within this fraction of the width from the target, otherwise no spread (never a silent 10-wide) |
| `min_open_interest` (100) / `max_quote_spread_pct` (0.15) | a leg is liquid only with this much open interest and a bid-ask no wider than 15 % of the mid |

With no tradable spread on either side the cycle abstains (`illiquid chain`)
without calling the model. Universe and groups: `UNDERLYING_GROUPS`.

The model answers `bullish`, `bearish`, `neutral` or `abstain`. **Neutral** is
an iron condor: the bull put and the bear call together, each gated and
recorded as its own position with the same size; if either wing is missing or
fails a gate, nothing opens.

## Exits — by state (`agent/exits.py`)

Evaluated every `EXIT_CHECK_SECONDS` (60 s) per open position, with one greeks
snapshot of the short leg and the clock. In this order:

| # | Rule | Parameter (default) | Closes when |
|---|------|---------------------|-------------|
| 1 | Expiration | — | it is the expiry day (assignment / pin risk) |
| 2 | Stop by delta | `stop_delta` (0.45) | the short leg's \|delta\| reached 0.45: the thesis is dead, whatever the P&L |
| 3 | Stop by credit | `stop_mult` (1.0) | the loss reached 1.0× the credit (cap under the delta stop, and the only stop when delta is unknown) |
| 4 | Take-profit by time | `take_profit_far` (0.50) / `take_profit_near` (0.25) / `take_profit_step_hours` (24) | captured ≥ 50 % with more than 24 h to expiry, ≥ 25 % in the last 24 h |
| 5 | Trailing take-profit | `trail_activation` (0.30) / `trail_giveback` (0.07) / `trail_vol_ref_pct` (0.8) | armed at 30 % captured, closes below the peak by a give-back of 7 pts at the reference vol, scaled by today's intraday realized vol (0.5x-2x) |
| 6 | Overnight | `overnight_mode` (flat) / `flatten_minutes_before_close` (30) | **flat**: every position that would sleep is closed 30 min before the close. **delta**: it may sleep only if its short \|delta\| ≤ `overnight_max_short_delta` (0.35) and the book's \|net delta\| ≤ `overnight_net_delta_pct` (0.03) of equity; unknown means close |

Why these, in one line each (post-mortem of 31-Aug → 3-Sep-2026):
- 40 % take-profit against a 2.0× stop needed an 83 % hit rate; we had 77 %.
- Every one of the week's losses was a position that slept; flat at 15:30 ET
  replayed to +$1,300 against the −$6,518 we realized.
- Six bear calls on SPY+QQQ were one $18.7k bet (18.7 % of equity).
- Two entries at 15:03 and 15:56 ET for $0.56 of credit carried the same $3k
  of risk as a good spread.

## Validating a change before it touches an order

```
python -m optionwright.replay                        # current table parameters
python -m optionwright.replay --stop-delta 0.40 --overnight-mode delta
```

Runs the exit rules over the recorded ticks (`position_ticks`, one row per open
position per minute since 0.4.0) and prints, per position, where these
parameters would have closed it against where the agent actually did.
