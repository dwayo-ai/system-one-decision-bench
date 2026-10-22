import json

from jev_bench import tasks as T
from jev_bench.cache import Cache
from jev_bench.core import Choice
from jev_bench.deciders.mock import MockDecider
from jev_bench.runner import run_task
from jev_bench.tasks.leads import load_leads


def fake_task():
    def loader(limit, seed):
        ex = [T.Example(f"{i:03d}", f"text {i}", "a") for i in range(10)]
        return T.sample(ex, limit, seed), {"dataset": "fake"}

    return T.Task("fake", Choice("?", ("a", "b")), "src", "lic", False, loader)


class Counting(MockDecider):
    calls = 0

    def decide(self, q, i):
        Counting.calls += 1
        return super().decide(q, i)


def test_second_run_is_fully_cached(tmp_path):
    cache = Cache(":memory:")
    Counting.calls = 0
    r1 = run_task(fake_task(), Counting(), cache, limit=10, warmup=2, out=tmp_path / "r.json")
    first_calls = Counting.calls
    assert first_calls == 10 + 2  # 10 real + 2 discarded warm-ups
    r2 = run_task(fake_task(), Counting(), cache, limit=10, warmup=2, out=tmp_path / "r.json")
    assert Counting.calls == first_calls and r2["cache_hits"] == 10
    assert [x["decision"] for x in r1["rows"]] == [x["decision"] for x in r2["rows"]]
    assert json.loads((tmp_path / "r.json").read_text())["mock"] is True


def test_refresh_bypasses_cache(tmp_path):
    cache = Cache(":memory:")
    run_task(fake_task(), MockDecider(), cache, limit=5, warmup=0, out=tmp_path / "r.json")
    r = run_task(fake_task(), MockDecider(), cache, limit=5, warmup=0, refresh=True, out=tmp_path / "r.json")
    assert r["cache_hits"] == 0


def test_sampling_is_reproducible_and_seed_dependent():
    ex = [T.Example(f"{i:03d}", "t", 0) for i in range(100)]
    a, b = T.sample(ex, 10, 0), T.sample(ex, 10, 0)
    assert a == b and a != T.sample(ex, 10, 1)


def test_cache_keyed_by_backend_and_model():
    assert Cache.key("jev", "jev-1.13.0", "t", "1") != Cache.key("jev", "jev-1.14.0", "t", "1")


def test_synthetic_leads_are_seeded():
    a, _ = load_leads(50, 3)
    b, _ = load_leads(50, 3)
    assert a == b and {e.label for e in a} <= {0, 1, 2, 3}


def test_changing_the_question_does_not_reuse_cached_answers(tmp_path):
    cache = Cache(":memory:")
    Counting.calls = 0
    run_task(fake_task(), Counting(), cache, limit=5, warmup=0, out=tmp_path / "r.json")
    changed = fake_task()
    changed = T.Task(changed.name, Choice("a different prompt?", ("a", "b")), "s", "l", False, changed.loader)
    r = run_task(changed, Counting(), cache, limit=5, warmup=0, out=tmp_path / "r.json")
    assert r["cache_hits"] == 0 and Counting.calls == 10


def test_lead_rubric_thresholds_match_generator():
    from jev_bench.tasks.leads import _score

    assert [_score(p) for p in (1, 2, 3, 4, 6, 7)] == [0, 1, 1, 2, 2, 3]


def test_cache_key_includes_request_settings_but_keeps_legacy_keys():
    legacy = Cache.key("jev", "jev-1.13.0", "t", "1")
    assert legacy == Cache.key("jev", "jev-1.13.0", "t", "1", "")
    assert Cache.key("c", "m", "t", "1", "max_tokens=128;thinking=disabled") != Cache.key(
        "c", "m", "t", "1", "max_tokens=1024;thinking=default"
    )
    assert Cache.key("c", "m", "t", "1", "x") != Cache.key("c", "m", "t", "1")
