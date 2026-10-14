#!/usr/bin/env python3
"""Fetches the Python subset of Li et al.'s DevBench
(github.com/open-compass/DevBench, Apache-2.0) -- the "22 repositories
with PRD, UML and architecture design, and executable acceptance and unit
tests" benchmark the paper's `li2024devbench` citation refers to.

NOT to be confused with the similarly-named github.com/microsoft/devbench
(a telemetry-driven code-completion benchmark, described in
docs/devbench.md) -- unrelated benchmark, same name.

Uses a sparse, shallow git clone (only `benchmark_data/python/`, `LICENSE`,
and `benchmark_data/README.md`) rather than the `datasets` library, since
DevBench is a plain GitHub repo, not a HuggingFace dataset.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_URL = "https://github.com/open-compass/DevBench.git"
CACHE_DIR = Path(__file__).resolve().parent / "cache" / "devbench"

ALL_REPOS = [
    "TextCNN", "ArXiv_digest", "chakin", "readtime", "hone",
    "stocktrends", "geotext", "lice", "particle-swarm-optimization", "Hybrid_Images",
]


class FetchError(RuntimeError):
    pass


def fetch(*, force: bool = False) -> Path:
    dest = CACHE_DIR / "python"
    if dest.exists() and not force:
        print(f"Already present: {dest} (pass --force to re-fetch)")
        return dest

    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = Path(tmp) / "DevBench"
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", REPO_URL, str(clone_dir)],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "sparse-checkout", "init", "--cone"], cwd=clone_dir, check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "sparse-checkout", "set", "benchmark_data/python"],
                cwd=clone_dir, check=True, capture_output=True, text=True,
            )
        except FileNotFoundError as exc:
            raise FetchError("git is not installed") from exc
        except subprocess.CalledProcessError as exc:
            raise FetchError(f"git clone/sparse-checkout failed: {exc.stderr}") from exc

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(clone_dir / "benchmark_data" / "python", dest)

        license_src = clone_dir / "LICENSE"
        if license_src.exists():
            shutil.copy(license_src, CACHE_DIR / "LICENSE")

    missing = [r for r in ALL_REPOS if not (dest / r / "repo_config.json").exists()]
    if missing:
        raise FetchError(f"fetched, but missing repo_config.json for: {missing}")
    return dest


def main() -> int:
    force = "--force" in sys.argv
    try:
        dest = fetch(force=force)
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"DevBench Python subset ready at {dest} ({len(ALL_REPOS)} repos).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
