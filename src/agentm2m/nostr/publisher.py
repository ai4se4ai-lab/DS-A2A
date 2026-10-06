"""`NostrEventSink`: AgentM2M envelopes -> signed Nostr events -> relays.

Mapping (an AgentM2M application namespace, *not* a standardized NIP):

  kind     NOSTR_KIND_AGENTM2M (4930, a regular kind: relays keep every event)
           NOSTR_KIND_AGENTM2M_PRESENCE (34930, addressable, d=<agent>) for agent.*
           presence, where "latest replaces previous" is exactly right
  content  the agentm2m.event JSON envelope (already privacy-redacted)
  tags     ["t","agentm2m"], ["t","agentm2m-run-<run>"]   relay-indexed filters
           ["p",<agent pubkey>]                           NIP-01 pubkey reference
           ["alt", ...]                                   NIP-31 human summary
           ["type"|"workspace"|"run"|"agent"|"handoff"|"rule"|"target"|
            "binding"|"trace"|"context", ...]             client-side filters

Publishing never blocks the engine: the signed event goes to the outbox
first, one publish attempt follows, and after a relay failure a circuit
breaker sends further events straight to the outbox until the backoff
expires. `flush()` drains the outbox.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable

from ..observability.events import SCHEMA, SCHEMA_VERSION, AgentM2MEvent
from .errors import RelayError
from .events import NostrEvent, UnsignedEvent
from .outbox import Outbox
from .relay import PublishResult
from .signer import Signer

NOSTR_KIND_AGENTM2M = 4930
NOSTR_KIND_AGENTM2M_PRESENCE = 34930


def envelope_tags(ev: AgentM2MEvent, agent_pubkey: str | None) -> list[list[str]]:
    tags: list[list[str]] = [["t", "agentm2m"]]
    if ev.run_id:
        tags.append(["t", f"agentm2m-run-{ev.run_id}"])
    tags.append(["type", ev.event_type])
    tags.append(["schema", f"{SCHEMA}/{SCHEMA_VERSION}"])
    tags.append(["workspace", ev.workspace_id])
    for name, value in (
        ("run", ev.run_id), ("agent", ev.agent_id), ("handoff", ev.handoff_id), ("rule", ev.rule_id),
        ("target", ev.target_key), ("binding", ev.binding), ("trace", ev.trace_id),
    ):
        if value:
            tags.append([name, str(value)])
    tags.extend(["context", c] for c in ev.context_ids)
    if agent_pubkey:
        tags.append(["p", agent_pubkey])
    if ev.event_type.startswith("agent.") and ev.agent_id:
        tags.append(["d", ev.agent_id])
    subject = " ".join(x for x in (ev.agent_id, ev.handoff_id, ev.binding) if x)
    tags.append(["alt", f"AgentM2M event: {ev.event_type}{' (' + subject + ')' if subject else ''}"])
    return tags


class NostrEventSink:
    enabled = True

    def __init__(
        self,
        relay: object,
        outbox: Outbox,
        *,
        signer_for: Callable[[str | None], Signer],
        pubkey_for: Callable[[str], str | None] = lambda _agent: None,
        kind: int = NOSTR_KIND_AGENTM2M,
        presence_kind: int = NOSTR_KIND_AGENTM2M_PRESENCE,
        clock: Callable[[], float] = time.time,
        breaker_base: float = 1.0,
        breaker_max: float = 60.0,
    ) -> None:
        self.relay = relay
        self.outbox = outbox
        self.signer_for = signer_for
        self.pubkey_for = pubkey_for
        self.kind = kind
        self.presence_kind = presence_kind
        self.clock = clock
        self.breaker_base = breaker_base
        self.breaker_max = breaker_max
        self._retry_at = 0.0
        self._breaker_delay = breaker_base
        self.publish_failures = 0
        self.last_error: str | None = None

    def to_nostr(self, ev: AgentM2MEvent) -> NostrEvent:
        signer = self.signer_for(ev.agent_id)
        agent_pk = self.pubkey_for(ev.agent_id) if ev.agent_id else None
        kind = self.presence_kind if ev.event_type.startswith("agent.") else self.kind
        content = json.dumps(ev.to_dict(), sort_keys=True, separators=(",", ":"), default=str)
        return signer.sign(UnsignedEvent(created_at=int(ev.ts), kind=kind, tags=envelope_tags(ev, agent_pk),
                                         content=content))

    @property
    def breaker_open(self) -> bool:
        return self.clock() < self._retry_at

    def _attempt(self, nev: NostrEvent) -> PublishResult:
        try:
            res = self.relay.publish(nev)  # type: ignore[attr-defined]
        except RelayError as exc:
            self.publish_failures += 1
            self.last_error = str(exc)
            self.outbox.fail(nev.id, str(exc))
            self._retry_at = self.clock() + self._breaker_delay
            self._breaker_delay = min(self._breaker_delay * 2, self.breaker_max)
            return PublishResult(False, nev.id, f"deferred: {exc}")
        self._breaker_delay = self.breaker_base
        if res.ok:
            self.outbox.ack(nev.id)
        else:
            self.publish_failures += 1
            self.last_error = res.message
            self.outbox.fail(nev.id, res.message)
        return res

    def publish(self, ev: AgentM2MEvent) -> PublishResult:
        nev = self.to_nostr(ev)
        self.outbox.add(nev.to_dict())
        if self.breaker_open:
            return PublishResult(False, nev.id, "deferred: relay backoff; queued in outbox")
        return self._attempt(nev)

    def flush(self, *, max_events: int | None = None) -> dict:
        published = failed = 0
        for rec in self.outbox.due():
            if self.breaker_open or (max_events is not None and published + failed >= max_events):
                break
            res = self._attempt(NostrEvent.from_dict(rec["event"]))
            if res.ok:
                published += 1
            else:
                failed += 1
        if published:
            self.outbox.compact()
        return {"published": published, "failed": failed, "remaining": self.outbox.depth()}
