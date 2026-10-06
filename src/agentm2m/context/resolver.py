"""Resolving a binding's declared context references for its owning agent.

The resolver is the only path from the context store into an LLM prompt:
it authorizes the *agent that owns the binding's target view* against each
context's policy, selects the declared items, and returns pins --

    {"context_id", "version", "digest", "item_ids", "content_digest"}

`content_digest` covers exactly the items the binding sees, so it is what
the version stamp depends on: a version bump that leaves those items
unchanged keeps the binding fresh, and any change to them makes it stale.
Anything that cannot be resolved (missing context, missing item, no read
right, expired) raises `ContextUnavailable`; callers block or escalate,
they never fall back to empty or invented context.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..rules.ast import ContextRefSpec
from .errors import ContextAccessDenied, ContextError, ContextNotFound
from .model import ContextItem, ContextSnapshot, items_digest


class ContextUnavailable(ContextError):
    """A declared context dependency cannot be satisfied right now."""


@dataclass(frozen=True)
class ResolvedContext:
    snapshot: ContextSnapshot
    items: tuple[ContextItem, ...]
    item_ids: tuple[str, ...]

    @property
    def content_digest(self) -> str:
        return items_digest(self.items)

    def pin(self) -> dict[str, Any]:
        return {
            "context_id": self.snapshot.context_id,
            "version": self.snapshot.version,
            "digest": self.snapshot.digest,
            "item_ids": list(self.item_ids),
            "content_digest": self.content_digest,
        }


class ContextResolver:
    def __init__(self, store: Any) -> None:
        self.store = store

    def resolve(self, refs: tuple[ContextRefSpec, ...], agent: str | None) -> list[ResolvedContext]:
        if not refs:
            return []
        if not agent:
            raise ContextUnavailable("the binding's target view has no owning agent to authorize context reads")
        out = []
        for ref in refs:
            try:
                snap, items = self.store.resolve(ref.context_id, as_agent=agent, item_ids=ref.item_ids)
            except ContextAccessDenied as exc:
                raise ContextUnavailable(str(exc)) from None  # "agent X may not read context Y (not a reader)"
            except ContextNotFound as exc:
                raise ContextUnavailable(f"context {ref.context_id!r} is unavailable: {exc}") from None
            out.append(ResolvedContext(snapshot=snap, items=tuple(items), item_ids=tuple(ref.item_ids)))
        return out


def public_pin(pin: dict) -> dict:
    """The pin as shown to hosts/agents (content digest is internal)."""
    return {k: pin[k] for k in ("context_id", "version", "digest", "item_ids")}


def render_context(resolved: list[ResolvedContext]) -> str:
    """Labelled, audit-friendly prompt section: every fact names its context,
    version, digest, item id, type and author."""
    lines = ["Authorized shared context (read-only collaboration knowledge; use it, do not invent more):"]
    for rc in resolved:
        s = rc.snapshot
        title = f" -- {s.title}" if s.title else ""
        lines.append(f"[context {s.context_id} v{s.version} sha256:{s.digest[:12]}]{title}")
        if not rc.items:
            lines.append("  (no items)")
        for item in rc.items:
            content = item.content if isinstance(item.content, str) else json.dumps(item.content, sort_keys=True)
            by = f", by {item.author}" if item.author else ""
            lines.append(f"- {item.id} ({item.type}{by}): {content}")
    return "\n".join(lines)
