"""RQ3 (evolution): after the team's first fixpoint (T1), add a Security
Reviewer mid-run via a HOT alone (agentm2m.team.hot.apply_hot), adapting
examples/03_security_reviewer_hot's pattern to a real DevBench repo.

Two things are reported, per the paper's decision to keep them separate:

* retroactive coverage (empirical, per repo/model/seed): the share of
  Operations that existed *before* the HOT that receive a review after it
  -- this is a real run-based measurement.
* glue lines changed (architectural, not per-repo): the HOT is written
  once and applies to every repo's Architect view uniformly, so this is a
  one-time integration cost, not something that varies per run. We count
  it directly off two literal code snippets below -- one showing the real
  HOT call (agentm2m), one showing the equivalent hand-written retrofit
  the free-text/shared-schema baselines would need (they have no runtime
  team model to attach a HOT to, so adding a reviewer means hand-editing
  both config files to enumerate every pre-existing operation and add a
  review prompt per operation). Both snippets are literal, inspectable
  strings, not hand-picked numbers.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentm2m.llm.base import LLMBackend
from agentm2m.team.hot import TeamChange, apply_hot
from agentm2m.team.runtime import TeamRuntime

from .devbench_agentm2m_config import build_team_for_task
from .devbench_loader import DevBenchTask
from .devbench_sec_metamodel import build_sec_mm
from .token_meter import TokenMeter

_RULES_DIR = Path(__file__).resolve().parent / "devbench_rules"


@dataclass
class Rq3Result:
    repo: str
    n_ops_before: int
    n_reviews_after: int
    retroactive_coverage: float
    tokens: int


def run_rq3(task: DevBenchTask, llm: LLMBackend, *, temperature: float = 0.2) -> Rq3Result:
    team = build_team_for_task(task)
    meter = TokenMeter(llm)
    runtime = TeamRuntime(team, meter, temperature=temperature, max_resamples=2, max_passes=2)
    runtime.run_to_fixpoint()  # T1
    n_ops_before = len(team.roots["Arch"].operations)

    sec_mm = build_sec_mm(team.views["Arch"])
    sec_root = sec_mm.get("SecModel")()
    apply_hot(
        team,
        TeamChange(
            agent_name="SecurityReviewer",
            view=sec_mm,
            view_root=sec_root,
            handoff_name="Arch2Sec",
            rule_path=_RULES_DIR / "Arch2Sec.agentm2m",
        ),
    )
    runtime.run_to_fixpoint()  # T2: the newly added hand-off runs against pre-existing Operations

    n_reviews_after = len(team.roots["Sec"].reviews)
    coverage = (n_reviews_after / n_ops_before) if n_ops_before else 0.0
    return Rq3Result(
        repo=task.repo,
        n_ops_before=n_ops_before,
        n_reviews_after=n_reviews_after,
        retroactive_coverage=coverage,
        tokens=meter.total_tokens,
    )


# --- glue-lines-changed comparison (architectural, counted once, not per repo) ---

AGENTM2M_HOT_GLUE = '''\
sec_mm = build_sec_mm(team.views["Arch"])
sec_root = sec_mm.get("SecModel")()
apply_hot(team, TeamChange(
    agent_name="SecurityReviewer", view=sec_mm, view_root=sec_root,
    handoff_name="Arch2Sec", rule_path=RULES_DIR / "Arch2Sec.agentm2m",
))
runtime.run_to_fixpoint()
'''

# What free_text_config.py AND shared_schema_config.py would each need,
# by hand, to add an equivalent review step -- there is no team model to
# attach a HOT to, so every pre-existing operation must be enumerated and
# reviewed explicitly, and this has to be written twice (once per baseline
# config file) rather than once.
MANUAL_RETROFIT_GLUE_PER_BASELINE = '''\
security_reviews = []
for op_name, op_signature in existing_operations:  # must enumerate every pre-existing operation by hand
    review_prompt = (
        "You are a Security Reviewer. Summarize any authentication/authorization "
        f"concerns for this operation signature: {op_signature}\\n"
        "Then rate the security risk as low, medium, or high."
    )
    review_out = llm.generate(review_prompt, temperature=temperature)
    security_reviews.append({"operation": op_name, "review": review_out})
turns.append(Turn("SecurityReviewer", review_prompt, review_out))
result_extra["security_reviews"] = security_reviews
'''


def _nonblank_lines(src: str) -> int:
    return len([ln for ln in src.splitlines() if ln.strip()])


def glue_line_counts() -> dict[str, int]:
    agentm2m_lines = _nonblank_lines(AGENTM2M_HOT_GLUE)
    manual_lines = _nonblank_lines(MANUAL_RETROFIT_GLUE_PER_BASELINE)
    return {
        "agentm2m_hot": agentm2m_lines,
        "manual_per_baseline_config": manual_lines,
        # written once per baseline file (free_text_config.py, shared_schema_config.py):
        "manual_total_across_baselines": manual_lines * 2,
    }
