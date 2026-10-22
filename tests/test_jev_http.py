"""JevHttpDecider against a fake transport. No network."""

import httpx
import pytest

from jev_bench.core import Choice, Score, YesNo
from jev_bench.deciders.jev_http import JevHttpDecider

PRICE = {"input_per_mtok": 0.042, "output_per_mtok": 0.0}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("jev_bench.deciders.common.time.sleep", lambda s: None)


def make(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return JevHttpDecider("jev", "jev-1.13.0", "https://api.example", "KEY", PRICE, client=client, **kw)


def ok(answer, model="jev-1.13.0"):
    return httpx.Response(
        200, json={"model": model, "answers": {"q": answer}, "usage": {"input_tokens": 1000, "output_tokens": 5}}
    )


def test_yes_no_request_and_response():
    seen = {}

    def handler(req):
        seen["auth"], seen["body"], seen["url"] = req.headers["authorization"], req.read(), str(req.url)
        return ok({"type": "noul", "noul": 0.93})

    d = make(handler).decide(YesNo("Is it spam?"), "buy now")
    assert seen["auth"] == "Bearer KEY" and seen["url"] == "https://api.example/v1/systemone"
    assert b'"type":"noul"' in seen["body"].replace(b" ", b"") and b"jev-1.13.0" in seen["body"]
    assert d.ok and d.label is True and d.probabilities == 0.93 and d.model_id == "jev-1.13.0"
    assert d.cost_usd == pytest.approx(1000 * 0.042 / 1e6)


def test_choice_and_score():
    c = make(lambda r: ok({"type": "choice", "choice": "b", "confidence": 0.7, "probabilities": {"a": 0.3, "b": 0.7}}))
    d = c.decide(Choice("?", ("a", "b")), "x")
    assert d.label == "b" and d.confidence == 0.7
    s = make(
        lambda r: ok(
            {
                "type": "score",
                "score": 1.7,
                "confidence": 0.5,
                "legend": {},
                "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7},
            }
        )
    )
    d = s.decide(Score("?", ("l", "m", "h")), "x")
    assert d.label == 2 and d.value == 1.7


def test_retries_429_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429) if len(calls) < 3 else ok({"type": "noul", "noul": 0.1})

    d = make(handler).decide(YesNo("?"), "x")
    assert d.ok and d.retries == 2 and len(calls) == 3


def test_gives_up_after_retries_and_counts_api_error():
    d = make(lambda r: httpx.Response(529), max_retries=2).decide(YesNo("?"), "x")
    assert not d.ok and d.error_kind == "api"


def test_transport_error_is_retried_then_reported():
    def handler(req):
        raise httpx.ConnectTimeout("slow")

    d = make(handler, max_retries=1).decide(YesNo("?"), "x")
    assert d.error_kind == "api" and "ConnectTimeout" in d.error


def test_invalid_output_is_counted_not_dropped():
    d = make(lambda r: ok({"type": "choice", "choice": "zzz", "probabilities": {}})).decide(
        Choice("?", ("a", "b")), "x"
    )
    assert d.error_kind == "invalid" and d.label is None


def test_validation_error_422_is_api_error():
    d = make(lambda r: httpx.Response(422, json={"detail": []})).decide(YesNo("?"), "x")
    assert d.error_kind == "api" and "422" in d.error
