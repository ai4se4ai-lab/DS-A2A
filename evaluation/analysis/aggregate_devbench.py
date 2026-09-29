"""Aggregates the DevBench pilot's RQ1/RQ2/RQ3 JSONL logs
(evaluation/harness/logs/devbench_rq{1,2,3}_<model>_<timestamp>.jsonl) into
the `tab:prelim`-shaped table docs/DS-A2A.tex's "Preliminary Evaluation and
Discussion" section reports, plus a paired bootstrap CI for the canary-
retention AgentM2M-vs-shared-schema difference (the paper's own RQ1
decision rule).
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path
from statistics import mean

LOGS_DIR = Path(__file__).resolve().parents[1] / "harness" / "logs"
RESULTS_CSV_DIR = Path(__file__).resolve().parents[1] / "results" / "csv"

CONFIG_COLUMNS = [("free_text", "Free text"), ("shared_schema", "Shared schema"), ("agentm2m", "AgentM2M")]
ROW_ORDER = [
    "Canary retention (%)",
    "P1 modes per trace",
    "Acceptance pass (%)",
    "Impact recall / precision",
    "Tokens per change (k)",
    "Glue lines changed",
    "Retroactive coverage (%)",
]


def _load_jsonl(paths: list[Path]) -> list[dict]:
    records = []
    for p in paths:
        for line in p.read_text().splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def discover(kind: str, *, logs_dir: Path | None = None, model: str | None = None) -> list[Path]:
    d = logs_dir or LOGS_DIR
    pattern = f"devbench_{kind}_{model.replace(':', '-') if model else '*'}_*.jsonl"
    return sorted(d.glob(pattern))


def compute_rq1(records: list[dict]) -> dict[str, dict]:
    by_config: dict[str, list[dict]] = {c: [] for c, _ in CONFIG_COLUMNS}
    for r in records:
        if "error" in r:
            continue
        if r.get("config") in by_config:
            by_config[r["config"]].append(r)
    out = {}
    for config_key, _ in CONFIG_COLUMNS:
        rows = by_config[config_key]
        if not rows:
            out[config_key] = None
            continue
        canary_retention = [
            (r["canary_pass"] / r["canary_total"]) if r.get("canary_total") else 0.0 for r in rows
        ]
        out[config_key] = {
            "canary_retention_pct": 100.0 * mean(canary_retention),
            "p1_per_trace": mean(r.get("mast_p1_count", 0) for r in rows),
            "acceptance_pct": 100.0 * mean(1.0 if r.get("acceptance_pass") else 0.0 for r in rows),
            "tokens_k": mean(r.get("total_tokens", 0) for r in rows) / 1000.0,
            "n": len(rows),
            "canary_retention_by_repo": {r["repo"]: (r["canary_pass"] / r["canary_total"]) if r.get("canary_total") else 0.0 for r in rows},
        }
    return out


def compute_rq2(records: list[dict]) -> dict[str, dict]:
    by_config: dict[str, list[dict]] = {c: [] for c, _ in CONFIG_COLUMNS}
    for r in records:
        if r.get("config") in by_config:
            by_config[r["config"]].append(r)
    out = {}
    for config_key, _ in CONFIG_COLUMNS:
        rows = by_config[config_key]
        if not rows:
            out[config_key] = None
            continue
        out[config_key] = {
            "recall": mean(r["recall"] for r in rows),
            "precision": mean(r["precision"] for r in rows),
            "tokens_k": mean(r.get("tokens", 0) for r in rows) / 1000.0,
            "n": len(rows),
        }
    return out


def compute_rq3(records: list[dict], *, glue_counts: dict[str, int]) -> dict:
    rows = [r for r in records if "error" not in r]
    coverage = mean(r["retroactive_coverage"] for r in rows) * 100.0 if rows else 0.0
    return {
        "retroactive_coverage_pct": coverage,
        "glue_lines_agentm2m": glue_counts["agentm2m_hot"],
        "glue_lines_manual": glue_counts["manual_total_across_baselines"],
        "n": len(rows),
    }


def bootstrap_ci(diffs: list[float], *, n_resamples: int = 10000, seed: int = 0) -> tuple[float, float, float]:
    """Percentile bootstrap over paired per-repo differences. Returns
    (point_estimate, lo95, hi95). With a small pilot (n repos), this
    interval is honestly wide -- that is the point of reporting it rather
    than only the point estimate (decision rule, Sec V)."""
    if not diffs:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    point = mean(diffs)
    resamples = [mean(rng.choices(diffs, k=len(diffs))) for _ in range(n_resamples)]
    resamples.sort()
    lo = resamples[int(0.025 * n_resamples)]
    hi = resamples[int(0.975 * n_resamples)]
    return point, lo, hi


def canary_bootstrap(rq1: dict[str, dict]) -> dict:
    agentm2m = rq1.get("agentm2m")
    shared = rq1.get("shared_schema")
    if not agentm2m or not shared:
        return {"available": False}
    a = agentm2m["canary_retention_by_repo"]
    s = shared["canary_retention_by_repo"]
    common_repos = sorted(set(a) & set(s))
    diffs = [100.0 * (a[r] - s[r]) for r in common_repos]
    point, lo, hi = bootstrap_ci(diffs)
    supported = lo > 0 and point > 10.0
    return {
        "available": True, "n_repos": len(common_repos), "point": point, "lo95": lo, "hi95": hi,
        "supported": supported,
    }


def write_raw_csv(rq1_records: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "repo", "config", "model", "seed", "mast_p1_count", "mast_p2_count", "mast_modes",
        "unit_pass", "acceptance_pass", "task_success", "canary_pass", "canary_total",
        "input_tokens", "output_tokens", "total_tokens", "llm_calls", "elapsed_s", "error",
    ]
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rq1_records:
            row = dict(r)
            if isinstance(row.get("mast_modes"), list):
                row["mast_modes"] = ";".join(row["mast_modes"])
            w.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None, help="restrict to one model's logs; default: all models pooled")
    parser.add_argument("--logs-dir", default=None)
    args = parser.parse_args()

    logs_dir = Path(args.logs_dir) if args.logs_dir else LOGS_DIR
    rq1_paths = discover("rq1", logs_dir=logs_dir, model=args.model)
    rq2_paths = discover("rq2", logs_dir=logs_dir, model=args.model)
    rq3_paths = discover("rq3", logs_dir=logs_dir, model=args.model)
    if not rq1_paths:
        print("No devbench_rq1_*.jsonl logs found; run evaluation.harness.devbench_run_all first.")
        return 1

    rq1_records = _load_jsonl(rq1_paths)
    rq2_records = _load_jsonl(rq2_paths) if rq2_paths else []
    rq3_records = _load_jsonl(rq3_paths) if rq3_paths else []

    from evaluation.harness.hot_reviewer_rq3 import glue_line_counts

    rq1 = compute_rq1(rq1_records)
    rq2 = compute_rq2(rq2_records) if rq2_records else {}
    rq3 = compute_rq3(rq3_records, glue_counts=glue_line_counts()) if rq3_records else {}
    boot = canary_bootstrap(rq1)

    RESULTS_CSV_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_{args.model.replace(':', '-')}" if args.model else "_pooled"
    write_raw_csv(rq1_records, RESULTS_CSV_DIR / f"devbench_raw_metrics{suffix}.csv")

    summary = {"rq1": rq1, "rq2": rq2, "rq3": rq3, "bootstrap": boot}
    out_json = RESULTS_CSV_DIR / f"devbench_summary{suffix}.json"
    out_json.write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
    print(f"\nWrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
