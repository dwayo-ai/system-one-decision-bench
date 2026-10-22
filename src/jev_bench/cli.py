"""jev-bench run | cascade | report | list"""

from __future__ import annotations

import argparse

from jev_bench import analysis
from jev_bench.backends import build_decider, list_backends
from jev_bench.cache import Cache
from jev_bench.config import CACHE_PATH, RESULTS_DIR, load_dotenv
from jev_bench.runner import DEFAULT_WORKERS, run_task
from jev_bench.tasks import DEFAULT_LIMIT, get_task


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--task", required=True)
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="examples per task (default 500)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="concurrent calls in flight")
    p.add_argument("--refresh", action="store_true", help="ignore the on-disk cache")


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="jev-bench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a task against one or more backends (comma-separated)")
    _common(r)
    r.add_argument("--backend", required=True)
    c = sub.add_parser("cascade", help="sweep the escalation threshold for primary > fallback")
    _common(c)
    c.add_argument("--primary", default="jev")
    c.add_argument("--fallback", default="chat-small")
    c.add_argument("--sweep", default="0.5:0.99:0.05", help="lo:hi:step")
    sub.add_parser("report", help="regenerate results/REPORT.md and charts from results/runs")
    sub.add_parser("list", help="list configured backends")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        for name, cfg in list_backends().items():
            print(f"{name:16} {cfg['kind']:9} {cfg.get('model', '')}")
        return
    if a.cmd == "report":
        from jev_bench.report import build_report

        print(build_report())
        return

    cache = Cache(CACHE_PATH)
    task = get_task(a.task)
    names = a.backend.split(",") if a.cmd == "run" else [a.primary, a.fallback]
    records = {}
    for name in names:
        rec = run_task(task, build_decider(name), cache, a.limit, a.seed, a.workers, a.refresh)
        records[name] = rec
        s = analysis.summarize(rec)
        tag = " [MOCK]" if rec["mock"] else ""
        print(
            f"{name}{tag}: n={s['n']} acc={s['accuracy']:.3f} p50={s['latency_ms']['p50']:.0f}ms "
            f"$/1k={s['cost_per_1000_usd']:.4f} invalid={s['invalid_rate']:.3f} error={s['error_rate']:.3f}"
        )
    if a.cmd == "cascade":
        result = analysis.cascade_sweep(records[a.primary], records[a.fallback], analysis.parse_sweep(a.sweep))
        path = analysis.write_cascade(result, RESULTS_DIR / "cascade")
        print(f"wrote {path} (+ .csv)")


if __name__ == "__main__":
    main()
