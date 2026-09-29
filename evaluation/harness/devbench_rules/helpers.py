"""Helpers for the DevBench rule modules (evaluation/harness/devbench_rules),
loaded via each module's `uses 'helpers.py';` declaration. Reuses the
generic validator building blocks from agentm2m.engine.validators; unlike
examples/01_devteam's helpers.py, operation *names* are passed through
verbatim (see devbench_metamodels.build_seed_req_model / Req2Arch.agentm2m's
`name <- s.id`) rather than mangled by a `toOpName`-style transform, since
they must match the real symbols DevBench's acceptance/unit tests import.
"""
from __future__ import annotations

import ast
import re

from agentm2m.engine.validators import python_compiles, signature_parses, signature_params

_FENCE_RE = re.compile(r"^\s*```(?:\w+)?\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)
_FIRST_FENCE_RE = re.compile(r"```(?:[\w+-]+)?[ \t]*\n(.*?)```", re.DOTALL)


def strip_fences(text: str) -> str:
    """Coder-tuned models routinely wrap output in markdown code fences even
    when told not to; strip them before validating/assembling so formatting
    doesn't masquerade as a real implementation defect."""
    m = _FENCE_RE.match(text or "")
    return m.group(1) if m else (text or "")


def extract_code(text: str) -> str:
    """Superset of strip_fences: also handles the very common "prose
    preamble + one fenced block (+ prose epilogue)" reply shape by taking the
    first fenced block. Without this, every such reply fails to parse and
    burns the whole resample budget on a formatting quirk -- in the pilot
    logs this was the single largest source of AgentM2M's token cost
    (every Req2Test oracle escalated)."""
    text = text or ""
    m = _FIRST_FENCE_RE.search(text)
    return m.group(1) if m else strip_fences(text)


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
    code = extract_code(signature).strip()
    sig = code.splitlines()[0] if code else ""
    return bool(_LENIENT_SIG_RE.match(sig))


def params(signature: str) -> list[str]:
    sig = extract_code(signature)
    if "->" not in sig and signature_parses(sig + " -> object"):
        return signature_params(sig + " -> object")
    return signature_params(sig)


def criteriaText(criteria) -> str:
    """Req2Arch: a story's criteria rendered as plain text for the
    structurally-copied Arch!Operation.spec."""
    return "\n".join(c.text for c in criteria if c.text)


def codeContext(op) -> str:
    """Arch2Code footprint: the operation's derived signature plus its
    structurally-carried spec. Still per-operation (reads nothing from any
    other operation/story), so RQ2's per-story impact locality is kept."""
    return f"Signature: {op.signature or ''}\nSpecification:\n{op.spec or ''}"


def compiles(body: str) -> bool:
    return python_compiles(extract_code(body))


def parsesRisk(raw: str) -> bool:
    return (raw or "").strip().lower() in {"low", "medium", "high"}


def failsOnStub(oracle_src: str) -> bool:
    """@check for Criterion2TestCase: the oracle must compile, must call
    `implementation(...)` without defining it itself, and must actually
    fail against an unimplemented stub (raise the stub's
    NotImplementedError or an AssertionError) -- i.e. it is a real check of
    the criterion, not a vacuous always-pass one and not one that tests its
    own inline re-implementation."""
    code = extract_code(oracle_src)
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False

    calls_impl = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "implementation"
        for n in ast.walk(tree)
    )
    defines_impl = any(
        (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == "implementation")
        or (isinstance(n, ast.Name) and n.id == "implementation" and isinstance(n.ctx, ast.Store))
        for n in ast.walk(tree)
    )
    if not calls_impl or defines_impl:
        return False

    def _stub(*_args, **_kwargs):
        raise NotImplementedError("stub")

    namespace = {"implementation": _stub}
    try:
        exec(code, namespace)  # noqa: S102 - sandboxed namespace, prototype-only
        test_fn = namespace.get("test_oracle")
        if not callable(test_fn):
            return False
        test_fn()
    except (NotImplementedError, AssertionError):
        return True
    except Exception:
        return False
    return False
