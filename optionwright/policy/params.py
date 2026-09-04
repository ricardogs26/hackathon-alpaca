"""
Rule parameters: the registry, the precedence resolver and the typed view the
engine reads. Phase 1 of the post-hackathon plan.

Every tunable of the gates and the exits is declared ONCE here with its type,
default, bounds and meaning. Values live in Postgres (`rules` table, see
storage/store.py) in scopes with precedence

    underlying:<SYM>  >  group:<NAME>  >  global  >  registry default

so one rule can carry a different threshold per correlation group or per symbol
without a second code path. The environment only SEEDS the global scope on
first start; after that the table is the source of truth and every change is
recorded in `rules_history` with who and why.

Pure module: no I/O. `Params` is a resolved snapshot the runner refreshes.
"""
from __future__ import annotations

from dataclasses import dataclass

GLOBAL = "global"


@dataclass(frozen=True)
class ParamSpec:
    key: str
    type: str            # float | int | bool | str
    default: object
    description: str
    lo: float | None = None
    hi: float | None = None
    choices: tuple[str, ...] = ()
    section: str = "risk"

    def coerce(self, raw) -> object:
        """Parse and validate a value (from env, the table or the API)."""
        if self.type == "bool":
            if isinstance(raw, bool):
                v = raw
            else:
                s = str(raw).strip().lower()
                if s not in ("true", "false", "1", "0", "yes", "no"):
                    raise ValueError(f"{self.key}: expected a boolean, got {raw!r}")
                v = s in ("true", "1", "yes")
            return v
        if self.type == "str":
            v = str(raw)
            if self.choices and v not in self.choices:
                raise ValueError(f"{self.key}: expected one of {self.choices}, got {v!r}")
            return v
        try:
            v = int(raw) if self.type == "int" else float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{self.key}: expected a {self.type}, got {raw!r}") from None
        if self.lo is not None and v < self.lo:
            raise ValueError(f"{self.key}: {v} below minimum {self.lo}")
        if self.hi is not None and v > self.hi:
            raise ValueError(f"{self.key}: {v} above maximum {self.hi}")
        return v


_SPECS = [
    # ── entries: gates (policy/gates.py) ─────────────────────────────────────
    ParamSpec("max_loss_pct", "float", 0.01, "Max loss of one position as a fraction of equity", 0.001, 0.10),
    ParamSpec("daily_budget_pct", "float", 0.05, "Capital-at-risk deployable per day, fraction of equity", 0.005, 0.50),
    ParamSpec("max_open_positions", "int", 6, "Concurrent spreads, all underlyings", 1, 50),
    ParamSpec("max_per_underlying", "int", 2, "Concurrent spreads on one symbol", 1, 20),
    ParamSpec("max_per_group", "int", 2, "Concurrent spreads on one correlation group (SPY+QQQ+IWM count together)", 1, 20),
    ParamSpec("group_cooldown_seconds", "float", 1800.0, "Re-entry cooldown after any trade in the same correlation group, seconds", 0.0, 86400.0),
    ParamSpec("max_direction_share", "float", 0.60, "Max share of open risk on one side (bullish or bearish); the first position is exempt", 0.34, 1.0),
    ParamSpec("max_net_delta_pct", "float", 0.03, "Cap on |net delta| of the book in $ as a fraction of equity (entries)", 0.001, 0.50),
    ParamSpec("min_reward_risk", "float", 0.20, "Minimum credit / max-loss of a candidate spread", 0.0, 2.0),
    ParamSpec("min_confidence", "float", 0.60, "Minimum LLM confidence to open", 0.0, 1.0),
    ParamSpec("cooldown_seconds", "float", 2700.0, "Re-entry cooldown per underlying, seconds", 0.0, 86400.0),
    ParamSpec("max_consecutive_losses", "int", 3, "Losing trades in a row that pause trading", 1, 20),
    ParamSpec("breaker_lookback_hours", "float", 24.0, "The losing streak only counts trades closed within this many hours; without a window a streak could never end", 1.0, 720.0),
    ParamSpec("max_daily_loss_pct", "float", 0.02, "Realized loss today (fraction of equity) that pauses new entries", 0.001, 0.50),
    ParamSpec("opening_blackout_minutes", "float", 30.0, "No entries this long after the open", 0.0, 240.0),
    ParamSpec("no_entry_minutes_before_close", "float", 60.0, "No entries this close to the close", 0.0, 240.0),
    ParamSpec("macro_blackout_minutes", "float", 60.0, "No entries this close to a macro print", 0.0, 480.0),
    # ── selection (options/select.py) ────────────────────────────────────────
    ParamSpec("short_delta", "float", 0.30, "Target |delta| of the short leg", 0.05, 0.50, section="selection"),
    ParamSpec("short_delta_volatile", "float", 0.20, "Target |delta| of the short leg when the regime is volatile (farther from the money)", 0.05, 0.50, section="selection"),
    ParamSpec("volatile_mode", "str", "neutral", "In a volatile regime: neutral = only iron condors; none = no entries; directional = no restriction", choices=("neutral", "none", "directional"), section="selection"),
    ParamSpec("intraday_trend_pct", "float", 0.25, "Perception: 30-minute move (%) that reads as an intraday trend", 0.01, 5.0, section="perception"),
    ParamSpec("intraday_vol_high_pct", "float", 1.2, "Perception: intraday realized vol (daily-equivalent %) above which the regime is volatile", 0.1, 20.0, section="perception"),
    ParamSpec("width_pct", "float", 0.0065, "Spread width as a fraction of spot (SPY 770 -> 5, IWM 295 -> 2), rounded to the strike step", 0.001, 0.05, section="selection"),
    ParamSpec("width_tolerance", "float", 0.5, "The long leg must sit within this fraction of the target width, else no spread", 0.0, 1.0, section="selection"),
    ParamSpec("min_open_interest", "int", 100, "Liquidity: minimum open interest of a leg", 0, 100000, section="selection"),
    ParamSpec("max_quote_spread_pct", "float", 0.15, "Liquidity: bid-ask no wider than this fraction of the mid", 0.01, 1.0, section="selection"),
    # ── exits (agent/exits.py) ───────────────────────────────────────────────
    ParamSpec("stop_delta", "float", 0.45, "Close when the short leg's |delta| reaches this (thesis dead)", 0.30, 0.99, section="exits"),
    ParamSpec("stop_mult", "float", 1.0, "Close when the loss reaches this multiple of the credit (cap under the delta stop)", 0.25, 5.0, section="exits"),
    ParamSpec("take_profit_far", "float", 0.50, "Take-profit (fraction of credit) with more than take_profit_step_hours to expiry", 0.05, 1.0, section="exits"),
    ParamSpec("take_profit_near", "float", 0.25, "Take-profit (fraction of credit) in the last take_profit_step_hours", 0.05, 1.0, section="exits"),
    ParamSpec("take_profit_step_hours", "float", 24.0, "Hours to expiry below which the near take-profit applies", 1.0, 240.0, section="exits"),
    ParamSpec("trail_activation", "float", 0.30, "Trailing take-profit arms once this fraction is captured", 0.05, 1.0, section="exits"),
    ParamSpec("trail_giveback", "float", 0.07, "Close if captured falls this far below its peak (at the reference vol)", 0.01, 0.5, section="exits"),
    ParamSpec("trail_vol_ref_pct", "float", 0.8, "Trailing in sigma terms: the give-back scales with intraday realized vol / this reference (0.5x-2x); 0 = fixed points", 0.0, 20.0, section="exits"),
    ParamSpec("overnight_mode", "str", "flat", "flat = close everything before the close; delta = let a position sleep only if it is small and the book is balanced", choices=("flat", "delta"), section="exits"),
    ParamSpec("flatten_minutes_before_close", "float", 30.0, "The overnight rule fires this many minutes before the close", 1.0, 240.0, section="exits"),
    ParamSpec("overnight_max_short_delta", "float", 0.35, "delta mode: max |delta| of the short leg to sleep", 0.05, 0.99, section="exits"),
    ParamSpec("overnight_net_delta_pct", "float", 0.03, "delta mode: max |net delta| of the book ($ / equity) to sleep", 0.001, 0.50, section="exits"),
]
REGISTRY: dict[str, ParamSpec] = {s.key: s for s in _SPECS}

