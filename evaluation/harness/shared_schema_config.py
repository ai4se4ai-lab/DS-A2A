"""Shared-schema configuration: the same three roles as free_text_config,
but every role writes into ONE shared, schema-validated dict rather than
passing raw text forward -- the PatchBoard-style baseline Sec V compares
against ("a single shared schema in the style of PatchBoard"; see
docs/DS-A2A.tex Sec IV Discussion, "Why not one shared schema?": "A shared
schema validates *each* write; AgentM2M additionally relates *pairs* of
views"). This isolates the effect of validating each write from
AgentM2M's additional relating of views via trace (agentm2m_config.py),
which is what the paper argues change impact and coverage actually need.

We hand-roll the schema-shape checker rather than adding a `jsonschema`
dependency: the venv doesn't have it installed, and the shapes here (three
flat records) don't need a general JSON-Schema engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentm2m.llm.base import LLMBackend

from .common import ConfigRunResult, Turn

CONFIG_NAME = "shared_schema"

# The single shared schema all three roles write into (PatchBoard-style: one
# flat, validated state object, no cross-field relations).
_SCHEMA: dict[str, dict[str, type]] = {
    "issue": {"problem_statement": str, "repo": str},
    "plan": {"operation": str, "rationale": str},
    "patch": {"diff": str},
}


@dataclass
class SchemaValidationError(Exception):
    stage: str
    errors: list[str]

    def __str__(self) -> str:  # pragma: no cover - debug convenience only
        return f"shared-schema validation failed at '{self.stage}': {'; '.join(self.errors)}"


def validate_write(state: dict[str, Any], stage: str) -> list[str]:
    """Validates state[stage] against _SCHEMA[stage]; returns error strings
    (empty == valid). Never raises -- callers record but don't abort on
    failure, matching PatchBoard's "validates each write" framing rather
    than a hard type system."""
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


def _parse_operation_rationale(text: str) -> tuple[str, str]:
    operation, rationale = "unspecified", text.strip()
    for line in text.splitlines():
        low = line.strip().lower()
        if low.startswith("operation:"):
            operation = line.split(":", 1)[1].strip() or operation
        elif low.startswith("rationale:"):
            rationale = line.split(":", 1)[1].strip() or rationale
    return operation, rationale


def run_shared_schema(task: dict, llm: LLMBackend, *, temperature: float = 0.2) -> ConfigRunResult:
    instance_id = task.get("instance_id", "unknown")
    state: dict[str, Any] = {
        "issue": {"problem_statement": task.get("problem_statement", ""), "repo": task.get("repo", "")}
    }
    validation_errors: dict[str, list[str]] = {"issue": validate_write(state, "issue")}
    turns: list[Turn] = []

    plan_prompt = (
        "You are a software Architect writing into a SHARED, schema-validated state "
        "object with fields state['plan'] = {'operation': ..., 'rationale': ...}. "
        f"Issue: {state['issue']['problem_statement']}\n"
        "Respond exactly as:\nOPERATION: <one or two words>\nRATIONALE: <why + roughly where>"
    )
    plan_out = llm.generate(plan_prompt, temperature=temperature)
    operation, rationale = _parse_operation_rationale(plan_out)
    state["plan"] = {"operation": operation, "rationale": rationale}
    validation_errors["plan"] = validate_write(state, "plan")
    turns.append(Turn("Architect", plan_prompt, plan_out))

    patch_prompt = (
        "You are a software Developer writing into the SAME shared, schema-validated "
        "state object, field state['patch'] = {'diff': ...}. Implement this plan:\n"
        f"operation={state['plan']['operation']!r}, rationale={state['plan']['rationale']!r}\n"
        "Output only the unified-diff-shaped patch text."
    )
    diff_out = llm.generate(patch_prompt, temperature=temperature)
    state["patch"] = {"diff": diff_out}
    validation_errors["patch"] = validate_write(state, "patch")
    turns.append(Turn("Developer", patch_prompt, diff_out))

    all_errors = [e for errs in validation_errors.values() for e in errs]
    return ConfigRunResult(
        instance_id=instance_id,
        config=CONFIG_NAME,
        patch_text=state["patch"]["diff"],
        turns=turns,
        extra={"shared_state": state, "validation_errors": validation_errors, "schema_valid": not all_errors},
    )
