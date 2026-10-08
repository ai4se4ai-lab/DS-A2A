"""Observability plane: typed AgentM2M lifecycle events.

The engine reports every meaningful state transition through one `Emitter`
(the single choke point that catches sink failures), into a pluggable
`EventSink`: Null (default), Memory, JSONL timeline, or Nostr. Events are a
*projection* of engine state -- authoritative state stays in state.json --
and carry digests, ids and counts rather than prompts or model content
unless privacy settings explicitly allow it.
"""
from .emitter import Emitter, noop_emit
from .metrics import compute_metrics, observability_coverage
from .events import EVENT_TYPES, SCHEMA, SCHEMA_VERSION, AgentM2MEvent, EventSchemaError
from .privacy import PrivacyConfig, redact
from .sink import (
    CompositeSink,
    EventSink,
    JsonlEventSink,
    MemoryEventSink,
    NullEventSink,
)

__all__ = [
    "EVENT_TYPES", "SCHEMA", "SCHEMA_VERSION", "AgentM2MEvent", "CompositeSink", "Emitter", "EventSchemaError",
    "EventSink", "JsonlEventSink", "MemoryEventSink", "NullEventSink", "PrivacyConfig", "compute_metrics", "noop_emit",
    "observability_coverage", "redact",
]
