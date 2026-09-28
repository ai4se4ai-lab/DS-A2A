"""AgentM2M configuration: the same Analyst/Architect/Developer roles as
free_text_config.py and shared_schema_config.py, but the hand-offs go
through the real engine (Definition 1 / Algorithm 1 of docs/DS-A2A.tex) --
`MetamodelBuilder`, `Team`, `TeamRuntime`, real `.agentm2m` rule files
under evaluation/harness/agentm2m_rules/ -- instead of being simulated.

Two view metamodels/hand-offs, small on purpose (this evaluation compares
hand-off discipline, not solving accuracy -- Sec V): Issue{id,
problem_statement, repo} --IssuePlan--> PatchPlan{id, operation, rationale}
--PlanPatch--> PatchDoc{id, diff}. `PatchDoc.plan` is a real cross-metamodel
reference resolved structurally (like the paper's `component <- s.epic`),
so the produced patch is *linked* to the plan it came from and the run
produces a real, inspectable trace model -- exactly what Sec IV's "Why not
one shared schema?" discussion says a single shared schema (
shared_schema_config.py) leaves implicit.

Defined locally rather than importing examples/01_devteam, per the
`importlib` workaround examples/03_security_reviewer_hot/run.py needs for
its own same-named-module collision: a small self-contained metamodel
here avoids that coupling entirely and keeps evaluation/ independent of
examples/.
"""
from __future__ import annotations

from pathlib import Path

from agentm2m.llm.base import LLMBackend
from agentm2m.metamodel import MetamodelBuilder
from agentm2m.team.model import Team
from agentm2m.team.runtime import TeamRuntime

from .common import ConfigRunResult, Turn

CONFIG_NAME = "agentm2m"

_RULES_DIR = Path(__file__).resolve().parent / "agentm2m_rules"


def build_issue_mm() -> MetamodelBuilder:
    b = MetamodelBuilder("Issue", "http://agentm2m/eval/issue")
    rec = b.eclass("IssueRecord")
    b.attribute(rec, "id")
    b.attribute(rec, "problem_statement")
    b.attribute(rec, "repo")
    root = b.eclass("IssueModel")
    b.add_root_slot(root, "issues", "IssueRecord")
    return b


def build_plan_mm() -> MetamodelBuilder:
    b = MetamodelBuilder("Plan", "http://agentm2m/eval/plan")
    plan = b.eclass("PatchPlan")
    b.attribute(plan, "id")
    b.attribute(plan, "operation")
    b.attribute(plan, "rationale")
    root = b.eclass("PlanModel")
    b.add_root_slot(root, "plans", "PatchPlan")
    return b


def build_patch_mm(plan_mm: MetamodelBuilder) -> MetamodelBuilder:
    b = MetamodelBuilder("Patch", "http://agentm2m/eval/patch")
    doc = b.eclass("PatchDoc")
    b.attribute(doc, "id")
    b.attribute(doc, "diff")
    b.reference(doc, "plan", plan_mm.get("PatchPlan"), many=False, containment=False)
    root = b.eclass("PatchModel")
    b.add_root_slot(root, "patches", "PatchDoc")
    return b


def build_team_for_tasks(tasks: list[dict]) -> Team:
    """Builds one team whose Issue view contains one IssueRecord per task in
    `tasks` -- used directly by run_agentm2m (single task) and by
    impact_injector.py (several tasks, to demonstrate that a change to one
    task's footprint doesn't obligate another's)."""
    issue_mm = build_issue_mm()
    plan_mm = build_plan_mm()
    patch_mm = build_patch_mm(plan_mm)

    issue_root = issue_mm.get("IssueModel")()
    for task in tasks:
        issue_root.issues.append(
            issue_mm.new(
                "IssueRecord",
                id=task.get("instance_id", "unknown"),
                problem_statement=task.get("problem_statement", ""),
                repo=task.get("repo", ""),
            )
        )
    plan_root = plan_mm.get("PlanModel")()
    patch_root = patch_mm.get("PatchModel")()

    team = Team()
    team.add_agent("Analyst", "Issue")
    team.add_view(issue_mm, issue_root)
    team.add_agent("Architect", "Plan")
    team.add_view(plan_mm, plan_root)
    team.add_agent("Developer", "Patch")
    team.add_view(patch_mm, patch_root)

    team.add_handoff("IssuePlan", _RULES_DIR / "IssuePlan.agentm2m", target_mm="Plan")
    team.add_handoff("PlanPatch", _RULES_DIR / "PlanPatch.agentm2m", target_mm="Patch")
    return team


def build_team_for_task(task: dict) -> Team:
    return build_team_for_tasks([task])


def run_agentm2m(task: dict, llm: LLMBackend, *, temperature: float = 0.2) -> ConfigRunResult:
    instance_id = task.get("instance_id", "unknown")
    team = build_team_for_task(task)
    runtime = TeamRuntime(team, llm, temperature=temperature)
    report = runtime.run_to_fixpoint()

    patches = list(team.roots["Patch"].patches)
    plans = list(team.roots["Plan"].plans)
    patch_text = patches[0].diff if patches else ""

    turns: list[Turn] = []
    for plan in plans:
        turns.append(Turn("Architect", "@llm operation (footprint: i.problem_statement)", plan.operation))
        turns.append(Turn("Architect", "@llm rationale (footprint: i.problem_statement)", plan.rationale))
    for doc in patches:
        turns.append(Turn("Developer", "@llm diff (footprint: p.rationale)", doc.diff))

    escalations = [
        {"target_key": e.target_key, "binding": e.binding, "rule": e.rule, "reason": e.reason}
        for e in report.escalations
    ]
    return ConfigRunResult(
        instance_id=instance_id,
        config=CONFIG_NAME,
        patch_text=patch_text,
        turns=turns,
        extra={
            "phi_holds": runtime.acceptance_holds(),
            "obligations": len(report.obligations),
            "escalations": escalations,
            "team_report_summary": report.summary(),
        },
    )
