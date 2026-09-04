from __future__ import annotations

from optionwright.settings import Settings


def test_rich_context_defaults_on():
    s = Settings(_env_file=None)
    assert s.agent_rich_context is True
    assert s.trend_flat_pct == 1.0
    assert s.vol_high_pct == 1.2


def test_rich_context_off_via_env(monkeypatch):
    monkeypatch.setenv("AGENT_RICH_CONTEXT", "false")
    s = Settings(_env_file=None)
    assert s.agent_rich_context is False
