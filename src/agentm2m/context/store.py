"""Context stores.

    MemoryContextStore  in-process (tests, scratch copies)
    LocalContextStore   `.agentm2m/state/context.json`, file-locked, reloads
                        when another process wrote it (cross-process safe)

Authorization is enforced *here*, for every caller (MCP, CLI, engine, Nostr
sync): reads need read rights, writes need write rights, and an expired or
missing context fails closed. Every version stays addressable; updates use
optimistic concurrency (`expected_version`), never last-writer-wins.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .errors import (
    ContextAccessDenied,
    ContextConflict,
    ContextError,
    ContextInvalid,
    ContextNotFound,
)
from .model import ContextItem, ContextPolicy, ContextSnapshot, check_id, items_digest

STORE_SCHEMA = "agentm2m.context"
STORE_SCHEMA_VERSION = 1


class ContextStore(Protocol):
    def create(self, context_id: str, *, policy: ContextPolicy, as_agent: str, title: str = "",
               items: list[ContextItem] | None = None) -> ContextSnapshot: ...

    def get(self, context_id: str, version: int | None = None, *, as_agent: str) -> ContextSnapshot: ...

    def update(self, context_id: str, *, as_agent: str, expected_version: int,
               items: list[ContextItem] | None = None, remove: list[str] | None = None,
               replace: bool = False) -> ContextSnapshot: ...

    def snapshot(self, context_id: str, version: int | None = None) -> ContextSnapshot: ...

    def resolve(self, context_id: str, *, as_agent: str,
                item_ids: tuple[str, ...] = ()) -> tuple[ContextSnapshot, tuple[ContextItem, ...]]: ...

    def authorize(self, context_id: str, agent: str, mode: str = "read") -> bool: ...


@dataclass
class _Entry:
    policy: ContextPolicy
    versions: list[ContextSnapshot] = field(default_factory=list)
    grants: set[str] = field(default_factory=set)

    @property
    def current(self) -> ContextSnapshot:
        return self.versions[-1]


class MemoryContextStore:
    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.RLock()

    # -- persistence hooks (no-ops in memory) ----------------------------

    @contextlib.contextmanager
    def _txn(self, write: bool) -> Iterator[None]:
        with self._lock:
            yield

    # -- helpers ----------------------------------------------------------

    def _entry(self, context_id: str) -> _Entry:
        e = self._entries.get(context_id)
        if e is None:
            raise ContextNotFound(f"no shared context {context_id!r}")
        return e

    def _require(self, e: _Entry, cid: str, agent: str, mode: str) -> None:
        now = self.clock()
        ok = e.policy.can_write(agent, now) if mode == "write" else e.policy.can_read(agent, now,
                                                                                      frozenset(e.grants))
        if not ok:
            why = "the context has expired" if e.policy.expired(now) else f"not a {'writer' if mode == 'write' else 'reader'}"
            raise ContextAccessDenied(f"agent {agent!r} may not {mode} context {cid!r} ({why})")

    @staticmethod
    def _stamp_items(items: list[ContextItem], agent: str) -> list[ContextItem]:
        """Attribute every written item to the *authenticated* writer: a
        caller cannot claim another agent's authorship or provenance."""
        from dataclasses import replace

        out = []
        for i in items:
            if not isinstance(i, ContextItem):
                i = ContextItem.from_dict(i)
            claimed = i.provenance.get("agent")
            if (i.author is not None and i.author != agent) or (claimed is not None and claimed != agent):
                raise ContextAccessDenied(
                    f"item {i.id}: agent {agent!r} cannot write an item attributed to another agent"
                )
            i = replace(i, author=agent, provenance={**i.provenance, "agent": agent})
            out.append(i)
        ids = [i.id for i in out]
        if len(ids) != len(set(ids)):
            raise ContextInvalid("duplicate context item ids in one write")
        return out

    def _new_version(self, cid: str, version: int, items: list[ContextItem], agent: str,
                     title: str) -> ContextSnapshot:
        items_t = tuple(sorted(items, key=lambda i: i.id))
        return ContextSnapshot(context_id=cid, version=version, items=items_t, digest=items_digest(items_t),
                               created_by=agent, ts=self.clock(), title=title)

    # -- operations -------------------------------------------------------

    def create(self, context_id: str, *, policy: ContextPolicy, as_agent: str, title: str = "",
               items: list[ContextItem] | None = None) -> ContextSnapshot:
        check_id(context_id, "context id")
        if as_agent != policy.owner:
            raise ContextAccessDenied(f"only the owner ({policy.owner}) may create context {context_id!r}")
        with self._txn(write=True):
            if context_id in self._entries:
                raise ContextError(f"shared context {context_id!r} already exists")
            snap = self._new_version(context_id, 1, self._stamp_items(list(items or []), as_agent), as_agent, title)
            self._entries[context_id] = _Entry(policy=policy, versions=[snap])
            return snap

    def get(self, context_id: str, version: int | None = None, *, as_agent: str) -> ContextSnapshot:
        with self._txn(write=False):
            e = self._entry(context_id)
            self._require(e, context_id, as_agent, "read")
            return self._version(e, context_id, version)

    @staticmethod
    def _version(e: _Entry, cid: str, version: int | None) -> ContextSnapshot:
        if version is None:
            return e.current
        for s in e.versions:
            if s.version == version:
                return s
        raise ContextNotFound(f"context {cid!r} has no version {version}")

    # Engine-internal operations (no acting agent): snapshot, apply_snapshot,
    # ensure, set_policy. They are never exposed through MCP/CLI directly;
    # callers outside the engine must use the authorized methods above.

    def snapshot(self, context_id: str, version: int | None = None) -> ContextSnapshot:
        """Engine-internal read (no agent). Not an authorization path."""
        with self._txn(write=False):
            return self._version(self._entry(context_id), context_id, version)

    def update(self, context_id: str, *, as_agent: str, expected_version: int,
               items: list[ContextItem] | None = None, remove: list[str] | None = None,
               replace: bool = False) -> ContextSnapshot:
        with self._txn(write=True):
            e = self._entry(context_id)
            self._require(e, context_id, as_agent, "write")
            cur = e.current
            if expected_version != cur.version:
                raise ContextConflict(context_id, expected_version, cur.version)
            new_items = self._stamp_items(list(items or []), as_agent)
            merged: dict[str, ContextItem] = {} if replace else {i.id: i for i in cur.items}
            for rid in remove or []:
                if rid not in merged:
                    raise ContextNotFound(f"context {context_id!r} has no item {rid!r} to remove")
                merged.pop(rid)
            for i in new_items:
                merged[i.id] = i
            if items_digest(list(merged.values())) == cur.digest:
                return cur  # identical content: no new version
            snap = self._new_version(context_id, cur.version + 1, list(merged.values()), as_agent, cur.title)
            e.versions.append(snap)
            return snap

    def apply_snapshot(self, snap: ContextSnapshot) -> ContextSnapshot:
        """Install a version received from elsewhere (Nostr sync). The caller
        has already verified the signature and the sender's write rights;
        this only enforces version continuity and content integrity."""
        if items_digest(snap.items) != snap.digest:
            raise ContextInvalid(f"context {snap.context_id} v{snap.version}: digest does not match content")
        with self._txn(write=True):
            e = self._entry(snap.context_id)
            cur = e.current
            if snap.version == cur.version and snap.digest == cur.digest:
                return cur
            if snap.version != cur.version + 1:
                raise ContextConflict(snap.context_id, snap.version - 1, cur.version)
            e.versions.append(snap)
            return snap

    def resolve(self, context_id: str, *, as_agent: str,
                item_ids: tuple[str, ...] = ()) -> tuple[ContextSnapshot, tuple[ContextItem, ...]]:
        snap = self.get(context_id, as_agent=as_agent)
        return snap, snap.select(item_ids)

    def authorize(self, context_id: str, agent: str, mode: str = "read") -> bool:
        with self._txn(write=False):
            e = self._entries.get(context_id)
            if e is None:
                return False
            try:
                self._require(e, context_id, agent, mode)
            except ContextAccessDenied:
                return False
            return True

    def policy(self, context_id: str) -> ContextPolicy:
        with self._txn(write=False):
            return self._entry(context_id).policy

    def grants(self, context_id: str) -> set[str]:
        with self._txn(write=False):
            return set(self._entry(context_id).grants)

    def readers(self, context_id: str, agents: list[str]) -> list[str]:
        return [a for a in agents if self.authorize(context_id, a, "read")]

    def set_policy(self, context_id: str, policy: ContextPolicy) -> None:
        """Replace the base policy (team.yaml is authoritative for declared contexts)."""
        with self._txn(write=True):
            self._entry(context_id).policy = policy

    def ensure(self, context_id: str, policy: ContextPolicy, title: str = "") -> ContextSnapshot:
        """Create an empty v1 for a declared context if missing; sync its policy."""
        with self._txn(write=True):
            e = self._entries.get(context_id)
            if e is None:
                snap = self._new_version(context_id, 1, [], policy.owner, title)
                self._entries[context_id] = _Entry(policy=policy, versions=[snap])
                return snap
            if e.policy != policy:
                e.policy = policy
            return e.current

    def _require_owner(self, e: _Entry, cid: str, agent: str, what: str) -> None:
        if agent != e.policy.owner or e.policy.expired(self.clock()):
            raise ContextAccessDenied(f"only the owner ({e.policy.owner}) may {what} context {cid!r}")

    def grant(self, context_id: str, agent: str, *, as_agent: str) -> None:
        """Add a runtime reader. Owner only: a writer may add knowledge but
        must not widen who can read it."""
        with self._txn(write=True):
            e = self._entry(context_id)
            self._require_owner(e, context_id, as_agent, "grant readers on")
            e.grants.add(agent)

    def revoke(self, context_id: str, agent: str, *, as_agent: str) -> None:
        with self._txn(write=True):
            e = self._entry(context_id)
            self._require_owner(e, context_id, as_agent, "revoke readers on")
            if agent not in e.grants:
                raise ContextError(f"{agent} has no runtime grant on {context_id!r} (team.yaml readers are edited there)")
            e.grants.discard(agent)

    def delete(self, context_id: str, *, as_agent: str) -> None:
        with self._txn(write=True):
            e = self._entry(context_id)
            if as_agent != e.policy.owner:
                raise ContextAccessDenied(f"only the owner ({e.policy.owner}) may delete context {context_id!r}")
            del self._entries[context_id]

    def list(self) -> list[str]:
        with self._txn(write=False):
            return sorted(self._entries)

    def exists(self, context_id: str) -> bool:
        with self._txn(write=False):
            return context_id in self._entries

    # -- (de)serialization --------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "schema": STORE_SCHEMA,
            "schema_version": STORE_SCHEMA_VERSION,
            "contexts": {
                cid: {"policy": e.policy.to_dict(), "grants": sorted(e.grants),
                      "versions": [s.to_dict() for s in e.versions]}
                for cid, e in sorted(self._entries.items())
            },
        }

    def _load_dict(self, d: dict) -> None:
        if d.get("schema") != STORE_SCHEMA or d.get("schema_version") != STORE_SCHEMA_VERSION:
            raise ContextInvalid(f"unsupported context store format {d.get('schema')!r}/{d.get('schema_version')!r}")
        entries: dict[str, _Entry] = {}
        for cid, raw in (d.get("contexts") or {}).items():
            entries[cid] = _Entry(policy=ContextPolicy.from_dict(raw["policy"]),
                                  versions=[ContextSnapshot.from_dict(s) for s in raw["versions"]],
                                  grants=set(raw.get("grants") or []))
        self._entries = entries


