"""Build a Decider from a backend name in config/models.yaml."""

from __future__ import annotations

from typing import Any

from jev_bench.config import load_yaml
from jev_bench.core import Decider
from jev_bench.deciders.chat import ChatLLMDecider
from jev_bench.deciders.jev_http import JevHttpDecider
from jev_bench.deciders.mock import MockDecider


def list_backends() -> dict[str, dict[str, Any]]:
    backends: dict[str, dict[str, Any]] = load_yaml("models.yaml")["backends"]
    return backends


def build_decider(name: str) -> Decider:
    cfg = list_backends()[name]
    kind = cfg["kind"]
    if kind == "mock":
        return MockDecider(
            name=name,
            seed=cfg.get("seed", 0),
            accuracy=cfg.get("accuracy", 0.85),
            latency_ms=cfg.get("latency_ms", 120.0),
            price_per_mtok=cfg.get("price_per_mtok", 0.042),
        )
    price = load_yaml("prices.yaml")["prices"][cfg["price_key"]]
    if kind == "jev_http":
        return JevHttpDecider.from_config(name, cfg, price)
    if kind == "chat":
        return ChatLLMDecider.from_config(name, cfg, price)
    raise ValueError(f"unknown backend kind {kind!r}")
