"""The single choke point between the engine and observability.

`Emitter.emit` never raises: an invalid event type or a failing sink is
counted (`errors`, `last_error`) and otherwise ignored, so observability can
never change what a transformation computes.
"""
from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from typing import Any

from .events import EVENT_TYPES, AgentM2MEvent
from .privacy import PrivacyConfig, redact
from .sink import NullEventSink

EmitFn = Callable[..., Any]


def noop_emit(event_type: str, **_kw: Any) -> None:
    return None


class Emitter:
    def __init__(
        self,
        sink: object | None = None,
        *,
        workspace_id: str = "",
        privacy: PrivacyConfig | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.sink = sink or NullEventSink()
        self.workspace_id = workspace_id
        self.privacy = privacy or PrivacyConfig()
        self.clock = clock
        self.run_id: str | None = None
        self.operation: str | None = None
        self.errors = 0
        self.last_error: str | None = None
        self.emitted = 0
        self._seq = itertools.count(1)
        self._runs = itertools.count(1)

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.sink, "enabled", True))

    def begin_run(self, operation: str) -> str:
        """Start a correlation scope (one workspace operation: run, edit, ...)."""
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(self.clock()))
        self.run_id = f"run-{stamp}-{next(self._runs):03d}"
        self.operation = operation
        return self.run_id

    def emit(self, event_type: str, *, payload: dict | None = None, **corr: Any) -> AgentM2MEvent | None:
        if not self.enabled:
            return None
        try:
            if event_type not in EVENT_TYPES:
                raise ValueError(f"unknown event type {event_type!r}")
            ctx = corr.pop("context_ids", ()) or ()
            ev = AgentM2MEvent(
                event_type=event_type,
                seq=next(self._seq),
                ts=self.clock(),
                workspace_id=self.workspace_id,
                run_id=corr.pop("run_id", self.run_id),
                operation=corr.pop("operation", self.operation),
                context_ids=tuple(ctx),
                payload=redact(payload or {}, self.privacy),
                **corr,
            )
            self.sink.publish(ev)  # type: ignore[attr-defined]
            self.emitted += 1
            return ev
        except Exception as exc:  # noqa: BLE001 - observability must never break the engine
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    __call__ = emit
