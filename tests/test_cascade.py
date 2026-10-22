import pytest

from jev_bench import analysis
from jev_bench.core import Choice, Decision, failure
from jev_bench.deciders.cascade import CascadeDecider, combine, should_escalate

Q = Choice("which?", ("a", "b"))


def dec(label, conf, cost=0.001, lat=100.0, model="m"):
    other = "b" if label == "a" else "a"
    return Decision(label, label, {label: conf, other: 1 - conf}, lat, cost, model)


class Scripted:
    is_mock = False

    def __init__(self, name, results):
        self.name, self.model_id, self.results, self.calls = name, name, list(results), 0

    def decide(self, question, input):
        self.calls += 1
        return self.results.pop(0)


def test_no_escalation_when_confident():
    p, f = Scripted("p", [dec("a", 0.9)]), Scripted("f", [dec("b", 0.99)])
    d = CascadeDecider(p, f, 0.8).decide(Q, "x")
    assert d.label == "a" and not d.escalated and f.calls == 0


def test_escalates_below_threshold_and_sums_cost_and_latency():
    p, f = Scripted("p", [dec("a", 0.6, 0.001, 100)]), Scripted("f", [dec("b", 0.99, 0.05, 900)])
    d = CascadeDecider(p, f, 0.8).decide(Q, "x")
    assert d.label == "b" and d.escalated
    assert d.cost_usd == pytest.approx(0.051) and d.latency_ms == pytest.approx(1000)


def test_threshold_boundary_is_strictly_below():
    assert not should_escalate(dec("a", 0.8), 0.8)
    assert should_escalate(dec("a", 0.79), 0.8)


def test_primary_failure_always_escalates():
    p = Scripted("p", [failure("invalid", "bad", "m", 50)])
    f = Scripted("f", [dec("a", 0.7)])
    assert CascadeDecider(p, f, 0.0).decide(Q, "x").escalated


def test_combine_marks_escalated():
    assert combine(dec("a", 0.5), dec("b", 0.9)).escalated


def _run(backend, rows):
    return {
        "task": "t",
        "backend": backend,
        "model_id": backend,
        "resolved_model_ids": [backend],
        "mock": False,
        "synthetic": False,
        "question": "Choice",
        "n": len(rows),
        "workers": 1,
        "rows": [{"id": str(i), "label": g, "decision": d.to_dict()} for i, (g, d) in enumerate(rows)],
    }


def test_simulate_cascade_trades_cost_for_accuracy():
    gold = ["a", "a", "a", "a"]
    primary = _run("p", [("a", dec("a", 0.95)), ("a", dec("a", 0.9)), ("a", dec("b", 0.6)), ("a", dec("b", 0.55))])
    fallback = _run("f", [(g, dec("a", 0.99, 0.05, 900)) for g in gold])
    lo = analysis.simulate_cascade(primary, fallback, 0.5)  # nothing escalates
    hi = analysis.simulate_cascade(primary, fallback, 0.99)  # everything escalates
    mid = analysis.simulate_cascade(primary, fallback, 0.8)  # the two weak calls escalate
    assert (lo["accuracy"], lo["pct_escalated"]) == (0.5, 0.0)
    assert (hi["accuracy"], hi["pct_escalated"]) == (1.0, 100.0)
    assert (mid["accuracy"], mid["pct_escalated"]) == (1.0, 50.0)
    assert lo["cost_per_1000_usd"] < mid["cost_per_1000_usd"] < hi["cost_per_1000_usd"]


def test_cascade_sweep_reports_retained_and_cost_ratio():
    primary = _run("p", [("a", dec("a", 0.9, 0.001))])
    fallback = _run("f", [("a", dec("a", 0.9, 0.1))])
    res = analysis.cascade_sweep(primary, fallback, [0.5, 0.95])
    assert res["sweep"][0]["accuracy_retained_vs_fallback"] == 1.0
    assert res["sweep"][0]["cost_vs_fallback"] == pytest.approx(0.01)
    assert res["sweep"][1]["cost_vs_fallback"] == pytest.approx(1.01)


def test_mismatched_examples_rejected():
    a, b = _run("p", [("a", dec("a", 0.9))]), _run("f", [("a", dec("a", 0.9))])
    b["rows"][0]["id"] = "other"
    with pytest.raises(ValueError):
        analysis.simulate_cascade(a, b, 0.5)


def test_parse_sweep():
    assert analysis.parse_sweep("0.5:0.99:0.05")[0] == 0.5
    assert analysis.parse_sweep("0.5:0.9:0.1") == [0.5, 0.6, 0.7, 0.8, 0.9]


def test_cascade_ci_and_paired_difference():
    primary = _run("p", [("a", dec("a", 0.95)), ("a", dec("b", 0.6)), ("a", dec("a", 0.9)), ("a", dec("b", 0.55))])
    fallback = _run("f", [("a", dec("a", 0.99, 0.05, 900)) for _ in range(4)])
    r = analysis.simulate_cascade(primary, fallback, 0.5)  # nothing escalates: 2 of 4 right vs fallback 4 of 4
    assert r["accuracy"] == 0.5
    assert r["accuracy_ci_lo"] <= 0.5 <= r["accuracy_ci_hi"]
    assert r["acc_minus_fallback"] == pytest.approx(-0.5)
    assert r["acc_minus_fallback_lo"] <= -0.5 <= r["acc_minus_fallback_hi"]


