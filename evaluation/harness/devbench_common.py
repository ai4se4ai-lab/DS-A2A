"""Shared plumbing for the three DevBench config runners
(devbench_{free_text,shared_schema,agentm2m}_config.py): assembling
generated code into a runnable module and grading it against a repo's real,
unmodified DevBench acceptance/unit tests.

Assembly convention (a documented limitation, not hidden): AgentM2M's Code
view produces one function/method body per Arch!Operation with no
file-path knowledge, and the free-text/shared-schema baselines produce one
undifferentiated code blob. We therefore assemble everything into a single
module string and materialize an identical copy of it at *every* target
file `repo_config.json`'s `code_file_DAG` names for a repo. For the 8/10
repos with exactly one target file this is exact; for the 2 multi-file
repos (hone, particle-swarm-optimization) and TextCNN it over-provides
(each file gets every generated symbol, most of them unused) rather than
under-providing -- generated *content* is still fully accountable to the
LLM, only cross-file placement is not modeled.
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from .devbench_loader import DevBenchTask


@dataclass
class GeneratedSymbol:
    component: str
    name: str
    body: str  # a full "def name(...):" block (see devbench_rules/Arch2Code.agentm2m)


def assemble_module(symbols: list[GeneratedSymbol]) -> str:
    by_component: dict[str, list[GeneratedSymbol]] = {}
    for s in symbols:
        by_component.setdefault(s.component, []).append(s)

    parts: list[str] = []
    for component, syms in by_component.items():
        blocks = [textwrap.dedent(_strip_wrapping_fence(s.body)).strip() for s in syms if s.body]
        if not blocks:
            continue
        if component == "Global_functions":
            parts.append("\n\n".join(blocks))
        else:
            body = "\n\n".join(textwrap.indent(b, "    ") for b in blocks)
            parts.append(f"class {component}:\n{body}")
    return "\n\n\n".join(parts) + "\n"


_FENCE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)
_WRAPPING_FENCE_RE = re.compile(r"^\s*```(?:\w+)?\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)


def _strip_wrapping_fence(text: str) -> str:
    """Same defensive stripping as devbench_rules/helpers.py's strip_fences,
    applied to a symbol's stored body before assembly -- @check validates
    truthiness but never rewrites the stored attribute, so a fenced-but-
    otherwise-valid body must be unwrapped again here or the assembled
    module won't parse."""
    m = _WRAPPING_FENCE_RE.match(text or "")
    return m.group(1) if m else (text or "")


def extract_code_blob(text: str) -> str:
    """Free-text/shared-schema baselines produce prose ending in one fenced
    code block for the final implementation; take the first fenced block,
    or the raw text if the model didn't fence it (best-effort, honest about
    a malformed response rather than silently failing)."""
    m = _FENCE_RE.search(text or "")
    return (m.group(1) if m else (text or "")).strip() + "\n"


def _normalize_cmd(cmd: str) -> str:
    """Route `pytest ...` / `python ...` test commands through this
    process's own interpreter (`sys.executable`) instead of relying on
    `$PATH`, so the project's venv (with DevBench's deps installed) is
    always what actually runs the tests."""
    if cmd.startswith("pytest "):
        return f"{sys.executable} -m pytest " + cmd[len("pytest ") :]
    if cmd.startswith("python "):
        return f"{sys.executable} " + cmd[len("python ") :]
    return cmd


@dataclass
class DevBenchTestResult:
    unit_pass: bool
    acceptance_pass: bool
    canary_pass: int
    canary_total: int
    unit_detail: str = ""
    acceptance_detail: str = ""
    canary_detail: str = ""
    timed_out: bool = False


def _run(cmd: str, *, cwd: Path, timeout: float) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            shlex.split(_normalize_cmd(cmd)),
            shell=False,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        tail = (proc.stdout or "")[-2000:] + (proc.stderr or "")[-1000:]
        return proc.returncode == 0, tail
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT after {timeout}s"


def run_devbench_tests(
    task: DevBenchTask,
    module_text: str,
    *,
    canary_test_sources: dict[str, str] | None = None,
    timeout: float = 90.0,
) -> DevBenchTestResult:
    scratch = Path(tempfile.mkdtemp(prefix=f"devbench_{task.repo}_"))
    try:
        shutil.copytree(task.path, scratch, dirs_exist_ok=True)
        for rel in task.target_files:
            dest = scratch / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(module_text, encoding="utf-8")

        canary_total = 0
        canary_pass = 0
        canary_detail = ""
        if canary_test_sources:
            # Load the target file directly (not `import package.module`):
            # a package-relative import runs the package's own, untouched
            # __init__.py first, which for several repos re-exports real
            # symbols (e.g. chakin's `from .downloader import download,
            # search`) -- if the assembled module doesn't define *those*
            # correctly, __init__.py itself fails to import and drags down
            # every canary test too, even ones with perfectly correct
            # bodies. Canary retention is meant to isolate hand-off
            # fidelity from whether the harder, unrelated real operations
            # also happened to come out right.
            target_file_abs = str((scratch / task.target_files[0]).resolve())
            unit_dir = scratch / task.unit_tests_dir
            unit_dir.mkdir(parents=True, exist_ok=True)
            canary_files = []
            for fname, src in canary_test_sources.items():
                resolved = src.replace("TARGET_FILE_PATH", target_file_abs)
                fpath = unit_dir / fname
                fpath.write_text(resolved, encoding="utf-8")
                canary_files.append(str(fpath.relative_to(scratch)))
            ok, detail = _run(
                f"pytest -q {' '.join(canary_files)}", cwd=scratch, timeout=timeout
            )
            canary_detail = detail
            canary_total = len(canary_test_sources)
            # Count individual passes from pytest's summary line rather than
            # only the aggregate exit code, so a partial-credit fraction is
            # possible (canary retention is a fraction, not a boolean).
            m = re.search(r"(\d+) passed", detail)
            canary_pass = int(m.group(1)) if m else (canary_total if ok else 0)

        unit_ok, unit_detail = _run(task.unit_test_cmd, cwd=scratch, timeout=timeout)
        acc_ok, acc_detail = _run(task.acceptance_test_cmd, cwd=scratch, timeout=timeout)

        return DevBenchTestResult(
            unit_pass=unit_ok,
            acceptance_pass=acc_ok,
            canary_pass=canary_pass,
            canary_total=canary_total,
            unit_detail=unit_detail,
            acceptance_detail=acc_detail,
            canary_detail=canary_detail,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
