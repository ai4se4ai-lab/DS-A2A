#!/usr/bin/env python3
"""Reads the same evaluation/harness/logs/*.jsonl records aggregate.py
reads and produces the figures backing Table II (Sec V): a grouped bar
chart of P1+P2 modes per trace, a bar chart of task success (%), a
bar/scatter of impact precision vs. recall (RQ2), and a bar chart of
tokens/task, one PNG per figure under evaluation/results/figures/.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from statistics import mean

import matplotlib

matplotlib.use("Agg")  # headless: no display needed to render PNGs
import matplotlib.pyplot as plt

from .aggregate import CONFIG_COLUMNS, compute_impact_metrics, discover_logs, load_records

FIGURES_DIR = Path(__file__).resolve().parents[1] / "results" / "figures"


def _per_config(records: list[dict]) -> dict[str, list[dict]]:
    by_config: dict[str, list[dict]] = {c: [] for c, _ in CONFIG_COLUMNS}
    for r in records:
        if r.get("config") in by_config:
            by_config[r["config"]].append(r)
    return by_config


def plot_mast_modes(records: list[dict], out_dir: Path) -> Path:
    by_config = _per_config(records)
    labels = [label for _, label in CONFIG_COLUMNS]
    p1 = [mean(r.get("mast_p1_count", 0) for r in by_config[c]) if by_config[c] else 0 for c, _ in CONFIG_COLUMNS]
    p2 = [mean(r.get("mast_p2_count", 0) for r in by_config[c]) if by_config[c] else 0 for c, _ in CONFIG_COLUMNS]

    x = range(len(labels))
    width = 0.35
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar([i - width / 2 for i in x], p1, width, label="P1 modes/trace")
    ax.bar([i + width / 2 for i in x], p2, width, label="P2 modes/trace")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("MAST modes per trace")
    ax.set_title("P1 + P2 MAST failure modes per trace (RQ1)")
    ax.legend()
    fig.tight_layout()

    out_path = out_dir / "mast_modes_per_trace.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_task_success(records: list[dict], out_dir: Path) -> Path:
    by_config = _per_config(records)
    labels = [label for _, label in CONFIG_COLUMNS]
    success = [
        100.0 * mean((1.0 if r.get("task_success_proxy") else 0.0) for r in by_config[c]) if by_config[c] else 0.0
        for c, _ in CONFIG_COLUMNS
    ]

    fig, ax = plt.subplots(figsize=(5, 4))
    ax.bar(labels, success)
    ax.set_ylabel("Task success (%) [proxy]")
    ax.set_ylim(0, 100)
    ax.set_title("Task success proxy by configuration")
    fig.tight_layout()

    out_path = out_dir / "task_success.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_impact_precision_recall(out_dir: Path, impact_metrics: dict[str, tuple[float | None, float | None]] | None = None) -> Path:
    impact_metrics = impact_metrics if impact_metrics is not None else compute_impact_metrics()
    configs = [c for c, _ in CONFIG_COLUMNS if impact_metrics.get(c, (None, None))[0] is not None]
    labels = dict(CONFIG_COLUMNS)

    fig, ax = plt.subplots(figsize=(5, 4))
    for c in configs:
        precision, recall = impact_metrics[c]
        ax.scatter([recall], [precision], s=80, label=labels[c])
        ax.annotate(labels[c], (recall, precision), textcoords="offset points", xytext=(6, 6))
    ax.set_xlabel("Impact recall")
    ax.set_ylabel("Impact precision")
    ax.set_xlim(0, 1.05)
    ax.set_ylim(0, 1.05)
    ax.set_title("Impact precision vs. recall (RQ2)")
    fig.tight_layout()

    out_path = out_dir / "impact_precision_recall.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_tokens_per_task(records: list[dict], out_dir: Path) -> Path:
    by_config = _per_config(records)
    labels = [label for _, label in CONFIG_COLUMNS]
    tokens_k = [
        mean(r.get("total_tokens", 0) for r in by_config[c]) / 1000.0 if by_config[c] else 0.0
        for c, _ in CONFIG_COLUMNS
    ]

    fig, ax = plt.subplots(figsize=(5, 4))
    ax.bar(labels, tokens_k)
    ax.set_ylabel("LLM tokens per task (k)")
    ax.set_title("Token cost by configuration")
    fig.tight_layout()

    out_path = out_dir / "tokens_per_task.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def make_all_figures(records: list[dict], out_dir: str | Path = FIGURES_DIR) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return [
        plot_mast_modes(records, out_dir),
        plot_task_success(records, out_dir),
        plot_impact_precision_recall(out_dir),
        plot_tokens_per_task(records, out_dir),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-dir", default=None)
    parser.add_argument("--log-file", action="append", default=None)
    parser.add_argument("--out-dir", default=str(FIGURES_DIR))
    args = parser.parse_args()

    log_paths = [Path(p) for p in args.log_file] if args.log_file else discover_logs(Path(args.logs_dir) if args.logs_dir else None)
    if not log_paths:
        print("no logs found; run evaluation/harness/run_all_configs.py first")
        return 1

    records = load_records(log_paths)
    paths = make_all_figures(records, args.out_dir)
    for p in paths:
        print(f"Wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
