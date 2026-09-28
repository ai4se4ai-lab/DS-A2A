#!/usr/bin/env python3
"""Emits a LaTeX `tabular` snippet mirroring Table II (`tab:pilot`,
docs/DS-A2A.tex lines ~550-568) exactly, populated with the real
aggregated numbers from aggregate.compute_table, so it can be pasted back
into the paper in place of the \\ph{} placeholders.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .aggregate import ROW_ORDER, compute_table, discover_logs, load_records

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "results" / "csv" / "pilot_table.tex"

_ROW_TEX_LABELS = {
    "P1 modes per trace (MAST)": "P1 modes per trace (MAST)",
    "P2 modes per trace (MAST)": "P2 modes per trace (MAST)",
    "Task success (%)": "Task success (\\%)",
    "Impact recall / precision (RQ2)": "Impact recall / precision (RQ2)",
    "LLM tokens per task (k)": "LLM tokens per task (k)",
}

_COLUMNS = ["Free text", "Shared schema", "AgentM2M"]


def render_tabular(table: dict[str, dict[str, str]], *, n_tasks: int | None = None) -> str:
    caption_scope = f"{n_tasks} " if n_tasks is not None else ""
    lines = [
        "\\begin{table}[t]",
        "\\centering",
        f"\\caption{{Pilot reporting template on {caption_scope}\\devteam{{}}-shaped tasks "
        "(evaluation/analysis/make_table.py; see evaluation/README.md for the run this reproduces).}",
        "\\label{tab:pilot}",
        "\\footnotesize",
        "\\setlength{\\tabcolsep}{4pt}",
        "\\begin{tabular}{@{}lccc@{}}",
        "\\toprule",
        "Metric & Free text & Shared schema & \\tool{} \\\\",
        "\\midrule",
    ]
    for row in ROW_ORDER:
        label = _ROW_TEX_LABELS[row]
        cells = " & ".join(table[row].get(c, "--") for c in _COLUMNS)
        lines.append(f"{label} & {cells} \\\\")
    lines += ["\\bottomrule", "\\end{tabular}", "\\end{table}"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-dir", default=None)
    parser.add_argument("--log-file", action="append", default=None)
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    log_paths = [Path(p) for p in args.log_file] if args.log_file else discover_logs(Path(args.logs_dir) if args.logs_dir else None)
    if not log_paths:
        print("no logs found; run evaluation/harness/run_all_configs.py first")
        return 1

    records = load_records(log_paths)
    n_tasks = len({r["instance_id"] for r in records})
    table = compute_table(records)
    tex = render_tabular(table, n_tasks=n_tasks)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(tex)
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
