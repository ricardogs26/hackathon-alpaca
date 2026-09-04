"""Rule parameters: registry, coercion/bounds and the precedence resolver."""
from types import SimpleNamespace

import pytest

from optionwright.policy.params import GLOBAL, REGISTRY, Params, group_scope, seed_from_settings, underlying_scope, validate_scope


def test_registry_defaults_are_valid_for_their_own_specs():
    for spec in REGISTRY.values():
        assert spec.coerce(spec.default) == spec.default


def test_coerce_types_and_bounds():
    assert REGISTRY["max_open_positions"].coerce("4") == 4
    assert REGISTRY["stop_delta"].coerce("0.5") == 0.5
    assert REGISTRY["overnight_mode"].coerce("delta") == "delta"
    with pytest.raises(ValueError):
        REGISTRY["stop_delta"].coerce(0.1)          # below minimum
    with pytest.raises(ValueError):
        REGISTRY["max_open_positions"].coerce("x")  # not an int
    with pytest.raises(ValueError):
        REGISTRY["overnight_mode"].coerce("maybe")  # not a choice


def test_precedence_underlying_over_group_over_global_over_default():
    p = Params({GLOBAL: {"stop_delta": 0.40}, group_scope("index"): {"stop_delta": 0.42},
                underlying_scope("spy"): {"stop_delta": 0.50}})
    assert p.get("stop_delta") == 0.40
    assert p.get("stop_delta", group="index") == 0.42
    assert p.get("stop_delta", underlying="SPY", group="index") == 0.50
    assert p.get("stop_delta", underlying="QQQ", group="index") == 0.42
    assert p.get("take_profit_far") == REGISTRY["take_profit_far"].default   # untouched -> default
    assert p.source("stop_delta", underlying="SPY") == "underlying:SPY"
    assert p.source("take_profit_far") == "default"


def test_effective_lists_every_key():
    assert set(Params().effective()) == set(REGISTRY)


def test_unknown_key_is_a_bug_not_a_default():
    with pytest.raises(KeyError):
        Params().get("no_such_rule")


def test_scope_validation():
    assert validate_scope("global") == "global"
    assert validate_scope("group:index") == "group:index"
    with pytest.raises(ValueError):
        validate_scope("team:x")
    with pytest.raises(ValueError):
        validate_scope("underlying:")


def test_seed_from_settings_reads_matching_fields_only():
    s = SimpleNamespace(max_loss_pct=0.02, stop_delta=0.5, unrelated=1)
    seed = seed_from_settings(s)
    assert seed == {"max_loss_pct": 0.02, "stop_delta": 0.5}
    with pytest.raises(ValueError):
        seed_from_settings(SimpleNamespace(max_loss_pct=5.0))   # out of bounds is refused at the seed
