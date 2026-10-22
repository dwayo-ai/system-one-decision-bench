"""Deterministic, seedable fake backend for tests and key-less development.

Everything it produces is synthetic. Runs that use it are flagged `mock` and the report prints
a MOCK banner. Do not quote its numbers.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

from jev_bench.core import Choice, Decision, Label, Question, Score, YesNo
from jev_bench.deciders.common import render_input


class MockDecider:
    is_mock = True

    def __init__(
        self,
        name: str = "mock",
        seed: int = 0,
        accuracy: float = 0.85,
        latency_ms: float = 120.0,
        price_per_mtok: float = 0.042,
        truth: dict[str, Label] | None = None,
    ) -> None:
        self.name = name
        self.seed = seed
        self.accuracy = accuracy
        self.latency = latency_ms
        self.price = price_per_mtok
        # input text -> gold label; the harness fills this so the mock can be right ~accuracy of the time
        self.truth: dict[str, Label] = truth if truth is not None else {}
        self.model_id = f"mock-seed{seed}-acc{accuracy}"

    def _rng(self, question: Question, text: str) -> random.Random:
        h = hashlib.sha256(f"{self.seed}|{question}|{text}".encode()).hexdigest()
        return random.Random(int(h[:16], 16))

    def decide(self, question: Question, input: str | dict[str, Any]) -> Decision:
        text = render_input(input)
        rng = self._rng(question, text)
        gold = self.truth.get(text)
        correct = rng.random() < self.accuracy
        # confident when right, shakier when wrong, so the cascade has something to find
        conf = rng.uniform(0.7, 0.99) if correct else rng.uniform(0.4, 0.8)
        latency = max(5.0, rng.lognormvariate(0, 0.35) * self.latency)
        tokens = max(1, len(text) // 4)
        cost = tokens * self.price / 1e6

        if isinstance(question, YesNo):
            truth = bool(gold) if gold is not None else rng.random() < 0.5
            label = truth if correct else not truth
            p_yes = conf if label else 1 - conf
            return self._ok(label, label, p_yes, latency, cost, tokens)
        if isinstance(question, Choice):
            opts = question.options
            truth_s = str(gold) if gold in opts else rng.choice(opts)
            pred = truth_s if correct else rng.choice([o for o in opts if o != truth_s] or [truth_s])
            rest = (1 - conf) / max(1, len(opts) - 1)
            probs = {o: (conf if o == pred else rest) for o in opts}
            return self._ok(pred, pred, probs, latency, cost, tokens)
        assert isinstance(question, Score)
        n = len(question.levels)
        truth_i = int(gold) if isinstance(gold, int) and not isinstance(gold, bool) else rng.randrange(n)
        wrong = [i for i in range(n) if i != truth_i] or [truth_i]
        pred_i = truth_i if correct else min(wrong, key=lambda i: abs(i - truth_i))
        rest = (1 - conf) / max(1, n - 1)
        sprobs = {str(i): (conf if i == pred_i else rest) for i in range(n)}
        expected = sum(int(k) * p for k, p in sprobs.items())
        return self._ok(expected, pred_i, sprobs, latency, cost, tokens)

    def _ok(
        self,
        value: Label | float,
        label: Label,
        probs: dict[str, float] | float,
        latency: float,
        cost: float,
        tokens: int,
    ) -> Decision:
        return Decision(
            value=value,
            label=label,
            probabilities=probs,
            latency_ms=latency,
            cost_usd=cost,
            model_id=self.model_id,
            raw={"mock": True},
            input_tokens=tokens,
            output_tokens=0,
        )
