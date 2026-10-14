#!/usr/bin/env python3
"""Reproduces Table II (`tab:pilot`, docs/DS-A2A.tex Sec V) from
evaluation/harness/logs/*.jsonl: reads every (task, config) record written
by run_all_configs.py, computes the per-configuration aggregates for
exactly Table II's five rows and three columns, and writes:

* evaluation/results/csv/pilot_table.csv -- the aggregated table.
* evaluation/results/csv/raw_metrics.csv -- the underlying per-task,
  per-config rows, for anyone who wants to redo the stats.

"Impact recall / precision (RQ2)" is "--" for the free-text column (Sec V:
"Impact recall/precision (RQ2), only meaningful for the shared-schema and
AgentM2M columns"), since free-text hand-offs have no change-impact
mechanism to measure at all.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean

LOGS_DIR = Path(__file__).resolve().parents[1] / "harness" / "logs"
RESULTS_CSV_DIR = Path(__file__).resolve().parents[1] / "results" / "csv"

CONFIG_COLUMNS = [("free_text", "Free text"), ("shared_schema", "Shared schema"), ("agentm2m", "AgentM2M")]

ROW_ORDER = [
    "P1 modes per trace (MAST)",
    "P2 modes per trace (MAST)",
    "Task success (%)",
    "Impact recall / precision (RQ2)",
    "LLM tokens per task (k)",
]


def load_records(log_paths: list[Path]) -> list[dict]:
    records: list[dict] = []
    for p in log_paths:
        with p.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def discover_logs(logs_dir: Path | None = None) -> list[Path]:
    return sorted((logs_dir or LOGS_DIR).glob("run_*.jsonl"))


def compute_impact_metrics() -> dict[str, tuple[float | None, float | None]]:
    """RQ2 precision/recall per config, computed once rather than per task:
    Obl(Delta)'s over/under-approximation is a structural property of the
    hand-off rules (Proposition 2), independent of sampled text quality, so
    a deterministic MockBackend is used here regardless of which LLM
    produced the main run's patches -- this keeps Table II's RQ2 numbers
    reproducible without requiring network access or a matching --llm.

    Returns {config_key: (precision, recall)}, with (None, None) for
    free_text (no change-impact mechanism to measure).
    """
    from agentm2m.llm.mock_backend import MockBackend

    from ..harness.impact_injector import run_impact_injection

    llm = MockBackend()
    agentm2m_result = run_impact_injection(llm, mode="agentm2m")
    shared_result = run_impact_injection(llm, mode="shared_schema")
    return {
        "free_text": (None, None),
        "shared_schema": (shared_result.precision, shared_result.recall),
        "agentm2m": (agentm2m_result.precision, agentm2m_result.recall),
    }


def compute_table(
    records: list[dict], *, impact_metrics: dict[str, tuple[float | None, float | None]] | None = None
) -> dict[str, dict[str, str]]:
    """Returns {row_label: {column_label: formatted_cell}}, shaped exactly
    like Table II (tab:pilot): 5 rows x 3 columns."""
    impact_metrics = impact_metrics if impact_metrics is not None else compute_impact_metrics()

    by_config: dict[str, list[dict]] = {c: [] for c, _ in CONFIG_COLUMNS}
    for r in records:
        if r.get("config") in by_config:
            by_config[r["config"]].append(r)

    table: dict[str, dict[str, str]] = {row: {} for row in ROW_ORDER}
    for config_key, column_label in CONFIG_COLUMNS:
        rows = by_config[config_key]
        if not rows:
            for row in ROW_ORDER:
                table[row][column_label] = "--"
            continue

        p1_mean = mean(r.get("mast_p1_count", 0) for r in rows)
        p2_mean = mean(r.get("mast_p2_count", 0) for r in rows)
        success_pct = 100.0 * mean(1.0 if r.get("task_success_proxy") else 0.0 for r in rows)
        tokens_k = mean(r.get("total_tokens", 0) for r in rows) / 1000.0
        precision, recall = impact_metrics.get(config_key, (None, None))

        table["P1 modes per trace (MAST)"][column_label] = f"{p1_mean:.2f}"
        table["P2 modes per trace (MAST)"][column_label] = f"{p2_mean:.2f}"
        table["Task success (%)"][column_label] = f"{success_pct:.1f}"
        table["Impact recall / precision (RQ2)"][column_label] = (
            "--" if recall is None or precision is None else f"{recall:.2f} / {precision:.2f}"
        )
        table["LLM tokens per task (k)"][column_label] = f"{tokens_k:.2f}"

    return table


def write_pilot_csv(table: dict[str, dict[str, str]], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    columns = [c for _, c in CONFIG_COLUMNS]
    with out_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Metric", *columns])
        for row in ROW_ORDER:
            writer.writerow([row, *(table[row].get(c, "--") for c in columns)])
    return out_path


_RAW_FIELDNAMES = [
    "instance_id",
    "config",
    "mast_p1_count",
    "mast_p2_count",
    "mast_modes",
    "task_success_proxy",
    "proxy_detail",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "llm_calls",
]


def write_raw_csv(records: list[dict], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_RAW_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            row = dict(r)
            if isinstance(row.get("mast_modes"), list):
                row["mast_modes"] = ";".join(row["mast_modes"])
            writer.writerow(row)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--logs-dir", default=None, help="directory of run_*.jsonl logs (default: evaluation/harness/logs)")
    parser.add_argument("--log-file", action="append", default=None, help="specific log file(s); repeatable, overrides --logs-dir discovery")
    parser.add_argument("--out-table", default=str(RESULTS_CSV_DIR / "pilot_table.csv"))
    parser.add_argument("--out-raw", default=str(RESULTS_CSV_DIR / "raw_metrics.csv"))
    args = parser.parse_args()

    log_paths = [Path(p) for p in args.log_file] if args.log_file else discover_logs(Path(args.logs_dir) if args.logs_dir else None)
    if not log_paths:
        print("no logs found; run evaluation/harness/run_all_configs.py first")
        return 1

    records = load_records(log_paths)
    table = compute_table(records)
    pilot_path = write_pilot_csv(table, args.out_table)
    raw_path = write_raw_csv(records, args.out_raw)
    print(f"Wrote {pilot_path}")
    print(f"Wrote {raw_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
