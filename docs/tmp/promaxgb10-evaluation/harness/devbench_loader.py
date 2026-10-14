"""Loads a DevBench (Li et al., open-compass/DevBench) Python repository's
PRD, UML class diagram, and real acceptance/unit tests into the shape the
DevTeam Req/Arch/Code/Test metamodels expect (examples/01_devteam), instead
of asking the LLM to invent an architecture from scratch.

DevBench's own "Implementation" stage gives a DevAgent the full PRD + UML +
architecture-design docs and grades it against the repo's real, executable
acceptance/unit tests (see docs/DS-A2A.tex Sec V and evaluation/README.md).
We mirror that: each real UML operation becomes one Req!UserStory whose
Criterion embeds the ground-truth symbol name/parameters (so a coordination
failure shows up as *lost or garbled* information during hand-off, per
Prop. 1/2, rather than as "the LLM guessed a different API").
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

CACHE_ROOT = Path(__file__).resolve().parents[1] / "benchmarks" / "cache" / "devbench" / "python"

ALL_REPOS = [
    "TextCNN", "ArXiv_digest", "chakin", "readtime", "hone",
    "stocktrends", "geotext", "lice", "particle-swarm-optimization", "Hybrid_Images",
]

_CLASS_RE = re.compile(r"class\s+(\w+)\s*\{(.*?)\}", re.DOTALL)
_METHOD_RE = re.compile(r"^\s*[+\-#]\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)", re.MULTILINE)


@dataclass
class OperationSpec:
    component: str
    name: str
    params_hint: str  # raw parameter-list text from the UML, e.g. "text, wpm"


@dataclass
class DevBenchTask:
    repo: str
    path: Path
    prd_text: str
    acceptance_criteria: list[str]
    operations: list[OperationSpec]
    target_files: list[str]  # relative paths to overwrite with generated code
    unit_test_cmd: str
    acceptance_test_cmd: str
    unit_tests_dir: str
    acceptance_tests_dir: str
    dependencies_file: str | None = None
    extra: dict = field(default_factory=dict)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def parse_acceptance_criteria(prd_text: str) -> list[str]:
    """Bullets under '# Acceptance Criteria'; fall back to '# Features and
    Functionalities' bullets if that section is empty/missing (both are
    standard DevBench PRD.md sections -- verified across all 10 repos)."""

    def _bullets_under(heading: str) -> list[str]:
        m = re.search(
            rf"^#+\s*{re.escape(heading)}\s*$(.*?)(?=^#+\s|\Z)",
            prd_text,
            re.MULTILINE | re.DOTALL,
        )
        if not m:
            return []
        body = m.group(1)
        return [ln.strip().lstrip("-").strip() for ln in body.splitlines() if ln.strip().startswith("-")]

    bullets = _bullets_under("Acceptance Criteria")
    if not bullets:
        bullets = _bullets_under("Features and Functionalities")
    return bullets or ["Implement the behavior described in the PRD."]


def parse_uml_operations(uml_text: str) -> list[OperationSpec]:
    """Parse mermaid `classDiagram` blocks (DevBench's UML_class.md format):
    `class Name { +method(args) ... }`. Attribute-only lines (no parens) are
    skipped; constructors on the synthetic 'Global_functions' holder (used
    by DevBench for free functions) are skipped since it is never
    instantiated."""
    ops: list[OperationSpec] = []
    seen: set[tuple[str, str]] = set()
    for cls_match in _CLASS_RE.finditer(uml_text):
        component = cls_match.group(1)
        body = cls_match.group(2)
        for m in _METHOD_RE.finditer(body):
            name, params = m.group(1), m.group(2)
            if name == "__init__" and component == "Global_functions":
                continue
            key = (component, name)
            if key in seen:
                continue
            seen.add(key)
            ops.append(OperationSpec(component=component, name=name, params_hint=params.strip()))
    return ops


def load_repo_config(repo_dir: Path) -> dict:
    return json.loads(_read(repo_dir / "repo_config.json"))


# Upstream repo_config.json test commands that do not match the repo's own
# layout (hone's point at a `test/` dir and a top-level `test_acceptance.py`
# that do not exist; its tests live in unit_tests/ and acceptance_tests/).
# Without these, even the reference implementation fails to grade.
_TEST_CMD_OVERRIDES: dict[str, dict[str, str]] = {
    "hone": {
        "unit_test_script": "pytest --cov=hone --cov-report=term-missing --json-report "
        "--json-report-file=unit_test_report.json unit_tests",
        "acceptance_test_script": "python -m unittest acceptance_tests/test_acceptance.py",
    },
}


def load_devbench_task(repo_name: str, *, cache_root: Path | None = None) -> DevBenchTask:
    root = cache_root or CACHE_ROOT
    repo_dir = root / repo_name
    cfg = load_repo_config(repo_dir)

    prd_text = _read(repo_dir / cfg["PRD"])
    uml_text = _read(repo_dir / cfg["UML_class"])
    criteria = parse_acceptance_criteria(prd_text)
    operations = parse_uml_operations(uml_text)
    if not operations:
        operations = [OperationSpec(component="Global_functions", name="run", params_hint="")]

    dag = cfg.get("code_file_DAG") or {}
    target_files = sorted({*dag.keys(), *(f for files in dag.values() for f in files)})
    if cfg.get("src_files"):
        target_files = sorted(set(target_files) | set(cfg["src_files"]))
    if not target_files:
        raise ValueError(f"{repo_name}: no target files found in repo_config.json's code_file_DAG")

    return DevBenchTask(
        repo=repo_name,
        path=repo_dir,
        prd_text=prd_text,
        acceptance_criteria=criteria,
        operations=operations,
        target_files=target_files,
        unit_test_cmd=_TEST_CMD_OVERRIDES.get(repo_name, {}).get("unit_test_script", cfg["unit_test_script"]),
        acceptance_test_cmd=_TEST_CMD_OVERRIDES.get(repo_name, {}).get(
            "acceptance_test_script", cfg["acceptance_test_script"]
        ),
        unit_tests_dir=cfg["unit_tests"],
        acceptance_tests_dir=cfg["acceptance_tests"],
        dependencies_file=cfg.get("dependencies"),
        extra={"repo_config": cfg},
    )


def load_all_tasks(*, cache_root: Path | None = None) -> list[DevBenchTask]:
    return [load_devbench_task(name, cache_root=cache_root) for name in ALL_REPOS]


def criterion_text_for(task: DevBenchTask, op: OperationSpec, *, include_module_criteria: bool = True) -> str:
    """Builds the footprint text a Req!Criterion carries for one operation.
    Ground-truth symbol name/params are embedded so a coordination failure
    shows up as the hand-off *losing* this information, not as the LLM
    guessing a different API surface than the real tests expect.

    `include_module_criteria=False` omits the module-wide acceptance bullets
    (they are the same for every operation). AgentM2M's Req model holds them
    once, as ModuleCriterion elements, instead of repeating them in every
    per-operation footprint, where they were 65-86% of each prompt's source
    text. The baselines keep the default."""
    is_method = op.component != "Global_functions"
    role_hint = (
        f"This is an instance method of class `{op.component}`; include `self` as the first parameter."
        if is_method
        else "This is a free function (not a class method)."
    )
    head = f"Implement `{op.name}({op.params_hint})` on component `{op.component}`. {role_hint}"
    if not include_module_criteria:
        return head
    bullets = "\n".join(f"- {c}" for c in task.acceptance_criteria)
    return f"{head}\nModule-level acceptance criteria this operation must help satisfy:\n{bullets}"
