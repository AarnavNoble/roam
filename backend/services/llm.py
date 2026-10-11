"""
LLM client with model failover.

Models in config.GROQ_MODELS are tried in order. Each has its own circuit
breaker, so a model that has been retired (404), is rate-limited (429) or is
timing out stops being called and the next one takes its place. If none
answers, LLMUnavailable is raised and the pipeline writes the itinerary text
deterministically instead of failing the request.

Token usage is recorded on every call's span, which is where the cost numbers
in the benchmarks come from.
"""

import json

import groq
from groq import Groq

from backend import config
from backend.core.resilience import CircuitBreaker, Deadline, DeadlineExceeded, UpstreamError
from backend.core.telemetry import stage

CALL_TIMEOUT_S = 40.0
_breakers: dict[str, CircuitBreaker] = {}
_client: Groq | None = None


class LLMUnavailable(UpstreamError):
    """No configured model produced a usable answer."""


def _breaker(model: str) -> CircuitBreaker:
    if model not in _breakers:
        _breakers[model] = CircuitBreaker(f"llm:{model}", failure_threshold=2, reset_after=120)
    return _breakers[model]


def get_client() -> Groq:
    global _client
    if not config.GROQ_API_KEY:
        raise LLMUnavailable("GROQ_API_KEY not set")
    if _client is None:
        # Retries are handled here by moving to the next model, not by the SDK.
        _client = Groq(api_key=config.GROQ_API_KEY, max_retries=0)
    return _client


def chat(messages: list[dict], *, tools: list[dict] | None = None, json_mode: bool = False,
         max_tokens: int = 2500, temperature: float = 0.4,
         deadline: Deadline | None = None) -> tuple[object, dict]:
    """
    One chat completion against the first healthy model.
    Returns (message, meta) where meta has the model used and token usage.
    """
    client = get_client()
    errors: list[str] = []
    for model in config.GROQ_MODELS:
        # Checked before the breaker is consulted: running out of our own time
        # says nothing about the model, so it must not count as its failure.
        try:
            timeout = deadline.timeout(CALL_TIMEOUT_S) if deadline else CALL_TIMEOUT_S
        except DeadlineExceeded:
            errors.append("request deadline exceeded")
            break
        breaker = _breaker(model)
        if not breaker.allow():
            errors.append(f"{model}: circuit open")
            continue
        kwargs: dict = {"model": model, "messages": messages, "temperature": temperature,
                        "max_completion_tokens": max_tokens}
        if model.startswith("openai/gpt-oss"):
            kwargs["reasoning_effort"] = "low"  # reasoning tokens are billed and slow
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            with stage("llm.call", model=model) as span:
                response = client.chat.completions.create(timeout=timeout, **kwargs)
                usage = response.usage
                span.set_attribute("llm.prompt_tokens", usage.prompt_tokens)
                span.set_attribute("llm.completion_tokens", usage.completion_tokens)
        except groq.NotFoundError as e:
            # The model no longer exists. Keep its breaker open for an hour.
            breaker.record_failure()
            breaker.record_failure()
            breaker.reset_after = 3600
            errors.append(f"{model}: {type(e).__name__}")
            continue
        except (groq.APIError, UpstreamError) as e:
            breaker.record_failure()
            errors.append(f"{model}: {type(e).__name__}")
            continue
        breaker.record_success()
        meta = {"model": model, "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens, "failed_over": errors}
        return response.choices[0].message, meta
    raise LLMUnavailable("; ".join(errors) or "no models configured")


def complete_json(prompt: str, *, max_tokens: int = 2500,
                  deadline: Deadline | None = None) -> tuple[dict, dict]:
    """
    Ask for a JSON object. An answer that does not parse raises LLMUnavailable
    without trying another model: the caller's deterministic writer takes over,
    which is cheaper than spending a second completion on the same prompt.
    """
    message, meta = chat([{"role": "user", "content": prompt}], json_mode=True,
                         max_tokens=max_tokens, deadline=deadline)
    try:
        data = json.loads(message.content or "")
    except json.JSONDecodeError as e:
        raise LLMUnavailable(f"{meta['model']} returned invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise LLMUnavailable(f"{meta['model']} returned {type(data).__name__}, expected object")
    return data, meta
