"""Config loading. Model versions live in config/models.yaml, prices in config/prices.yaml."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
RESULTS_DIR = ROOT / "results"
CACHE_PATH = ROOT / ".cache" / "jev-bench.sqlite3"


def load_yaml(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / name) as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict)
    return data


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader (KEY=VALUE lines). Existing environment variables win. Never logs values."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
