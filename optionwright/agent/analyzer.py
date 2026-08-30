"""
The LLM analyzer. It receives a fully pre-digested context (candidate spreads and
every comparison already computed in code) and returns ONLY a direction
(bullish / bearish / abstain) with a confidence. It never does arithmetic and
never sizes a trade.

Uses an OpenAI-compatible client, so LLM_BASE_URL can point at a local Ollama,
Featherless, or any /v1 endpoint. Hard rules carried over from a production
trading agent:
  - JSON response format, low temperature, bounded timeout
  - abstention is always a valid answer
  - a malformed or degenerate response becomes an ABSTAIN, never a fabricated
    trade (parsing is isolated in `_parse_proposal`, unit-tested)
  - the prompt forbids the model from recomputing any number it is given
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from optionwright.options.models import Direction
from optionwright.settings import get_settings

logger = logging.getLogger("optionwright.analyzer")

_SYSTEM = (
    "You are the direction module of an options trading agent. You are given a "
    "market snapshot and two pre-built, defined-risk credit spreads whose strikes, "
    "credit and max loss have ALREADY been computed in code. Do NOT recompute any "
    "number. Your only job: decide whether the next move favors the bull put "
    "spread (bullish), the bear call spread (bearish), or neither (abstain). "
    'Reply ONLY as JSON: {"direction":"bullish|bearish|abstain","confidence":0.0-1.0,'
    '"rationale":"one short sentence"}. Abstain whenever the edge is unclear.'
)


@dataclass(frozen=True)
class Proposal:
    direction: Direction
    confidence: float       # 0..1
    rationale: str


_ABSTAIN = Proposal(Direction.ABSTAIN, 0.0, "abstain (no clear edge or bad response)")


def _parse_proposal(raw: str) -> Proposal:
    """
    Parse the model's JSON into a Proposal. Any malformation, unknown direction,
    or out-of-range confidence collapses to a safe ABSTAIN — never a trade.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return _ABSTAIN
    if not isinstance(data, dict):
        return _ABSTAIN

    raw_dir = str(data.get("direction", "")).strip().lower()
    try:
        direction = Direction(raw_dir)
    except ValueError:
        return _ABSTAIN

    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    rationale = str(data.get("rationale", "")).strip()[:200] or "(no rationale)"
    if direction is Direction.ABSTAIN:
        return Proposal(Direction.ABSTAIN, conf, rationale)
    return Proposal(direction, conf, rationale)


def build_user_prompt(context: dict) -> str:
    """Compact, numbers-resolved context. `context` is built by the loop."""
    return json.dumps(context, separators=(",", ":"))


def propose(context: dict) -> Proposal:
    """Ask the LLM for a direction. Any failure returns ABSTAIN."""
    s = get_settings()
    try:
        from openai import OpenAI

        client = OpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key, timeout=s.llm_timeout_seconds)
        resp = client.chat.completions.create(
            model=s.llm_model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": build_user_prompt(context)},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or ""
    except Exception as exc:  # network, timeout, bad endpoint — all safe-abstain
        logger.warning("analyzer LLM call failed: %s", exc)
        return _ABSTAIN
    return _parse_proposal(raw)
