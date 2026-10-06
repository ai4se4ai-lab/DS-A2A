"""Collaboration plane: shared context.

A shared context is a typed, versioned, content-addressed set of
collaboration facts (findings, decisions, constraints) that an agent
publishes and *explicitly authorized* agents may consume. It is a different
mechanism from a hand-off: a transformation propagates structured
artifacts between views; a context propagates knowledge. Bindings opt in
with `@llm(prompt, footprint, context=['id'])`, and the pinned context
digest becomes part of the binding's version stamp, so a context change
creates ordinary AgentM2M obligations -- there is no second invalidation
system.
"""
from .errors import (
    ContextAccessDenied,
    ContextConflict,
    ContextError,
    ContextInvalid,
    ContextNotFound,
)
from .model import ContextItem, ContextPolicy, ContextReference, ContextSnapshot
from .store import ContextStore, LocalContextStore, MemoryContextStore

__all__ = [
    "ContextAccessDenied", "ContextConflict", "ContextError", "ContextInvalid", "ContextItem", "ContextNotFound",
    "ContextPolicy", "ContextReference", "ContextSnapshot", "ContextStore", "LocalContextStore",
    "MemoryContextStore",
]
