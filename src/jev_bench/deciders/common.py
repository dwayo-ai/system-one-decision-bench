"""Helpers shared by backends: input rendering, pricing and retry/backoff."""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


def render_input(input: str | dict[str, Any]) -> str:
    return input if isinstance(input, str) else json.dumps(input, ensure_ascii=False, sort_keys=True)


def cost_usd(input_tokens: int | None, output_tokens: int | None, price: dict[str, Any]) -> float:
    """tokens x published price. `price` has input_per_mtok / output_per_mtok (see config/prices.yaml)."""
    return (input_tokens or 0) * float(price["input_per_mtok"]) / 1e6 + (output_tokens or 0) * float(
        price["output_per_mtok"]
    ) / 1e6


class RetryableError(Exception):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def with_retries(
    fn: Callable[[], T],
    max_retries: int = 4,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] | None = None,
    rng: random.Random | None = None,
) -> tuple[T, int]:
    """Call fn(); on RetryableError back off exponentially (with jitter). Returns (result, retries).

    Re-raises the last RetryableError once retries are exhausted.
    """
    rng = rng or random.Random()
    sleep = sleep or time.sleep  # resolved at call time so tests can patch time.sleep
    for attempt in range(max_retries + 1):
        try:
            return fn(), attempt
        except RetryableError as e:
            if attempt == max_retries:
                raise
            delay = e.retry_after if e.retry_after is not None else base_delay * 2**attempt
            sleep(delay + rng.uniform(0, base_delay))
    raise AssertionError("unreachable")
