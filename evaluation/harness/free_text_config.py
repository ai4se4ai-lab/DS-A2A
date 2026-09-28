"""Baseline configuration: minimal prompt-chaining with free-text hand-offs
between roles, no shared structure, no validation at any hop (Sec V
"Design": "free-text hand-offs"). Each role's raw output becomes literally
the next role's input -- this is deliberately what P1 in the paper's
Table I ("lossy hand-offs between heterogeneous views") describes: nothing
records what was actually produced, and nothing checks it before the next
agent reasons over it.
"""
from __future__ import annotations

from agentm2m.llm.base import LLMBackend

from .common import ConfigRunResult, Turn

CONFIG_NAME = "free_text"


def run_free_text(task: dict, llm: LLMBackend, *, temperature: float = 0.2) -> ConfigRunResult:
    instance_id = task.get("instance_id", "unknown")
    problem = task.get("problem_statement", "")
    repo = task.get("repo", "")

    analyst_prompt = (
        f"You are a software Analyst. Repository: {repo}\n"
        f"Issue report:\n{problem}\n\n"
        "In free text, summarize the bug/feature request and what needs to change."
    )
    analyst_out = llm.generate(analyst_prompt, temperature=temperature)

    architect_prompt = (
        "You are a software Architect. Here is the Analyst's free-text summary of "
        "an issue -- nothing about it is structured or validated:\n\n"
        f"{analyst_out}\n\n"
        "In free text, describe which file(s)/function(s) to change and how."
    )
    architect_out = llm.generate(architect_prompt, temperature=temperature)

    developer_prompt = (
        "You are a software Developer. Here is the Architect's free-text plan -- "
        "again, nothing about it is structured or validated:\n\n"
        f"{architect_out}\n\n"
        "Produce a unified-diff-shaped patch implementing this plan. Output only the patch text."
    )
    patch_text = llm.generate(developer_prompt, temperature=temperature)

    turns = [
        Turn("Analyst", analyst_prompt, analyst_out),
        Turn("Architect", architect_prompt, architect_out),
        Turn("Developer", developer_prompt, patch_text),
    ]
    return ConfigRunResult(instance_id=instance_id, config=CONFIG_NAME, patch_text=patch_text, turns=turns)
