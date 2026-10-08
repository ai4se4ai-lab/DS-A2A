"""Write the environment record the paper's data-availability statement promises: server and model
versions/digests, hardware, interpreter, package versions. Paths and user names are not recorded.

    python -m evaluation.harness.record_environment [--out evaluation/results/follow-up-study/environment.json]
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import requests

MODELS = ("qwen2.5-coder:7b", "qwen3.8:27b")


def _cmd(*args: str) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="evaluation/results/follow-up-study/environment.json")
    ap.add_argument("--base-url", default="http://localhost:11434")
    args = ap.parse_args()
    env: dict = {"python": sys.version.split()[0], "platform": platform.platform(), "machine": platform.machine()}
    for pkg in ("agentm2m", "pytest", "numpy", "pandas", "scipy", "matplotlib", "requests", "pyecore", "lark"):
        try:
            env.setdefault("packages", {})[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            pass
    try:
        env["ollama_version"] = requests.get(f"{args.base_url}/api/version", timeout=5).json().get("version")
        tags = {m["name"]: m for m in requests.get(f"{args.base_url}/api/tags", timeout=5).json().get("models", [])}
        env["models"] = {}
        for m in MODELS:
            if m in tags:
                info = requests.post(f"{args.base_url}/api/show", json={"name": m}, timeout=30).json()
                d = info.get("details", {})
                env["models"][m] = {"digest": tags[m].get("digest"), "size_bytes": tags[m].get("size"),
                                    "family": d.get("family"), "parameter_size": d.get("parameter_size"),
                                    "quantization": d.get("quantization_level"), "format": d.get("format")}
    except requests.RequestException as exc:
        env["ollama_error"] = type(exc).__name__
    gpu = _cmd("nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader")
    env["gpu"] = gpu or "n/a"
    env["cpu_count"] = _cmd("nproc")
    mem = _cmd("free", "-g").splitlines()
    env["memory_gb_total"] = mem[1].split()[1] if len(mem) > 1 else "n/a"
    env["git_commit"] = _cmd("git", "rev-parse", "--short", "HEAD")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(env, indent=1) + "\n")
    print(json.dumps(env, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