class LocalContextStore(MemoryContextStore):
    """JSON-file store; every transaction reloads if the file changed and
    holds an exclusive file lock for writes (cross-process safe)."""

    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time) -> None:
        super().__init__(clock=clock)
        self.path = Path(path)
        self._fingerprint: Any = None

    def _fp(self) -> Any:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def _reload_if_changed(self) -> None:
        fp = self._fp()
        if fp != self._fingerprint:
            if fp is None:
                self._entries = {}
            else:
                self._load_dict(json.loads(self.path.read_text(encoding="utf-8")))
            self._fingerprint = fp

    @contextlib.contextmanager
    def _txn(self, write: bool) -> Iterator[None]:
        with self._lock:
            if not write:
                self._reload_if_changed()
                yield
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path.with_suffix(".lock"), "a+") as fh:
                try:
                    import fcntl

                    fcntl.flock(fh, fcntl.LOCK_EX)
                except (ImportError, OSError):  # pragma: no cover - non-POSIX
                    pass
                try:
                    self._reload_if_changed()
                    before = json.dumps(self.to_dict(), sort_keys=True)
                    yield
                    after = self.to_dict()
                    if json.dumps(after, sort_keys=True) != before:
                        tmp = self.path.with_suffix(".json.tmp")
                        tmp.write_text(json.dumps(after, indent=1, sort_keys=True, ensure_ascii=False),
                                       encoding="utf-8")
                        os.replace(tmp, self.path)
                        self._fingerprint = self._fp()
                except BaseException:
                    self._fingerprint = None  # discard partial in-memory changes on next access
                    raise
                finally:
                    try:
                        import fcntl

                        fcntl.flock(fh, fcntl.LOCK_UN)
                    except (ImportError, OSError):  # pragma: no cover
                        pass
