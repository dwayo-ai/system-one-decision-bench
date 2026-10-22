"""Run one task against one backend, with caching, a fixed sample and warm-up excluded."""

from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from jev_bench.cache import Cache
from jev_bench.config import RESULTS_DIR, ROOT
from jev_bench.core import Decider, Decision
from jev_bench.tasks import DEFAULT_LIMIT, Task

WARMUP_CALLS = 3
DEFAULT_WORKERS = 4


def git_state() -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        return sha + ("+dirty" if dirty else "")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "uncommitted"


def run_path(task: str, backend: str) -> Path:
    return RESULTS_DIR / "runs" / f"{task}__{backend}.json"


def run_task(
    task: Task,
    decider: Decider,
    cache: Cache,
    limit: int = DEFAULT_LIMIT,
    seed: int = 0,
    workers: int = DEFAULT_WORKERS,
    refresh: bool = False,
    warmup: int = WARMUP_CALLS,
    out: Path | None = None,
) -> dict[str, Any]:
    """Returns the run record (also written to results/runs/<task>__<backend>.json)."""
    examples, meta = task.load(limit, seed)
    if hasattr(decider, "truth"):  # MockDecider needs gold to be right ~accuracy of the time
        decider.truth.update({e.text: e.label for e in examples})

    # the question text is part of the key, so editing a prompt or rubric can never reuse stale answers
    task_key = f"{task.name}@{hashlib.sha256(repr(task.question).encode()).hexdigest()[:8]}"
    settings = getattr(decider, "cache_settings", "")
    keys = [Cache.key(decider.name, decider.model_id, task_key, e.id, settings) for e in examples]
    cached = [None if refresh else cache.get(k) for k in keys]
    todo = [i for i, c in enumerate(cached) if c is None]

    if todo and warmup:  # warm connections/servers; these calls are discarded, never cached or timed
        for e in examples[:warmup]:
            decider.decide(task.question, e.text)

    def call(i: int) -> Decision:
        return decider.decide(task.question, examples[i].text)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, d in zip(todo, pool.map(call, todo), strict=True):
            cached[i] = d
            cache.put(keys[i], d)

    decisions: list[Decision] = [d for d in cached if d is not None]
    record = {
        "task": task.name,
        "backend": decider.name,
        "model_id": decider.model_id,
        "resolved_model_ids": sorted({d.model_id for d in decisions if d.ok}),
        "mock": decider.is_mock,
        "synthetic": task.synthetic,
        "question": task.question.__class__.__name__,
        "n": len(examples),
        "seed": seed,
        "limit": limit,
        "workers": workers,
        "max_tokens": getattr(decider, "max_tokens", None),
        "thinking": getattr(decider, "thinking", None),
        "warmup_calls": warmup,
        "cache_hits": len(examples) - len(todo),
        "dataset": meta,
        "source": task.source,
        "license": task.license,
        "date": datetime.date.today().isoformat(),
        "git": git_state(),
        "rows": [
            {"id": e.id, "label": e.label, **({"group": e.group} if e.group else {}), "decision": d.to_dict()}
            for e, d in zip(examples, decisions, strict=True)
        ],
    }
    path = out or run_path(task.name, decider.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1))
    return record
