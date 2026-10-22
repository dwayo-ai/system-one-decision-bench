"""A chat model asked for structured JSON. Baseline and cascade fallback.

Probabilities are the model's *verbalised* confidence, not logprobs, so calibration numbers for
this backend measure that and nothing more. See README limitations.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Protocol

from jev_bench.core import Choice, Decision, Question, YesNo, failure
from jev_bench.deciders.common import RetryableError, cost_usd, render_input, with_retries

SYSTEM = (
    "You are a precise classifier. Answer the question about the input. Reply with ONE JSON object and nothing else."
)


class ChatClient(Protocol):
    def complete(self, system: str, user: str) -> tuple[str, int | None, int | None, str]:
        """Return (text, input_tokens, output_tokens, resolved_model_id). Raise RetryableError to retry."""
        ...


def build_prompt(question: Question, text: str) -> str:
    if isinstance(question, YesNo):
        fmt = '{"answer": true|false, "confidence": <number 0.5-1.0, how sure you are>}'
        body = f"Question (yes/no): {question.prompt}"
    elif isinstance(question, Choice):
        opts = "\n".join(f"- {o}" for o in question.options)
        fmt = '{"choice": "<exactly one option>", "confidence": <number 0-1, how sure you are>}'
        body = f"Question: {question.prompt}\nOptions:\n{opts}"
    else:
        lv = "\n".join(f"{i}: {d}" for i, d in enumerate(question.levels))
        fmt = '{"level": <integer>, "confidence": <number 0-1, how sure you are>}'
        body = f"Question: {question.prompt}\nPlace the input on this ordered rubric:\n{lv}"
    return f"{body}\n\nInput:\n{text}\n\nRespond as JSON: {fmt}"


def parse_reply(question: Question, text: str) -> tuple[Any, Any, Any]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object in reply")
    obj = json.loads(m.group(0))
    conf = obj.get("confidence")
    if not isinstance(conf, int | float) or isinstance(conf, bool) or not 0 <= conf <= 1:
        raise ValueError(f"bad confidence: {conf!r}")
    conf = float(conf)
    if isinstance(question, YesNo):
        ans = obj.get("answer")
        if not isinstance(ans, bool):
            raise ValueError(f"bad answer: {ans!r}")
        conf = max(conf, 0.5)
        return ans, ans, conf if ans else 1 - conf
    if isinstance(question, Choice):
        c = obj.get("choice")
        if c not in question.options:
            raise ValueError(f"choice not in options: {c!r}")
        rest = (1 - conf) / max(1, len(question.options) - 1)
        return c, c, {o: (conf if o == c else rest) for o in question.options}
    lvl = obj.get("level")
    n = len(question.levels)
    if not isinstance(lvl, int) or isinstance(lvl, bool) or not 0 <= lvl < n:
        raise ValueError(f"level outside rubric: {lvl!r}")
    rest = (1 - conf) / max(1, n - 1)
    return float(lvl), lvl, {str(i): (conf if i == lvl else rest) for i in range(n)}


class AnthropicChat:
    # max_tokens comes from config/models.yaml. Current Claude models may think adaptively and thinking shares the
    # budget, so too small a value truncates the answer (counted as an invalid output).
    def __init__(self, model: str, max_tokens: int = 1024, thinking: str | None = None) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=0)  # retries handled by with_retries
        self.model, self.max_tokens, self.thinking = model, max_tokens, thinking

    def complete(self, system: str, user: str) -> tuple[str, int | None, int | None, str]:
        a = self._anthropic
        extra: dict[str, Any] = {"thinking": {"type": "disabled"}} if self.thinking == "disabled" else {}
        try:
            r = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                **extra,
            )
        except (a.RateLimitError, a.APIConnectionError, a.InternalServerError) as e:
            raise RetryableError(f"{type(e).__name__}: {e}") from e
        text = "".join(b.text for b in r.content if isinstance(b, a.types.TextBlock))
        return text, r.usage.input_tokens, r.usage.output_tokens, r.model


class OpenAIChat:
    def __init__(self, model: str, max_tokens: int = 1024) -> None:
        import openai

        self._openai = openai
        self.client = openai.OpenAI(max_retries=0)
        self.model, self.max_tokens = model, max_tokens

    def complete(self, system: str, user: str) -> tuple[str, int | None, int | None, str]:
        o = self._openai
        try:
            r = self.client.chat.completions.create(
                model=self.model,
                max_completion_tokens=self.max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
            )
        except (o.RateLimitError, o.APIConnectionError, o.InternalServerError) as e:
            raise RetryableError(f"{type(e).__name__}: {e}") from e
        u = r.usage
        return (
            r.choices[0].message.content or "",
            u.prompt_tokens if u else None,
            u.completion_tokens if u else None,
            r.model,
        )


class ChatLLMDecider:
    is_mock = False

    def __init__(self, name: str, client: ChatClient, model: str, price: dict[str, Any], max_retries: int = 4) -> None:
        self.name, self.client, self.model_id, self.price = name, client, model, price
        self.max_retries = max_retries
        self.max_tokens: int | None = getattr(client, "max_tokens", None)
        self.thinking: str | None = getattr(client, "thinking", None)
        # part of the cache key: answers from a different token budget or thinking mode must not be reused
        self.cache_settings = f"max_tokens={self.max_tokens};thinking={self.thinking or 'default'}"

    @classmethod
    def from_config(cls, name: str, cfg: dict[str, Any], price: dict[str, Any]) -> ChatLLMDecider:
        provider = cfg["provider"]
        if provider == "anthropic":
            client: ChatClient = AnthropicChat(cfg["model"], cfg.get("max_tokens", 1024), cfg.get("thinking"))
        elif provider == "openai":
            client = OpenAIChat(cfg["model"], cfg.get("max_tokens", 1024))
        else:
            raise ValueError(f"unknown chat provider {provider!r}")
        if cfg.get("api_key_env") and cfg["api_key_env"] not in os.environ:
            raise KeyError(cfg["api_key_env"])
        return cls(name, client, cfg["model"], price)

    def decide(self, question: Question, input: str | dict[str, Any]) -> Decision:
        prompt = build_prompt(question, render_input(input))
        start = time.perf_counter()
        try:
            (text, tin, tout, resolved), retries = with_retries(
                lambda: self.client.complete(SYSTEM, prompt), self.max_retries
            )
        except RetryableError as e:
            return failure("api", str(e), self.model_id, (time.perf_counter() - start) * 1000)
        except Exception as e:  # non-retryable provider error (auth, bad request)
            return failure("api", f"{type(e).__name__}: {e}", self.model_id, (time.perf_counter() - start) * 1000)
        latency = (time.perf_counter() - start) * 1000
        try:
            value, label, probs = parse_reply(question, text)
        except (ValueError, json.JSONDecodeError) as e:
            bad = failure("invalid", f"{type(e).__name__}: {e}", resolved, latency, text[:500], retries)
            bad.input_tokens, bad.output_tokens, bad.cost_usd = tin, tout, cost_usd(tin, tout, self.price)  # billed anyway
            return bad
        return Decision(
            value=value,
            label=label,
            probabilities=probs,
            latency_ms=latency,
            cost_usd=cost_usd(tin, tout, self.price),
            model_id=resolved,
            raw=text,
            input_tokens=tin,
            output_tokens=tout,
            retries=retries,
        )