def test_best_thresholds_rules():
    primary = _run("p", [("a", dec("a", 0.95, 0.001)), ("a", dec("b", 0.6, 0.001))] * 2)
    fallback = _run("f", [("a", dec("a", 0.99, 0.05, 900)) for _ in range(4)])
    res = analysis.cascade_sweep(primary, fallback, [0.5, 0.8, 0.99])
    sel = res["selected"]
    assert sel["max_accuracy"]["tau"] == 0.8  # first threshold that reaches 1.0, and cheaper than 0.99
    assert sel["cheapest_acceptable"]["tau"] == 0.8
    weak = _run("p", [("a", dec("b", 0.99, 0.001)) for _ in range(4)])  # confidently wrong: never escalates
    none = analysis.cascade_sweep(weak, fallback, [0.5, 0.9])["selected"]["cheapest_acceptable"]
    assert none is None


def test_error_breakdown_ranks_classes_and_counts_failures():
    rows = [
        ("x", dec("y", 0.9)),
        ("x", dec("y", 0.9)),
        ("x", dec("x", 0.9)),
        ("z", failure("api", "boom", "m")),
        ("w", dec("w", 0.9)),
    ]
    e = analysis.error_breakdown(_run("p", rows), k=2)
    assert e["n_wrong"] == 3
    assert e["top_classes"][0] == {"label": "x", "wrong": 2, "total": 3, "most_common_wrong_prediction": "y"}
    assert e["top_classes"][1]["label"] == "z" and e["top_classes"][1]["most_common_wrong_prediction"] == "(failed call)"


def test_error_breakdown_yes_no_categories():
    def yn(label, pred, group=None):
        d = Decision(pred, pred, 0.9 if pred else 0.1, 1.0, 0.0, "m")
        row = {"id": "i", "label": label, "decision": d.to_dict()}
        if group:
            row["group"] = group
        return row

    run = {
        "question": "YesNo",
        "n": 4,
        "backend": "p",
        "rows": [
            yn(True, False, "Violence, Threat"),
            yn(True, True, "Violence"),
            yn(False, True),
            yn(True, False, "Threat"),
        ],
    }
    e = analysis.error_breakdown(run)
    assert (e["missed_positive"], e["false_alarm"]) == (2, 1)
    assert e["top_categories"][0] == {"category": "Threat", "wrong": 2, "total": 2}


def test_failed_fallback_keeps_primary_answer_but_charges_both():
    bad = failure("invalid", "no JSON", "f", 800)
    bad.cost_usd = 0.002  # tokens were billed even though the output was unusable
    d = combine(dec("a", 0.6, 0.001, 100), bad)
    assert d.ok and d.label == "a" and d.escalated
    assert d.cost_usd == pytest.approx(0.003) and d.latency_ms == pytest.approx(900)


def test_both_failed_stays_failed_and_failed_primary_uses_fallback():
    assert not combine(failure("api", "x", "p"), failure("invalid", "y", "f")).ok
    assert combine(failure("api", "x", "p"), dec("b", 0.9)).label == "b"


def test_replay_counts_fallback_failures_and_keeps_jev():
    primary = _run("p", [("a", dec("a", 0.6)), ("a", dec("a", 0.6))])
    fallback = _run("f", [("a", failure("invalid", "bad", "f", 500)), ("a", dec("b", 0.9))])
    r = analysis.simulate_cascade(primary, fallback, 0.99)
    assert r["fallback_failed_on_escalated"] == 1
    assert r["accuracy"] == 0.5  # call 1 keeps Jev's right answer, call 2 takes the wrong fallback answer
    res = analysis.cascade_sweep(primary, fallback, [0.5])
    assert res["fallback_only"]["invalid_rate"] == 0.5 and res["fallback_only"]["failure_rate"] == 0.5


def test_parse_sweep_never_exceeds_hi_and_includes_it():
    taus = analysis.parse_sweep("0.5:0.99:0.05")
    assert taus[0] == 0.5 and taus[-1] == 0.99 and max(taus) <= 0.99
    assert taus[:-1] == [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]


def test_cascade_vs_primary_diff():
    primary = _run("p", [("a", dec("a", 0.95)), ("a", dec("b", 0.6))])
    fallback = _run("f", [("a", dec("a", 0.99, 0.05, 900)) for _ in range(2)])
    r = analysis.simulate_cascade(primary, fallback, 0.9)  # escalates the weak call, which the fallback gets right
    assert r["acc_minus_primary"] == pytest.approx(0.5)
    assert analysis.cascade_sweep(primary, fallback, [0.9])["sweep"][0]["cost_vs_primary"] > 1


def test_mcnemar_exact_known_values():
    assert analysis.mcnemar_exact(0, 0) == 1.0
    assert analysis.mcnemar_exact(5, 5) == 1.0
    assert analysis.mcnemar_exact(0, 8) == pytest.approx(2 / 256)  # 2 * (1/2^8)
    assert analysis.mcnemar_exact(2, 8) == pytest.approx(2 * (1 + 10 + 45) / 1024)


def test_paired_comparison_counts_and_latency_ratio():
    a = _run("a", [("x", dec("x", 0.9, lat=100)), ("x", dec("y", 0.9, lat=300)), ("x", dec("x", 0.9)), ("x", dec("y", 0.9))])
    b = _run("b", [("x", dec("x", 0.9, lat=50)), ("x", dec("x", 0.9)), ("x", dec("y", 0.9)), ("x", dec("y", 0.9))])
    p = analysis.paired_comparison(a, b)
    assert (p["both_right"], p["only_a_right"], p["only_b_right"], p["both_wrong"]) == (1, 1, 1, 1)
    assert p["acc_a_minus_b"] == 0 and p["mcnemar_exact_p"] == 1.0
    assert p["latency_p95_over_p50_a"] >= 1
    b["rows"][0]["id"] = "other"
    with pytest.raises(ValueError):
        analysis.paired_comparison(a, b)
