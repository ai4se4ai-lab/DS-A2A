"""The AgentM2M event envelope (schema `agentm2m.event`, version 1).

Correlation ids let an observer rebuild the execution tree:

    run -> hand-off -> rule -> target -> binding -> context version
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA = "agentm2m.event"
SCHEMA_VERSION = 1

EVENT_TYPES = frozenset({
    # team
    "team.loaded", "team.validated", "team.started", "team.completed", "team.failed", "team.evolved",
    # agents (presence projection)
    "agent.registered", "agent.online", "agent.idle", "agent.working", "agent.waiting", "agent.blocked",
    "agent.failed", "agent.completed",
    # hand-offs
    "handoff.started", "handoff.matching", "handoff.target_created", "handoff.target_deleted", "handoff.completed",
    # bindings
    "binding.requested", "binding.prompt_prepared", "binding.submitted", "binding.accepted", "binding.rejected",
    "binding.escalated", "binding.stale", "binding.blocked",
    # change propagation
    "change.requested", "change.previewed", "obligation.created", "obligation.discharged", "impact.computed",
    # traceability
    "trace.created", "trace.updated", "trace.deleted",
    # shared context
    "context.created", "context.updated", "context.attached", "context.read", "context.detached",
    "context.invalidated", "context.received", "context.rejected",
    # errors
    "engine.error", "nostr.error", "context.error", "validation.error",
})

CORRELATION_FIELDS = (
    "run_id", "operation", "agent_id", "handoff_id", "rule_id", "target_key", "binding", "trace_id", "context_ids",
)


class EventSchemaError(ValueError):
    """An event does not conform to a schema version this build understands."""


@dataclass(frozen=True)
class AgentM2MEvent:
    event_type: str
    seq: int
    ts: float
    workspace_id: str
    run_id: str | None = None
    operation: str | None = None
    agent_id: str | None = None
    handoff_id: str | None = None
    rule_id: str | None = None
    target_key: str | None = None
    binding: str | None = None
    trace_id: str | None = None
    context_ids: tuple[str, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def uid(self) -> str:
        """Stable id for deduplication: unique per (run, sequence number)."""
        return f"{self.run_id or '-'}:{self.seq}"

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "event_type": self.event_type,
            "seq": self.seq,
            "ts": self.ts,
            "workspace_id": self.workspace_id,
            "run_id": self.run_id,
            "operation": self.operation,
            "agent_id": self.agent_id,
            "handoff_id": self.handoff_id,
            "rule_id": self.rule_id,
            "target_key": self.target_key,
            "binding": self.binding,
            "trace_id": self.trace_id,
            "context_ids": list(self.context_ids),
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AgentM2MEvent:
        if not isinstance(d, dict) or d.get("schema") != SCHEMA:
            raise EventSchemaError(f"not an {SCHEMA} object")
        if d.get("schema_version") != SCHEMA_VERSION:
            raise EventSchemaError(f"unsupported {SCHEMA} schema_version {d.get('schema_version')!r}")
        if d.get("event_type") not in EVENT_TYPES:
            raise EventSchemaError(f"unknown event_type {d.get('event_type')!r}")
        try:
            return cls(
                event_type=d["event_type"],
                seq=int(d["seq"]),
                ts=float(d["ts"]),
                workspace_id=str(d["workspace_id"]),
                run_id=d.get("run_id"),
                operation=d.get("operation"),
                agent_id=d.get("agent_id"),
                handoff_id=d.get("handoff_id"),
                rule_id=d.get("rule_id"),
                target_key=d.get("target_key"),
                binding=d.get("binding"),
                trace_id=d.get("trace_id"),
                context_ids=tuple(d.get("context_ids") or ()),
                payload=dict(d.get("payload") or {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EventSchemaError(f"malformed {SCHEMA}: {exc}") from None
