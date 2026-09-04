"""
Exit management, phase 1: rules whose thresholds are functions of the
position's STATE (short-leg delta, time to expiry, time to the close, whether
it sleeps), not fixed multiples of the credit.

Pure and unit-tested: the runner feeds the live inputs (the quote, one greeks
snapshot, the clock) and calls decide_exit; the arithmetic never touches the
network. Every input that comes from the network is optional: when it is
missing the rule that needs it is skipped and the credit-based rules still
protect the position (degrade-safe, never blind).

A credit spread is opened for a credit `C` and closed by buying it back for a
debit `P`. Realized P&L per share is `C - P`; "captured" is `(C - P) / C`,
negative when losing.

Order (fixed):
  1. expiration day            -> close (assignment / pin risk)
  2. stop by delta             -> the short leg's |delta| reached stop_delta: the
                                  thesis is dead, whatever the P&L
  3. stop by credit            -> loss reached stop_mult x credit (cap under the
                                  delta stop, and the only stop when delta is unknown)
  4. take-profit by time       -> take_profit_far with more than step_hours to
                                  expiry, take_profit_near in the last hours (the
                                  theta is collected; the gamma left isn't worth it)
  5. trailing take-profit      -> arms at trail_activation, closes on a give-back
  6. overnight rule            -> flatten_minutes before the close, a position that
                                  would sleep is closed ("flat" mode) or kept only if
                                  small and the book is balanced ("delta" mode)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitParams:
    stop_delta: float = 0.45
    stop_mult: float = 1.0
    take_profit_far: float = 0.50
    take_profit_near: float = 0.25
    take_profit_step_hours: float = 24.0
    trail_activation: float = 0.30
    trail_giveback: float = 0.07
    trail_vol_ref_pct: float = 0.8         # give-back scales with intraday vol / this (0 = fixed)
    overnight_mode: str = "flat"           # flat | delta
    flatten_minutes_before_close: float = 30.0
    overnight_max_short_delta: float = 0.35
    overnight_net_delta_pct: float = 0.03

    @classmethod
    def from_params(cls, params, underlying: str | None = None, group: str | None = None) -> "ExitParams":
        g = lambda k: params.get(k, underlying, group)  # noqa: E731
        return cls(
            stop_delta=g("stop_delta"), stop_mult=g("stop_mult"),
            take_profit_far=g("take_profit_far"), take_profit_near=g("take_profit_near"),
            take_profit_step_hours=g("take_profit_step_hours"),
            trail_activation=g("trail_activation"), trail_giveback=g("trail_giveback"),
            trail_vol_ref_pct=g("trail_vol_ref_pct"), overnight_mode=g("overnight_mode"),
            flatten_minutes_before_close=g("flatten_minutes_before_close"),
            overnight_max_short_delta=g("overnight_max_short_delta"),
            overnight_net_delta_pct=g("overnight_net_delta_pct"),
        )


@dataclass(frozen=True)
class ExitDecision:
    close: bool
    reason: str


def take_profit_threshold(p: ExitParams, hours_to_expiry: float | None) -> float:
    """far when more than step_hours remain (or unknown), near in the last hours."""
    if hours_to_expiry is not None and hours_to_expiry <= p.take_profit_step_hours:
        return p.take_profit_near
    return p.take_profit_far


def trail_giveback_for(p: ExitParams, vol_intradia_pct: float | None) -> float:
    """The trailing give-back in sigma terms: 7 points at the reference vol,
    twice that when the underlying moves twice as much (a calm-day trail whipsaws
    on a volatile day), half when it is asleep. Clamped 0.5x-2x. Fixed when the
    reference is 0 or the vol is unknown."""
    if not p.trail_vol_ref_pct or vol_intradia_pct is None or vol_intradia_pct <= 0:
        return p.trail_giveback
    scale = max(0.5, min(2.0, vol_intradia_pct / p.trail_vol_ref_pct))
    return round(p.trail_giveback * scale, 4)


def decide_exit(
    credit: float,
    current_price: float,
    is_expiry_day: bool,
    peak_captured: float = 0.0,
    params: ExitParams | None = None,
    *,
    short_delta: float | None = None,
    hours_to_expiry: float | None = None,
    hours_to_close: float | None = None,
    sleeps_tonight: bool | None = None,
    book_net_delta_pct: float | None = None,
    vol_intradia_pct: float | None = None,
) -> ExitDecision:
    """
    credit             : premium received per share when opened (> 0)
    current_price      : current debit to buy the spread back (>= 0)
    is_expiry_day      : True on the expiration date -> force close
    peak_captured      : high-water mark of `captured`
    short_delta        : |delta| of the short leg now (None = unknown)
    hours_to_expiry    : None = unknown -> far take-profit applies
    hours_to_close     : hours until today's session close (None = unknown)
    sleeps_tonight     : True if the position outlives today's close
    book_net_delta_pct : |net delta of the whole book in $| / equity (None = unknown)
    """
    p = params or ExitParams()

    # 1 — Force close on expiration day, whatever the P&L.
    if is_expiry_day:
        return ExitDecision(True, "expiration force-close")

    if credit <= 0:
        return ExitDecision(False, "hold (no credit basis)")

    captured = (credit - current_price) / credit

    # 2 — Stop by delta: the short strike is being reached. Thesis dead.
    if short_delta is not None and short_delta >= p.stop_delta:
        return ExitDecision(True, "stop (short delta {:.2f} >= {:.2f})".format(short_delta, p.stop_delta))

    # 3 — Stop by credit: the cap, and the only stop when delta is unknown.
    loss = current_price - credit
    if loss >= p.stop_mult * credit:
        return ExitDecision(True, "stop-loss ({:.1f}x credit)".format(loss / credit))

    # 4 — Take-profit stepped by time to expiry.
    tp = take_profit_threshold(p, hours_to_expiry)
    if captured >= tp:
        return ExitDecision(True, "take-profit ({:.0%} of credit, threshold {:.0%})".format(captured, tp))

    # 5 — Trailing take-profit: once armed, close on a pull-back from the peak,
    # the give-back scaled to how much the underlying is actually moving today.
    giveback = trail_giveback_for(p, vol_intradia_pct)
    if peak_captured >= p.trail_activation and captured <= peak_captured - giveback:
        return ExitDecision(True, "trailing take-profit (peak {:.0%}, now {:.0%}, give-back {:.0%})".format(
            peak_captured, captured, giveback))

    # 6 — Overnight rule: never sleep with what the post-mortem showed loses.
    if sleeps_tonight and hours_to_close is not None and hours_to_close * 60.0 <= p.flatten_minutes_before_close:
        if p.overnight_mode == "flat":
            return ExitDecision(True, "overnight flatten ({:.0%} captured)".format(captured))
        # delta mode: sleep only if the leg is small AND the book is balanced AND both are known.
        if short_delta is None or short_delta > p.overnight_max_short_delta:
            return ExitDecision(True, "overnight: short delta {} > {:.2f}".format(
                "unknown" if short_delta is None else f"{short_delta:.2f}", p.overnight_max_short_delta))
        if book_net_delta_pct is None or book_net_delta_pct > p.overnight_net_delta_pct:
            return ExitDecision(True, "overnight: book net delta {} > {:.1%}".format(
                "unknown" if book_net_delta_pct is None else f"{book_net_delta_pct:.1%}", p.overnight_net_delta_pct))

    return ExitDecision(False, "hold")
