#!/usr/bin/env python3
"""Produces the manual-relabelling template Sec V's protocol calls for
("a sample is re-labelled manually"): reads a run_all_configs.py JSONL log,
takes a sample of (task, config) records, and writes
evaluation/results/csv/manual_relabel_sample.csv with the mast_annotator's
guess plus empty columns for a human to fill in agreement/correction. This
script does NOT do the manual labelling itself -- that's a human step.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .mast_annotator import MastAnnotation

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "results" / "csv" / "manual_relabel_sample.csv"

FIELDNAMES = [
    "instance_id",
    "config",
    "annotator_modes_present",
    "human_agrees",  # human fills in: yes / no / partial
    "human_modes_present",  # human fills in: corrected mode-id list, ';'-separated
    "human_notes",
]


def build_rows(annotations: list[MastAnnotation]) -> list[dict[str, str]]:
    return [
        {
            "instance_id": a.instance_id,
            "config": a.config,
            "annotator_modes_present": ";".join(a.modes_present),
            "human_agrees": "",
            "human_modes_present": "",
            "human_notes": "",
        }
        for a in annotations
    ]


def write_relabel_csv(annotations: list[MastAnnotation], out_path: str | Path = DEFAULT_OUTPUT) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(build_rows(annotations))
    return out_path


def annotations_from_log(log_path: str | Path) -> list[MastAnnotation]:
    records = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            records.append(
                MastAnnotation(
                    instance_id=d["instance_id"],
                    config=d["config"],
                    modes_present=d.get("mast_modes", []),
                    raw_response=d.get("mast_raw", ""),
                )
            )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-file", required=True, help="a run_all_configs.py JSONL log")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--sample-n", type=int, default=15, help="how many rows to sample for manual relabelling")
    args = parser.parse_args()

    records = annotations_from_log(args.log_file)
    sample = records[: args.sample_n]
    out = write_relabel_csv(sample, args.out)
    print(f"Wrote {len(sample)} row(s) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
