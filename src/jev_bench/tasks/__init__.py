"""Benchmark tasks: dataset loader + question + gold labels."""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from jev_bench.core import Label, Question

DEFAULT_LIMIT = 500


@dataclass(frozen=True)
class Example:
    id: str
    text: str
    label: Label
    group: str | None = None  # optional sub-category (e.g. Aegis violated categories) for error breakdowns


@dataclass(frozen=True)
class Task:
    name: str
    question: Question
    source: str
    license: str
    synthetic: bool
    loader: Callable[[int, int], tuple[list[Example], dict[str, Any]]]  # (limit, seed) -> (examples, meta)

    def load(self, limit: int = DEFAULT_LIMIT, seed: int = 0) -> tuple[list[Example], dict[str, Any]]:
        return self.loader(limit, seed)


def sample(items: Sequence[Example], limit: int, seed: int) -> list[Example]:
    """Fixed-seed sample: same (items, limit, seed) always gives the same examples in the same order."""
    items = sorted(items, key=lambda e: e.id)
    if limit >= len(items):
        return list(items)
    return random.Random(seed).sample(items, limit)


def get_task(name: str) -> Task:
    from jev_bench.tasks.registry import TASKS, resolve

    try:
        return resolve(TASKS[name])
    except KeyError:
        raise SystemExit(f"unknown task {name!r}; choose from {sorted(TASKS)}") from None
