"""Event sinks. Every sink implements `publish(event)`; `Emitter` is the
only caller and absorbs any exception a sink raises."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from .events import AgentM2MEvent


@runtime_checkable
class EventSink(Protocol):
    enabled: bool

    def publish(self, ev: AgentM2MEvent) -> object: ...


class NullEventSink:
    """Default: observability off; the emitter short-circuits before building events."""

    enabled = False

    def publish(self, ev: AgentM2MEvent) -> None:
        return None


class MemoryEventSink:
    enabled = True

    def __init__(self) -> None:
        self.events: list[AgentM2MEvent] = []

    def publish(self, ev: AgentM2MEvent) -> None:
        self.events.append(ev)

    def types(self) -> list[str]:
        return [e.event_type for e in self.events]


class JsonlEventSink:
    """Local, append-only execution timeline (state/observability/events.jsonl)."""

    enabled = True

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def publish(self, ev: AgentM2MEvent) -> None:
        line = json.dumps(ev.to_dict(), sort_keys=True, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")


class CompositeSink:
    """Fan out to several sinks; one failing sink never starves the others
    (the first failure is re-raised afterwards so the emitter counts it)."""

    def __init__(self, sinks: list[object]) -> None:
        self.sinks = [s for s in sinks if getattr(s, "enabled", True)]
        self.enabled = bool(self.sinks)

    def publish(self, ev: AgentM2MEvent) -> None:
        first: Exception | None = None
        for s in self.sinks:
            try:
                s.publish(ev)
            except Exception as exc:  # noqa: BLE001 - isolate sinks from each other
                first = first or exc
        if first is not None:
            raise first


def event_category(event_type: str) -> str:
    """Publish category of an event type (config `nostr.publish.<category>`)."""
    if event_type.endswith(".error"):
        return "errors"
    prefix = event_type.split(".", 1)[0]
    return {"agent": "agents", "binding": "bindings", "trace": "traces", "context": "contexts"}.get(prefix, "execution")


class FilteredSink:
    """Pass through only the event categories `allow(category)` accepts."""

    enabled = True

    def __init__(self, sink: object, allow) -> None:
        self.sink = sink
        self.allow = allow

    def publish(self, ev: AgentM2MEvent) -> object:
        if self.allow(event_category(ev.event_type)):
            return self.sink.publish(ev)  # type: ignore[attr-defined]
        return None

    def flush(self, **kw) -> object:
        flush = getattr(self.sink, "flush", None)
        return flush(**kw) if flush else None
