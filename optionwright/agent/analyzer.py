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
    "Eres un estratega de opciones. Recibes: señales de mercado ya calculadas en "
    "código (tendencia diaria e intradía, VWAP, momentum, régimen), tu memoria de "
    "trades recientes en este subyacente, un resumen del libro abierto (posiciones, "
    "P&L del día, racha), y dos spreads de crédito de riesgo definido ya construidos "
    "(strikes, crédito, max loss — NO los recalcules). Razona con las señales y el "
    "contexto para decidir si el próximo movimiento favorece el bull put (alcista), "
    "el bear call (bajista), los dos a la vez porque el precio se quedará en el "
    "rango (neutral = iron condor; solo con tendencia lateral y régimen tranquilo), "
    "o ninguno (abstain). El riesgo, la concentración y el tamaño los maneja el "
    'código; tu único trabajo es la dirección. Responde SOLO como JSON: '
    '{"direction":"bullish|bearish|neutral|abstain","confidence":0.0-1.0,'
    '"rationale":"una frase corta citando las señales"}. Abstente cuando no haya '
    "ventaja clara."
)


@dataclass(frozen=True)
class Proposal:
    direction: Direction
    confidence: float       # 0..1
    rationale: str
    model: str | None = None        # who answered (primary model, fallback model, or None on abstain-by-error)
    latency_ms: int | None = None


class EmptyCompletion(RuntimeError):
    """The endpoint answered 200 with no content (Featherless does this every
    few market hours). Distinguished from a transport error so it is logged
    honestly and retried once before the fallback decides."""


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


def _messages(context: dict) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": build_user_prompt(context)},
    ]


def _call_ollama_native(base_url: str, model: str, timeout: int, context: dict) -> str:
    """Ollama native /api/chat with think=false (its OpenAI endpoint ignores it)."""
    import httpx

    host = base_url.rstrip("/").removesuffix("/v1")
    resp = httpx.post(
        f"{host}/api/chat",
        json={
            "model": model,
            "think": False,
            "format": "json",
            "stream": False,
            "keep_alive": "30m",  # hold in VRAM so 5-min-apart cycles stay warm
            "options": {"temperature": 0.2, "num_predict": 150},
            "messages": _messages(context),
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("message", {}).get("content", "") or ""


def _openai_extra(extra_body) -> dict:
    """`extra_body` for the OpenAI client from a JSON string/dict (LLM_EXTRA_BODY),
    e.g. {"chat_template_kwargs": {"enable_thinking": false}} for Qwen3.x."""
    if not extra_body:
        return {}
    body = json.loads(extra_body) if isinstance(extra_body, str) else dict(extra_body)
    return {"extra_body": body} if body else {}


def _call_openai(base_url: str, model: str, api_key: str, timeout: int, context: dict, extra_body=None) -> str:
    """OpenAI-compatible path for Featherless / real OpenAI / other hosts."""
    from openai import OpenAI

    # max_retries=0: a slow call fails once, instead of the client silently
    # retrying and tripling the wall time.
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0)
    resp = client.chat.completions.create(
        model=model,
        messages=_messages(context),
        temperature=0.2,
        max_tokens=150,
        response_format={"type": "json_object"},
        **_openai_extra(extra_body),
    )
    if not resp.choices or resp.choices[0].message is None or not (resp.choices[0].message.content or "").strip():
        raise EmptyCompletion(f"{model} returned an empty completion")
    return resp.choices[0].message.content


def _call_primary(s, context: dict) -> str:
    if s.llm_native_ollama:
        return _call_ollama_native(s.llm_base_url, s.llm_model, s.llm_timeout_seconds, context)
    return _call_openai(s.llm_base_url, s.llm_model, s.llm_api_key, s.llm_timeout_seconds, context,
                        getattr(s, "llm_extra_body", ""))


def _call_fallback(s, context: dict) -> str:
    # The fallback is always a local Ollama (native), so a Featherless/OpenAI
    # outage degrades to the on-GPU model instead of an all-day abstain.
    return _call_ollama_native(s.llm_fallback_base_url, s.llm_fallback_model, s.llm_timeout_seconds, context)


def propose(context: dict) -> Proposal:
    """
    Ask the LLM for a direction. Tries the primary endpoint (e.g. Featherless);
    on an empty or failed answer retries it ONCE after a short pause (tech-debt
    3.1: the fallback is the weaker model, so the primary gets a second chance
    first); then the local-Ollama fallback. Only if everything fails does it
    abstain — never a fabricated trade. Records who decided and how long it took.
    """
    import time
    from dataclasses import replace

    from optionwright import metrics

    s = get_settings()
    t0 = time.time()
    raw, model_used, outcome = "", None, None

    attempts = 2 if getattr(s, "llm_retry_primary", True) else 1
    for attempt in range(attempts):
        try:
            raw = _call_primary(s, context)
            model_used, outcome = s.llm_model, ("primary" if attempt == 0 else "retry")
            break
        except EmptyCompletion as exc:
            logger.warning("primary LLM returned empty (attempt %d/%d): %s", attempt + 1, attempts, exc)
            metrics.ERRORS.labels(where="llm_empty").inc()
        except Exception as exc:
            logger.warning("primary LLM failed (attempt %d/%d): %s", attempt + 1, attempts, exc)
            metrics.ERRORS.labels(where="llm").inc()
        if attempt + 1 < attempts:
            time.sleep(getattr(s, "llm_retry_delay_s", 2.0))

    if not raw and s.llm_fallback_base_url:
        try:
            raw = _call_fallback(s, context)
            model_used, outcome = s.llm_fallback_model, "fallback"
            logger.info("used LLM fallback (%s)", s.llm_fallback_model)
        except Exception as exc:
            logger.warning("fallback LLM failed: %s", exc)
            metrics.ERRORS.labels(where="llm_fallback").inc()

    latency_ms = int((time.time() - t0) * 1000)
    if not raw:
        metrics.LLM_DECISIONS.labels(model="none", outcome="abstain_error").inc()
        return replace(_ABSTAIN, latency_ms=latency_ms)
    proposal = _parse_proposal(raw)
    metrics.LLM_DECISIONS.labels(model=model_used or "?", outcome=outcome or "?").inc()
    metrics.record_llm(time.time() - t0, proposal.confidence)
    metrics.record_decision(proposal.direction.value)
    return replace(proposal, model=model_used, latency_ms=latency_ms)
