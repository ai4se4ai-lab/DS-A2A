#!/usr/bin/env python3
"""Fetches a sample of `princeton-nlp/SWE-bench_Lite` (Sec V "Prototype":
the evaluation runs against public benchmark tasks) via the HuggingFace
`datasets` library and writes a normalized JSONL file that the rest of
`evaluation/` reads through `evaluation.harness.common.load_tasks`.

    python evaluation/benchmarks/fetch_swebench_lite.py --n 20 --seed 0
    python evaluation/benchmarks/fetch_swebench_lite.py --full

If `datasets` isn't installed or there is no network access, this script
fails with a clear message rather than a bare traceback, and
`evaluation/benchmarks/fixtures/toy_tasks.jsonl` (three small hand-written,
clearly-synthetic "SWE-bench-shaped" records) is the documented manual
fallback: every harness script that takes `--tasks-file` accepts either a
real cache file written here or that fixture file, so nothing downstream
requires a live download to be exercised (see evaluation/README.md).
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

DATASET_ID = "princeton-nlp/SWE-bench_Lite"
CACHE_DIR = Path(__file__).resolve().parent / "cache"

# Fields passed through verbatim from the real dataset schema when present;
# every downstream consumer treats a missing field as "" / [] rather than
# crashing, so this list is a courtesy subset, not a strict contract.
_PASSTHROUGH_FIELDS = [
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "patch",
    "test_patch",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "version",
    "created_at",
]


class FetchError(RuntimeError):
    pass


def _normalize_record(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field, "" if field != "FAIL_TO_PASS" and field != "PASS_TO_PASS" else "[]") for field in _PASSTHROUGH_FIELDS}


def fetch_sample(n: int = 20, *, seed: int = 0, split: str = "test", full: bool = False) -> list[dict[str, Any]]:
    """Loads SWE-bench_Lite and returns `n` reproducibly-sampled, normalized
    task records (or all of them if `full=True`). Raises FetchError with a
    human-readable message on any failure (missing package, no network,
    HF Hub error, unexpected schema) -- callers should catch this and fall
    back to the fixture file rather than letting the pipeline die.
    """
    try:
        import datasets
    except ImportError as exc:
        raise FetchError(
            "the `datasets` package is not installed. Install it with "
            "`pip install -e .[eval]`, or use "
            "evaluation/benchmarks/fixtures/toy_tasks.jsonl via --tasks-file "
            "for the rest of the pipeline in the meantime."
        ) from exc

    try:
        ds = datasets.load_dataset(DATASET_ID, split=split)
    except Exception as exc:  # noqa: BLE001 - network/HF Hub errors of many shapes
        raise FetchError(
            f"could not fetch '{DATASET_ID}' (split={split}) from the HuggingFace "
            f"Hub: {exc}. If this is a sandboxed/offline environment, use "
            "evaluation/benchmarks/fixtures/toy_tasks.jsonl via --tasks-file "
            "instead, or download the dataset manually on a machine with "
            "network access and copy the resulting JSONL into "
            "evaluation/benchmarks/cache/."
        ) from exc

    if full:
        indices = list(range(len(ds)))
    else:
        n = min(n, len(ds))
        indices = sorted(random.Random(seed).sample(range(len(ds)), n))

    return [_normalize_record(ds[i]) for i in indices]


def write_jsonl(records: list[dict[str, Any]], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=20, help="sample size (default: 20)")
    parser.add_argument("--seed", type=int, default=0, help="sampling seed for reproducibility")
    parser.add_argument("--split", default="test", help="dataset split (default: test)")
    parser.add_argument("--full", action="store_true", help="fetch all ~300 instances instead of a sample")
    parser.add_argument("--out", default=None, help="output JSONL path (default: cache/swebench_lite_{sample,full}.jsonl)")
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else CACHE_DIR / f"swebench_lite_{'full' if args.full else 'sample'}.jsonl"

    try:
        records = fetch_sample(args.n, seed=args.seed, split=args.split, full=args.full)
    except FetchError as exc:
        print(f"error: {exc}")
        return 1

    written = write_jsonl(records, out_path)
    print(f"Wrote {len(records)} task(s) to {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
