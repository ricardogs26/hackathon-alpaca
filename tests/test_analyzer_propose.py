"""propose(): primary → retry → fallback → abstain, and who decided (tech-debt 3.1)."""
from types import SimpleNamespace

import pytest

from optionwright import metrics
from optionwright.agent import analyzer
from optionwright.agent.analyzer import EmptyCompletion, _openai_extra, propose
from optionwright.options.models import Direction

GOOD = '{"direction":"bearish","confidence":0.7,"rationale":"baja"}'


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(analyzer, "get_settings", lambda: SimpleNamespace(
        llm_native_ollama=False, llm_base_url="http://p", llm_model="Qwen/72B", llm_api_key="k", llm_timeout_seconds=5,
        llm_fallback_base_url="http://f", llm_fallback_model="qwen3.5:9b", llm_retry_primary=True, llm_retry_delay_s=0.0,
        llm_extra_body=""))


def _val(model, outcome):
    return metrics.LLM_DECISIONS.labels(model=model, outcome=outcome)._value.get()


def test_primary_answers_first_time(monkeypatch):
    monkeypatch.setattr(analyzer, "_call_primary", lambda s, c: GOOD)
    before = _val("Qwen/72B", "primary")
    p = propose({})
    assert p.direction is Direction.BEARISH and p.model == "Qwen/72B" and p.latency_ms is not None
    assert _val("Qwen/72B", "primary") == before + 1


def test_empty_primary_is_retried_once_and_the_retry_decides(monkeypatch):
    calls = {"n": 0}

    def primary(s, c):
        calls["n"] += 1
        if calls["n"] == 1:
            raise EmptyCompletion("empty")
        return GOOD

    monkeypatch.setattr(analyzer, "_call_primary", primary)
    monkeypatch.setattr(analyzer, "_call_fallback", lambda s, c: (_ for _ in ()).throw(AssertionError("fallback must not run")))
    before = _val("Qwen/72B", "retry")
    p = propose({})
    assert calls["n"] == 2 and p.model == "Qwen/72B" and _val("Qwen/72B", "retry") == before + 1


def test_two_empties_go_to_the_fallback(monkeypatch):
    monkeypatch.setattr(analyzer, "_call_primary", lambda s, c: (_ for _ in ()).throw(EmptyCompletion("empty")))
    monkeypatch.setattr(analyzer, "_call_fallback", lambda s, c: GOOD)
    before = _val("qwen3.5:9b", "fallback")
    p = propose({})
    assert p.model == "qwen3.5:9b" and _val("qwen3.5:9b", "fallback") == before + 1


def test_everything_failing_abstains_and_counts_it(monkeypatch):
    monkeypatch.setattr(analyzer, "_call_primary", lambda s, c: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(analyzer, "_call_fallback", lambda s, c: (_ for _ in ()).throw(RuntimeError("down too")))
    before = _val("none", "abstain_error")
    p = propose({})
    assert p.direction is Direction.ABSTAIN and p.model is None and _val("none", "abstain_error") == before + 1


def test_retry_can_be_disabled(monkeypatch):
    monkeypatch.setattr(analyzer, "get_settings", lambda: SimpleNamespace(
        llm_native_ollama=False, llm_base_url="http://p", llm_model="M", llm_api_key="k", llm_timeout_seconds=5,
        llm_fallback_base_url="", llm_fallback_model="", llm_retry_primary=False, llm_retry_delay_s=0.0, llm_extra_body=""))
    calls = {"n": 0}
    monkeypatch.setattr(analyzer, "_call_primary", lambda s, c: calls.__setitem__("n", calls["n"] + 1) or (_ for _ in ()).throw(EmptyCompletion("e")))
    assert propose({}).direction is Direction.ABSTAIN and calls["n"] == 1


def test_openai_extra_body_from_json_string():
    assert _openai_extra("") == {}
    assert _openai_extra('{"chat_template_kwargs":{"enable_thinking":false}}') == {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    assert _openai_extra({"a": 1}) == {"extra_body": {"a": 1}}


def test_empty_completion_is_detected_in_the_openai_path(monkeypatch):
    class _Msg:
        content = ""

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kw):
            _Completions.kw = kw
            return _Resp()

    class _Client:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=_Completions())

    import openai
    monkeypatch.setattr(openai, "OpenAI", _Client)
    with pytest.raises(EmptyCompletion):
        analyzer._call_openai("http://x", "M", "k", 5, {"a": 1}, '{"chat_template_kwargs":{"enable_thinking":false}}')
    assert _Completions.kw["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