# Settings field that seeds each key (env = seed only).
SEED_FROM_SETTINGS: dict[str, str] = {k: k for k in REGISTRY}


def group_scope(name: str) -> str:
    return f"group:{name}"


def underlying_scope(symbol: str) -> str:
    return f"underlying:{symbol.upper()}"


def validate_scope(scope: str) -> str:
    if scope == GLOBAL or scope.startswith("group:") or scope.startswith("underlying:"):
        if scope.endswith(":"):
            raise ValueError(f"empty scope name in {scope!r}")
        return scope
    raise ValueError(f"unknown scope {scope!r}; use global, group:<name> or underlying:<SYM>")


class Params:
    """A resolved snapshot: values by scope, read through the precedence chain."""

    def __init__(self, values: dict[str, dict[str, object]] | None = None):
        self._v = {scope: dict(kv) for scope, kv in (values or {}).items()}

    def get(self, key: str, underlying: str | None = None, group: str | None = None):
        spec = REGISTRY[key]   # KeyError on purpose: an unknown key is a bug, not a default
        for scope in self._scopes(underlying, group):
            if key in self._v.get(scope, {}):
                return spec.coerce(self._v[scope][key])
        return spec.default

    def effective(self, underlying: str | None = None, group: str | None = None) -> dict[str, object]:
        return {k: self.get(k, underlying, group) for k in REGISTRY}

    def source(self, key: str, underlying: str | None = None, group: str | None = None) -> str:
        """Which scope supplied the value (for the API / dashboard)."""
        for scope in self._scopes(underlying, group):
            if key in self._v.get(scope, {}):
                return scope
        return "default"

    @staticmethod
    def _scopes(underlying: str | None, group: str | None) -> list[str]:
        scopes = []
        if underlying:
            scopes.append(underlying_scope(underlying))
        if group:
            scopes.append(group_scope(group))
        scopes.append(GLOBAL)
        return scopes


def seed_from_settings(settings) -> dict[str, object]:
    """Global seed values taken from settings/env (validated against the registry)."""
    out = {}
    for key, field in SEED_FROM_SETTINGS.items():
        if hasattr(settings, field):
            out[key] = REGISTRY[key].coerce(getattr(settings, field))
    return out
