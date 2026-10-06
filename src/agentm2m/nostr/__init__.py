"""Optional Nostr transport for AgentM2M observability and context sync.

Nostr is a *projection* of AgentM2M state, never its source of truth: the
engine decides structure, acceptance (phi) and write rights (omega) locally,
and a relay that is down, slow or hostile can delay observation but never
change what a transformation means (see docs/NOSTR.md).

Importing this package needs no extra dependency; signing and relay I/O
import `coincurve` / `websockets` lazily (`pip install agentm2m[nostr]`).
"""
from .errors import IdentityError, KeyFormatError, NostrError, NostrUnavailable
from .identity import AgentIdentity, load_identities

__all__ = ["AgentIdentity", "IdentityError", "KeyFormatError", "NostrError", "NostrUnavailable", "load_identities"]
