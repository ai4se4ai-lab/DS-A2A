"""Helpers for the DevBench rule modules (evaluation/harness/devbench_rules),
loaded via each module's `uses 'helpers.py';` declaration. Reuses the
generic validator building blocks from agentm2m.engine.validators; unlike
examples/01_devteam's helpers.py, operation *names* are passed through
verbatim (see devbench_metamodels.build_seed_req_model / Req2Arch.agentm2m's
`name <- s.id`) rather than mangled by a `toOpName`-style transform, since
they must match the real symbols DevBench's acceptance/unit tests import.
"""
from __future__ import annotations

import re

from agentm2m.engine.validators import python_compiles, signature_parses, signature_params

_FENCE_RE = re.compile(r"^\s*```(?:\w+)?\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)


def strip_fences(text: str) -> str:
    """Coder-tuned models routinely wrap output in markdown code fences even
    when told not to; strip them before validating/assembling so formatting
    doesn't masquerade as a real implementation defect."""
    m = _FENCE_RE.match(text or "")
    return m.group(1) if m else (text or "")


def realName(qualified_id: str) -> str:
    """UserStory.id is "Component::realName" (globally unique, so the trace
    engine's element_key never collides across two components that each
    define, say, their own __init__); this recovers the real symbol name
    DevBench's tests actually import."""
    return qualified_id.rsplit("::", 1)[-1]


_LENIENT_SIG_RE = re.compile(
    r"^(?:def\s+)?[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)\s*(?:->\s*\S+)?\s*:?$"
)


def parses(signature: str) -> bool:
    """Lenient on purpose: real models very often omit an explicit return
    type or add a leading "def "/trailing ":" even when told not to.
    agentm2m.engine.validators.signature_parses (shared core, used by
    examples/01_devteam) requires the strict "name(args) -> Type" shape;
    devbench_rules defines its own, looser check instead of relaxing the
    shared one, so this pilot's construct validity doesn't depend on a
    formatting quirk unrelated to hand-off fidelity."""
    sig = strip_fences(signature).strip().splitlines()[0] if strip_fences(signature).strip() else ""
    return bool(_LENIENT_SIG_RE.match(sig))


def params(signature: str) -> list[str]:
    sig = strip_fences(signature)
    if "->" not in sig and signature_parses(sig + " -> object"):
        return signature_params(sig + " -> object")
    return signature_params(sig)


def compiles(body: str) -> bool:
    return python_compiles(strip_fences(body))


def parsesRisk(raw: str) -> bool:
    return (raw or "").strip().lower() in {"low", "medium", "high"}


def failsOnStub(oracle_src: str) -> bool:
    """@check for Criterion2TestCase: the oracle must compile *and* actually
    fail against an unimplemented stub, i.e. it must call `implementation()`
    and assert something about the result rather than being a vacuous
    always-pass check (same convention as examples/01_devteam)."""
    if not python_compiles(oracle_src):
        return False

    def _stub(*_args, **_kwargs):
        raise NotImplementedError("stub")

    namespace = {"implementation": _stub}
    try:
        exec(oracle_src, namespace)  # noqa: S102 - sandboxed namespace, prototype-only
        test_fn = namespace.get("test_oracle")
        if not callable(test_fn):
            return False
        test_fn()
    except Exception:
        return True
    return False
