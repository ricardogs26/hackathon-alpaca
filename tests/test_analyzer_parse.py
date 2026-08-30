"""
Tests for the analyzer's JSON parsing. The invariant that matters: anything the
model gets wrong collapses to ABSTAIN, never a fabricated trade.
"""
from __future__ import annotations

from optionwright.agent.analyzer import _parse_proposal
from optionwright.options.models import Direction


def test_valid_bullish():
    p = _parse_proposal('{"direction":"bullish","confidence":0.7,"rationale":"uptrend holding"}')
    assert p.direction is Direction.BULLISH
    assert p.confidence == 0.7
    assert "uptrend" in p.rationale


def test_valid_bearish():
    p = _parse_proposal('{"direction":"bearish","confidence":0.55,"rationale":"rolling over"}')
    assert p.direction is Direction.BEARISH


def test_abstain_is_respected():
    p = _parse_proposal('{"direction":"abstain","confidence":0.0,"rationale":"chop"}')
    assert p.direction is Direction.ABSTAIN


def test_malformed_json_abstains():
    assert _parse_proposal("not json at all").direction is Direction.ABSTAIN


def test_unknown_direction_abstains():
    assert _parse_proposal('{"direction":"moon","confidence":0.9}').direction is Direction.ABSTAIN


def test_out_of_range_confidence_clamped():
    assert _parse_proposal('{"direction":"bullish","confidence":9}').confidence == 1.0
    assert _parse_proposal('{"direction":"bullish","confidence":-3}').confidence == 0.0


def test_non_numeric_confidence_defaults_zero():
    p = _parse_proposal('{"direction":"bullish","confidence":"high"}')
    assert p.direction is Direction.BULLISH and p.confidence == 0.0


def test_missing_rationale_has_placeholder():
    p = _parse_proposal('{"direction":"bullish","confidence":0.6}')
    assert p.rationale == "(no rationale)"


def test_json_array_abstains():
    assert _parse_proposal('[1,2,3]').direction is Direction.ABSTAIN
