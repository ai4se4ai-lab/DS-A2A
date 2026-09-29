"""Canary criteria (RQ1 fidelity measurement).

Five fixed, repo-independent synthetic operations, each with a behavior
pinned to an arbitrary constant that cannot be guessed from the function
name alone -- only from actually reading and retaining the criterion text
through the Req -> Arch -> Code hand-off chain (Prop. 1/2's "no information
loss" claim). None of the five appear in any real DevBench PRD, so passing
their test cannot be explained by memorizing the reference solution.

"Canary retention" = the fraction of the 5 canary tests that pass against
the final assembled module for a given (repo, config, model) run.
"""
from __future__ import annotations

from dataclasses import dataclass

from .devbench_loader import OperationSpec


@dataclass
class CanarySpec:
    op: OperationSpec
    criterion_text: str
    test_source: str  # a self-contained pytest file loading `TARGET_FILE_PATH` directly


_TEST_HEADER = (
    "import importlib.util as _ilu\n"
    "_spec = _ilu.spec_from_file_location('_canary_target', 'TARGET_FILE_PATH')\n"
    "_m = _ilu.module_from_spec(_spec)\n"
    "_spec.loader.exec_module(_m)\n\n\n"
)


def _canary(name: str, params_hint: str, criterion_text: str, test_body: str) -> CanarySpec:
    op = OperationSpec(component="Global_functions", name=name, params_hint=params_hint)
    return CanarySpec(op=op, criterion_text=f"[Canary] {criterion_text}", test_source=_TEST_HEADER + test_body)


CANARIES: list[CanarySpec] = [
    _canary(
        "canary_checksum_1",
        "payload",
        "Implement `canary_checksum_1(payload: str) -> str`: it must return the input string reversed, "
        'with the literal suffix "-9f2" appended (e.g. "abc" -> "cba-9f2"). This exact suffix is required.',
        "def test_canary_checksum_1():\n"
        "    assert _m.canary_checksum_1('abc') == 'cba-9f2'\n"
        "    assert _m.canary_checksum_1('') == '-9f2'\n",
    ),
    _canary(
        "canary_threshold_2",
        "value",
        "Implement `canary_threshold_2(value: int) -> bool`: it must return True if and only if `value` is "
        "strictly greater than 137 (this exact threshold, not any other value).",
        "def test_canary_threshold_2():\n"
        "    assert _m.canary_threshold_2(138) is True\n"
        "    assert _m.canary_threshold_2(137) is False\n"
        "    assert _m.canary_threshold_2(0) is False\n",
    ),
    _canary(
        "canary_format_3",
        "name, count",
        'Implement `canary_format_3(name: str, count: int) -> str`: it must return f"{name}#{count:04d}" '
        '(the count zero-padded to exactly 4 digits), e.g. canary_format_3("x", 7) -> "x#0007".',
        "def test_canary_format_3():\n"
        "    assert _m.canary_format_3('x', 7) == 'x#0007'\n"
        "    assert _m.canary_format_3('run', 123) == 'run#0123'\n",
    ),
    _canary(
        "canary_merge_4",
        "a, b",
        "Implement `canary_merge_4(a: list, b: list) -> list`: it must return the sorted list of the "
        "deduplicated union of `a` and `b` (i.e. sorted(set(a) | set(b))).",
        "def test_canary_merge_4():\n"
        "    assert _m.canary_merge_4([3, 1], [1, 2]) == [1, 2, 3]\n"
        "    assert _m.canary_merge_4([], [5]) == [5]\n",
    ),
    _canary(
        "canary_flag_5",
        "text",
        'Implement `canary_flag_5(text: str) -> str`: it must return the exact literal string "CANARY_OK" '
        'if the substring "agentm2m" appears anywhere in `text` (case-insensitive), otherwise it must '
        'return the exact literal string "CANARY_MISS".',
        "def test_canary_flag_5():\n"
        "    assert _m.canary_flag_5('uses AgentM2M internally') == 'CANARY_OK'\n"
        "    assert _m.canary_flag_5('unrelated text') == 'CANARY_MISS'\n",
    ),
]


def canary_stories_for(task) -> list[tuple[OperationSpec, str]]:
    """`(OperationSpec, criterion_text)` pairs, ready for
    devbench_metamodels.build_seed_req_model(..., canary_stories=...)."""
    return [(c.op, c.criterion_text) for c in CANARIES]


def canary_test_files() -> dict[str, str]:
    """`{filename: source}` for every canary test, with `TARGET_FILE_PATH`
    still unresolved -- devbench_common.run_devbench_tests substitutes the
    real absolute target-file path before writing these into the scratch
    repo's unit-tests directory."""
    return {f"test_canary_{i}.py": c.test_source for i, c in enumerate(CANARIES, start=1)}
