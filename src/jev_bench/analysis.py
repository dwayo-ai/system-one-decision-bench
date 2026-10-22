"""Turn run records into the numbers the report shows."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from jev_bench import metrics
from jev_bench.core import Decision
from jev_bench.deciders.cascade import combine, should_escalate


def _decisions(run: dict[str, Any]) -> list[Decision]:
    return [Decision.from_dict(r["decision"]) for r in run["rows"]]


def _correct(run: dict[str, Any], ds: list[Decision]) -> list[bool]:
    return [d.ok and d.label == r["label"] for r, d in zip(run["rows"], ds, strict=True)]


def summarize(run: dict[str, Any]) -> dict[str, Any]:
    ds = _decisions(run)
    gold = [r["label"] for r in run["rows"]]
    correct = _correct(run, ds)
    ok = [d for d in ds if d.ok]
    lat = [d.latency_ms for d in ok]
    lo, hi = metrics.bootstrap_ci([float(c) for c in correct])
    out: dict[str, Any] = {
        "task": run["task"],
        "backend": run["backend"],
        "model_id": run["model_id"],
        "resolved_model_ids": run["resolved_model_ids"],
        "mock": run["mock"],
        "synthetic": run["synthetic"],
        "n": run["n"],
        "accuracy": metrics.accuracy([True] * len(correct), correct),  # failures count as wrong
        "accuracy_ci95": [lo, hi],
        "macro_f1": metrics.macro_f1(gold, [d.label if d.ok else None for d in ds]),
        "latency_ms": {f"p{q}": metrics.percentile(lat, q) for q in (50, 95, 99)},
        "cost_per_1000_usd": metrics.cost_per_1000([d.cost_usd for d in ds]),
        **metrics.rates([d.error_kind for d in ds]),
        "retried_calls": sum(d.retries > 0 for d in ds),
        "workers": run["workers"],
    }
    confs = [(d.confidence, c) for d, c in zip(ds, correct, strict=True) if d.ok and d.confidence is not None]
    out["ece"] = metrics.expected_calibration_error([c for c, _ in confs], [k for _, k in confs])
    out["reliability"] = metrics.reliability_bins([c for c, _ in confs], [k for _, k in confs])
    if run["question"] == "Score":
        pairs = [
            (int(r["label"]), int(d.label))
            for r, d in zip(run["rows"], ds, strict=True)
            if d.ok and d.label is not None
        ]
        out["mae_levels"] = metrics.mae([float(a) for a, _ in pairs], [float(b) for _, b in pairs])
    return out


def simulate_cascade(primary: dict[str, Any], fallback: dict[str, Any], tau: float) -> dict[str, Any]:
    """Replay a cascade offline from two cached runs over the same examples (no new API calls)."""
    if [r["id"] for r in primary["rows"]] != [r["id"] for r in fallback["rows"]]:
        raise ValueError("primary and fallback runs cover different examples")
    chosen: list[Decision] = []
    escalated = fallback_failed = 0
    for pr, fr in zip(primary["rows"], fallback["rows"], strict=True):
        p, f = Decision.from_dict(pr["decision"]), Decision.from_dict(fr["decision"])
        if should_escalate(p, tau):
            chosen.append(combine(p, f))
            escalated += 1
            fallback_failed += not f.ok
        else:
            chosen.append(p)
    gold = [r["label"] for r in primary["rows"]]
    correct = [d.ok and d.label == g for d, g in zip(chosen, gold, strict=True)]
    lat = [d.latency_ms for d in chosen if d.ok]
    n = len(chosen)
    fb_correct = _correct(fallback, _decisions(fallback))
    pr_correct = _correct(primary, _decisions(primary))
    lo, hi = metrics.bootstrap_ci([float(c) for c in correct])
    # paired: same examples in both arms, so bootstrap the per-example difference
    dlo, dhi = metrics.bootstrap_ci([float(c) - float(f) for c, f in zip(correct, fb_correct, strict=True)])
    pr_lo, pr_hi = metrics.bootstrap_ci([float(c) - float(p) for c, p in zip(correct, pr_correct, strict=True)])
    return {
        "tau": tau,
        "accuracy": sum(correct) / n,
        "accuracy_ci_lo": lo,
        "accuracy_ci_hi": hi,
        "acc_minus_primary": (sum(correct) - sum(pr_correct)) / n,
        "acc_minus_primary_lo": pr_lo,
        "acc_minus_primary_hi": pr_hi,
        "acc_minus_fallback": (sum(correct) - sum(fb_correct)) / n,
        "acc_minus_fallback_lo": dlo,
        "acc_minus_fallback_hi": dhi,
        "pct_escalated": 100 * escalated / n,
        "fallback_failed_on_escalated": fallback_failed,
        "cost_per_1000_usd": metrics.cost_per_1000([d.cost_usd for d in chosen]),
        "latency_p50_ms": metrics.percentile(lat, 50),
        "latency_p95_ms": metrics.percentile(lat, 95),
    }


def parse_sweep(spec: str) -> list[float]:
    lo, hi, step = (float(x) for x in spec.split(":"))
    n = math.floor((hi - lo) / step + 1e-9)  # never step past hi
    taus = [round(lo + i * step, 4) for i in range(n + 1)]
    return taus + ([hi] if taus[-1] < hi else [])  # hi itself is always included, e.g. 0.5..0.95 and 0.99


def cascade_sweep(primary: dict[str, Any], fallback: dict[str, Any], taus: list[float]) -> dict[str, Any]:
    base = summarize(fallback)
    sp0 = summarize(primary)
    rows = []
    for tau in taus:
        r = simulate_cascade(primary, fallback, tau)
        r["accuracy_retained_vs_fallback"] = r["accuracy"] / base["accuracy"] if base["accuracy"] else math.nan
        r["cost_vs_primary"] = (
            r["cost_per_1000_usd"] / sp0["cost_per_1000_usd"] if sp0["cost_per_1000_usd"] else math.nan
        )
        r["cost_vs_fallback"] = (
            r["cost_per_1000_usd"] / base["cost_per_1000_usd"] if base["cost_per_1000_usd"] else math.nan
        )
        rows.append(r)
    sp = summarize(primary)
    return {
        "task": primary["task"],
        "primary": primary["backend"],
        "fallback": fallback["backend"],
        "mock": primary["mock"] or fallback["mock"],
        "n": primary["n"],
        "primary_only": {
            "accuracy": sp["accuracy"],
            "cost_per_1000_usd": sp["cost_per_1000_usd"],
            "latency_ms": sp["latency_ms"],
        },
        "fallback_only": {
            "accuracy": base["accuracy"],
            "failure_rate": base["invalid_rate"] + base["error_rate"],
            "invalid_rate": base["invalid_rate"],
            "error_rate": base["error_rate"],
            "cost_per_1000_usd": base["cost_per_1000_usd"],
            "latency_ms": base["latency_ms"],
        },
        "sweep": rows,
        "selected": best_thresholds({"sweep": rows, "fallback_only": {"accuracy": base["accuracy"]}}),
        "acceptable_accuracy_drop": ACCEPTABLE_DROP,
    }


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact McNemar p-value: under H0 the discordant pairs split 50/50 (binomial)."""
    n = only_a + only_b
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(only_a, only_b) + 1)) / 2**n
    return float(min(1.0, 2 * tail))


