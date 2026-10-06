"""Tables for the shared-context evaluation (evaluation/harness/context_eval.py).

    python -m evaluation.analysis.aggregate_context evaluation/results/context/context_eval.csv
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

COLUMNS = [
    ("config", "Configuration"),
    ("task_success", "phi"),
    ("retention", "Retention"),
    ("retention_after_change", "Retention (after change)"),
    ("change_precision", "Change P"),
    ("change_recall", "Change R"),
    ("llm_calls", "LLM calls"),
    ("change_llm_calls", "Change calls"),
    ("context_reuse_agents", "Reuse (agents)"),
    ("context_induced_obligations", "Ctx. obligations"),
    ("observability_coverage", "Obs. coverage"),
]
LABELS = {
    "free_text": "Free-text hand-off",
    "agentm2m": "AgentM2M",
    "agentm2m_context": "AgentM2M + Context",
    "agentm2m_context_nostr": "AgentM2M + Context + Nostr",
}


def load(path: str | Path) -> list[dict]:
    with open(path) as fh:
        return list(csv.DictReader(fh))


def _cell(row: dict, key: str) -> str:
    v = row.get(key, "")
    if key == "config":
        return LABELS.get(v, v)
    return "--" if v in ("", None) else str(v)


def to_markdown(rows: list[dict]) -> str:
    head = "| " + " | ".join(h for _, h in COLUMNS) + " |"
    sep = "|" + "|".join("---" for _ in COLUMNS) + "|"
    body = ["| " + " | ".join(_cell(r, k) for k, _ in COLUMNS) + " |" for r in rows]
    return "\n".join([head, sep, *body])


def to_latex(rows: list[dict]) -> str:
    lines = [r"\begin{tabular}{l" + "r" * (len(COLUMNS) - 1) + "}", r"\toprule",
             " & ".join(h for _, h in COLUMNS) + r" \\", r"\midrule"]
    for r in rows:
        lines.append(" & ".join(_cell(r, k).replace("_", r"\_") for k, _ in COLUMNS) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    path = Path(argv[0] if argv else "evaluation/results/context/context_eval.csv")
    rows = load(path)
    (path.parent / "context_eval.md").write_text(to_markdown(rows) + "\n")
    (path.parent / "context_eval.tex").write_text(to_latex(rows) + "\n")
    print(to_markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
