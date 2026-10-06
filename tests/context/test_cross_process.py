"""Phase 16: shared context across OS processes (same workspace on disk).

Process A publishes, process B resolves the context and binds; B publishes
an update that A observes as obligations; two processes racing on one
expected_version produce exactly one winner and one CONTEXT_CONFLICT.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from agentm2m.llm.mock_backend import MockBackend
from agentm2m.workspace import Workspace

from ._fixtures import make_ctx_workspace

REPO = Path(__file__).resolve().parents[2]


def _py(code: str, *args: str, timeout: float = 60) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(code), *args],
        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={"PYTHONPATH": f"{REPO / 'src'}:{REPO}", "PATH": "/usr/bin:/bin"},
    )


def _result(p: subprocess.Popen) -> dict:
    out, err = p.communicate(timeout=60)
    assert p.returncode == 0, err
    return json.loads(out.strip().splitlines()[-1])


PUBLISH = """
import json, sys
from agentm2m.workspace import Workspace, WorkspaceError
ws = Workspace(sys.argv[1], backend="host")
try:
    r = ws.context_update("security-review", as_agent="Architect", expected_version=int(sys.argv[2]),
                          items=[{"id": sys.argv[3], "type": "security-finding", "content": sys.argv[4]}])
    print(json.dumps({"ok": True, "version": r["version"]}))
except WorkspaceError as exc:
    print(json.dumps({"ok": False, "error": str(exc)}))
"""

BIND = """
import json, sys
from agentm2m.llm.mock_backend import MockBackend
from agentm2m.workspace import Workspace
ws = Workspace(sys.argv[1], llm=MockBackend(), max_resamples=3)
r = ws.run()
team = ws._require().team
link = team.traces["Arch2Code"].get("Operation2CodeEdit", "op=Operation#op_s2")
print(json.dumps({"phi": ws.acceptance()["phi"], "pin": link.context_pins["body"][0]["version"]}))
"""


def test_publish_in_a_bind_in_b_update_seen_by_a(tmp_path: Path):
    ws_a = make_ctx_workspace(tmp_path, llm=MockBackend())
    assert _result(_py(PUBLISH, str(tmp_path), "1", "finding-001", "auth required"))["version"] == 2
    b = _result(_py(BIND, str(tmp_path)))
    assert b == {"phi": True, "pin": 2}
    # A's in-memory workspace sees B's accepted values and the shared context
    a = Workspace(tmp_path, llm=MockBackend(), max_resamples=3)
    assert a.acceptance()["phi"] is True
    assert _result(_py(PUBLISH, str(tmp_path), "2", "finding-002", "rate limit login"))["version"] == 3
    stale = {(s["target_key"], s["binding"]) for s in ws_a.binding_states() if s["state"] == "stale"}
    assert len(stale) == 5 and {b for _, b in stale} == {"body", "oracle"}
    assert ws_a.contexts.snapshot("security-review").version == 3


RACE = """
import json, sys, time, os
from agentm2m.workspace import Workspace, WorkspaceError
ws = Workspace(sys.argv[1], backend="host")
ws._require()
go = sys.argv[2]
while not os.path.exists(go):
    time.sleep(0.005)
try:
    r = ws.context_update("security-review", as_agent="Architect", expected_version=1,
                          items=[{"id": sys.argv[3], "type": "note", "content": sys.argv[3]}])
    print(json.dumps({"ok": True, "version": r["version"]}))
except WorkspaceError as exc:
    print(json.dumps({"ok": False, "error": str(exc)}))
"""


def test_concurrent_updates_have_exactly_one_winner(tmp_path: Path):
    project = tmp_path / "p"
    project.mkdir()
    make_ctx_workspace(project)
    go = tmp_path / "go"
    procs = [_py(RACE, str(project), str(go), f"item-{i}") for i in range(4)]
    import time

    time.sleep(1.0)  # let all four load the workspace and wait on the barrier
    go.write_text("go")
    results = [_result(p) for p in procs]
    winners = [r for r in results if r["ok"]]
    losers = [r for r in results if not r["ok"]]
    assert len(winners) == 1 and winners[0]["version"] == 2
    assert len(losers) == 3 and all("CONTEXT_CONFLICT" in r["error"] for r in losers)
    snap = Workspace(project, backend="host").contexts.snapshot("security-review")
    assert snap.version == 2 and len(snap.items) == 1
