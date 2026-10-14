#!/usr/bin/env python3
"""Setup gate: run each DevBench repository's UNMODIFIED reference implementation against its own unit and
acceptance tests (the paper selects the 8 repos whose reference passes). Writes a JSON report; exits 1 if any
repo in --require fails, or if canary/other fixtures cannot be loaded."""
import argparse, json, shutil, sys, tempfile
from pathlib import Path
from evaluation.harness.devbench_common import _run
from evaluation.harness.devbench_loader import ALL_REPOS, load_devbench_task

ap = argparse.ArgumentParser()
ap.add_argument("--require", nargs="+", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--network-repos", nargs="*", default=[],
                help="repos whose acceptance tests need live internet (absent on GPU nodes): only unit tests gate them")
a = ap.parse_args()
report, bad = {}, []
for repo in ALL_REPOS:
    t = load_devbench_task(repo)
    scratch = Path(tempfile.mkdtemp(prefix=f"ref_{repo}_"))
    try:
        shutil.copytree(t.path, scratch, dirs_exist_ok=True)
        u, ud = _run(t.unit_test_cmd, cwd=scratch, timeout=300)
        c, cd = _run(t.acceptance_test_cmd, cwd=scratch, timeout=300)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    report[repo] = {"unit_pass": u, "acceptance_pass": c, "n_operations": len(t.operations),
                    "n_criteria": len(t.acceptance_criteria), "required": repo in a.require, "network_bound": repo in a.network_repos,
                    "unit_tail": ud[-300:], "acceptance_tail": cd[-300:]}
    print(f"{repo:30s} unit={u} acceptance={c} ops={len(t.operations)}", flush=True)
    if repo in a.require and not (u and (c or repo in a.network_repos)):
        bad.append(repo)
Path(a.out).parent.mkdir(parents=True, exist_ok=True)
Path(a.out).write_text(json.dumps(report, indent=1))
if bad:
    print("reference implementation fails its own tests:", bad, file=sys.stderr); sys.exit(1)
