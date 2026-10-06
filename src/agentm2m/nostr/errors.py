from __future__ import annotations


class NostrError(RuntimeError):
    """Base class for Nostr transport errors."""


class NostrUnavailable(NostrError):
    """The optional `agentm2m[nostr]` dependencies are not installed."""


class KeyFormatError(NostrError, ValueError):
    """A key or bech32 string is malformed."""


class IdentityError(NostrError, ValueError):
    """An agent identity declared in team.yaml is malformed or unsafe."""


class InvalidEvent(NostrError, ValueError):
    """An event failed structural, id or signature verification."""


class RelayError(NostrError):
    """A relay could not be reached, timed out, or refused an event."""
