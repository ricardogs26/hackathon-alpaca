"""
Reconciliation (tech-debt 1.2): the book in Postgres against the book at the
broker, leg by leg. Pure: the runner feeds both sides. A mismatch is reported
and blocks new entries; it is never "fixed" automatically — a position the
agent does not know about is exactly the thing a human must look at.
"""
from __future__ import annotations

from dataclasses import dataclass

LIVE_STATUSES = ("open", "closing")   # rows that must have legs at the broker


@dataclass(frozen=True)
class Mismatch:
    symbol: str
    expected: int      # signed contracts per the DB (short legs negative)
    actual: int        # signed contracts per the broker

    def __str__(self) -> str:
        return f"{self.symbol}: db {self.expected:+d} vs broker {self.actual:+d}"


def expected_legs(rows: list[dict]) -> dict[str, int]:
    """Signed contracts per option symbol implied by the live spreads."""
    out: dict[str, int] = {}
    for r in rows:
        if r.get("status") not in LIVE_STATUSES:
            continue
        n = int(r["contracts"])
        out[r["short_symbol"]] = out.get(r["short_symbol"], 0) - n
        out[r["long_symbol"]] = out.get(r["long_symbol"], 0) + n
    return {k: v for k, v in out.items() if v != 0}


def diff(expected: dict[str, int], actual: dict[str, int]) -> list[Mismatch]:
    """Every symbol where the two books disagree, sorted."""
    out = []
    for sym in sorted(set(expected) | set(actual)):
        e, a = int(expected.get(sym, 0)), int(actual.get(sym, 0))
        if e != a:
            out.append(Mismatch(sym, e, a))
    return out
