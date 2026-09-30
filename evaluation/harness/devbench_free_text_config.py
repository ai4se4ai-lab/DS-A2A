"""DevBench free-text baseline: the same four DevTeam roles (Analyst,
Architect, Developer, Tester) as devbench_agentm2m_config.py, but each
role's raw prose becomes literally the next role's input -- no persistent
view models, no validation, no trace. Analyst/Architect/Developer/Tester
all see the *same total information* the AgentM2M config's Req view holds
(PRD text + every operation's ground-truth signature hint, including
canaries); what differs is only whether that information survives being
relayed through free text at each hop (Prop. 1's information-loss claim).
"""
from __future__ import annotations

from agentm2m.llm.base import LLMBackend

from .canary import canary_stories_for, canary_test_files
from .common import ConfigRunResult, Turn
from .devbench_common import extract_code_blob, run_devbench_tests
from .devbench_loader import DevBenchTask, criterion_text_for

CONFIG_NAME = "free_text"


def _all_criteria_text(task: DevBenchTask) -> str:
    lines = [criterion_text_for(task, op) for op in task.operations]
    lines += [text for _op, text in canary_stories_for(task)]
    return "\n\n".join(lines)


def run_free_text(
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

    analyst_prompt = (
        f"You are a software Analyst. Product requirements document:\n{task.prd_text}\n\n"
        f"Individual operations required, with ground-truth signature hints:\n{criteria_text}\n\n"
        "In free text, summarize the user stories and acceptance criteria for a hand-off to "
        "the Architect."
    )
    analyst_out = llm.generate(analyst_prompt, temperature=temperature)

    architect_prompt = (
        "You are a software Architect. Here is the Analyst's free-text summary -- nothing "
        f"about it is structured or validated:\n\n{analyst_out}\n\n"
        "In free text, design the module: list every component/class and every operation's "
        "exact Python signature (parameter list and return type)."
    )
    architect_out = llm.generate(architect_prompt, temperature=temperature)

    developer_prompt = (
        "You are a software Developer. Here is the Architect's free-text design -- again, "
        f"nothing about it is structured or validated:\n\n{architect_out}\n\n"
        "Implement the COMPLETE Python module described, with a full function/method "
        "definition for every operation listed. Output the entire module as a single fenced "
        "```python code block and nothing else."
    )
    developer_out = llm.generate(developer_prompt, temperature=temperature)

    tester_prompt = (
        "You are a software Tester. Here is the Developer's free-text implementation -- "
        f"again, nothing about it is structured or validated:\n\n{developer_out[:4000]}\n\n"
        "In free text, describe the test cases you would write to check the acceptance criteria."
    )
    tester_out = llm.generate(tester_prompt, temperature=temperature)

    module_text = extract_code_blob(developer_out)
    test_result = (
        run_devbench_tests(task, module_text, canary_test_sources=canary_test_files()) if grade else None
    )

    turns = [
        Turn("Analyst", analyst_prompt, analyst_out),
        Turn("Architect", architect_prompt, architect_out),
        Turn("Developer", developer_prompt, developer_out),
        Turn("Tester", tester_prompt, tester_out),
    ]
    return ConfigRunResult(
        instance_id=task.repo,
        config=CONFIG_NAME,
        patch_text=module_text,
        turns=turns,
        extra={"test_result": vars(test_result) if test_result else {}},
    )
