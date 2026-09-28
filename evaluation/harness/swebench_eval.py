#!/usr/bin/env python3
"""Applies a produced patch to the real repo at `base_commit` and,
**only when `--docker` is explicitly passed**, invokes the official
`swebench` package's evaluation harness (listed under pyproject.toml's
`[project.optional-dependencies].eval`).

Two modes:

* default (no `--docker`): a lightweight, always-available PROXY for task
  success -- does the patch apply cleanly / is it well-formed at all? This
  is explicitly *not* the paper's real success metric (running
  FAIL_TO_PASS/PASS_TO_PASS inside the official SWE-bench Docker images);
  it exists so run_all_configs.py / evaluation/analysis/aggregate.py
  always have *some* success signal for Table II without Docker or a
  network-heavy image pull.
* `--docker`: real SWE-bench evaluation via the `swebench` package +
  Docker. Defensively guarded: a missing install or unavailable Docker
  daemon raises a clear, actionable RuntimeError rather than crashing the
  rest of the pipeline, and nothing else in evaluation/ depends on this
  path succeeding.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

_DIFF_HUNK_RE = re.compile(r"^@@ .* @@", re.MULTILINE)
_DIFF_HEADER_RE = re.compile(r"^(---|\+\+\+) ", re.MULTILINE)


@dataclass
class ProxyEvalResult:
    instance_id: str
    applies_cleanly: bool
    is_well_formed: bool
    detail: str

    @property
    def success_proxy(self) -> bool:
        return self.applies_cleanly and self.is_well_formed


def is_well_formed_patch(patch_text: str) -> bool:
    """Structural proxy check: does `patch_text` look like a unified diff
    (file headers and/or hunk markers) and isn't trivially empty?"""
    if not patch_text or len(patch_text.strip()) < 10:
        return False
    return bool(_DIFF_HUNK_RE.search(patch_text) or _DIFF_HEADER_RE.search(patch_text))


def try_git_apply(patch_text: str, repo_dir: str | Path, *, check_only: bool = True) -> tuple[bool, str]:
    """`git apply --check` of patch_text against repo_dir (expected checked
    out at the task's base_commit). Returns (applied_ok, detail)."""
    repo_dir = Path(repo_dir)
    if not (repo_dir / ".git").exists():
        return False, f"{repo_dir} is not a git checkout"
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
        f.write(patch_text)
        patch_path = f.name
    try:
        args = ["git", "apply", "--check" if check_only else "--reject", patch_path]
        result = subprocess.run(args, cwd=str(repo_dir), capture_output=True, text=True, timeout=30)
        return result.returncode == 0, (result.stderr or result.stdout).strip()
    except Exception as exc:  # noqa: BLE001 - git/OS errors of many shapes
        return False, str(exc)
    finally:
        Path(patch_path).unlink(missing_ok=True)


def proxy_eval(instance_id: str, patch_text: str, *, repo_dir: str | Path | None = None) -> ProxyEvalResult:
    well_formed = is_well_formed_patch(patch_text)
    if repo_dir is not None:
        applies, detail = try_git_apply(patch_text, repo_dir)
    else:
        applies, detail = well_formed, "no repo checkout given; well-formedness used as the apply proxy"
    return ProxyEvalResult(instance_id=instance_id, applies_cleanly=applies, is_well_formed=well_formed, detail=detail)


def docker_eval(task: dict, patch_text: str, *, work_dir: str | Path | None = None) -> dict:
    """Real SWE-bench evaluation via the official `swebench` package +
    Docker. Only invoked when --docker is passed."""
    try:
        from swebench.harness.run_evaluation import main as swebench_main  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "swebench/Docker not available in this environment; install the "
            "`eval` extra (`pip install -e .[eval]`) and Docker to run --docker mode."
        ) from exc

    work_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="swebench_eval_"))
    predictions_path = work_dir / "predictions.jsonl"
    predictions_path.write_text(
        json.dumps(
            {"instance_id": task["instance_id"], "model_patch": patch_text, "model_name_or_path": "agentm2m-eval"}
        )
        + "\n"
    )
    run_id = f"agentm2m-{uuid.uuid4().hex[:8]}"
    try:
        swebench_main(
            dataset_name="princeton-nlp/SWE-bench_Lite",
            split="test",
            predictions_path=str(predictions_path),
            max_workers=1,
            run_id=run_id,
            instance_ids=[task["instance_id"]],
        )
    except Exception as exc:  # noqa: BLE001 - Docker daemon/image-pull failures of many shapes
        raise RuntimeError(
            f"swebench Docker evaluation failed for {task.get('instance_id')}: {exc}. This "
            "usually means Docker isn't running or the instance image couldn't be pulled; "
            "the quick/mock pipeline (default mode, no --docker) does not require this."
        ) from exc
    return {"run_id": run_id, "predictions_path": str(predictions_path), "work_dir": str(work_dir)}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks-file", required=True)
    parser.add_argument("--patches-file", required=True, help="JSONL of {instance_id, patch_text}")
    parser.add_argument("--repo-dir", default=None, help="optional local checkout to git-apply against")
    parser.add_argument("--docker", action="store_true", help="run the real swebench/Docker evaluation (opt-in)")
    args = parser.parse_args()

    tasks = {}
    with open(args.tasks_file) as f:
        for line in f:
            line = line.strip()
            if line:
                t = json.loads(line)
                tasks[t["instance_id"]] = t

    with open(args.patches_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            instance_id, patch_text = rec["instance_id"], rec["patch_text"]
            if args.docker:
                task = tasks.get(instance_id, {"instance_id": instance_id})
                try:
                    result = docker_eval(task, patch_text)
                    print(f"{instance_id}: docker eval submitted -> {result}")
                except RuntimeError as exc:
                    print(f"{instance_id}: docker eval unavailable: {exc}")
            else:
                result = proxy_eval(instance_id, patch_text, repo_dir=args.repo_dir)
                print(
                    f"{instance_id}: proxy success={result.success_proxy} "
                    f"(applies={result.applies_cleanly}, well_formed={result.is_well_formed})"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
