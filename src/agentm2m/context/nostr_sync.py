"""Shared context over Nostr: cross-process / cross-machine collaboration.

Nostr is the *transport*; the local `ContextStore` stays the database and
the local policy stays the authority. Outbound, a new version of a context
whose policy says `visibility: relay` is published as one signed event,
signed by the *writing agent's own key* (the engine key cannot vouch for an
agent's write). Inbound, an event changes the local store only after every
check passes, in this order:

    1. NIP-01 structure, id and BIP-340 signature
    2. schema `agentm2m.context-snapshot` v1, kind, namespace (same team)
    3. the claimed writer is a known agent and the signing pubkey is *its* key
    4. the context exists locally and the local policy lets that agent write
    5. version continuity: exactly local+1 on top of the local digest
       (older/equal -> duplicate or "old version"; other base -> conflict)
    6. content integrity: the snapshot digest matches its items
    7. every new or changed item is attributed to the writer

Content leaves the machine only for `visibility: relay` contexts; use a
private relay for anything confidential (no NIP-44 encryption yet).
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

from ..nostr.errors import InvalidEvent, RelayError
from ..nostr.events import UnsignedEvent
from ..nostr.verifier import verify_event
from .errors import ContextError
from .model import ContextSnapshot, items_digest

NOSTR_KIND_CONTEXT = 4931
SCHEMA = "agentm2m.context-snapshot"
SCHEMA_VERSION = 1


class NostrContextSync:
    def __init__(
        self,
        store: Any,
        relay: Any,
        *,
        namespace: str,
        pubkey_of: Callable[[str], str | None],
        signer_for: Callable[[str], Any],
        kind: int = NOSTR_KIND_CONTEXT,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.store = store
        self.relay = relay
        self.namespace = namespace
        self.pubkey_of = pubkey_of
        self.signer_for = signer_for
        self.kind = kind
        self.on_event = on_event or (lambda _kind, _info: None)
        self._pending: dict[tuple[str, int], tuple[ContextSnapshot, dict]] = {}
        self._lock = threading.RLock()

    @property
    def tag(self) -> str:
        return f"agentm2m-ctx-{self.namespace}"

    # -- outbound -----------------------------------------------------------

    def publish(self, snap: ContextSnapshot, *, previous_digest: str, as_agent: str) -> dict:
        policy = self.store.policy(snap.context_id)
        if policy.visibility != "relay":
            return {"published": False, "reason": f"context {snap.context_id!r} has visibility "
                                                   f"{policy.visibility!r}; only 'relay' contexts are published"}
        signer = self.signer_for(as_agent)
        if signer is None or signer.pubkey != self.pubkey_of(as_agent):
            return {"published": False, "reason": f"agent {as_agent} has no local signing key matching its "
                                                   "team.yaml pubkey; only the writer can sign its own update"}
        content = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "namespace": self.namespace,
                   "as_agent": as_agent, "previous_digest": previous_digest, "snapshot": snap.to_dict()}
        ev = signer.sign(UnsignedEvent(
            created_at=int(time.time()), kind=self.kind,
            tags=[["t", "agentm2m"], ["t", self.tag], ["context", snap.context_id],
                  ["version", str(snap.version)], ["p", signer.pubkey],
                  ["alt", f"AgentM2M shared context {snap.context_id} v{snap.version}"]],
            content=json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        ))
        try:
            res = self.relay.publish(ev)
        except RelayError as exc:
            return {"published": False, "reason": str(exc), "event_id": ev.id}
        return {"published": bool(res.ok), "reason": res.message, "event_id": ev.id}

    # -- inbound -----------------------------------------------------------

    def _decode(self, raw: Any) -> tuple[tuple[ContextSnapshot, dict] | None, str | None]:
        try:
            ev = verify_event(raw)
        except InvalidEvent as exc:
            return None, f"invalid event: {exc}"
        if ev.kind != self.kind:
            return None, f"schema: unexpected kind {ev.kind}"
        try:
            c = json.loads(ev.content)
        except ValueError:
            return None, "schema: content is not JSON"
        if not isinstance(c, dict) or c.get("schema") != SCHEMA or c.get("schema_version") != SCHEMA_VERSION:
            return None, "schema: not an agentm2m.context-snapshot v1"
        if c.get("namespace") != self.namespace:
            return None, f"wrong namespace {c.get('namespace')!r} (this team is {self.namespace!r})"
        agent = c.get("as_agent")
        expected = self.pubkey_of(agent) if isinstance(agent, str) else None
        if expected is None:
            return None, f"unknown agent {agent!r} (no Nostr identity in team.yaml)"
        if expected != ev.pubkey:
            return None, f"signing pubkey does not belong to claimed agent {agent}"
        try:
            snap = ContextSnapshot.from_dict(c["snapshot"])
        except (KeyError, TypeError, ValueError, ContextError) as exc:
            return None, f"schema: bad snapshot ({exc})"
        if not self.store.exists(snap.context_id):
            return None, f"unknown context {snap.context_id!r} (contexts are never created from a relay)"
        if not self.store.policy(snap.context_id).can_write(agent, time.time()):
            return None, f"agent {agent} is not a writer of {snap.context_id!r} under the local policy"
        if items_digest(snap.items) != c["snapshot"].get("digest"):
            return None, "content digest mismatch (tampered snapshot)"
        meta = {"event_id": ev.id, "agent": agent, "previous_digest": c.get("previous_digest")}
        return (snap, meta), None

    def _apply(self, snap: ContextSnapshot, meta: dict) -> str:
        cur = self.store.snapshot(snap.context_id)
        if snap.version <= cur.version:
            try:
                if self.store.snapshot(snap.context_id, snap.version).digest == snap.digest:
                    return "duplicate"
            except ContextError:
                pass
            return f"old version {snap.version} (local is at {cur.version})"
        if snap.version > cur.version + 1:
            return "pending"
        if meta["previous_digest"] != cur.digest:
            return f"conflict: v{snap.version} is based on a version this node does not have"
        current_items = {i.id: i for i in cur.items}
        for item in snap.items:
            if current_items.get(item.id) != item and item.author != meta["agent"]:
                return f"item {item.id} is attributed to another agent than its writer {meta['agent']}"
        try:
            self.store.apply_snapshot(snap)
        except ContextError as exc:
            return f"conflict: {exc}"
        return "applied"

    def receive(self, raw: Any, report: dict | None = None) -> dict:
        """Process one untrusted event (and anything it unblocks)."""
        report = report if report is not None else {"applied": 0, "duplicates": 0, "rejected": []}
        decoded, reason = self._decode(raw)
        eid = raw.get("id") if isinstance(raw, dict) else None
        if decoded is None:
            report["rejected"].append({"event_id": eid, "reason": reason})
            self.on_event("context.rejected", {"event_id": eid, "reason": reason})
            return report
        snap, meta = decoded
        with self._lock:
            self._pending[(snap.context_id, snap.version)] = (snap, meta)
            self._drain(report)
        return report

    def _drain(self, report: dict) -> None:
        progress = True
        while progress:
            progress = False
            for key in sorted(self._pending, key=lambda k: (k[0], k[1])):
                snap, meta = self._pending[key]
                outcome = self._apply(snap, meta)
                if outcome == "pending":
                    continue
                del self._pending[key]
                progress = True
                if outcome == "applied":
                    report["applied"] += 1
                    self.on_event("context.received", {"context_id": snap.context_id, "version": snap.version,
                                                       "digest": snap.digest, "agent": meta["agent"],
                                                       "event_id": meta["event_id"]})
                elif outcome == "duplicate":
                    report["duplicates"] += 1
                else:
                    report["rejected"].append({"event_id": meta["event_id"], "reason": outcome})
                    self.on_event("context.rejected", {"event_id": meta["event_id"], "reason": outcome,
                                                       "context_id": snap.context_id})
                break

    def pull(self) -> dict:
        """Fetch this team's context events from the relay and apply what verifies."""
        report: dict = {"applied": 0, "duplicates": 0, "rejected": []}
        raws = self.relay.query([{"kinds": [self.kind], "#t": [self.tag]}])
        for raw in sorted(raws, key=lambda d: (d.get("created_at", 0) if isinstance(d, dict) else 0)):
            self.receive(raw, report)
        with self._lock:
            for (cid, version), (_snap, meta) in sorted(self._pending.items()):
                report["rejected"].append({"event_id": meta["event_id"],
                                           "reason": f"conflict: missing versions before {cid} v{version}"})
            self._pending.clear()
        return report

    def subscribe(self) -> Any:
        """Apply verified updates as they arrive (history first)."""
        return self.relay.subscribe([{"kinds": [self.kind], "#t": [self.tag]}], self.receive)
