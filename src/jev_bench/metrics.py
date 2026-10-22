"""Metric functions. Plain Python, no numpy, so they are easy to check by hand."""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any


def accuracy(y_true: Sequence[Any], y_pred: Sequence[Any]) -> float:
    if not y_true:
        return math.nan
    return float(sum(t == p for t, p in zip(y_true, y_pred, strict=True)) / len(y_true))


def macro_f1(y_true: Sequence[Any], y_pred: Sequence[Any]) -> float:
    """Unweighted mean of per-class F1 over every class seen in truth or predictions."""
    classes = set(y_true) | (set(y_pred) - {None})  # a failed call (None) is wrong, not a class
    if not classes:
        return math.nan
    f1s = []
    for c in classes:
        tp = sum(t == c and p == c for t, p in zip(y_true, y_pred, strict=True))
        fp = sum(t != c and p == c for t, p in zip(y_true, y_pred, strict=True))
        fn = sum(t == c and p != c for t, p in zip(y_true, y_pred, strict=True))
        f1s.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return sum(f1s) / len(f1s)


def mae(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    if not y_true:
        return math.nan
    return sum(abs(t - p) for t, p in zip(y_true, y_pred, strict=True)) / len(y_true)


def reliability_bins(confidences: Sequence[float], correct: Sequence[bool], n_bins: int = 10) -> list[dict[str, float]]:
    """Equal-width bins over [0, 1]. Each: lo, hi, n, mean_confidence, accuracy (empty bins omitted)."""
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for c, ok in zip(confidences, correct, strict=True):
        bins[min(int(c * n_bins), n_bins - 1)].append((c, ok))
    out = []
    for i, b in enumerate(bins):
        if b:
            out.append(
                {
                    "lo": i / n_bins,
                    "hi": (i + 1) / n_bins,
                    "n": len(b),
                    "mean_confidence": sum(c for c, _ in b) / len(b),
                    "accuracy": sum(ok for _, ok in b) / len(b),
                }
            )
    return out


def expected_calibration_error(confidences: Sequence[float], correct: Sequence[bool], n_bins: int = 10) -> float:
    """ECE = sum over bins of (n_bin / N) * |accuracy_bin - mean_confidence_bin|."""
    n = len(confidences)
    if n == 0:
        return math.nan
    return sum(
        b["n"] / n * abs(b["accuracy"] - b["mean_confidence"]) for b in reliability_bins(confidences, correct, n_bins)
    )


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile, q in [0, 100] (same convention as numpy's default)."""
    if not values:
        return math.nan
    s = sorted(values)
    k = (len(s) - 1) * q / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def bootstrap_ci(
    values: Sequence[float],
    stat: Callable[[Sequence[float]], float] = lambda v: sum(v) / len(v),
    n_resamples: int = 1000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval for `stat` (default: the mean)."""
    if not values:
        return (math.nan, math.nan)
    rng = random.Random(seed)
    n = len(values)
    stats = sorted(stat([values[rng.randrange(n)] for _ in range(n)]) for _ in range(n_resamples))
    return (percentile(stats, 100 * alpha / 2), percentile(stats, 100 * (1 - alpha / 2)))


def cost_per_1000(costs: Sequence[float]) -> float:
    return 1000 * sum(costs) / len(costs) if costs else math.nan


def rates(error_kinds: Sequence[str | None]) -> dict[str, float]:
    """Share of calls that failed: `invalid` (unparseable/invalid output) and `api` (transport/HTTP)."""
    n = len(error_kinds)
    c = Counter(error_kinds)
    return {
        "invalid_rate": c["invalid"] / n if n else math.nan,
        "error_rate": c["api"] / n if n else math.nan,
    }
