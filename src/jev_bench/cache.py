"""On-disk decision cache keyed by (backend, model_id, task, example_id[, request settings]).

Reruns are cheap and reproducible. Failures are cached too: a rerun does not silently retry them.
Use `--refresh` to ignore the cache.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from jev_bench.core import Decision


class Cache:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS d (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        self._lock = threading.Lock()

    @staticmethod
    def key(backend: str, model_id: str, task: str, example_id: str, settings: str = "") -> str:
        """`settings` (e.g. max_tokens, thinking mode) is part of the key only when set, so older keys stay valid."""
        return json.dumps([backend, model_id, task, example_id, *([settings] if settings else [])])

    def get(self, key: str) -> Decision | None:
        with self._lock:
            row = self._db.execute("SELECT v FROM d WHERE k=?", (key,)).fetchone()
        return Decision.from_dict(json.loads(row[0])) if row else None

    def put(self, key: str, decision: Decision) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO d VALUES (?, ?)", (key, json.dumps(decision.to_dict())))
            self._db.commit()
