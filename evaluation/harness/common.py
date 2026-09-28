"""Shared data structures and task-loading helpers used by all three
hand-off configurations (free_text_config.py, shared_schema_config.py,
agentm2m_config.py) and by run_all_configs.py.

Each configuration takes one SWE-bench-shaped task record (see
evaluation/benchmarks/fetch_swebench_lite.py / fixtures/toy_tasks.jsonl)
and the same pluggable `agentm2m.llm.base.LLMBackend`, and produces one
`ConfigRunResult`: the final patch-shaped text plus a `turns` transcript
used by mast_annotator.py and manual_relabel.py. Sec V is explicit that
solving the task correctly is out of scope here -- what is compared is the
*hand-off discipline* between the same three roles (Analyst/Architect-ish
planner/Developer), not patch-application accuracy.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Turn:
    role: str
    prompt: str
    response: str


@dataclass
class ConfigRunResult:
    instance_id: str
    config: str  # "free_text" | "shared_schema" | "agentm2m"
    patch_text: str
    turns: list[Turn] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # config-specific detail (validation errors, obligations, ...)

    def transcript_text(self) -> str:
        """Flattened transcript handed to the MAST annotator / manual relabelling."""
        parts = [f"[{t.role}]\nPROMPT: {t.prompt}\nRESPONSE: {t.response}" for t in self.turns]
        return "\n\n".join(parts)


def load_tasks(path: str | Path) -> list[dict]:
    path = Path(path)
    tasks: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


def default_tasks_file() -> Path:
    """The synthetic fixture (evaluation/benchmarks/fixtures/toy_tasks.jsonl)
    used when no --tasks-file is given: three small, clearly-labelled
    hand-written "SWE-bench-shaped" records, so run_all_configs.py always
    has something to run without a real download (evaluation/README.md)."""
    here = Path(__file__).resolve().parent
    return here.parent / "benchmarks" / "fixtures" / "toy_tasks.jsonl"
