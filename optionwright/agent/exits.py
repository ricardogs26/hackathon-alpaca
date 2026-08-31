"""
Exit management. Given an open credit spread and its current value, decide
whether to close it. Pure and unit-tested: the loop fetches the live price and
calls decide_exit, so the arithmetic never touches the network.

A credit spread is opened for a credit `C` (received per share) and closed by
buying it back for a debit `P` (its current value). Realized P&L per share is
`C - P`. As the short options decay, P falls toward 0 and the trade profits.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitParams:
    take_profit_pct: float = 0.50   # close once 50% of the credit is captured
    stop_mult: float = 2.0          # close once the loss reaches 2x the credit


@dataclass(frozen=True)
class ExitDecision:
    close: bool
    reason: str


def decide_exit(
    credit: float,
    current_price: float,
    is_expiry_day: bool,
    params: ExitParams | None = None,
) -> ExitDecision:
    """
    credit         : premium received per share when the spread was opened (> 0)
    current_price  : current debit to buy the spread back (>= 0)
    is_expiry_day  : True on the expiration date -> force close (assignment/pin risk)
    """
    p = params or ExitParams()

    # 1 — Force close on expiration day, whatever the P&L. Never let a short leg
    #     ride into expiration (assignment and pin risk).
    if is_expiry_day:
        return ExitDecision(True, "expiration force-close")

    if credit <= 0:
        return ExitDecision(False, "hold (no credit basis)")

    captured = credit - current_price          # profit per share if closed now
    # 2 — Take profit: captured at least take_profit_pct of the credit.
    if captured >= p.take_profit_pct * credit:
        return ExitDecision(True, f"take-profit ({captured / credit:.0%} of credit)")

    # 3 — Stop loss: the loss (current_price - credit) reached stop_mult x credit.
    loss = current_price - credit
    if loss >= p.stop_mult * credit:
        return ExitDecision(True, f"stop-loss ({loss / credit:.1f}x credit)")

    return ExitDecision(False, "hold")
