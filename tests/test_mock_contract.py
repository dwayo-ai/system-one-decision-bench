"""The Decider contract, checked against MockDecider for every question kind."""

import math

import pytest

from jev_bench.core import Choice, Decision, Score, YesNo
from jev_bench.deciders.mock import MockDecider

QUESTIONS = [
    YesNo("Is this spam?"),
    Choice("Which team?", ("billing", "technical", "sales")),
    Score("How urgent?", ("low", "medium", "high")),
]


@pytest.mark.parametrize("q", QUESTIONS)
def test_returns_valid_decision(q):
    d = MockDecider(seed=1).decide(q, "hello there")
    assert isinstance(d, Decision) and d.ok and d.error_kind is None
    assert d.latency_ms > 0 and d.cost_usd >= 0 and d.model_id.startswith("mock")
    assert d.confidence is not None and 0 < d.confidence <= 1.0
    if isinstance(q, YesNo):
        assert isinstance(d.label, bool) and isinstance(d.probabilities, float)
    elif isinstance(q, Choice):
        assert d.label in q.options
        assert isinstance(d.probabilities, dict) and math.isclose(sum(d.probabilities.values()), 1.0)
    else:
        assert d.label in range(len(q.levels))
        assert isinstance(d.probabilities, dict) and math.isclose(sum(d.probabilities.values()), 1.0)


@pytest.mark.parametrize("q", QUESTIONS)
def test_deterministic_for_same_seed_and_input(q):
    assert MockDecider(seed=7).decide(q, "x") == MockDecider(seed=7).decide(q, "x")


def test_accuracy_tracks_setting_when_truth_known():
    q = Choice("Which?", ("a", "b", "c", "d"))
    texts = [f"text {i}" for i in range(400)]
    m = MockDecider(seed=0, accuracy=0.8, truth={t: "a" for t in texts})
    hits = sum(m.decide(q, t).label == "a" for t in texts)
    assert 0.72 < hits / 400 < 0.88


def test_dict_input_is_accepted():
    assert MockDecider().decide(YesNo("ok?"), {"a": 1}).ok


def test_is_flagged_mock():
    assert MockDecider().is_mock is True
