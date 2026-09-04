"""
The universe: which underlyings the agent trades and which CORRELATION GROUP
each belongs to (phase 2). SPY, QQQ and IWM move together — three positions on
them are one bet — so caps and cooldowns count per group, and rule parameters
can carry a per-group value (scope `group:<name>`).

Configured as one string, e.g. "index:SPY,QQQ,IWM;megacap:AAPL,NVDA,AMZN,TSLA".
Pure module.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Universe:
    groups: dict[str, tuple[str, ...]]        # group -> symbols, in configured order

    @property
    def symbols(self) -> list[str]:
        return [s for syms in self.groups.values() for s in syms]

    def group_of(self, symbol: str) -> str | None:
        symbol = symbol.upper()
        for g, syms in self.groups.items():
            if symbol in syms:
                return g
        return None

    def peers(self, symbol: str) -> tuple[str, ...]:
        """Every symbol in the same group (the symbol included)."""
        g = self.group_of(symbol)
        return self.groups.get(g, (symbol.upper(),)) if g else (symbol.upper(),)


def parse_groups(spec: str) -> Universe:
    """'index:SPY,QQQ;megacap:AAPL' -> Universe. A symbol may appear once."""
    groups: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()
    for part in (spec or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"group without a name: {part!r} (use name:SYM,SYM)")
        name, syms = part.split(":", 1)
        name = name.strip().lower()
        symbols = tuple(s.strip().upper() for s in syms.split(",") if s.strip())
        if not name or not symbols:
            raise ValueError(f"empty group in {part!r}")
        for s in symbols:
            if s in seen:
                raise ValueError(f"{s} appears in more than one group")
            seen.add(s)
        groups[name] = groups.get(name, ()) + symbols
    return Universe(groups)


def flat_universe(symbols: list[str]) -> Universe:
    """No groups configured: every symbol is its own group (the pre-phase-2 behaviour)."""
    return Universe({s.lower(): (s.upper(),) for s in symbols})
