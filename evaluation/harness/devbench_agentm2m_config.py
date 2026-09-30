"""DevBench configuration that routes hand-offs through the real engine
(Definition 1 / Algorithm 1) using the DevTeam Req/Arch/Code/Test
metamodels (devbench_metamodels.py) and rules (devbench_rules/*.agentm2m),
seeded from a real DevBench repo's PRD + UML instead of the toy
payments/notifications example.
"""
from __future__ import annotations

from pathlib import Path

from agentm2m.llm.base import LLMBackend
from agentm2m.team.model import Team
from agentm2m.team.runtime import TeamRuntime

from .canary import canary_stories_for, canary_test_files
from .common import ConfigRunResult, Turn
from .devbench_common import GeneratedSymbol, assemble_module, run_devbench_tests
from .devbench_loader import DevBenchTask
from .devbench_metamodels import build_arch_mm, build_code_mm, build_req_mm, build_seed_req_model, build_test_mm

CONFIG_NAME = "agentm2m"

_RULES_DIR = Path(__file__).resolve().parent / "devbench_rules"


def build_team_for_task(task: DevBenchTask) -> Team:
    req_mm = build_req_mm()
    arch_mm = build_arch_mm()
    code_mm = build_code_mm(arch_mm)
    test_mm = build_test_mm(req_mm)

    req_root = build_seed_req_model(req_mm, task, canary_stories=canary_stories_for(task))
    arch_root = arch_mm.get("ArchModel")()
    code_root = code_mm.get("CodeModel")()
    test_root = test_mm.get("TestModel")()

    team = Team()
    team.add_agent("Analyst", "Req")
    team.add_view(req_mm, req_root)
    team.add_agent("Architect", "Arch")
    team.add_view(arch_mm, arch_root)
    team.add_agent("Developer", "Code")
    team.add_view(code_mm, code_root)
    team.add_agent("Tester", "Test")
    team.add_view(test_mm, test_root)

    team.add_handoff("Req2Arch", _RULES_DIR / "Req2Arch.agentm2m", target_mm="Arch")
    team.add_handoff("Arch2Code", _RULES_DIR / "Arch2Code.agentm2m", target_mm="Code")
    team.add_handoff("Req2Test", _RULES_DIR / "Req2Test.agentm2m", target_mm="Test")
    return team


def run_agentm2m(task: DevBenchTask, llm: LLMBackend, *, temperature: float = 0.2) -> ConfigRunResult:
    team = build_team_for_task(task)
    # A binding that exhausts its resample budget without ever being
    # accepted has no stamp recorded, so it is retried in full on every
    # subsequent pass (engine/binding.py: only an *accepted* value's digest
    # short-circuits re-invocation). With no cross-operation dependencies
    # in this metamodel, one full pass plus one confirmation pass is enough
    # to reach a real fixpoint, so max_passes/max_resamples are kept small
    # to bound worst-case cost per repo (still >= 2 tries per binding).
    runtime = TeamRuntime(team, llm, temperature=temperature, max_resamples=2, max_passes=2)
    report = runtime.run_to_fixpoint()

    code_edits = list(team.roots["Code"].edits)
    symbols = [
        GeneratedSymbol(component=ce.operation.component.name, name=ce.operation.name, body=ce.body or "")
        for ce in code_edits
        if ce.operation is not None
    ]
    module_text = assemble_module(symbols)
    test_result = run_devbench_tests(task, module_text, canary_test_sources=canary_test_files())

    turns: list[Turn] = []
    for op in team.roots["Arch"].operations:
        turns.append(Turn("Architect", f"@llm signature (footprint: s.criteria) for {op.name}", op.signature or ""))
    for ce in code_edits:
        turns.append(Turn("Developer", f"@llm body (footprint: op.signature) for {ce.name}", ce.body or ""))
    for tc in team.roots["Test"].cases:
        turns.append(Turn("Tester", f"@llm oracle (footprint: c.text) for {tc.name}", tc.oracle or ""))

    escalations = [
        {"target_key": e.target_key, "binding": e.binding, "rule": e.rule, "reason": e.reason}
        for e in report.escalations
    ]
    return ConfigRunResult(
        instance_id=task.repo,
        config=CONFIG_NAME,
        patch_text=module_text,
        turns=turns,
        extra={
            "phi_holds": runtime.acceptance_holds(),
            "obligations": len(report.obligations),
            "escalations": escalations,
            "team_report_summary": report.summary(),
            "test_result": vars(test_result),
        },
    )
