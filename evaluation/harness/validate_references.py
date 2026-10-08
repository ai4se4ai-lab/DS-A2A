"""Reference-validity check: does each DevBench Python repo's *reference*
implementation pass its own unit and acceptance tests in this environment?

This defines the subject-inclusion rule for the evaluation (a repo whose own
reference fails its tests cannot discriminate between configurations), and
replaces the previously unrecorded claim that "reference implementations pass
their own tests". Output: <out>/reference_validity.csv.

    python -m evaluation.harness.validate_references \
        --out evaluation/results/follow-up-study/csv/reference_validity.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import tempfile
import time
from pathlib import Path

from .devbench_common import _run
from .devbench_loader import ALL_REPOS, load_devbench_task

_PASSED = re.compile(r"(\d+) passed")
_FAILED = re.compile(r"(\d+) failed")
_RAN = re.compile(r"Ran (\d+) tests?")


def _scrub(text: str) -> str:
    """Remove machine-specific absolute paths (user names, venv locations) from captured output."""
    for prefix in (str(Path.home()), str(Path(__file__).resolve().parents[2])):
        text = text.replace(prefix, "<path>")
    return re.sub(r"/tmp/[A-Za-z0-9_./-]+", "<tmp>", text)


def _counts(detail: str) -> tuple[int | None, int | None]:
    p, f = _PASSED.search(detail), _FAILED.search(detail)
    if p or f:
        return (int(p.group(1)) if p else 0), (int(f.group(1)) if f else 0)
    r = _RAN.search(detail)
    if r:
        return (int(r.group(1)) if "OK" in detail else None), (0 if "OK" in detail else None)
    return None, None


def validate(repo: str, timeout: float) -> dict:
    task = load_devbench_task(repo)
    scratch = Path(tempfile.mkdtemp(prefix=f"ref_{repo}_"))
    try:
        shutil.copytree(task.path, scratch, dirs_exist_ok=True)
        t0 = time.time()
        unit_ok, unit_detail = _run(task.unit_test_cmd, cwd=scratch, timeout=timeout)
        acc_ok, acc_detail = _run(task.acceptance_test_cmd, cwd=scratch, timeout=timeout)
        up, uf = _counts(unit_detail)
        ap, af = _counts(acc_detail)
        return {
            "repo": repo, "n_operations": len(task.operations), "n_target_files": len(task.target_files),
            "unit_pass": unit_ok, "unit_passed": up, "unit_failed": uf,
            "acceptance_pass": acc_ok, "acceptance_passed": ap, "acceptance_failed": af,
            "reference_valid": bool(unit_ok and acc_ok),
            "seconds": round(time.time() - t0, 1),
            "unit_tail": _scrub(unit_detail[-300:]).replace("\n", " | "),
            "acceptance_tail": _scrub(acc_detail[-300:]).replace("\n", " | "),
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", nargs="*", default=ALL_REPOS)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--out", default="evaluation/results/follow-up-study/csv/reference_validity.csv")
    args = ap.parse_args()
    rows = []
    for repo in args.repos:
        print(f"validating reference of {repo} ...", flush=True)
        row = validate(repo, args.timeout)
        print(f"  unit={row['unit_pass']} acceptance={row['acceptance_pass']}", flush=True)
        rows.append(row)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
