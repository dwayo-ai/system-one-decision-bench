"""Core types: the three question kinds, and the Decision every backend returns."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

Label = bool | str | int  # yes/no -> bool, choice -> option name, score -> level index


@dataclass(frozen=True)
class YesNo:
    prompt: str


@dataclass(frozen=True)
class Choice:
    prompt: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class Score:
    """An ordered rubric. `levels[i]` describes level i (0 = lowest)."""

    prompt: str
    levels: tuple[str, ...]


Question = YesNo | Choice | Score


@dataclass
class Decision:
    """The result of one decision.

    value:         bool (yes/no), option name (choice) or expected level, a float (score)
    label:         the discrete prediction: bool, option name, or integer level
    probabilities: P(yes) as a float for yes/no; {option or level index: p} otherwise
    error_kind:    None on success, "invalid" if the output could not be parsed or validated,
                   "api" for transport/HTTP failures. Failures are counted, never dropped.
    """

    value: Label | float | None
    label: Label | None
    probabilities: dict[str, float] | float | None
    latency_ms: float
    cost_usd: float
    model_id: str
    raw: Any = None
    error: str | None = None
    error_kind: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    escalated: bool = False
    retries: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def confidence(self) -> float | None:
        """Top-outcome probability. This is the quantity the cascade thresholds on."""
        p = self.probabilities
        if p is None:
            return None
        if isinstance(p, float):
            return max(p, 1.0 - p)
        return max(p.values()) if p else None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Decision:
        return cls(**d)


def failure(
    kind: str, message: str, model_id: str, latency_ms: float = 0.0, raw: Any = None, retries: int = 0
) -> Decision:
    return Decision(
        value=None,
        label=None,
        probabilities=None,
        latency_ms=latency_ms,
        cost_usd=0.0,
        model_id=model_id,
        raw=raw,
        error=message,
        error_kind=kind,
        retries=retries,
    )


class Decider(Protocol):
    """One interface for 'make a typed decision about this input'."""

    name: str
    model_id: str
    is_mock: bool

    def decide(self, question: Question, input: str | dict[str, Any]) -> Decision: ...
