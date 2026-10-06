"""Agent presence, *derived* from the event stream (never stored separately,
so it cannot drift from engine state). States: offline | idle | working |
waiting | blocked | failed."""
from __future__ import annotations

from collections.abc import Iterable

from .events import AgentM2MEvent

_TRANSITIONS = {
    "binding.requested": "working",
    "binding.submitted": "working",
    "binding.accepted": "idle",
    "binding.rejected": "working",
    "binding.blocked": "blocked",
    "binding.escalated": "failed",
    "agent.idle": "idle",
    "agent.working": "working",
    "agent.waiting": "waiting",
    "agent.blocked": "blocked",
    "agent.failed": "failed",
}


def derive_presence(events: Iterable[AgentM2MEvent], agents: Iterable[str]) -> dict[str, str]:
    state = dict.fromkeys(agents, "offline")
    for ev in events:
        if ev.agent_id is None or ev.agent_id not in state:
            continue
        new = _TRANSITIONS.get(ev.event_type)
        if new == "working" and ev.event_type == "binding.requested" and ev.payload.get("deferred"):
            new = "waiting"  # host mode: the value is awaited from Claude Code
        if new is not None:
            state[ev.agent_id] = new
    return state
