import math

from jev_bench import metrics


def test_accuracy_and_macro_f1():
    t = ["a", "a", "b", "b"]
    p = ["a", "b", "b", "b"]
    assert metrics.accuracy(t, p) == 0.75
    # class a: tp=1 fp=0 fn=1 -> 2/3 ; class b: tp=2 fp=1 fn=0 -> 4/5
    assert math.isclose(metrics.macro_f1(t, p), (2 / 3 + 4 / 5) / 2)


def test_failed_calls_are_wrong_not_a_class():
    assert metrics.macro_f1(["a", "b"], ["a", None]) == (1.0 + 0.0) / 2


def test_mae():
    assert metrics.mae([0, 2, 4], [1, 2, 2]) == 1.0


def test_ece_perfect_and_overconfident():
    assert metrics.expected_calibration_error([0.5] * 4, [True, True, False, False]) == 0.0
    # always 100% confident, right half the time: |0.5 - 1.0| = 0.5
    assert math.isclose(metrics.expected_calibration_error([1.0] * 4, [True, True, False, False]), 0.5)


def test_reliability_bins_shape():
    bins = metrics.reliability_bins([0.05, 0.95, 0.99], [False, True, True], n_bins=10)
    assert [b["n"] for b in bins] == [1, 2]
    assert bins[1]["accuracy"] == 1.0


def test_percentile_matches_linear_interpolation():
    v = [10, 20, 30, 40]
    assert metrics.percentile(v, 50) == 25.0
    assert metrics.percentile(v, 0) == 10 and metrics.percentile(v, 100) == 40
    assert math.isclose(metrics.percentile(v, 95), 38.5)


def test_bootstrap_ci_is_deterministic_and_brackets_mean():
    vals = [1.0] * 50 + [0.0] * 50
    a = metrics.bootstrap_ci(vals, seed=3)
    assert a == metrics.bootstrap_ci(vals, seed=3)
    assert a[0] < 0.5 < a[1]


def test_cost_and_rates():
    assert metrics.cost_per_1000([0.001, 0.003]) == 2.0
    r = metrics.rates([None, "invalid", "api", None])
    assert r == {"invalid_rate": 0.25, "error_rate": 0.25}
