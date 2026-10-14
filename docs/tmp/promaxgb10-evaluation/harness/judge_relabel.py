"""Independent-judge relabeling: a stand-in for the "20% re-labelled
manually" step in the paper's protocol. No human annotator is available in
this automated pipeline, so a 20% sample of traces is re-annotated by one
of the *other two* pilot models (never the model whose trace is being
judged), and agreement with the original (self-judged) MAST annotation is
reported as the proxy for inter-rater reliability. This is explicitly a
limitation, not a substitute for Cemri et al.'s own (unreleased) human
validation -- see docs/DS-A2A.tex Sec V "Threats".
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from agentm2m.config import LLMConfig
from agentm2m.llm.factory import make_backend

from . import mast_annotator

SAMPLE_FRACTION = 0.2


@dataclass
class RelabelRow:
    repo: str
    config: str
    model: str
    judge_model: str
    original_modes: list[str]
    judge_modes: list[str]
    agree: bool


def sample_records(records: list[dict], *, fraction: float = SAMPLE_FRACTION, seed: int = 0) -> list[dict]:
    usable = [r for r in records if "mast_raw" in r and "error" not in r]
    n = max(1, round(len(usable) * fraction))
    rng = random.Random(seed)
    return rng.sample(usable, min(n, len(usable)))


def relabel(records: list[dict], *, judge_model: str, judge_provider: str = "ollama", seed: int = 0) -> list[RelabelRow]:
    cfg = LLMConfig.from_env()
    judge_llm = make_backend(cfg, override_provider=judge_provider, override_model=judge_model)

    rows: list[RelabelRow] = []
    for r in sample_records(records, seed=seed):
        transcript = r.get("transcript_text") or r.get("mast_raw", "")
        annotation = mast_annotator.annotate(r["repo"], r["config"], transcript, judge_llm, temperature=0.0)
        original = set(r.get("mast_modes", []))
        judged = set(annotation.modes_present)
        rows.append(
            RelabelRow(
                repo=r["repo"], config=r["config"], model=r["model"], judge_model=judge_model,
                original_modes=sorted(original), judge_modes=sorted(judged), agree=(original == judged),
            )
        )
    return rows


def agreement_rate(rows: list[RelabelRow]) -> float:
    return sum(1 for r in rows if r.agree) / len(rows) if rows else 0.0


def write_csv(rows: list[RelabelRow], out_path: str | Path) -> Path:
    import csv

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["repo", "config", "model", "judge_model", "original_modes", "judge_modes", "agree"])
        for r in rows:
            w.writerow([r.repo, r.config, r.model, r.judge_model, ";".join(r.original_modes), ";".join(r.judge_modes), r.agree])
    return out_path


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--judge-model", required=True, help="a DIFFERENT model than the one that produced --log-file")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    records = [json.loads(line) for line in Path(args.log_file).read_text().splitlines() if line.strip()]
    rows = relabel(records, judge_model=args.judge_model)
    rate = agreement_rate(rows)
    out_path = args.out or Path(args.log_file).with_suffix("").as_posix() + f"_relabel_by_{args.judge_model.replace(':', '-')}.csv"
    write_csv(rows, out_path)
    print(f"Agreement rate: {rate:.2f} over {len(rows)} sampled traces. Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
