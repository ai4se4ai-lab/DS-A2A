"""NIP-01 events: the signed envelope every relay understands.

    id  = sha256(utf8(json([0, pubkey, created_at, kind, tags, content])))
    sig = BIP-340 Schnorr signature of id by pubkey

Serialization follows NIP-01 exactly: no whitespace, UTF-8 (non-ASCII left
literal), and only JSON's mandatory escapes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class UnsignedEvent:
    created_at: int
    kind: int
    tags: list[list[str]] = field(default_factory=list)
    content: str = ""


def serialize_for_id(pubkey: str, ev: UnsignedEvent) -> str:
    return json.dumps(
        [0, pubkey, ev.created_at, ev.kind, ev.tags, ev.content],
        separators=(",", ":"),
        ensure_ascii=False,
    )


def event_id(pubkey: str, ev: UnsignedEvent) -> str:
    return hashlib.sha256(serialize_for_id(pubkey, ev).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NostrEvent:
    id: str
    pubkey: str
    created_at: int
    kind: int
    tags: list[list[str]]
    content: str
    sig: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pubkey": self.pubkey,
            "created_at": self.created_at,
            "kind": self.kind,
            "tags": [list(t) for t in self.tags],
            "content": self.content,
            "sig": self.sig,
        }

    @classmethod
    def from_dict(cls, d: dict) -> NostrEvent:
        """Structural decode only; use `verifier.verify_event` for trust."""
        return cls(
            id=d["id"],
            pubkey=d["pubkey"],
            created_at=d["created_at"],
            kind=d["kind"],
            tags=[list(t) for t in d["tags"]],
            content=d["content"],
            sig=d["sig"],
        )

    def unsigned(self) -> UnsignedEvent:
        return UnsignedEvent(created_at=self.created_at, kind=self.kind, tags=self.tags, content=self.content)

    def tag_values(self, name: str) -> list[str]:
        return [t[1] for t in self.tags if len(t) > 1 and t[0] == name]

    def first_tag(self, name: str) -> str | None:
        vals = self.tag_values(name)
        return vals[0] if vals else None
