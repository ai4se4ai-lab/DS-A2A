"""RQ2 (change, Sec V): "Under injected requirement changes, what are the
precision and recall of Obl(Delta) against manually labelled impact sets?
Proposition 2 predicts recall = 1; precision measures footprint
over-approximation."

Uses the real engine (agentm2m_config's Issue->Plan->Patch hand-offs) on a
small two-task seed model, edits one task's problem_statement (a synthetic
requirement change), re-runs to a fixpoint, and reads Obl(Delta) straight
off `TeamRunReport.obligations` (which is exactly Algorithm 1's stamp-check
result -- see `agentm2m.engine.obligations.from_handoff_report`), the same
mechanism examples/02_devteam_change_propagation exercises. That predicted
impact set is compared against a hand-written oracle: only the edited
task's Plan/Patch should be obligated, not the untouched second task's --
the second task is a negative control that makes precision meaningful.

For the shared-schema baseline there is no trace/footprint mechanism to
compute a selective Obl(Delta) at all (Sec IV: "a single shared schema...
leaves relations between perspectives implicit"); the only principled
thing a flat, unrelated shared-state pipeline can do on any upstream edit
is regenerate every downstream field, for every task, since it doesn't
know which ones the edit could have affected. `mode="shared_schema"`
models that directly (no LLM calls) as the full-recompute set, illustrating
the precision loss Sec IV predicts without literally re-running a second
LLM pipeline for a metric neither baseline's mechanism can produce
selectively.
"""
from __future__ import annotations

from dataclasses import dataclass

from agentm2m.llm.base import LLMBackend
from agentm2m.team.runtime import TeamRuntime

from .agentm2m_config import build_team_for_tasks

# Only TASK_A is edited after the initial run; TASK_B is the negative
# control (its footprint is untouched, so nothing about it should be
# obligated).
SEED_TASKS = [
    {
        "instance_id": "impact-A",
        "repo": "acme/widgets",
        "problem_statement": "Fix an off-by-one error in the pagination helper's page-window computation.",
    },
    {
        "instance_id": "impact-B",
        "repo": "acme/widgets",
        "problem_statement": "Add input validation to the signup form's email field.",
    },
]


def oracle_targets_agentm2m() -> set[tuple[str, str]]:
    """Hand-labelled: the change only touches impact-A's footprint, so only
    its Plan (operation, rationale) and, transitively, its Patch (diff)
    should be re-sampled."""
    return {
        ("IssuePlan", "Issue2Plan::p::i=IssueRecord#impact-A"),
        ("PlanPatch", "Plan2Patch::d::p=PatchPlan#impact-A"),
    }


@dataclass
class ImpactResult:
    mode: str
    predicted: set[tuple[str, str]]
    oracle: set[tuple[str, str]]
    precision: float
    recall: float


def _precision_recall(predicted: set, oracle: set) -> tuple[float, float]:
    tp = len(predicted & oracle)
    precision = tp / len(predicted) if predicted else 0.0
    recall = tp / len(oracle) if oracle else 0.0
    return precision, recall


def run_impact_injection_agentm2m(llm: LLMBackend, *, temperature: float = 0.2) -> ImpactResult:
    team = build_team_for_tasks(SEED_TASKS)
    runtime = TeamRuntime(team, llm, temperature=temperature)
    runtime.run_to_fixpoint()  # initial run: establishes trace links + accepted values

    edited = next(i for i in team.roots["Issue"].issues if i.id == "impact-A")
    edited.problem_statement += " Also clamp the returned page size to at most 100 items."

    report2 = runtime.run_to_fixpoint()
    predicted = {(o.handoff, o.target_key) for o in report2.obligations}
    oracle = oracle_targets_agentm2m()
    precision, recall = _precision_recall(predicted, oracle)
    return ImpactResult(mode="agentm2m", predicted=predicted, oracle=oracle, precision=precision, recall=recall)


def run_impact_injection_shared_schema() -> ImpactResult:
    """No engine call: a flat shared-schema pipeline has no footprint
    mechanism to consult, so its only principled response to any upstream
    edit is "regenerate every downstream field, for every task" -- see
    module docstring. This computes precision/recall for that fixed
    full-recompute strategy against the same oracle shape (scoped to the
    two SEED_TASKS instance ids)."""
    predicted = {
        ("plan", "impact-A"),
        ("plan", "impact-B"),
        ("patch", "impact-A"),
        ("patch", "impact-B"),
    }
    oracle = {("plan", "impact-A"), ("patch", "impact-A")}
    precision, recall = _precision_recall(predicted, oracle)
    return ImpactResult(mode="shared_schema", predicted=predicted, oracle=oracle, precision=precision, recall=recall)


def run_impact_injection(llm: LLMBackend, *, temperature: float = 0.2, mode: str = "agentm2m") -> ImpactResult:
    if mode == "agentm2m":
        return run_impact_injection_agentm2m(llm, temperature=temperature)
    if mode == "shared_schema":
        return run_impact_injection_shared_schema()
    raise ValueError(f"unknown impact_injector mode '{mode}' (expected 'agentm2m' or 'shared_schema')")


def main() -> int:
    from agentm2m.config import LLMConfig
    from agentm2m.llm.factory import make_backend

    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    cfg = LLMConfig.from_env()
    llm = make_backend(cfg, override_provider=args.llm, override_model=args.model)

    for mode in ("agentm2m", "shared_schema"):
        result = run_impact_injection(llm, temperature=cfg.temperature, mode=mode)
        print(f"[{mode}] predicted={sorted(result.predicted)}")
        print(f"[{mode}] oracle={sorted(result.oracle)}")
        print(f"[{mode}] precision={result.precision:.2f} recall={result.recall:.2f}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
