"""Regenerate results/REPORT.md, tables (json/csv) and charts from committed run records."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from jev_bench.analysis import error_breakdown, paired_comparison, summarize  # noqa: E402
from jev_bench.config import RESULTS_DIR, load_yaml  # noqa: E402
from jev_bench.core import Decision  # noqa: E402

VENDOR = {
    "latency": "70-500 ms end to end (reported by third-party write-ups; Jev's own docs give no latency figure)",
    "price": "$0.042 per million input tokens, output free (Jev docs, models page, retrieved 2026-10-22)",
}


def _f(x: float, nd: int = 3) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


NOT_TESTED = [
    "The cascade was tested only with a fallback that is no more accurate than Jev (on this run chat-small is lower on banking77, a clear gap in the paired comparison, and within noise on banking77-10 and guardrail), so it says nothing about cascades with a stronger fallback.",
    "Frontier models were not benchmarked. The only chat model here is a small one, so a cascade win against it says nothing about a frontier fallback.",
    "Gateways other than direct HTTP (LiteLLM, Vercel AI Gateway, Portkey, TrueFoundry, Requesty/OpenRouter, Arize) were not tested live.",
    "Results are a snapshot on the run date with pinned model IDs (config/models.yaml); a different model version or day can give different numbers.",
]


def list_prices(backends: set[str]) -> list[str]:
    """One line per priced backend: price, retrieval date and source, from config/prices.yaml."""
    cfg, prices = load_yaml("models.yaml")["backends"], load_yaml("prices.yaml")["prices"]
    out = []
    for b in sorted(backends):
        key = cfg.get(b, {}).get("price_key")
        if key in prices and cfg[b]["kind"] != "mock":
            p = prices[key]
            out.append(
                f"{b} ({key}): ${p['input_per_mtok']} in / ${p['output_per_mtok']} out per million tokens, "
                f"list price on {p['retrieved']} ({p['source']})"
            )
    return out


def _ci(lo: float, hi: float) -> str:
    return f"[{_f(lo)}, {_f(hi)}]"


def summary_tables(sums: list[dict[str, Any]], cascades: list[dict[str, Any]]) -> list[str]:
    """Headline tables for real (non-mock) cascades: accuracy and ECE, then cost and latency vs fallback-only."""
    real = [c for c in cascades if not c["mock"]]
    if not real:
        return []
    by = {(s["task"], s["backend"]): s for s in sums}
    A = [
        "## Summary: Jev alone, fallback alone, cascade",
        "",
        "Threshold rules are fixed in advance: **max accuracy** (ties: cheaper) and **cheapest acceptable** "
        "(lowest list-price cost among thresholds within 1 percentage point of fallback-only accuracy). "
        "Cascade accuracy CIs are 95% percentile bootstrap over examples.",
        "",
        "| task | fallback | Jev acc [CI] | fallback acc [CI] | cascade, max accuracy: acc [CI] (tau) | "
        "cascade, cheapest acceptable: acc [CI] (tau) | ECE Jev | ECE fallback | fallback failure rate (invalid / API error) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    B = [
        "",
        "Cost is list price on the retrieval date above, not measured spend. Latency is measured client-side; an escalated call pays Jev's latency plus the fallback's.",
        "",
        "| task | fallback | rule | tau | escalated % | $/1k cascade vs fallback-only | p50 / p95 ms cascade vs fallback-only | acc minus fallback-only [95% CI] | acc minus Jev-only [95% CI] | cost vs Jev-only |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    V: list[str] = [""]
    for c in sorted(real, key=lambda c: c["task"]):
        j, f = by.get((c["task"], c["primary"])), by.get((c["task"], c["fallback"]))
        if not j or not f:
            continue
        sel = c["selected"]
        mx, ch = sel["max_accuracy"], sel["cheapest_acceptable"]

        def cell(r: dict[str, Any] | None) -> str:
            if r is None:
                return "none within 1 pp"
            return f"{_f(r['accuracy'])} {_ci(r['accuracy_ci_lo'], r['accuracy_ci_hi'])} (tau {r['tau']:.2f})"

        A.append(
            f"| {c['task']} | {c['fallback']} | {_f(j['accuracy'])} {_ci(*j['accuracy_ci95'])} | "
            f"{_f(f['accuracy'])} {_ci(*f['accuracy_ci95'])} | {cell(mx)} | {cell(ch)} | {_f(j['ece'])} | {_f(f['ece'])} | "
            f"{_f(f['invalid_rate'] + f['error_rate'])} ({_f(f['invalid_rate'])} / {_f(f['error_rate'])}) |"
        )
        fl = c["fallback_only"]["latency_ms"]
        for rule, r in (("max accuracy", mx), ("cheapest acceptable", ch)):
            if r is None:
                continue
            B.append(
                f"| {c['task']} | {c['fallback']} | {rule} | {r['tau']:.2f} | {_f(r['pct_escalated'], 1)} | "
                f"${_f(r['cost_per_1000_usd'], 4)} vs ${_f(f['cost_per_1000_usd'], 4)} ({_f(r['cost_vs_fallback'])}x) | "
                f"{_f(r['latency_p50_ms'], 0)} / {_f(r['latency_p95_ms'], 0)} vs {_f(fl['p50'], 0)} / {_f(fl['p95'], 0)} | "
                f"{r['acc_minus_fallback']:+.3f} [{r['acc_minus_fallback_lo']:+.3f}, {r['acc_minus_fallback_hi']:+.3f}] | "
                f"{r['acc_minus_primary']:+.3f} [{r['acc_minus_primary_lo']:+.3f}, {r['acc_minus_primary_hi']:+.3f}] | "
                f"{_f(r['cost_vs_primary'])}x |"
            )
        if ch is None:
            V.append(
                f"- **{c['task']}**: no threshold keeps cascade accuracy within 1 pp of {c['fallback']}-only "
                f"({_f(f['accuracy'])}); the cascade does not match the fallback here."
            )
            continue
        vs_fb = (
            f"costs {_f(ch['cost_vs_fallback'])}x the {c['fallback']}-only baseline at list price"
            if ch["cost_vs_fallback"] < 1
            else f"**does not beat the {c['fallback']}-only baseline on cost** ({_f(ch['cost_vs_fallback'])}x at list price)"
        )
        no_gain = ch["acc_minus_primary_lo"] <= 0 <= ch["acc_minus_primary_hi"]
        vs_jev = (
            f"Against Jev alone it adds {ch['acc_minus_primary']:+.3f} accuracy "
            f"[{ch['acc_minus_primary_lo']:+.3f}, {ch['acc_minus_primary_hi']:+.3f}] at {_f(ch['cost_vs_primary'])}x Jev's cost"
            + (": **no measurable accuracy gain over Jev alone**, so the saving versus the fallback comes from Jev itself, not from the cascade." if no_gain else ".")
        )
        V.append(
            f"- **{c['task']}**: at tau {ch['tau']:.2f} ({_f(ch['pct_escalated'], 1)}% escalated) the cascade {vs_fb}; "
            f"accuracy difference vs fallback-only {ch['acc_minus_fallback']:+.3f} "
            f"[{ch['acc_minus_fallback_lo']:+.3f}, {ch['acc_minus_fallback_hi']:+.3f}]. {vs_jev}"
        )
    return A + B + V + [""]


def baseline_config(run: dict[str, Any]) -> str:
    """Exact chat-baseline request configuration from the run record, e.g. 'claude-haiku-5-5, thinking disabled, max_tokens=128'."""
    mode = "disabled" if run.get("thinking") == "disabled" else "default"
    return f"{run['model_id']}, thinking {mode}, max_tokens={run['max_tokens']}"


def paired_section(runs: list[dict[str, Any]]) -> list[str]:
    """Jev vs the chat baseline on identical example ids: paired bootstrap CI on the accuracy difference and exact McNemar."""
    rows = []
    for t in sorted({r["task"] for r in runs}):
        a = next((r for r in runs if r["task"] == t and r["backend"] == "jev" and not r["mock"]), None)
        b = next((r for r in runs if r["task"] == t and r["backend"] == "chat-small" and not r["mock"]), None)
        if a and b:
            p = paired_comparison(a, b)
            rows.append(
                f"| {t} | {p['n']} | {p['both_right']} | {p['only_a_right']} | {p['only_b_right']} | {p['both_wrong']} | "
                f"{p['acc_a_minus_b']:+.3f} [{p['acc_a_minus_b_lo']:+.3f}, {p['acc_a_minus_b_hi']:+.3f}] | "
                f"{p['mcnemar_exact_p']:.4f} | {p['latency_p95_over_p50_a']:.2f} | {p['latency_p95_over_p50_b']:.2f} |"
            )
    if not rows:
        return []
    return [
        "## Paired comparison: Jev vs chat-small on the same example ids",
        "",
        "Accuracy difference is Jev minus chat-small (paired percentile bootstrap, 95% CI). McNemar is the exact two-sided test on the "
        "discordant pairs (one model right, the other wrong). Failed calls count as wrong. Three tasks were tested with no multiplicity correction. "
        "Latency ratio is measured p95 / p50 per backend (tail heaviness).",
        "",
        "| task | n | both right | only Jev right | only chat-small right | both wrong | acc Jev minus chat-small [95% CI] | McNemar exact p | Jev p95/p50 | chat-small p95/p50 |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *rows,
        "",
    ]


def class_count_section(sums: list[dict[str, Any]]) -> list[str]:
    small, big = "banking77-10", "banking77"
    backs = sorted({s["backend"] for s in sums if s["task"] == big and not s["mock"]})
    rows = []
    for b in backs:
        a = next((s for s in sums if s["task"] == small and s["backend"] == b), None)
        z = next((s for s in sums if s["task"] == big and s["backend"] == b), None)
        if a and z:
            rows.append(
                f"| {b} | {_f(a['accuracy'])} {_ci(*a['accuracy_ci95'])} (n={a['n']}) | "
                f"{_f(z['accuracy'])} {_ci(*z['accuracy_ci95'])} (n={z['n']}) | {a['accuracy'] - z['accuracy']:+.3f} |"
            )
    if not rows:
        return []
    return [
        "## Accuracy by class count: banking77-10 vs banking77",
        "",
        "| backend | 10 classes | 77 classes | difference |",
        "|---|---|---|---|",
        *rows,
        "",
        "Caveat: the 10 classes are hand-picked and the example sets differ, so this is a comparison of two tasks, not a controlled test of class count alone.",
        "",
    ]


def reliability_table(s: dict[str, Any]) -> list[str]:
    if not s["reliability"]:
        return []
    return [
        f"Reliability data, {_label(s)} (ECE {_f(s['ece'])}):",
        "",
        "| confidence bin | n | mean confidence | accuracy |",
        "|---|---|---|---|",
        *(
            f"| {b['lo']:.1f}-{b['hi']:.1f} | {b['n']} | {_f(b['mean_confidence'])} | {_f(b['accuracy'])} |"
            for b in s["reliability"]
        ),
        "",
    ]


def error_lines(run: dict[str, Any]) -> list[str]:
    e = error_breakdown(run)
    head = f"Where {run['backend']} is wrong ({e['n_wrong']} of {run['n']}; failed calls count as wrong):"
    if e["kind"] == "yes_no":
        cats = "; ".join(f"{c['category']} ({c['wrong']} of {c['total']})" for c in e["top_categories"]) or "n/a"
        return [
            head,
            "",
            f"- Missed unsafe: {e['missed_positive']}; false alarms on safe: {e['false_alarm']}.",
            f"- Violated categories most often wrong (wrong of total, a message can have several): {cats}.",
            "",
        ]
    return [
        head,
        "",
        *(
            f"- `{c['label']}`: {c['wrong']} of {c['total']} wrong, most often predicted as `{c['most_common_wrong_prediction']}`."
            for c in e["top_classes"]
        ),
        "",
    ]


def load_runs(results: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted((results / "runs").glob("*.json"))]


def load_cascades(results: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted((results / "cascade").glob("*.json"))]


def _label(s: dict[str, Any]) -> str:
    return str(s["backend"]) + (" (MOCK)" if s["mock"] else "")


def chart_accuracy_vs_cost(sums: list[dict[str, Any]], out: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for s in sums:
        ax.scatter(max(s["cost_per_1000_usd"], 1e-6), s["accuracy"], marker="x" if s["mock"] else "o")
        ax.annotate(f"{_label(s)}\n{s['task']}", (max(s["cost_per_1000_usd"], 1e-6), s["accuracy"]), fontsize=6)
    ax.set_xscale("log")
    ax.set_xlabel("cost per 1,000 decisions (USD, log scale)")
    ax.set_ylabel("accuracy")
    ax.set_title("Accuracy vs cost")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def chart_calibration(task: str, sums: list[dict[str, Any]], out: Path) -> None:
    fig, ax = plt.subplots(figsize=(4.5, 4.5))
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="perfectly calibrated")
    for s in sums:
        b = s["reliability"]
        ax.plot(
            [x["mean_confidence"] for x in b],
            [x["accuracy"] for x in b],
            marker="o",
            label=f"{_label(s)} (ECE {_f(s['ece'])})",
        )
    ax.set_xlabel("mean confidence in bin")
    ax.set_ylabel("accuracy in bin")
    ax.set_title(f"Calibration: {task}")
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def chart_latency(task: str, runs: list[dict[str, Any]], out: Path) -> None:
    data, labels = [], []
    for r in runs:
        lat = [Decision.from_dict(x["decision"]).latency_ms for x in r["rows"] if x["decision"]["error"] is None]
        if lat:
            data.append(lat)
            labels.append(r["backend"] + (" (MOCK)" if r["mock"] else ""))
    if not data:
        return
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.boxplot(data, vert=False, tick_labels=labels, showfliers=True)
    ax.set_xscale("log")
    ax.set_xlabel("client-side latency per call (ms, log scale)")
    ax.set_title(f"Latency: {task}")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def chart_cascade(c: dict[str, Any], out: Path) -> None:
    taus = [r["tau"] for r in c["sweep"]]
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    ax.plot(
        taus,
        [100 * r["accuracy_retained_vs_fallback"] for r in c["sweep"]],
        marker="o",
        label="accuracy retained vs fallback-only (%)",
    )
    ax.plot(
        taus,
        [100 * (1 - r["cost_vs_fallback"]) for r in c["sweep"]],
        marker="s",
        label="cost saved vs fallback-only (%)",
    )
    ax.plot(taus, [r["pct_escalated"] for r in c["sweep"]], marker="^", ls=":", label="calls escalated (%)")
    ax.set_xlabel("threshold tau (escalate if top probability < tau)")
    ax.set_ylabel("percent")
    ax.set_title(f"Cascade {c['primary']} > {c['fallback']}: {c['task']}" + (" [MOCK]" if c["mock"] else ""))
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def build_report(results: Path = RESULTS_DIR) -> Path:
    runs, cascades = load_runs(results), load_cascades(results)
    if not runs:
        raise SystemExit("no runs found in results/runs; run `jev-bench run` first")
    charts = results / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    sums = [summarize(r) for r in runs]
    any_mock = any(s["mock"] for s in sums)
    all_mock = all(s["mock"] for s in sums)

    (results / "summary.json").write_text(json.dumps(sums, indent=1))
    with open(results / "summary.csv", "w", newline="") as f:
        cols = [
            "task",
            "backend",
            "model_id",
            "mock",
            "synthetic",
            "n",
            "accuracy",
            "macro_f1",
            "ece",
            "invalid_rate",
            "error_rate",
            "cost_per_1000_usd",
        ]
        w = csv.writer(f)
        w.writerow(cols + ["latency_p50_ms", "latency_p95_ms", "latency_p99_ms"])
        for s in sums:
            w.writerow([s[c] for c in cols] + [s["latency_ms"][k] for k in ("p50", "p95", "p99")])

    L: list[str] = ["# system-one-decision-bench report", ""]
    if any_mock:
        L += [
            "> **MOCK DATA.** "
            + ("Every number below comes" if all_mock else "Rows marked (MOCK) come")
            + " from the synthetic `mock` backend, not from Jev or any real model. Do not quote them.",
            "",
        ]
    first = runs[0]
    chat_cfg = sorted({baseline_config(r) for r in runs if r.get("max_tokens") and not r["mock"]})
    L += [
        f"- Run date(s): {', '.join(sorted({r['date'] for r in runs}))}",
        f"- Git: {', '.join(sorted({r['git'] for r in runs}))}",
        "- Methodology: fixed seed and sample, identical inputs to every backend, warm-up calls excluded from latency, "
        f"latency measured client-side with network included at concurrency {first['workers']} (calls in flight), "
        "failed or unparseable calls count as wrong and are reported separately, accuracy CIs are 95% percentile bootstrap.",
        "",
        "Cost columns are token usage x list price (not measured spend): " + "; ".join(list_prices({str(r["backend"]) for r in runs})) + ".",
        "",
        *(
            [
                "- **Chat baseline configuration: " + "; ".join(f"{c}" for c in chat_cfg) + ".**"
                + (
                    " Thinking is disabled, which is **not Haiku 5.5's default behaviour** (default: adaptive thinking on). "
                    "In an initial smoke test with thinking on and this token budget, a substantial share of calls spent all their tokens on thinking and returned no answer."
                    if any("thinking disabled" in c for c in chat_cfg)
                    else ""
                ),
                "",
            ]
            if chat_cfg
            else []
        ),
        "## Vendor claims vs what we measured",
        "",
        "| | Vendor-reported | Measured here (Jev rows, this run) |",
        "|---|---|---|",
    ]
    jev = [s for s in sums if s["backend"].startswith("jev") and not s["mock"]]
    if jev:
        p50 = ", ".join(
            f"{s['task']}: {_f(s['latency_ms']['p50'], 0)} / {_f(s['latency_ms']['p95'], 0)} ms" for s in jev
        )
        cost = ", ".join(f"{s['task']}: ${_f(s['cost_per_1000_usd'], 4)}" for s in jev)
    else:
        p50 = cost = "no real Jev run yet"
    L += [
        f"| Latency (p50 / p95) | {VENDOR['latency']} | {p50} |",
        f"| Cost per 1,000 decisions | {VENDOR['price']} | {cost} |",
        "",
    ]

    if not any(not s["mock"] and not s["backend"].startswith("jev") for s in sums):
        L += [
            "> **No real chat-model baseline in this report.** Only Jev has been run for real, so nothing here says how Jev "
            "compares with a chat model. Accuracy numbers stand alone.",
            "",
        ]
    tasks = sorted({s["task"] for s in sums})
    L += summary_tables(sums, cascades) + paired_section(runs) + class_count_section(sums)
    chart_accuracy_vs_cost(sums, charts / "accuracy_vs_cost.png")
    L += ["![accuracy vs cost](charts/accuracy_vs_cost.png)", ""]
    for t in tasks:
        ts = [s for s in sums if s["task"] == t]
        tr = [r for r in runs if r["task"] == t]
        r0 = tr[0]
        L += [
            f"## {t}",
            "",
            f"Source: {r0['source']} ({r0['license']}). n = {r0['n']}, seed {r0['seed']}"
            + (", **synthetic data, not for headline claims**" if r0["synthetic"] else "")
            + f". Dataset revision: {r0['dataset'].get('revision') or 'unknown'}.",
            "",
            "| backend | resolved model id | acc [95% CI] | macro-F1 | ECE | p50 / p95 / p99 ms (measured) | $/1k (list price) | invalid | error |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for s in ts:
            lat = s["latency_ms"]
            L.append(
                f"| {_label(s)} | {', '.join(s['resolved_model_ids']) or s['model_id']} | {_f(s['accuracy'])} "
                f"[{_f(s['accuracy_ci95'][0])}, {_f(s['accuracy_ci95'][1])}] | {_f(s['macro_f1'])} | {_f(s['ece'])} | "
                f"{_f(lat['p50'], 0)} / {_f(lat['p95'], 0)} / {_f(lat['p99'], 0)} | {_f(s['cost_per_1000_usd'], 4)} | "
                f"{_f(s['invalid_rate'], 3)} | {_f(s['error_rate'], 3)} |"
            )
        real = [s for s in ts if not s["mock"]]
        if len(real) > 1:
            best = max(real, key=lambda s: s["accuracy"])
            L += [
                "",
                f"Highest accuracy among real backends on this task: **{best['backend']}** ({_f(best['accuracy'])}).",
                "",
            ]
        else:
            L += [""]
        chart_calibration(t, ts, charts / f"calibration_{t}.png")
        chart_latency(t, tr, charts / f"latency_{t}.png")
        L += [f"![calibration](charts/calibration_{t}.png) ![latency](charts/latency_{t}.png)", ""]
        for s in ts:
            if not s["mock"]:
                L += reliability_table(s)
        for r in tr:
            if not r["mock"]:
                L += error_lines(r)
    for c in cascades:
        name = f"cascade_{c['task']}__{c['primary']}__{c['fallback']}.png"
        chart_cascade(c, charts / name)
        po, fo = c["primary_only"], c["fallback_only"]
        L += [
            f"## Cascade: {c['primary']} > {c['fallback']} on {c['task']}" + (" (MOCK)" if c["mock"] else ""),
            "",
            f"Primary only: accuracy {_f(po['accuracy'])}, ${_f(po['cost_per_1000_usd'], 4)}/1k. "
            f"Fallback only: accuracy {_f(fo['accuracy'])}, ${_f(fo['cost_per_1000_usd'], 4)}/1k.",
            "",
            "| tau | accuracy [95% CI] | acc minus fallback-only [95% CI] | escalated % | fallback failed on escalated | $/1k (list price) | cost vs fallback | p50 ms | p95 ms |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in c["sweep"]:
            L.append(
                f"| {r['tau']:.2f} | {_f(r['accuracy'])} {_ci(r['accuracy_ci_lo'], r['accuracy_ci_hi'])} | "
                f"{r['acc_minus_fallback']:+.3f} [{r['acc_minus_fallback_lo']:+.3f}, {r['acc_minus_fallback_hi']:+.3f}] | "
                f"{_f(r['pct_escalated'], 1)} | {r['fallback_failed_on_escalated']} | {_f(r['cost_per_1000_usd'], 4)} | {_f(r['cost_vs_fallback'])} | "
                f"{_f(r['latency_p50_ms'], 0)} | {_f(r['latency_p95_ms'], 0)} |"
            )
        L += ["", f"![cascade](charts/{name})", ""]
    L += [
        "## Limitations",
        "",
        "- Jev is a hosted, early-access model; this is a snapshot on the run date, not a permanent property of the model.",
        "- Calibration: ECE is reported as measured, with no confidence interval, from 400-500 examples and 10 equal-width bins. This report does not claim that either model is better calibrated.",
        "- The cascade was tested only with a fallback that is no more accurate than Jev, so its results say nothing about cascades with a stronger fallback.",
        "- Chat-model probabilities are *verbalised* confidences, not token probabilities, so their calibration is not comparable like-for-like with Jev's.",
        "- Cost is token usage x list price (config/prices.yaml); it ignores batch discounts, caching and volume terms.",
        "- Samples are capped (default 500 per task); intervals show the resulting uncertainty.",
        "- The best thresholds are picked on the same examples they are measured on, so the best-threshold accuracy and any gain over Jev alone are optimistic; the CIs do not account for choosing among several thresholds.",
        "- The cascade is replayed offline from two cached runs; an escalated call is charged both calls' cost and latency. If the fallback call fails or returns invalid output, the cascade keeps Jev's answer and the failure is counted separately.",
        *(
            [
                "- Chat baseline request settings: "
                + "; ".join(f"{r['backend']}: {baseline_config(r)}" for r in {r["backend"]: r for r in runs if r.get("max_tokens")}.values())
                + ". A call that runs out of tokens is an invalid output and is counted as a failure."
            ]
            if any(r.get("max_tokens") for r in runs)
            else []
        ),
        "",
        "## Not tested",
        "",
        *(f"- {x}" for x in NOT_TESTED),
        "",
    ]
    out = results / "REPORT.md"
    out.write_text("\n".join(L))
    return out
