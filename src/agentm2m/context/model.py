"""Shared-context data model.

    ContextItem       one typed fact: id, type, content, author, provenance, confidence, scope
    ContextSnapshot   an immutable version of a context: items + content digest
    ContextReference  (context_id, version, digest, item_ids): what a binding consumed
    ContextPolicy     owner / readers / writers / visibility / expiry

A context is *not* a transcript or memory dump: every item is addressable,
attributed and typed, so it can be pinned, traced and invalidated.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from typing import Any

from .errors import ContextInvalid

_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}$")
VISIBILITIES = ("private", "team", "relay")
PROVENANCE_KEYS = ("agent", "view", "element", "trace", "handoff", "nostr_event")


def check_id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise ContextInvalid(f"{what} must match {_ID.pattern} (got {value!r})")
    return value


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class ContextItem:
    id: str
    type: str
    content: Any  # str or JSON object
    author: str | None = None
    provenance: dict[str, str] = field(default_factory=dict)
    confidence: float | None = None
    scope: str | None = None

    def __post_init__(self) -> None:
        check_id(self.id, "context item id")
        if not isinstance(self.type, str) or not self.type.strip():
            raise ContextInvalid(f"context item {self.id}: 'type' is required")
        if not isinstance(self.content, (str, dict)):
            raise ContextInvalid(f"context item {self.id}: content must be a string or an object")
        if self.confidence is not None and not (
            isinstance(self.confidence, (int, float)) and 0.0 <= self.confidence <= 1.0
        ):
            raise ContextInvalid(f"context item {self.id}: confidence must be within [0, 1]")
        bad = set(self.provenance) - set(PROVENANCE_KEYS)
        if bad:
            raise ContextInvalid(f"context item {self.id}: unknown provenance field(s) {sorted(bad)}")

    def __hash__(self) -> int:
        return hash(canonical(self.to_dict()))

    def with_content(self, content: Any) -> ContextItem:
        return replace(self, content=content)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"id": self.id, "type": self.type, "content": self.content}
        if self.author is not None:
            d["author"] = self.author
        if self.provenance:
            d["provenance"] = dict(self.provenance)
        if self.confidence is not None:
            d["confidence"] = self.confidence
        if self.scope is not None:
            d["scope"] = self.scope
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ContextItem:
        if not isinstance(d, dict):
            raise ContextInvalid("context item must be an object")
        try:
            return cls(
                id=d["id"],
                type=d["type"],
                content=d["content"],
                author=d.get("author"),
                provenance=dict(d.get("provenance") or {}),
                confidence=d.get("confidence"),
                scope=d.get("scope"),
            )
        except KeyError as exc:
            raise ContextInvalid(f"context item is missing {exc}") from None


def items_digest(items: tuple[ContextItem, ...] | list[ContextItem]) -> str:
    """Content address of a set of items: order-independent, time-independent."""
    payload = [i.to_dict() for i in sorted(items, key=lambda i: i.id)]
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ContextSnapshot:
    context_id: str
    version: int
    items: tuple[ContextItem, ...]
    digest: str
    created_by: str | None = None
    ts: float = 0.0
    title: str = ""

    def item(self, item_id: str) -> ContextItem:
        for i in self.items:
            if i.id == item_id:
                return i
        from .errors import ContextNotFound

        raise ContextNotFound(f"context {self.context_id} v{self.version} has no item {item_id!r}")

    def select(self, item_ids: tuple[str, ...] | list[str] | None) -> tuple[ContextItem, ...]:
        if not item_ids:
            return self.items
        return tuple(self.item(i) for i in item_ids)

    def to_dict(self, *, with_content: bool = True) -> dict:
        d = {
            "context_id": self.context_id,
            "version": self.version,
            "digest": self.digest,
            "created_by": self.created_by,
            "ts": self.ts,
            "title": self.title,
        }
        if with_content:
            d["items"] = [i.to_dict() for i in self.items]
        else:
            d["item_ids"] = [i.id for i in self.items]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ContextSnapshot:
        items = tuple(ContextItem.from_dict(i) for i in d.get("items") or [])
        return cls(
            context_id=d["context_id"],
            version=int(d["version"]),
            items=items,
            digest=d.get("digest") or items_digest(items),
            created_by=d.get("created_by"),
            ts=float(d.get("ts") or 0.0),
            title=d.get("title") or "",
        )


@dataclass(frozen=True)
class ContextReference:
    context_id: str
    version: int
    digest: str
    item_ids: tuple[str, ...] = ()

    @classmethod
    def of(cls, snap: ContextSnapshot, item_ids: tuple[str, ...] | list[str] = ()) -> ContextReference:
        return cls(snap.context_id, snap.version, snap.digest, tuple(item_ids))

    def to_dict(self) -> dict:
        return {"context_id": self.context_id, "version": self.version, "digest": self.digest,
                "item_ids": list(self.item_ids)}

    @classmethod
    def from_dict(cls, d: dict) -> ContextReference:
        return cls(d["context_id"], int(d["version"]), d["digest"], tuple(d.get("item_ids") or ()))


@dataclass(frozen=True)
class ContextPolicy:
    owner: str
    readers: frozenset[str] = frozenset()
    writers: frozenset[str] = frozenset()
    visibility: str = "private"  # private: listed readers | team: every agent may read | relay: + published content
    expiry: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.owner, str) or not self.owner:
            raise ContextInvalid("context policy needs an owner agent")
        if self.visibility not in VISIBILITIES:
            raise ContextInvalid(f"visibility must be one of {VISIBILITIES}, got {self.visibility!r}")

    def expired(self, now: float) -> bool:
        return self.expiry is not None and now >= self.expiry

    def can_write(self, agent: str, now: float) -> bool:
        return not self.expired(now) and (agent == self.owner or agent in self.writers)

    def can_read(self, agent: str, now: float, grants: frozenset[str] = frozenset()) -> bool:
        if self.expired(now):
            return False
        return (
            agent == self.owner
            or agent in self.writers
            or agent in self.readers
            or agent in grants
            or self.visibility in ("team", "relay")
        )

    def to_dict(self) -> dict:
        return {"owner": self.owner, "readers": sorted(self.readers), "writers": sorted(self.writers),
                "visibility": self.visibility, "expiry": self.expiry}

    @classmethod
    def from_dict(cls, d: dict) -> ContextPolicy:
        if not isinstance(d, dict):
            raise ContextInvalid("context policy must be a mapping")

        def names(key: str) -> frozenset[str]:
            v = d.get(key) or []
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ContextInvalid(f"context policy '{key}' must be a list of agent names")
            return frozenset(v)

        expiry = d.get("expiry")
        return cls(owner=d.get("owner") or "", readers=names("readers"), writers=names("writers"),
                   visibility=d.get("visibility") or "private",
                   expiry=float(expiry) if expiry is not None else None)
