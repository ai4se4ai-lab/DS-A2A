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

from agentm2m.engine.validators import Rejected, python_compiles, signature_parses, signature_params

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
    if m:
        return m.group(1)
    code = strip_fences(text)
    stripped = code.strip()
    # Inline code: a one-line answer wrapped in single backticks, e.g.
    # `__init__(self, seconds: int) -> None` (seen causing a signature
    # escalation on readtime).
    if len(stripped) > 1 and stripped[0] == "`" and stripped[-1] == "`" and "\n" not in stripped:
        return stripped.strip("`").strip()
    return code


def realName(qualified_id: str) -> str:
    """UserStory.id is "Component::realName" (globally unique, so the trace
    engine's element_key never collides across two components that each
    define, say, their own __init__); this recovers the real symbol name
    DevBench's tests actually import."""
    return qualified_id.rsplit("::", 1)[-1]


_LENIENT_SIG_RE = re.compile(
    r"^(?:def\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)\s*(?:->\s*\S+)?\s*:?$"
)


def parses(signature: str) -> bool:
    """Lenient on purpose: real models very often omit an explicit return
    type or add a leading "def "/trailing ":" even when told not to.
    agentm2m.engine.validators.signature_parses (shared core, used by
    examples/01_devteam) requires the strict "name(args) -> Type" shape;
    devbench_rules defines its own, looser check instead of relaxing the
    shared one, so this pilot's construct validity doesn't depend on a
    formatting quirk unrelated to hand-off fidelity. Also accepts a
    class-qualified name (`GeoText.__init__(self, ...)`), which models emit
    for methods: the qualifier is redundant with the operation's component,
    not a wrong signature."""
    code = extract_code(signature).strip()
    sig = code.splitlines()[0] if code else ""
    if _LENIENT_SIG_RE.match(sig):
        return True
    return Rejected("respond with only the signature, name(param1, param2) -> ReturnType, on one line")


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
    if python_compiles(extract_code(body)):
        return True
    return Rejected("the code is not valid Python")


def definesName(body: str, name: str) -> bool:
    """@check for Operation2CodeEdit: the body must define a top-level
    function/method with the operation's exact name. compiles() alone
    accepted, e.g., a `def process_text(...)` for `__init__`, which the
    assembled module then silently lacks."""
    try:
        tree = ast.parse(extract_code(body))
    except SyntaxError:
        return Rejected("the code is not valid Python")
    if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name for n in tree.body):
        return True
    return Rejected(f"the code must define a top-level `def {name}(...)` with exactly that name")


def loads(body: str) -> bool:
    """@check for Operation2CodeEdit: the body's top-level statements
    (imports, the def itself) must execute. compiles() only parses, so a
    body with e.g. `from typing import Context` was accepted and then made
    the whole assembled module fail to import -- taking every other
    operation and all canaries down with it (observed: lice). Function
    bodies are not run; annotations are lazy, as in the assembled module."""
    code = extract_code(body)
    try:
        exec(compile("from __future__ import annotations\n" + code, "<body>", "exec"), {})  # noqa: S102 - prototype-only
    except Exception as exc:  # noqa: BLE001 - any load failure is a rejection
        detail = f"{type(exc).__name__}: {exc}"[:160]
        return Rejected(f"the code fails when loaded ({detail}); import only modules that exist")
    return True


def parsesRisk(raw: str) -> bool:
    return (raw or "").strip().lower() in {"low", "medium", "high"}


def _drop_truncated_tail(code: str, *, max_drop: int = 3) -> str:
    """An oracle that hit the output-token cap ends mid-statement (observed:
    a model adding assert after assert until cut off), which made an
    otherwise complete test_oracle() unparsable and escalated it after
    burning the whole cap twice. Dropping up to `max_drop` trailing lines
    recovers the complete prefix; if that still does not parse, the
    original text is returned and rejected as before."""
    try:
        ast.parse(code)
        return code
    except SyntaxError:
        pass
    lines = code.rstrip().splitlines()
    for k in range(1, min(max_drop, len(lines) - 1) + 1):
        candidate = "\n".join(lines[:-k])
        try:
            ast.parse(candidate)
            return candidate
        except SyntaxError:
            continue
    return code


def failsOnStub(oracle_src: str) -> bool:
    """@check for Criterion2TestCase: the oracle must compile, must call
    `implementation(...)` without defining it itself, and must actually
    fail against an unimplemented stub (raise the stub's
    NotImplementedError or an AssertionError) -- i.e. it is a real check of
    the criterion, not a vacuous always-pass one and not one that tests its
    own inline re-implementation."""
    code = _drop_truncated_tail(extract_code(oracle_src))
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return Rejected("the oracle is not valid Python")

    calls_impl = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "implementation"
        for n in ast.walk(tree)
    )
    defines_impl = any(
        (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == "implementation")
        or (isinstance(n, ast.Name) and n.id == "implementation" and isinstance(n.ctx, ast.Store))
        for n in ast.walk(tree)
    )
    if defines_impl:
        return Rejected("do not define `implementation` yourself; it is provided")
    if not calls_impl:
        return Rejected(
            "the oracle must call `implementation(...)` directly (for a method, pass the "
            "arguments after `self`); do not construct classes or call the operation by its own name"
        )

    def _stub(*_args, **_kwargs):
        raise NotImplementedError("stub")

    namespace = {"implementation": _stub}
    try:
        exec(code, namespace)  # noqa: S102 - sandboxed namespace, prototype-only
        test_fn = namespace.get("test_oracle")
        if not callable(test_fn):
            return Rejected("define a function named `test_oracle()`")
        test_fn()
    except (NotImplementedError, AssertionError):
        return True
    except Exception as exc:  # noqa: BLE001 - any other failure is a broken oracle
        detail = f"{type(exc).__name__}: {exc}"[:160]
        return Rejected(
            f"the oracle crashed before checking the result ({detail}); "
            "use only `implementation(...)` and Python built-ins"
        )
    return Rejected("the oracle passes even when `implementation` is unimplemented, so it checks nothing")
