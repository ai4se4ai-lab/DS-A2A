"""Driver for the DevBench evaluation (Python repos x 3 configs x seeds,
run once per model and seed -- see evaluation/README.md "DevBench pilot" section
and docs/DS-A2A.tex Sec V). Structurally mirrors run_all_configs.py (the
SWE-bench-Lite track, left untouched) but adds the `model` field the older
schema never had, and threads MAST annotation + canary retention +
DevBench's own real acceptance/unit tests through every record.

Usage:
    python -m evaluation.harness.devbench_run_all --llm ollama --model qwen2.5-coder:7b \\
        --seed 1 --out-dir evaluation/results/follow-up-study/raw
    python -m evaluation.harness.devbench_run_all --llm mock          # smoke test
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from agentm2m.config import LLMConfig
from agentm2m.llm.factory import make_backend

from . import mast_annotator
from .devbench_agentm2m_config import run_agentm2m
from .devbench_free_text_config import run_free_text
from .devbench_loader import ALL_REPOS, load_devbench_task
from .devbench_shared_schema_config import run_shared_schema
from .hot_reviewer_rq3 import run_rq3
from .impact_injector_devbench import (
    run_impact_injection_agentm2m,
    run_impact_injection_free_text,
    run_impact_injection_shared_schema,
)
from .token_meter import TokenMeter

CONFIG_RUNNERS = {
    "free_text": run_free_text,
    "shared_schema": run_shared_schema,
    "agentm2m": run_agentm2m,
}
CONFIG_NAMES = tuple(CONFIG_RUNNERS.keys())

LOGS_DIR = Path(__file__).resolve().parent / "logs"


def run_one(config_name: str, repo: str, task, llm, *, model: str, seed: int, temperature: float) -> dict:
    runner = CONFIG_RUNNERS[config_name]
    t0 = time.time()
    meter = TokenMeter(llm)
    result = runner(task, meter, temperature=temperature)
    elapsed = time.time() - t0
    # The MAST judge is evaluation overhead, not part of the system under
    # test: meter it separately so it never inflates a config's token cost
    # (it used to be charged to `meter`, i.e. proportional to how long each
    # config's transcript happened to be).
    eval_meter = TokenMeter(llm)
    annotation = mast_annotator.annotate(repo, config_name, result.transcript_text(), eval_meter, temperature=0.0)

    test_result = result.extra.get("test_result", {})
    record = {
        "repo": repo,
        "config": config_name,
        "model": model,
        "seed": seed,
        "transcript_text": result.transcript_text(),
        "mast_modes": annotation.modes_present,
        "mast_p1_count": annotation.p1_count,
        "mast_p2_count": annotation.p2_count,
        "mast_raw": annotation.raw_response,
        "unit_pass": bool(test_result.get("unit_pass")),
        "acceptance_pass": bool(test_result.get("acceptance_pass")),
        "task_success": bool(test_result.get("acceptance_pass")),
        "canary_pass": test_result.get("canary_pass", 0),
        "canary_total": test_result.get("canary_total", 0),
        "input_tokens": meter.input_tokens,
        "output_tokens": meter.output_tokens,
        "total_tokens": meter.total_tokens,
        "llm_calls": meter.calls,
        "exact_token_calls": meter.exact_calls,
        "eval_tokens": eval_meter.total_tokens,
        "elapsed_s": round(elapsed, 1),
        "extra": {k: v for k, v in result.extra.items() if k != "test_result"},
    }
    return record


def run_all(repos: list[str], *, llm, model: str, seed: int = 0, temperature: float = 0.2, log_fh=None) -> list[dict]:
    records = []
    for repo in repos:
        task = load_devbench_task(repo)
        for config_name in CONFIG_NAMES:
            print(f"[{datetime.now().isoformat(timespec='seconds')}] {model} / {repo} / {config_name} ...", flush=True)
            try:
                record = run_one(config_name, repo, task, llm, model=model, seed=seed, temperature=temperature)
            except Exception as exc:  # noqa: BLE001 - keep the pilot going, record the failure honestly
                record = {
                    "repo": repo, "config": config_name, "model": model, "seed": seed,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            records.append(record)
            print(f"    -> success={record.get('task_success')} p1={record.get('mast_p1_count')} "
                  f"tokens={record.get('total_tokens')} elapsed={record.get('elapsed_s')}s "
                  f"error={record.get('error')}", flush=True)
            if log_fh is not None:
                log_fh.write(json.dumps(record) + "\n")
                log_fh.flush()
    return records


def run_rq2_all(repos: list[str], *, llm, model: str, temperature: float, log_fh, seed: int = 0) -> None:
    for repo in repos:
        task = load_devbench_task(repo)
        print(f"[RQ2] {model} / {repo} ...", flush=True)
        for kind, results in (
            ("agentm2m", run_impact_injection_agentm2m(task, llm, temperature=temperature)),
            ("free_text", run_impact_injection_free_text(task, llm, temperature=temperature)),
            ("shared_schema", run_impact_injection_shared_schema(task, llm, temperature=temperature)),
        ):
            for res in results:
                record = {
                    "repo": repo, "config": kind, "model": model, "seed": seed,
                    "change_kind": res.change.kind, "target_op": res.change.target_op,
                    "predicted": sorted(res.predicted), "oracle": sorted(res.oracle),
                    "precision": res.precision, "recall": res.recall, "tokens": res.tokens,
                    "identification_tokens": res.identification_tokens,
                    "propagation_tokens": res.propagation_tokens,
                    "build_tokens": res.build_tokens,
                }
                log_fh.write(json.dumps(record) + "\n")
        log_fh.flush()


def run_rq3_all(repos: list[str], *, llm, model: str, temperature: float, log_fh, seed: int = 0) -> None:
    for repo in repos:
        task = load_devbench_task(repo)
        print(f"[RQ3] {model} / {repo} ...", flush=True)
        try:
            res = run_rq3(task, llm, temperature=temperature)
            record = {
                "repo": repo, "model": model, "seed": seed, "n_ops_before": res.n_ops_before,
                "n_reviews_after": res.n_reviews_after, "retroactive_coverage": res.retroactive_coverage,
                "tokens": res.tokens,
            }
        except Exception as exc:  # noqa: BLE001
            record = {"repo": repo, "model": model, "seed": seed, "error": f"{type(exc).__name__}: {exc}"}
        log_fh.write(json.dumps(record) + "\n")
        log_fh.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos", nargs="*", default=ALL_REPOS)
    parser.add_argument("--llm", default=os.environ.get("LLM_PROVIDER", "ollama"))
    parser.add_argument("--model", default=None, help="model tag; required for --llm ollama/openai/anthropic")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--out", default=None, help="explicit path for the RQ1 log (legacy)")
    parser.add_argument("--out-dir", default=None, help="directory for the rq1/rq2/rq3 logs (default: harness/logs)")
    parser.add_argument("--skip-rq1", action="store_true", help="resume a run whose RQ1 log is already complete")
    parser.add_argument("--skip-rq2", action="store_true")
    parser.add_argument("--skip-rq3", action="store_true")
    args = parser.parse_args()

    cfg = LLMConfig.from_env()
    llm = make_backend(cfg, override_provider=args.llm, override_model=args.model)
    model_tag = args.model or cfg.model
    if hasattr(llm, "seed"):
        llm.seed = args.seed  # forwarded to Ollama options.seed so the logged seed is real

    logs_dir = Path(args.out_dir) if args.out_dir else LOGS_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = f"{model_tag.replace(':', '-')}_s{args.seed}"
    rq1_path = Path(args.out) if args.out else logs_dir / f"devbench_rq1_{tag}_{stamp}.jsonl"
    rq2_path = logs_dir / f"devbench_rq2_{tag}_{stamp}.jsonl"
    rq3_path = logs_dir / f"devbench_rq3_{tag}_{stamp}.jsonl"

    print(f"Using LLM backend: {llm.name} ({model_tag})", flush=True)

    if not args.skip_rq1:
        print(f"=== RQ1: writing to {rq1_path} ===", flush=True)
        with rq1_path.open("w") as fh:
            run_all(args.repos, llm=llm, model=model_tag, seed=args.seed, temperature=args.temperature, log_fh=fh)

    if not args.skip_rq2:
        print(f"=== RQ2: writing to {rq2_path} ===", flush=True)
        with rq2_path.open("w") as fh:
            run_rq2_all(args.repos, llm=llm, model=model_tag, temperature=args.temperature, log_fh=fh, seed=args.seed)

    if not args.skip_rq3:
        print(f"=== RQ3: writing to {rq3_path} ===", flush=True)
        with rq3_path.open("w") as fh:
            run_rq3_all(args.repos, llm=llm, model=model_tag, temperature=args.temperature, log_fh=fh, seed=args.seed)

    print(f"Done. RQ1={rq1_path} RQ2={rq2_path} RQ3={rq3_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
