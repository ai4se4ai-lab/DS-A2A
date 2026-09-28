"""Structural and stochastic binding evaluation (Algorithm 1, lines 5-10).

Structural bindings are ordinary OCL-subset expressions; when a structural
expression evaluates to a *source* model element (or a list of them), it is
resolved to the corresponding *target* element through the hand-off's trace
-- exactly ATL's implicit target-resolution for reference bindings
(`component <- s.epic` yields the Component generated from s.epic).

Stochastic bindings sample v ~ D(prompt (+) footprint) and only commit the
value once its `@check` validator accepts it; the resample loop is bounded
by `k` and never loops past it (Proposition 3): on exhaustion the binding
is escalated, not retried forever.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyecore.ecore import EcoreUtils

from ..llm.base import LLMBackend, LLMError
from ..rules.ast import StochasticBinding, StructuralBinding, TargetPattern
from .expr import Helpers, eval_expr
from .lift import LIFT_BINDING_NAME, LiftRejected, lift_json_into_element
from .matcher import Match
from .trace import TraceLink, TraceModel, digest, element_key


@dataclass
class Escalation:
    target_key: str
    binding: str
    rule: str
    reason: str


def _find_feature(target_obj: Any, name: str):
    for f in target_obj.eClass.eAllStructuralFeatures():
        if f.name == name:
            return f
    return None


def resolve_structural_value(value: Any, expected_type: Any, trace: TraceModel, target_registry: dict[str, Any]) -> Any:
    """ATL's implicit target resolution: a structural binding value that is
    itself a *source* model element is translated to the corresponding
    *target* element via the trace -- but only when it doesn't already
    conform to the target feature's declared type (e.g. `component <-
    s.epic` needs resolving; `operation <- op` in a hand-off that targets
    Arch!Operation directly by reference does not: op already fits)."""
    if isinstance(value, list):
        return [resolve_structural_value(v, expected_type, trace, target_registry) for v in value]
    if hasattr(value, "eClass"):
        if expected_type is not None and EcoreUtils.isinstance(value, expected_type):
            return value
        try:
            key = element_key(value)
        except Exception:
            return value
        link = trace.target_for_source(key)
        if link is not None and link.target_key in target_registry:
            return target_registry[link.target_key]
        return value
    return value


def apply_structural_bindings(
    target_pattern: TargetPattern,
    target_obj: Any,
    match: Match,
    trace: TraceModel,
    target_registry: dict[str, Any],
    helpers: Helpers,
) -> None:
    for b in target_pattern.bindings:
        if isinstance(b, StructuralBinding):
            raw = eval_expr(b.expr, match.bindings, helpers)
            feature = _find_feature(target_obj, b.name)
            expected_type = getattr(feature, "eType", None) if feature is not None else None
            setattr(target_obj, b.name, resolve_structural_value(raw, expected_type, trace, target_registry))


def _footprint_to_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {_footprint_to_text(v)}" for v in value)
    if hasattr(value, "eClass"):
        feats = {f.name: getattr(value, f.name) for f in value.eClass.eAllStructuralFeatures()}
        return f"{value.eClass.name}({feats})"
    return str(value)


def apply_stochastic_binding(
    binding: StochasticBinding,
    target_var: str,
    target_obj: Any,
    match: Match,
    trace_link: TraceLink,
    llm: LLMBackend,
    helpers: Helpers,
    *,
    max_resamples: int,
    temperature: float,
) -> tuple[bool, Escalation | None]:
    """Returns (value_changed_this_run, escalation_or_None)."""
    footprint = eval_expr(binding.footprint_expr, match.bindings, helpers)
    fp_digest = digest(footprint)
    if trace_link.stamps.get(binding.name) == fp_digest:
        return (False, None)  # footprint unchanged since acceptance -> no re-invocation

    prompt_head = eval_expr(binding.prompt_expr, match.bindings, helpers)
    prompt = f"{prompt_head}\n\nContext (footprint only):\n{_footprint_to_text(footprint)}"

    last_reason = "no attempts made"
    for _attempt in range(max_resamples):
        try:
            raw = llm.generate(prompt, temperature=temperature)
        except LLMError as exc:
            last_reason = str(exc)
            continue

        # Algorithm 1, line 10: "if accepted: t.f_b <- v" -- the target is
        # only written once a sample is accepted. Lift already satisfies
        # this (lift_json_into_element raises *before* writing any
        # EAttribute if the JSON is rejected); for an ordinary binding we
        # must not commit `raw` to target_obj until any @check has passed,
        # and never leave a rejected sample sitting on the target after the
        # resample budget is exhausted. @check itself never needs the
        # premature write: check_scope already supplies the sampled value
        # under `binding.name` directly.
        ok: bool
        if binding.name == LIFT_BINDING_NAME:
            try:
                lift_json_into_element(raw, target_obj)
            except LiftRejected as exc:
                ok, last_reason = False, str(exc)
            else:
                ok = True
        else:
            ok = True

        if ok and binding.check_expr is not None:
            check_scope = {**match.bindings, target_var: target_obj, binding.name: raw}
            try:
                ok = bool(eval_expr(binding.check_expr, check_scope, helpers))
            except Exception as exc:  # noqa: BLE001 - a failing @check is a rejection, not a crash
                ok, last_reason = False, f"@check raised: {exc}"
            if not ok:
                last_reason = "@check rejected the sampled value"

        if ok:
            if binding.name != LIFT_BINDING_NAME:
                setattr(target_obj, binding.name, raw)
            trace_link.stamps[binding.name] = fp_digest
            trace_link.footprints[binding.name] = footprint
            return (True, None)

    return (
        False,
        Escalation(target_key=trace_link.target_key, binding=binding.name, rule=trace_link.rule, reason=last_reason),
    )
