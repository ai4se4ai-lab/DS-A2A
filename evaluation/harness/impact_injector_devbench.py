"""RQ2 (change impact) for the DevBench pilot: 3 PRD edits per repo
(tighten/add/remove one operation's criterion), generalizing
evaluation/harness/impact_injector.py's 2-hardcoded-task pattern to all 10
real repos with a ground-truth impact set *derived from the rule
definitions* rather than hand-typed per task.

Ground truth (declared-footprint oracle): in devbench_rules/Req2Arch.agentm2m
and Arch2Code.agentm2m, Story2Operation's stochastic `signature` binding has
footprint `s.criteria` (that story's own criteria only -- no cross-story
read), and Operation2CodeEdit's `body` binding has footprint `op.signature`
(that operation's own signature only). Because every UserStory maps to
exactly one Operation/CodeEdit and no rule reads another story's data, the
declared footprint of a change to one story's criterion is, by
construction, exactly {that operation}: this is a *general* consequence of
the rules as written, computed the same way for every repo/change, not a
value hand-picked per task the way the SWE-bench-Lite track's oracle is.

For "add", the new operation is itself the only footprint the change can
touch (nothing referenced it before). For "remove" (the story's status
flips out of `#accepted`), Req2Arch's guard drops the match, so Prop. 1's
"a matched element cannot be silently dropped" becomes "a *removed* match
must be deleted, not left stale" -- the impacted set is still exactly
{that operation}.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from agentm2m.llm.base import LLMBackend
from agentm2m.team.runtime import TeamRuntime

from .devbench_agentm2m_config import build_team_for_task
from .devbench_loader import DevBenchTask
from .token_meter import TokenMeter

_KEY_SUFFIX_RE = re.compile(r"#([^#]+?)(?:\.C\d+)?$")
_QUALIFIER_RE = re.compile(r"^.*::")


@dataclass
class ChangeSpec:
    kind: str  # "tighten" | "add" | "remove"
    target_op: str  # operation name the change targets (or the new op's name for "add")
    description: str  # natural-language sentence describing the change, for the free-text baseline


@dataclass
class ImpactResult:
    change: ChangeSpec
    predicted: set[str]
    oracle: set[str]
    precision: float
    recall: float
    tokens: int
    detail: str = ""


def _op_name_from_target_key(key: str) -> str:
    """Recovers the short operation name from a target_key like
    "Story2Operation::op::s=UserStory#Component::realName" (or the same
    suffixed with ".C1" for a Criterion-keyed match) -- the trace engine's
    element_key uses the qualified "Component::realName" id for
    collision-freedom (devbench_metamodels.py), but RQ2's oracle is
    reported in terms of the short, human-readable operation name."""
    m = _KEY_SUFFIX_RE.search(key)
    suffix = m.group(1) if m else key
    return _QUALIFIER_RE.sub("", suffix)


def make_changes(task: DevBenchTask) -> list[ChangeSpec]:
    ops = task.operations
    tighten_op = ops[0].name
    remove_op = ops[-1].name if len(ops) > 1 else ops[0].name
    new_op_name = "extra_feature_op"
    return [
        ChangeSpec(
            kind="tighten",
            target_op=tighten_op,
            description=(
                f"Tighten the acceptance criterion for `{tighten_op}`: it must now also raise "
                "ValueError on empty or None input."
            ),
        ),
        ChangeSpec(
            kind="add",
            target_op=new_op_name,
            description=(
                f"Add a new operation `{new_op_name}()` to the module: it must return the "
                "literal string 'ADDED_OK'."
            ),
        ),
        ChangeSpec(
            kind="remove",
            target_op=remove_op,
            description=f"Remove the operation `{remove_op}` from the module; it is no longer required.",
        ),
    ]


def _story_matches(story_id: str, target_op: str) -> bool:
    """story.id is qualified "Component::realName" (devbench_metamodels.py);
    ChangeSpec.target_op is the short, human-readable name."""
    return story_id.rsplit("::", 1)[-1] == target_op


def _apply_change_agentm2m(team, change: ChangeSpec, req_mm) -> None:
    req_root = team.roots["Req"]
    if change.kind == "tighten":
        for story in req_root.stories:
            if _story_matches(story.id, change.target_op):
                for c in story.criteria:
                    c.text = c.text + " STRICT UPDATE: must also raise ValueError on empty/None input."
    elif change.kind == "add":
        epic = req_root.epics[0]
        qid = f"Global_functions::{change.target_op}"
        story = req_mm.new("UserStory", id=qid, status="accepted", epic=epic)
        story.criteria.append(
            req_mm.new(
                "Criterion",
                id=f"{qid}.C1",
                text=f"Implement `{change.target_op}()` on component `Global_functions`. "
                "It must return the literal string 'ADDED_OK'.",
            )
        )
        req_root.stories.append(story)
    elif change.kind == "remove":
        for story in req_root.stories:
            if _story_matches(story.id, change.target_op):
                story.status = "removed"


def run_impact_injection_agentm2m(task: DevBenchTask, llm: LLMBackend, *, temperature: float = 0.2) -> list[ImpactResult]:
    results = []
    for change in make_changes(task):
        team = build_team_for_task(task)
        meter = TokenMeter(llm)
        runtime = TeamRuntime(team, meter, temperature=temperature, max_resamples=2, max_passes=2)
        runtime.run_to_fixpoint()  # T1: establish trace/accepted values

        _apply_change_agentm2m(team, change, team.views["Req"])

        # NOT runtime.run_to_fixpoint(): TeamRunReport.handoff_reports is
        # overwritten (not accumulated) each pass, so a created/deleted
        # element from an early pass that a later, quiet confirmation pass
        # doesn't repeat would be silently lost from report.handoff_reports
        # (report.obligations IS accumulated across passes and is unaffected
        # -- this only matters for created/deleted). With no cross-operation
        # dependencies in this metamodel, one manual pass over the 3
        # hand-offs in order is enough to capture one isolated change's full
        # immediate impact.
        predicted: set[str] = set()
        for handoff_name in ("Req2Arch", "Arch2Code", "Req2Test"):
            report = runtime.run_handoff_once(handoff_name)
            for key in report.created:
                predicted.add(_op_name_from_target_key(key))
            for key in report.deleted:
                predicted.add(_op_name_from_target_key(key))
            for target_key, _binding in report.resampled:
                predicted.add(_op_name_from_target_key(target_key))

        oracle = {change.target_op}
        tp = len(predicted & oracle)
        precision = tp / len(predicted) if predicted else 0.0
        recall = tp / len(oracle) if oracle else 1.0
        results.append(
            ImpactResult(
                change=change, predicted=predicted, oracle=oracle,
                precision=precision, recall=recall, tokens=meter.total_tokens,
                detail=f"predicted={sorted(predicted)} oracle={sorted(oracle)}",
            )
        )
    return results


def run_impact_injection_free_text(task: DevBenchTask, llm: LLMBackend, *, temperature: float = 0.2) -> list[ImpactResult]:
    all_ops = [op.name for op in task.operations]
    results = []
    for change in make_changes(task):
        prompt = (
            f"Current module design for `{task.repo}` has these operations: {', '.join(all_ops)}.\n"
            f"Requested change: {change.description}\n"
            "List, one per line, exactly which of the above operations (by name) need to be "
            "re-implemented because of this change. If the change adds a brand-new operation, "
            "name it too."
        )
        meter = TokenMeter(llm)
        out = meter.generate(prompt, temperature=temperature)
        mentioned = {op for op in all_ops + [change.target_op] if re.search(rf"\b{re.escape(op)}\b", out)}
        oracle = {change.target_op}
        tp = len(mentioned & oracle)
        precision = tp / len(mentioned) if mentioned else 0.0
        recall = tp / len(oracle) if oracle else 1.0
        results.append(
            ImpactResult(
                change=change, predicted=mentioned, oracle=oracle,
                precision=precision, recall=recall, tokens=meter.total_tokens, detail=out,
            )
        )
    return results


def run_impact_injection_shared_schema(task: DevBenchTask) -> list[ImpactResult]:
    """Fixed, non-measured strategy (no LLM calls): shared_schema_config's
    single implementation blob means any change forces regenerating
    everything downstream -- recall is trivially 1, precision measures the
    resulting over-approximation."""
    all_ops = {op.name for op in task.operations}
    results = []
    for change in make_changes(task):
        predicted = all_ops | {change.target_op}
        oracle = {change.target_op}
        precision = len(predicted & oracle) / len(predicted) if predicted else 0.0
        results.append(
            ImpactResult(
                change=change, predicted=predicted, oracle=oracle,
                precision=precision, recall=1.0, tokens=0,
                detail="shared-schema: regenerate everything downstream",
            )
        )
    return results
