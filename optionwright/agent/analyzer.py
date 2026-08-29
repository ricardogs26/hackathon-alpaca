"""
The LLM analyzer. It receives a fully pre-digested context — candidate spreads
and every comparison already computed in code — and returns ONLY a direction
(bullish / bearish / abstain) with a confidence. It never does arithmetic and
never sizes a trade.

Uses an OpenAI-compatible client so LLM_BASE_URL can point at a local Ollama,
Featherless, or any /v1 endpoint. Hard rules carried over from a production
trading agent:
  - format=json, low temperature, bounded timeout
  - abstention is always a valid answer
  - a malformed / degenerate response becomes an ABSTAIN, never a fabricated trade
  - the prompt forbids the model from recomputing any number it is given

Scaffold: prompt and parsing land Sunday with tests over canned LLM responses.
"""
from __future__ import annotations

from dataclasses import dataclass

from optionwright.options.models import Direction


@dataclass(frozen=True)
class Proposal:
    direction: Direction
    confidence: float       # 0..1
    rationale: str


def propose(context: dict) -> Proposal:
    raise NotImplementedError("agent.analyzer.propose — implemented Sunday with tests")
