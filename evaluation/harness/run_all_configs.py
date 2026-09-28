#!/usr/bin/env python3
"""Orchestrates the pilot comparison of Sec V "Design": runs
free_text_config, shared_schema_config, and agentm2m_config over the same
tasks with the same pluggable LLM backend, annotates each run's transcript
with the (approximate) MAST classifier, meters tokens, scores the
lightweight task-success proxy, and writes one JSONL record per
(task, config) to evaluation/harness/logs/run_<timestamp>.jsonl -- the
input evaluation/analysis/aggregate.py reads to reproduce Table II.

    python evaluation/harness/run_all_configs.py --llm mock              # quick, zero-setup
    python evaluation/harness/run_all_configs.py --llm ollama --n 20 \\
        --tasks-file evaluation/benchmarks/cache/swebench_lite_sample.jsonl
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable

from agentm2m.config import LLMConfig
from agentm2m.llm.base import LLMBackend
from agentm2m.llm.factory import make_backend

from .agentm2m_config import CONFIG_NAME as AGENTM2M, run_agentm2m
from .common import ConfigRunResult, default_tasks_file, load_tasks
from .free_text_config import CONFIG_NAME as FREE_TEXT, run_free_text
from .mast_annotator import annotate
from .shared_schema_config import CONFIG_NAME as SHARED_SCHEMA, run_shared_schema
from .swebench_eval import proxy_eval
from .token_meter import TokenMeter

LOGS_DIR = Path(__file__).resolve().parent / "logs"

_RUNNERS: dict[str, Callable[[dict, Any], ConfigRunResult]] = {
    FREE_TEXT: run_free_text,
    SHARED_SCHEMA: run_shared_schema,
    AGENTM2M: run_agentm2m,
}
CONFIG_NAMES = (FREE_TEXT, SHARED_SCHEMA, AGENTM2M)


def run_one(config_name: str, task: dict, llm: LLMBackend, *, temperature: float) -> dict:
    meter = TokenMeter(llm)
    runner = _RUNNERS[config_name]
    result = runner(task, meter, temperature=temperature)

    annotation = annotate(result.instance_id, config_name, result.transcript_text(), meter, temperature=0.0)
    proxy = proxy_eval(result.instance_id, result.patch_text)

    return {
        "instance_id": result.instance_id,
        "config": config_name,
        "patch_text": result.patch_text,
        "mast_modes": annotation.modes_present,
        "mast_p1_count": annotation.p1_count,
        "mast_p2_count": annotation.p2_count,
        "mast_raw": annotation.raw_response,
        "task_success_proxy": proxy.success_proxy,
        "proxy_detail": proxy.detail,
        "input_tokens": meter.input_tokens,
        "output_tokens": meter.output_tokens,
        "total_tokens": meter.total_tokens,
        "llm_calls": meter.calls,
        "extra": result.extra,
    }


def run_all(tasks: list[dict], *, llm: LLMBackend, temperature: float = 0.2) -> list[dict]:
    return [
        run_one(config_name, task, llm, temperature=temperature)
        for task in tasks
        for config_name in CONFIG_NAMES
    ]


def write_log(records: list[dict], out_path: str | Path | None = None) -> Path:
    if out_path is None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = LOGS_DIR / f"run_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--tasks-file", default=None,
        help="JSONL of task records (real cache or the fixture); defaults to "
             "evaluation/benchmarks/fixtures/toy_tasks.jsonl if omitted",
    )
    parser.add_argument("--quick", action="store_true", help="alias for the small-sample default (no-op; kept for CLI clarity)")
    parser.add_argument("--n", type=int, default=None, help="limit to the first N tasks")
    parser.add_argument("--llm", default=None, help="ollama|openai|anthropic|mock (default: $LLM_PROVIDER, else ollama)")
    parser.add_argument("--model", default=None)
    parser.add_argument("--out", default=None, help="output JSONL path (default: harness/logs/run_<timestamp>.jsonl)")
    args = parser.parse_args()

    tasks_file = Path(args.tasks_file) if args.tasks_file else default_tasks_file()
    tasks = load_tasks(tasks_file)
    if args.n:
        tasks = tasks[: args.n]

    cfg = LLMConfig.from_env()
    llm = make_backend(cfg, override_provider=args.llm, override_model=args.model)
    print(f"Loaded {len(tasks)} task(s) from {tasks_file}; LLM backend: {llm.name}")

    records = run_all(tasks, llm=llm, temperature=cfg.temperature)
    out_path = write_log(records, args.out)
    print(f"Wrote {len(records)} record(s) to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
