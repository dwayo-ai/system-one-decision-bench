import pytest

from jev_bench.core import Choice, Score, YesNo
from jev_bench.deciders.chat import ChatLLMDecider, build_prompt, parse_reply
from jev_bench.deciders.common import RetryableError

PRICE = {"input_per_mtok": 10.0, "output_per_mtok": 50.0}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("jev_bench.deciders.common.time.sleep", lambda s: None)


class Fake:
    def __init__(self, *replies):
        self.replies = list(replies)

    def complete(self, system, user):
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r, 100, 20, "resolved-model"


def decider(*replies):
    return ChatLLMDecider("chat", Fake(*replies), "pinned", PRICE)


def test_yes_no_probability_direction():
    assert parse_reply(YesNo("?"), '{"answer": true, "confidence": 0.9}')[2] == 0.9
    assert parse_reply(YesNo("?"), '{"answer": false, "confidence": 0.9}')[2] == pytest.approx(0.1)


def test_choice_spreads_remaining_mass():
    _, label, probs = parse_reply(Choice("?", ("a", "b", "c")), 'sure: {"choice": "b", "confidence": 0.8}')
    assert label == "b" and probs == {"a": pytest.approx(0.1), "b": 0.8, "c": pytest.approx(0.1)}


def test_decide_success_prices_and_resolved_model():
    d = decider('{"level": 2, "confidence": 0.7}').decide(Score("?", ("l", "m", "h")), "x")
    assert d.ok and d.label == 2 and d.model_id == "resolved-model"
    assert d.cost_usd == pytest.approx(100 * 10 / 1e6 + 20 * 50 / 1e6)


@pytest.mark.parametrize(
    "bad",
    [
        "no json here",
        '{"choice": "zzz", "confidence": 0.5}',
        '{"choice": "a"}',
        '{"choice": "a", "confidence": 7}',
        "{broken",
    ],
)
def test_unparseable_output_is_counted(bad):
    d = decider(bad).decide(Choice("?", ("a", "b")), "x")
    assert d.error_kind == "invalid" and d.raw


def test_rate_limit_retried():
    d = decider(RetryableError("429"), '{"answer": true, "confidence": 0.6}').decide(YesNo("?"), "x")
    assert d.ok and d.retries == 1


def test_non_retryable_provider_error_reported():
    d = decider(PermissionError("bad key")).decide(YesNo("?"), "x")
    assert d.error_kind == "api" and "bad key" in d.error


def test_prompt_lists_options_and_rubric():
    assert "- billing" in build_prompt(Choice("?", ("billing", "sales")), "hi")
    assert "1: m" in build_prompt(Score("?", ("l", "m")), "hi")


def test_invalid_output_is_a_failure_but_still_billed():
    d = decider("no json here").decide(Choice("?", ("a", "b")), "x")
    assert not d.ok and d.error_kind == "invalid"
    assert d.input_tokens == 100 and d.output_tokens == 20
    assert d.cost_usd == pytest.approx(100 * 10 / 1e6 + 20 * 50 / 1e6)


def test_anthropic_sends_thinking_disabled_only_when_configured(monkeypatch):
    from types import SimpleNamespace

    import anthropic

    from jev_bench.deciders.chat import AnthropicChat

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    seen = []

    def fake_create(**kw):
        seen.append(kw)
        block = anthropic.types.TextBlock(type="text", text="{}")
        return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=1, output_tokens=1), model="m")

    for thinking, expect in (("disabled", {"type": "disabled"}), (None, None)):
        c = AnthropicChat("m", 128, thinking)
        monkeypatch.setattr(c.client.messages, "create", fake_create)
        c.complete("s", "u")
        assert seen[-1].get("thinking") == expect and seen[-1]["max_tokens"] == 128
    d = ChatLLMDecider("chat", AnthropicChat("m", 128, "disabled"), "m", PRICE)
    assert d.cache_settings == "max_tokens=128;thinking=disabled"
