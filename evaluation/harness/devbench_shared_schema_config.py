"""DevBench shared-schema baseline (PatchBoard-style): the same four roles
as devbench_agentm2m_config.py, writing into ONE shared, schema-validated
state dict instead of passing raw text forward. Isolates "validates each
write" (this file) from AgentM2M's additional relating of views via trace
(devbench_agentm2m_config.py) -- see docs/DS-A2A.tex Sec IV Discussion.
"""
from __future__ import annotations

from typing import Any

from agentm2m.llm.base import LLMBackend

from .canary import canary_stories_for, canary_test_files
from .common import ConfigRunResult, Turn
from .devbench_common import extract_code_blob, run_devbench_tests
from .devbench_loader import DevBenchTask, criterion_text_for

CONFIG_NAME = "shared_schema"

_SCHEMA: dict[str, dict[str, type]] = {
    "requirements": {"prd": str, "criteria": str},
    "design": {"operations": str},
    "implementation": {"code": str},
}


def validate_write(state: dict[str, Any], stage: str) -> list[str]:
    spec = _SCHEMA[stage]
    section = state.get(stage)
    errors: list[str] = []
    if not isinstance(section, dict):
        return [f"'{stage}' is not an object"]
    for field_name, expected_type in spec.items():
        if field_name not in section:
            errors.append(f"missing required field '{stage}.{field_name}'")
        elif not isinstance(section[field_name], expected_type):
            errors.append(f"'{stage}.{field_name}' has the wrong type (expected {expected_type.__name__})")
        elif expected_type is str and not section[field_name].strip():
            errors.append(f"'{stage}.{field_name}' is empty")
    return errors


def _all_criteria_text(task: DevBenchTask) -> str:
    lines = [criterion_text_for(task, op) for op in task.operations]
    lines += [text for _op, text in canary_stories_for(task)]
    return "\n\n".join(lines)


def run_shared_schema(
    task: DevBenchTask,
    llm: LLMBackend,
    *,
    temperature: float = 0.2,
    criteria_text: str | None = None,
    grade: bool = True,
) -> ConfigRunResult:
    """`criteria_text` overrides the task's own (RQ2 re-runs the pipeline on
    an edited requirement set); `grade=False` skips the DevBench test run."""
    if criteria_text is None:
        criteria_text = _all_criteria_text(task)
    state: dict[str, Any] = {"requirements": {"prd": task.prd_text, "criteria": criteria_text}}
    validation_errors: dict[str, list[str]] = {"requirements": validate_write(state, "requirements")}
    turns: list[Turn] = []

    design_prompt = (
        "You are a software Architect writing into a SHARED, schema-validated state object "
        "with field state['design'] = {'operations': ...}. "
        f"Requirements:\n{state['requirements']['criteria']}\n\n"
        "Write, in the 'operations' field, a design listing every component/class and every "
        "operation's exact Python signature."
    )
    design_out = llm.generate(design_prompt, temperature=temperature)
    state["design"] = {"operations": design_out}
    validation_errors["design"] = validate_write(state, "design")
    turns.append(Turn("Architect", design_prompt, design_out))

    impl_prompt = (
        "You are a software Developer writing into the SAME shared, schema-validated state "
        "object, field state['implementation'] = {'code': ...}. Implement this design:\n"
        f"{state['design']['operations']}\n\n"
        "Output the entire module as a single fenced ```python code block and nothing else."
    )
    impl_out = llm.generate(impl_prompt, temperature=temperature)
    state["implementation"] = {"code": impl_out}
    validation_errors["implementation"] = validate_write(state, "implementation")
    turns.append(Turn("Developer", impl_prompt, impl_out))

    tester_prompt = (
        "You are a software Tester reading the SAME shared state object's "
        f"state['implementation'] field:\n{impl_out[:4000]}\n\n"
        "In free text, describe the test cases you would write to check the acceptance criteria."
    )
    tester_out = llm.generate(tester_prompt, temperature=temperature)
    turns.append(Turn("Tester", tester_prompt, tester_out))

    module_text = extract_code_blob(state["implementation"]["code"])
    test_result = (
        run_devbench_tests(task, module_text, canary_test_sources=canary_test_files()) if grade else None
    )

    all_errors = [e for errs in validation_errors.values() for e in errs]
    return ConfigRunResult(
        instance_id=task.repo,
        config=CONFIG_NAME,
        patch_text=module_text,
        turns=turns,
        extra={
            "validation_errors": validation_errors,
            "schema_valid": not all_errors,
            "test_result": vars(test_result) if test_result else {},
        },
    )
