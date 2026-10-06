"""Shared helpers for the engine test suite (mirrors plugin/tests/conftest.py).

`good_value` answers pass every devteam validator; `fill_all` plays the
binding-worker subagent: it drains `next_bindings` and submits a value per
binding, exactly as Claude Code does through MCP.
"""
from __future__ import annotations

from collections.abc import Callable

from agentm2m.workspace import Workspace

GOOD = {
    "signature": "markTaskDone(id: TaskId) -> Task",
    "body": "def op(task_id):\n    return {'id': task_id, 'done': True}\n",
    "oracle": "def test_oracle():\n    result = implementation('t1')\n    assert result['done'] is True\n",
    "notes": "Requires an authenticated caller; only the task owner may modify it.",
    "risk": "medium",
}


def good_value(binding: dict) -> str:
    return GOOD[binding["binding"]]


def fill_all(
    ws: Workspace, answer: Callable[[dict], str] = good_value, *, agent: str | None = None, rounds: int = 20
) -> list[dict]:
    """Drain every pending binding (re-running so blocked ones unblock)."""
    results: list[dict] = []
    for _ in range(rounds):
        batch = ws.next_bindings(agent=agent, limit=50)["bindings"]
        if not batch:
            return results
        for b in batch:
            results.append(ws.submit_binding(b["target_key"], b["binding"], answer(b), b["footprint_version"]))
    raise AssertionError("bindings did not drain")
