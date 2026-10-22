"""Hugging Face dataset loaders. `datasets` is an optional dependency (pip install '.[data]')."""

from __future__ import annotations

from typing import Any

from jev_bench.tasks import Example, sample

BANKING77_REDUCED = (
    "card_arrival",
    "card_not_working",
    "lost_or_stolen_card",
    "cash_withdrawal_charge",
    "exchange_rate",
    "pending_transfer",
    "request_refund",
    "top_up_failed",
    "transfer_not_received_by_recipient",
    "verify_my_identity",
)


BANKING77 = "legacy-datasets/banking77"  # parquet mirror of PolyAI/banking77 (the original is script-based)


def _load(name: str, split: str, revision: str | None = None) -> Any:
    try:
        from datasets import load_dataset
    except ImportError as e:  # pragma: no cover
        raise SystemExit("install the data extra: pip install -e '.[data]'") from e
    return load_dataset(name, split=split, revision=revision)


def _revision(name: str, revision: str | None = None) -> str | None:
    try:
        from huggingface_hub import HfApi

        sha = HfApi().dataset_info(name, revision=revision).sha
        return str(sha) if sha else None
    except Exception:  # best effort; offline runs just omit it
        return None


def banking77_names() -> list[str]:
    ds = _load(BANKING77, "test")
    names: list[str] = ds.features["label"].names
    return names


def load_banking77(limit: int, seed: int, reduced: bool = False) -> tuple[list[Example], dict[str, Any]]:
    ds = _load(BANKING77, "test")
    names: list[str] = ds.features["label"].names
    if reduced and not set(BANKING77_REDUCED) <= set(names):
        raise ValueError("reduced class set does not match dataset labels")
    keep = set(BANKING77_REDUCED) if reduced else set(names)
    items = [Example(f"{i:05d}", r["text"], names[r["label"]]) for i, r in enumerate(ds) if names[r["label"]] in keep]
    return sample(items, limit, seed), {
        "dataset": BANKING77,
        "split": "test",
        "revision": _revision(BANKING77),
        "pool": len(items),
    }


def load_reviews(limit: int, seed: int) -> tuple[list[Example], dict[str, Any]]:
    name = "SetFit/amazon_reviews_multi_en"
    ds = _load(name, "test")
    items = [Example(f"{i:05d}", r["text"], int(r["label"])) for i, r in enumerate(ds)]
    return sample(items, limit, seed), {
        "dataset": name,
        "split": "test",
        "revision": _revision(name),
        "pool": len(items),
    }


def load_aegis(limit: int, seed: int) -> tuple[list[Example], dict[str, Any]]:
    name = "nvidia/Aegis-AI-Content-Safety-Dataset-2.0"
    ds = _load(name, "test")
    items = [
        Example(f"{i:05d}", r["prompt"], r["prompt_label"] == "unsafe", r.get("violated_categories") or None)
        for i, r in enumerate(ds)
        if r.get("prompt") and r["prompt"] != "REDACTED" and r.get("prompt_label") in ("safe", "unsafe")
    ]
    return sample(items, limit, seed), {
        "dataset": name,
        "split": "test",
        "revision": _revision(name),
        "pool": len(items),
    }
