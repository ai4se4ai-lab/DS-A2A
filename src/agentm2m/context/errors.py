from __future__ import annotations


class ContextError(RuntimeError):
    """Base class for shared-context errors."""


class ContextNotFound(ContextError, KeyError):
    def __str__(self) -> str:  # KeyError would quote the message
        return str(self.args[0]) if self.args else "context not found"


class ContextAccessDenied(ContextError, PermissionError):
    """The agent is not authorized to read or write this context."""


class ContextInvalid(ContextError, ValueError):
    """A context or context item is malformed."""


class ContextConflict(ContextError):
    """Optimistic concurrency failure: `expected_version` is not current."""

    def __init__(self, context_id: str, expected: int, current_version: int) -> None:
        super().__init__(
            f"CONTEXT_CONFLICT: {context_id} is at version {current_version}, not {expected}; "
            "fetch the current version, reconcile, and retry with expected_version="
            f"{current_version}"
        )
        self.context_id = context_id
        self.expected = expected
        self.current_version = current_version
