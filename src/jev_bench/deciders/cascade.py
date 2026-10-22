"""Confidence cascade: ask the cheap decider first, escalate to the fallback when it is unsure."""

from __future__ import annotations

from typing import Any

from jev_bench.core import Decider, Decision, Question


def should_escalate(primary: Decision, threshold: float) -> bool:
    """Escalate on any primary failure, or when its top probability is below the threshold."""
    conf = primary.confidence
    return (not primary.ok) or conf is None or conf < threshold


def combine(primary: Decision, fallback: Decision) -> Decision:
    """The decision a caller sees after escalation: fallback's answer, both calls' cost and latency.

    If the fallback call failed (API error or invalid output) but the primary answered, keep the primary's answer.
    """
    keep_primary = primary.ok and not fallback.ok
    d = Decision(**{**(primary if keep_primary else fallback).to_dict(), "escalated": True})
    d.latency_ms = primary.latency_ms + fallback.latency_ms
    d.cost_usd = primary.cost_usd + fallback.cost_usd
    return d


class CascadeDecider:
    is_mock = False

    def __init__(self, primary: Decider, fallback: Decider, threshold: float) -> None:
        self.primary, self.fallback, self.threshold = primary, fallback, threshold
        self.name = f"cascade[{primary.name}>{fallback.name}@{threshold:.2f}]"
        self.model_id = f"{primary.model_id}>{fallback.model_id}"
        self.is_mock = primary.is_mock or fallback.is_mock

    def decide(self, question: Question, input: str | dict[str, Any]) -> Decision:
        first = self.primary.decide(question, input)
        if not should_escalate(first, self.threshold):
            return first
        return combine(first, self.fallback.decide(question, input))
