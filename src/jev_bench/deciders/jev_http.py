"""Jev over HTTP: `POST /v1/systemone` (api.typesafe.ai directly, or a gateway that forwards it).

Request/response shapes follow the vendor's published API documentation.
A LiteLLM pass-through is the same call with base_url = <proxy>/typesafe and the proxy's own key.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from jev_bench.core import Choice, Decision, Question, YesNo, failure
from jev_bench.deciders.common import RetryableError, cost_usd, render_input, with_retries

QUESTION_KEY = "q"
RETRY_STATUS = {429, 500, 502, 503, 504, 529}


def build_question(question: Question) -> dict[str, Any]:
    if isinstance(question, YesNo):
        return {"type": "noul", "instructions": question.prompt}
    if isinstance(question, Choice):
        return {
            "type": "choice",
            "instructions": question.prompt,
            "criteria": {o: None for o in question.options},
        }
    return {"type": "score", "instructions": question.prompt, "criteria": list(question.levels)}


def parse_answer(question: Question, answer: Any) -> tuple[Any, Any, Any]:
    """Return (value, label, probabilities); raise ValueError if the answer does not fit the question."""
    if not isinstance(answer, dict):
        raise ValueError("answer is not an object")
    if isinstance(question, YesNo):
        p = answer.get("noul")
        if answer.get("type") != "noul" or not isinstance(p, int | float) or not 0 <= p <= 1:
            raise ValueError(f"bad noul answer: {answer!r}")
        return p >= 0.5, p >= 0.5, float(p)
    if isinstance(question, Choice):
        choice, probs = answer.get("choice"), answer.get("probabilities")
        if answer.get("type") != "choice" or choice not in question.options or not isinstance(probs, dict):
            raise ValueError(f"bad choice answer: {answer!r}")
        return choice, choice, {str(k): float(v) for k, v in probs.items()}
    score, probs = answer.get("score"), answer.get("probabilities")
    if answer.get("type") != "score" or not isinstance(score, int | float) or not isinstance(probs, dict):
        raise ValueError(f"bad score answer: {answer!r}")
    sprobs = {str(k): float(v) for k, v in probs.items()}
    if not sprobs or any(not 0 <= int(k) < len(question.levels) for k in sprobs):
        raise ValueError(f"score probabilities outside rubric: {answer!r}")
    top = max(sprobs, key=lambda k: sprobs[k])
    return float(score), int(top), sprobs


class JevHttpDecider:
    is_mock = False

    def __init__(
        self,
        name: str,
        model: str,
        base_url: str,
        api_key: str,
        price: dict[str, Any],
        timeout: float = 30.0,
        max_retries: int = 4,
        client: httpx.Client | None = None,
    ) -> None:
        self.name = name
        self.model_id = model  # pinned id from config; the resolved id is logged in Decision.model_id
        self.url = base_url.rstrip("/") + "/v1/systemone"
        self.price = price
        self.max_retries = max_retries
        self.client = client or httpx.Client(timeout=timeout)
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    @classmethod
    def from_config(cls, name: str, cfg: dict[str, Any], price: dict[str, Any]) -> JevHttpDecider:
        base = cfg.get("base_url") or os.environ[cfg["base_url_env"]] + cfg.get("base_url_suffix", "")
        return cls(name, cfg["model"], base, os.environ[cfg["api_key_env"]], price)

    def _post(self, body: dict[str, Any]) -> tuple[httpx.Response, float]:
        """One attempt. Returns (response, client-side latency in ms of this attempt)."""
        t0 = time.perf_counter()
        try:
            r = self.client.post(self.url, json=body, headers=self._headers)
        except httpx.TransportError as e:  # timeouts, connection resets
            raise RetryableError(f"{type(e).__name__}: {e}") from e
        if r.status_code in RETRY_STATUS:
            ra = r.headers.get("retry-after")
            raise RetryableError(f"HTTP {r.status_code}", float(ra) if ra and ra.isdigit() else None)
        return r, (time.perf_counter() - t0) * 1000

    def decide(self, question: Question, input: str | dict[str, Any]) -> Decision:
        body = {
            "model": self.model_id,
            "state": input if isinstance(input, dict) else render_input(input),
            "questions": {QUESTION_KEY: build_question(question)},
        }
        start = time.perf_counter()
        try:
            (r, latency), retries = with_retries(lambda: self._post(body), self.max_retries)
        except RetryableError as e:
            return failure("api", str(e), self.model_id, (time.perf_counter() - start) * 1000)
        # `latency` is the successful attempt only; time spent in backoff shows up via `retries`
        if r.status_code != 200:
            return failure("api", f"HTTP {r.status_code}: {r.text[:300]}", self.model_id, latency, retries=retries)
        try:
            data = r.json()
            value, label, probs = parse_answer(question, data["answers"][QUESTION_KEY])
            usage = data.get("usage") or {}
        except (ValueError, KeyError, TypeError) as e:
            return failure("invalid", f"{type(e).__name__}: {e}", self.model_id, latency, r.text[:500], retries)
        return Decision(
            value=value,
            label=label,
            probabilities=probs,
            latency_ms=latency,
            cost_usd=cost_usd(usage.get("input_tokens"), usage.get("output_tokens"), self.price),
            model_id=str(data.get("model", self.model_id)),
            raw=data,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            retries=retries,
        )
