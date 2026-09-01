"""
Exit management. Given an open credit spread and its current value, decide
whether to close it. Pure and unit-tested: the loop fetches the live price and
the position's high-water mark, and calls decide_exit, so the arithmetic never
touches the network.

A credit spread is opened for a credit `C` (received per share) and closed by
buying it back for a debit `P` (its current value). Realized P&L per share is
`C - P`. "captured" is the fraction of the credit locked if closed now,
`(C - P) / C`, which is negative when the position is losing.

Exit logic (in order): expiration force-close, stop-loss, a hard take-profit
ceiling, then a TRAILING take-profit that follows the peak and closes on a
pullback (so a position that runs up and reverses still exits in the green).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitParams:
    stop_mult: float = 2.0             # close once the loss reaches 2x the credit
    hard_take_profit: float = 0.60     # bank automatically at 60% of the credit
    trail_activation: float = 0.20     # trailing arms once 20% is captured
    trail_giveback: float = 0.10       # close if captured falls this far below its peak


@dataclass(frozen=True)
class ExitDecision:
    close: bool
    reason: str


def decide_exit(
    credit: float,
    current_price: float,
    is_expiry_day: bool,
    peak_captured: float = 0.0,
    params: ExitParams | None = None,
) -> ExitDecision:
    """
    credit         : premium received per share when opened (> 0)
    current_price  : current debit to buy the spread back (>= 0)
    is_expiry_day  : True on the expiration date -> force close (assignment/pin risk)
    peak_captured  : highest `captured` fraction this position has reached (high-water mark)
    """
    p = params or ExitParams()

    # 1 — Force close on expiration day, whatever the P&L.
    if is_expiry_day:
        return ExitDecision(True, "expiration force-close")

    if credit <= 0:
        return ExitDecision(False, "hold (no credit basis)")

    captured = (credit - current_price) / credit

    # 2 — Stop loss: the loss reached stop_mult x the credit.
    loss = current_price - credit
    if loss >= p.stop_mult * credit:
        return ExitDecision(True, "stop-loss ({:.1f}x credit)".format(loss / credit))

    # 3 — Hard take-profit ceiling: bank a big winner regardless of the trail.
    if captured >= p.hard_take_profit:
        return ExitDecision(True, "take-profit ({:.0%} of credit)".format(captured))

    # 4 — Trailing take-profit: once armed, close on a pullback from the peak.
    if peak_captured >= p.trail_activation and captured <= peak_captured - p.trail_giveback:
        return ExitDecision(
            True,
            "trailing take-profit (peak {:.0%}, now {:.0%})".format(peak_captured, captured),
        )

    return ExitDecision(False, "hold")
