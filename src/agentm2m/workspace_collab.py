"""Collaboration and observability operations of a `Workspace`.

Split from workspace.py for size; every method here is part of `Workspace`
(the mixin relies on its `_lock`, `_require`, `emitter`, `contexts`, ...).
All return JSON-able dicts and raise `WorkspaceError` for user errors, so
the MCP server and the CLI stay thin wrappers.
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from .context.errors import ContextError, ContextNotFound
from .context.model import ContextItem, ContextPolicy, items_digest
from .engine.executor import _index_existing_targets
from .engine.trace import element_key
from .rules.ast import StochasticBinding

if TYPE_CHECKING:  # pragma: no cover
    from .workspace import _Loaded


def _werr(exc: Exception):
    from .workspace import WorkspaceError

    return WorkspaceError(str(exc))


class CollaborationMixin:
    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _known_agent(self, loaded: _Loaded, agent: str) -> str:
        if agent not in loaded.team.agents:
            raise _werr(ValueError(f"unknown agent {agent!r}; agents: {sorted(loaded.team.agents)}"))
        return agent

    def _declared_contexts(self, loaded: _Loaded) -> dict:
        return dict(loaded.spec.get("contexts") or {})

    def _context_consumers(self, loaded: _Loaded) -> list[dict]:
        """Every (target, @llm binding) that declares a context dependency,
        with its current state (fresh/stale/missing/blocked/...)."""
        team, rt = loaded.team, loaded.runtime
        refs: dict[tuple[str, str, str], tuple] = {}
        for hname in team.handoffs:
            for rule in rt.module_for(hname).rules:
                for tp in rule.to_clause.patterns:
                    for b in tp.bindings:
                        if isinstance(b, StochasticBinding) and b.context_refs:
                            refs[(rule.name, tp.var, b.name)] = b.context_refs
        out = []
        for st in self.binding_states(loaded):
            rule, var, _m = st["target_key"].split("::", 2)
            r = refs.get((rule, var, st["binding"]))
            if r:
                out.append({**st, "contexts": [x.context_id for x in r]})
        return out

    @staticmethod
    def _items(raw: list | None) -> list[ContextItem]:
        return [i if isinstance(i, ContextItem) else ContextItem.from_dict(i) for i in (raw or [])]

    @staticmethod
    def _meta(snap: Any) -> dict:
        return {"context_id": snap.context_id, "version": snap.version, "digest": snap.digest,
                "title": snap.title, "item_ids": [i.id for i in snap.items]}

    # ------------------------------------------------------------------
    # shared context
    # ------------------------------------------------------------------

    def context_create(
        self,
        context_id: str,
        as_agent: str,
        title: str = "",
        readers: list[str] | None = None,
        writers: list[str] | None = None,
        visibility: str = "private",
        items: list[dict] | None = None,
    ) -> dict:
        """Create a runtime context owned by `as_agent` (declared contexts
        come from team.yaml and already exist)."""
        with self._lock:
            loaded = self._require()
            self._known_agent(loaded, as_agent)
            self._begin("context_create")
            try:
                policy = ContextPolicy(owner=as_agent, readers=frozenset(readers or ()),
                                       writers=frozenset(writers or ()), visibility=visibility)
                snap = self.contexts.create(context_id, policy=policy, as_agent=as_agent, title=title,
                                            items=self._items(items))
            except ContextError as exc:
                self.emitter.emit("context.error", agent_id=as_agent, context_ids=(str(context_id),),
                                  payload={"error": str(exc)[:300]})
                raise _werr(exc) from exc
            self.emitter.emit("context.created", agent_id=as_agent, context_ids=(context_id,), payload={
                "version": snap.version, "digest": snap.digest, "items": [i.id for i in snap.items],
                "readers": sorted(policy.readers), "writers": sorted(policy.writers), "visibility": visibility,
                "context_content": [i.to_dict() for i in snap.items],
            })
            self._after_op()
            return self._meta(snap)

    def context_get(self, context_id: str, as_agent: str, version: int | None = None) -> dict:
        with self._lock:
            loaded = self._require()
            self._known_agent(loaded, as_agent)
            try:
                snap = self.contexts.get(context_id, version, as_agent=as_agent)
            except ContextError as exc:
                raise _werr(exc) from exc
            self.emitter.emit("context.read", agent_id=as_agent, context_ids=(context_id,), payload={
                "version": snap.version, "digest": snap.digest, "items": [i.id for i in snap.items],
                "purpose": "agent-request",
            })
            return snap.to_dict()

    context_snapshot = context_get

    def context_update(
        self,
        context_id: str,
        as_agent: str,
        expected_version: int,
        items: list[dict] | None = None,
        remove: list[str] | None = None,
        replace: bool = False,
    ) -> dict:
        """Publish knowledge: a new version (optimistic concurrency on
        `expected_version`). Reports the bindings this obliges."""
        with self._lock:
            loaded = self._require()
            self._known_agent(loaded, as_agent)
            self._begin("context_update")
            try:
                before = self.contexts.snapshot(context_id)
                snap = self.contexts.update(context_id, as_agent=as_agent, expected_version=int(expected_version),
                                            items=self._items(items), remove=list(remove or []),
                                            replace=bool(replace))
            except ContextError as exc:
                self.emitter.emit("context.error", agent_id=as_agent, context_ids=(str(context_id),),
                                  payload={"error": str(exc)[:300]})
                raise _werr(exc) from exc
            changed = snap.version != before.version
            affected = []
            if changed:
                affected = [
                    {k: c[k] for k in ("handoff", "target_key", "binding", "agent", "state")}
                    for c in self._context_consumers(loaded)
                    if context_id in c["contexts"] and c["state"] != "fresh"
                ]
                self.emitter.emit("context.updated", agent_id=as_agent, context_ids=(context_id,), payload={
                    "version": snap.version, "previous_version": before.version, "digest": snap.digest,
                    "items": [i.id for i in snap.items], "affected_bindings": len(affected),
                    "context_content": [i.to_dict() for i in snap.items],
                })
                for a in affected:
                    self.emitter.emit("context.invalidated", agent_id=a["agent"], handoff_id=a["handoff"],
                                      target_key=a["target_key"], binding=a["binding"],
                                      context_ids=(context_id,), payload={"version": snap.version})
            self._after_op()
            return {**self._meta(snap), "changed": changed, "affected_bindings": affected}

    def context_list(self, as_agent: str | None = None) -> dict:
        """Metadata only (never content): what exists and who may read it."""
        with self._lock:
            loaded = self._require()
            declared = self._declared_contexts(loaded)
            out = []
            for cid in self.contexts.list():
                snap = self.contexts.snapshot(cid)
                pol = self.contexts.policy(cid)
                row = {"context_id": cid, "title": snap.title, "version": snap.version, "digest": snap.digest,
                       "owner": pol.owner, "visibility": pol.visibility, "item_count": len(snap.items),
                       "declared_in": "team.yaml" if cid in declared else "runtime"}
                row["readers"] = self.contexts.readers(cid, sorted(loaded.team.agents))
                if as_agent is not None:
                    row["can_read"] = self.contexts.authorize(cid, as_agent, "read")
                    row["can_write"] = self.contexts.authorize(cid, as_agent, "write")
                out.append(row)
            return {"contexts": out}

    def context_attach(self, context_id: str, agent: str, as_agent: str) -> dict:
        """Owner grants `agent` read access (a runtime grant)."""
        return self._grant(context_id, agent, as_agent, attach=True)

    def context_detach(self, context_id: str, agent: str, as_agent: str) -> dict:
        return self._grant(context_id, agent, as_agent, attach=False)

    def _grant(self, context_id: str, agent: str, as_agent: str, *, attach: bool) -> dict:
        with self._lock:
            loaded = self._require()
            self._known_agent(loaded, as_agent)
            self._known_agent(loaded, agent)
            self._begin("context_attach" if attach else "context_detach")
            try:
                (self.contexts.grant if attach else self.contexts.revoke)(context_id, agent, as_agent=as_agent)
            except ContextError as exc:
                raise _werr(exc) from exc
            self.emitter.emit("context.attached" if attach else "context.detached", agent_id=as_agent,
                              context_ids=(context_id,), payload={"reader": agent})
            self._after_op()
            return {"context_id": context_id, "agent": agent, "attached": attach,
                    "readers": self.contexts.readers(context_id, sorted(loaded.team.agents))}

    def context_search(self, query: str, as_agent: str, type: str | None = None, limit: int = 20) -> dict:
        """Case-insensitive search over items of contexts `as_agent` may read."""
        with self._lock:
            loaded = self._require()
            self._known_agent(loaded, as_agent)
            q = (query or "").lower()
            hits = []
            for cid in self.contexts.list():
                if not self.contexts.authorize(cid, as_agent, "read"):
                    continue
                snap = self.contexts.get(cid, as_agent=as_agent)
                matched = []
                for item in snap.items:
                    if type and item.type != type:
                        continue
                    blob = f"{item.id} {item.type} {item.content}".lower()
                    if q in blob:
                        matched.append(item)
                        hits.append({"context_id": cid, "version": snap.version, "item_id": item.id,
                                     "type": item.type, "author": item.author, "content": item.content})
                if matched:
                    self.emitter.emit("context.read", agent_id=as_agent, context_ids=(cid,), payload={
                        "version": snap.version, "digest": snap.digest, "items": [i.id for i in matched],
                        "purpose": "search"})
            return {"query": query, "hits": hits[: max(1, limit)], "total": len(hits)}

    def context_status(self) -> dict:
        with self._lock:
            loaded = self._require()
            consumers = self._context_consumers(loaded)
            declared = self._declared_contexts(loaded)
            out = []
            for cid in self.contexts.list():
                snap = self.contexts.snapshot(cid)
                mine = [c for c in consumers if cid in c["contexts"]]
                out.append({
                    "context_id": cid, "version": snap.version, "digest": snap.digest, "items": len(snap.items),
                    "owner": self.contexts.policy(cid).owner,
                    "readers": self.contexts.readers(cid, sorted(loaded.team.agents)),
                    "declared_in": "team.yaml" if cid in declared else "runtime",
                    "consumers": len(mine),
                    "stale_consumers": sum(1 for c in mine if c["state"] != "fresh"),
                })
            missing = sorted({x for c in consumers for x in c["contexts"]} - set(self.contexts.list()))
            return {"contexts": out, "missing_referenced": missing}

    def influence_query(self, context_id: str, item_id: str | None = None, transitive: bool = True) -> dict:
        """Which agent decisions (accepted @llm values) were derived from this
        context (or one of its items), and what was generated downstream from
        them -- the shared-context layer made traceable."""
        with self._lock:
            loaded = self._require()
            team = loaded.team
            item_meta = None
            if item_id and self.contexts.exists(context_id):
                try:
                    it = self.contexts.snapshot(context_id).item(item_id)
                    item_meta = {"id": it.id, "type": it.type, "author": it.author, "provenance": it.provenance}
                except ContextNotFound:
                    item_meta = None
            current = self.contexts.snapshot(context_id) if self.contexts.exists(context_id) else None
            registries = {v: _index_existing_targets(r) for v, r in team.roots.items()}
            direct = []
            for hname, tm in team.traces.items():
                for link in tm.links():
                    for bname, pins in link.context_pins.items():
                        for pin in pins:
                            if pin["context_id"] != context_id:
                                continue
                            if item_id and not self._pin_includes(pin, item_id):
                                continue
                            fresh = False
                            if current is not None:
                                try:
                                    fresh = items_digest(current.select(pin.get("item_ids") or ())) == \
                                        pin.get("content_digest")
                                except ContextNotFound:
                                    fresh = False
                            obj = registries[team.handoffs[hname].target_mm].get(link.target_key)
                            try:
                                key = element_key(obj) if obj is not None else None
                            except Exception:  # noqa: BLE001 - unkeyed element
                                key = None
                            direct.append({
                                "handoff": hname, "rule": link.rule, "target_key": link.target_key,
                                "element": f"{team.handoffs[hname].target_mm}:{key}" if key else None,
                                "binding": bname, "agent": self._owner_for_handoff(team, hname),
                                "pinned_version": pin["version"], "current": fresh,
                            })
            downstream = []
            if transitive:
                seen = set()
                for d in direct:
                    if d["element"] and d["element"] not in seen:
                        seen.add(d["element"])
                        bare = d["element"].split(":", 1)[1]
                        for hop in self.trace_query(bare)["downstream"]:
                            downstream.append({"via": d["element"], **hop})
            return {"context_id": context_id, "item_id": item_id, "item": item_meta, "direct": direct,
                    "downstream": downstream}

    def _pin_includes(self, pin: dict, item_id: str) -> bool:
        if pin.get("item_ids"):
            return item_id in pin["item_ids"]
        try:
            self.contexts.snapshot(pin["context_id"], pin["version"]).item(item_id)
            return True
        except ContextNotFound:
            return False

    def _validate_contexts(self, loaded: _Loaded) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        team = loaded.team
        declared = self._declared_contexts(loaded)
        for cid, raw in declared.items():
            raw = raw or {}
            for role in ("owner", "readers", "writers"):
                names = raw.get(role) or []
                for n in [names] if isinstance(names, str) else names:
                    if n not in team.agents:
                        warnings.append(f"contexts.{cid}.{role}: {n!r} is not (yet) an agent of this team")
        for hname, spec in team.handoffs.items():
            owner = team.owner_of(spec.target_mm)
            for rule in loaded.runtime.module_for(hname).rules:
                for tp in rule.to_clause.patterns:
                    for b in tp.bindings:
                        if not isinstance(b, StochasticBinding):
                            continue
                        for ref in b.context_refs:
                            where = f"{hname}: {rule.name}.{tp.var}.{b.name}"
                            if not self.contexts.exists(ref.context_id):
                                errors.append(f"{where} reads context {ref.context_id!r}, which is not declared "
                                              "in team.yaml contexts: and does not exist")
                                continue
                            if owner is None or not self.contexts.authorize(ref.context_id, owner.name, "read"):
                                who = owner.name if owner else "(no owner)"
                                errors.append(f"{where}: agent {who} is not a reader of context "
                                              f"{ref.context_id!r}")
        return errors, warnings

    # ------------------------------------------------------------------
    # identities and Nostr
    # ------------------------------------------------------------------

    @property
    def secrets(self):
        from .nostr.signer import SecretStore

        return SecretStore(self.dir / "secrets")

    _engine_signer_lock = threading.Lock()

    def _engine_signer(self):
        with self._engine_signer_lock:
            cached = getattr(self, "_engine_signer_cache", None)
            if cached is not None:
                return cached
            ref = self.config.nostr.engine_identity
            signer = self.secrets.get(ref) or self.secrets.create(ref)
            self._engine_signer_cache = signer
            return signer

    def _signer_for(self, agent: str | None):
        loaded = self._loaded
        if agent and loaded is not None:
            ident = loaded.team.identities.get(agent)
            if ident is not None and ident.identity_ref:
                signer = self.secrets.get(ident.identity_ref)
                if signer is not None and signer.pubkey == ident.nostr_pubkey:
                    return signer
        return self._engine_signer()

    def _pubkey_for(self, agent: str) -> str | None:
        loaded = self._loaded
        ident = loaded.team.identities.get(agent) if loaded is not None else None
        return ident.nostr_pubkey if ident is not None else None

    def _make_relay(self):
        from .nostr.relay import MultiRelay, WebSocketRelay

        return MultiRelay([WebSocketRelay(u, timeout=self.config.nostr.timeout) for u in self.config.nostr.relays])

    def _make_nostr_sink(self, clock: Any = None):
        from .nostr.outbox import Outbox
        from .nostr.publisher import NostrEventSink

        kw = {"clock": clock} if clock is not None else {}
        return NostrEventSink(
            self._make_relay(),
            Outbox(self.dir / "state" / "observability" / "outbox.jsonl", **kw),
            signer_for=self._signer_for,
            pubkey_for=self._pubkey_for,
            kind=self.config.nostr.kind,
            presence_kind=self.config.nostr.presence_kind,
            **kw,
        )

    def agent_identity(self, agent: str) -> dict:
        with self._lock:
            loaded = self._require()
            self._known_agent(loaded, agent)
            ident = loaded.team.identities[agent]
            has_key = False
            if ident.identity_ref:
                try:
                    signer = self.secrets.get(ident.identity_ref)
                    has_key = signer is not None and signer.pubkey == ident.nostr_pubkey
                except Exception:  # noqa: BLE001 - e.g. the nostr extra is missing
                    has_key = False
            return {**ident.to_dict(), "view": loaded.team.agents[agent].view, "has_local_signing_key": has_key}

    def nostr_keygen(self, ref: str) -> dict:
        """Create a signing key in .agentm2m/secrets/<ref>.key; returns the
        public key only."""
        from .nostr.keys import encode_npub

        try:
            signer = self.secrets.create(ref)
        except FileExistsError:
            raise _werr(ValueError(f"key {ref!r} already exists in {self.dir / 'secrets'}")) from None
        except ValueError as exc:
            raise _werr(exc) from exc
        return {"ref": ref, "pubkey": signer.pubkey, "npub": encode_npub(signer.pubkey),
                "path": str(self.dir / "secrets" / f"{ref}.key"),
                "next": f"add to team.yaml: agents.<Agent>.nostr: {{pubkey: {signer.pubkey}, identity_ref: {ref}}}"}

    def nostr_status(self) -> dict:
        cfg = self.config.nostr
        out: dict[str, Any] = {"enabled": bool(self._nostr), "relays": list(cfg.relays), "kind": cfg.kind,
                               "presence_kind": cfg.presence_kind, "outbox_depth": 0,
                               "secrets_dir": str(self.dir / "secrets"),
                               "privacy": self.config.observability.privacy.mode,
                               "publish": dict(cfg.publish), "emitter_errors": self.emitter.errors}
        if self._nostr is None:
            return out
        from .nostr.keys import encode_npub

        engine = self._engine_signer()
        out.update({
            "engine_pubkey": engine.pubkey, "engine_npub": encode_npub(engine.pubkey),
            "outbox_depth": self._nostr.outbox.depth(), "publish_failures": self._nostr.publish_failures,
            "last_error": self._nostr.last_error, "backing_off": self._nostr.breaker_open,
        })
        if self.exists():
            loaded = self._require()
            out["agents"] = {a: {"pubkey": i.nostr_pubkey, "identity_ref": i.identity_ref}
                             for a, i in loaded.team.identities.items()}
        return out

    def nostr_flush(self, force: bool = False) -> dict:
        if self._nostr is None:
            raise _werr(ValueError("Nostr is not enabled (set nostr.enabled and nostr.relays in .agentm2m/config.yaml)"))
        return self._nostr.flush(force=force)

    def _trusted_pubkeys(self) -> set[str]:
        keys = {self._engine_signer().pubkey}
        loaded = self._require()
        keys |= {i.nostr_pubkey for i in loaded.team.identities.values() if i.nostr_pubkey}
        return keys

    def _decode_verified(self, raw: dict, trusted: set[str]) -> tuple[dict | None, str | None]:
        """Verify a relay-supplied event and decode its AgentM2M envelope.
        Returns (event, None) or (None, rejection reason)."""
        import json

        from .nostr.errors import InvalidEvent
        from .nostr.verifier import verify_event
        from .observability.events import AgentM2MEvent, EventSchemaError

        try:
            ev = verify_event(raw)
        except InvalidEvent as exc:
            return None, f"invalid: {exc}"
        if ev.pubkey not in trusted:
            return None, "untrusted author"
        try:
            env = AgentM2MEvent.from_dict(json.loads(ev.content))
        except (ValueError, EventSchemaError) as exc:
            return None, f"schema: {exc}"
        if env.workspace_id != self.emitter.workspace_id:
            return None, "other workspace"
        return {**env.to_dict(), "nostr": {"id": ev.id, "pubkey": ev.pubkey, "created_at": ev.created_at,
                                           "kind": ev.kind}}, None

    def nostr_events(
        self,
        run_id: str | None = None,
        agent: str | None = None,
        handoff: str | None = None,
        event_type: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 500,
    ) -> dict:
        """Query the configured relays. Every event is verified (id,
        signature, trusted author, schema, workspace) before it is returned."""
        if self._nostr is None:
            raise _werr(ValueError("Nostr is not enabled"))
        from .nostr.errors import RelayError

        self._require()
        cfg = self.config.nostr
        f: dict[str, Any] = {"kinds": [cfg.kind, cfg.presence_kind],
                             "#t": [f"agentm2m-run-{run_id}" if run_id else "agentm2m"], "limit": max(1, limit)}
        if since is not None:
            f["since"] = int(since)
        if until is not None:
            f["until"] = int(until) + 1
        try:
            raw = self._nostr.relay.query([f])
        except RelayError as exc:
            raise _werr(exc) from exc
        trusted = self._trusted_pubkeys()
        events, rejected = [], 0
        reasons: dict[str, int] = {}
        for d in raw:
            env, why = self._decode_verified(d, trusted)
            if env is None:
                rejected += 1
                reasons[str(why).split(":")[0]] = reasons.get(str(why).split(":")[0], 0) + 1
                continue
            if agent and env["agent_id"] != agent or handoff and env["handoff_id"] != handoff:
                continue
            if event_type and env["event_type"] != event_type:
                continue
            events.append(env)
        events.sort(key=lambda e: (e["ts"], e["seq"]))
        return {"events": events, "verified": len(events), "rejected": rejected, "rejection_reasons": reasons}

    def nostr_subscribe(self, seconds: float = 5.0, limit: int = 100) -> dict:
        """Listen for this workspace's events for up to `seconds` (max 30);
        returns the verified ones."""
        if self._nostr is None:
            raise _werr(ValueError("Nostr is not enabled"))
        self._require()
        trusted = self._trusted_pubkeys()
        got: list[dict] = []
        rejected = [0]
        done = threading.Event()

        def on_event(d: dict) -> None:
            env, _why = self._decode_verified(d, trusted)
            if env is None:
                rejected[0] += 1
            elif len(got) < limit:
                got.append(env)
                if len(got) >= limit:
                    done.set()

        cfg = self.config.nostr
        sub = self._nostr.relay.subscribe([{"kinds": [cfg.kind, cfg.presence_kind], "#t": ["agentm2m"],
                                            "since": int(time.time())}], on_event)
        try:
            done.wait(min(max(seconds, 0.1), 30.0))
        finally:
            sub.close()
        return {"events": got, "verified": len(got), "rejected": rejected[0]}
