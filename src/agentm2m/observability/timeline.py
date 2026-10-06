"""Query the local JSONL execution timeline."""
from __future__ import annotations

import json
from pathlib import Path

from .events import AgentM2MEvent, EventSchemaError


def read_timeline(
    path: str | Path,
    *,
    run_id: str | None = None,
    agent: str | None = None,
    handoff: str | None = None,
    event_type: str | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int | None = None,
) -> list[AgentM2MEvent]:
    p = Path(path)
    if not p.is_file():
        return []
    out: list[AgentM2MEvent] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = AgentM2MEvent.from_dict(json.loads(line))
        except (ValueError, EventSchemaError):
            continue  # never silently reinterpret unknown schemas; skip them
        if run_id and ev.run_id != run_id:
            continue
        if agent and ev.agent_id != agent:
            continue
        if handoff and ev.handoff_id != handoff:
            continue
        if event_type and ev.event_type != event_type:
            continue
        if since is not None and ev.ts < since:
            continue
        if until is not None and ev.ts > until:
            continue
        out.append(ev)
    if limit is not None:
        out = out[-limit:]
    return out