def paired_comparison(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Backend a vs backend b on identical example ids. Failed calls count as wrong. No API calls."""
    if [r["id"] for r in a["rows"]] != [r["id"] for r in b["rows"]]:
        raise ValueError("runs cover different examples")
    ca, cb = _correct(a, _decisions(a)), _correct(b, _decisions(b))
    only_a = sum(x and not y for x, y in zip(ca, cb, strict=True))
    only_b = sum(y and not x for x, y in zip(ca, cb, strict=True))
    lo, hi = metrics.bootstrap_ci([float(x) - float(y) for x, y in zip(ca, cb, strict=True)])
    la, lb = summarize(a)["latency_ms"], summarize(b)["latency_ms"]
    return {
        "task": a["task"],
        "a": a["backend"],
        "b": b["backend"],
        "n": len(ca),
        "both_right": sum(x and y for x, y in zip(ca, cb, strict=True)),
        "both_wrong": sum(not x and not y for x, y in zip(ca, cb, strict=True)),
        "only_a_right": only_a,
        "only_b_right": only_b,
        "acc_a_minus_b": (sum(ca) - sum(cb)) / len(ca),
        "acc_a_minus_b_lo": lo,
        "acc_a_minus_b_hi": hi,
        "mcnemar_exact_p": mcnemar_exact(only_a, only_b),
        "latency_p95_over_p50_a": la["p95"] / la["p50"],
        "latency_p95_over_p50_b": lb["p95"] / lb["p50"],
    }


ACCEPTABLE_DROP = 0.01  # "acceptable accuracy": within 1 percentage point of the fallback-only baseline


def best_thresholds(result: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
    """Two fixed selection rules, stated up front so the threshold is not cherry-picked after the fact.

    max_accuracy:         highest cascade accuracy (ties: lowest cost).
    cheapest_acceptable:  lowest cost among thresholds whose accuracy is within ACCEPTABLE_DROP of fallback-only.
    """
    sweep = result["sweep"]
    most = max(sweep, key=lambda r: (r["accuracy"], -r["cost_per_1000_usd"]))
    floor = result["fallback_only"]["accuracy"] - ACCEPTABLE_DROP
    ok = [r for r in sweep if r["accuracy"] >= floor]
    cheap = min(ok, key=lambda r: (r["cost_per_1000_usd"], r["tau"])) if ok else None
    return {"max_accuracy": most, "cheapest_acceptable": cheap}


def error_breakdown(run: dict[str, Any], k: int = 3) -> dict[str, Any]:
    """Where a backend is wrong. Failed calls count as wrong. Yes/no: direction of error and violated categories."""
    ds = _decisions(run)
    wrong = [(r, d) for r, d, c in zip(run["rows"], ds, _correct(run, ds), strict=True) if not c]
    if run["question"] == "YesNo":
        total_cat: Counter[str] = Counter()
        missed_cat: Counter[str] = Counter()
        for r in run["rows"]:
            for c in (r.get("group") or "").split(", "):
                if c:
                    total_cat[c] += 1
        for r, _ in wrong:
            for c in (r.get("group") or "").split(", "):
                if c:
                    missed_cat[c] += 1
        return {
            "kind": "yes_no",
            "n_wrong": len(wrong),
            "missed_positive": sum(r["label"] is True for r, _ in wrong),
            "false_alarm": sum(r["label"] is False for r, _ in wrong),
            "top_categories": [
                {"category": c, "wrong": w, "total": total_cat[c]} for c, w in missed_cat.most_common(k)
            ],
        }
    totals = Counter(str(r["label"]) for r in run["rows"])
    per_class: Counter[str] = Counter(str(r["label"]) for r, _ in wrong)
    confused: dict[str, Counter[str]] = {}
    for r, d in wrong:
        confused.setdefault(str(r["label"]), Counter())[str(d.label) if d.ok else "(failed call)"] += 1
    ranked = sorted(per_class.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    return {
        "kind": "classes",
        "n_wrong": len(wrong),
        "top_classes": [
            {
                "label": c,
                "wrong": w,
                "total": totals[c],
                "most_common_wrong_prediction": confused[c].most_common(1)[0][0],
            }
            for c, w in ranked
        ],
    }


def write_cascade(result: dict[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{result['task']}__{result['primary']}__{result['fallback']}"
    (directory / f"{stem}.json").write_text(json.dumps(result, indent=1))
    with open(directory / f"{stem}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(result["sweep"][0]))
        w.writeheader()
        w.writerows(result["sweep"])
    return directory / f"{stem}.json"
