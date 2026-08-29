"""
Alpaca integration. Two responsibilities kept apart:

  - Market data / chain: read the option chain and quotes via alpaca-py.
  - Execution: place multi-leg spread orders through the **Alpaca CLI**
    (subprocess + structured JSON), which satisfies the hackathon's MCP-or-CLI
    requirement and is built for long-running agent sessions.

Paper only: the base URL is always paper-api.alpaca.markets and settings refuse
ALPACA_PAPER=false.

Scaffold: chain fetch + CLI order/fill verification land Sat/Sun once the paper
account keys exist.
"""
from __future__ import annotations

from optionwright.options.models import OptionQuote, VerticalSpread


def fetch_chain(underlying: str, expiry: str) -> list[OptionQuote]:
    raise NotImplementedError("broker.alpaca.fetch_chain — needs paper keys")


def submit_spread(spread: VerticalSpread, contracts: int) -> dict:
    """Place the two legs as a single multi-leg order via the Alpaca CLI."""
    raise NotImplementedError("broker.alpaca.submit_spread — needs paper keys")
