"""Agent identities: AgentM2M agent id (internal) <-> Nostr public key (external).

The agent id from team.yaml stays the engine's identifier; a Nostr public key
is optional metadata naming who signs that agent's events. Secrets never
live here: team.yaml may name a signing key only indirectly (`identity_ref`),
resolved by `agentm2m.nostr.signer.SecretStore` from the environment or
`.agentm2m/secrets/`.

    agents:
      Architect:
        display_name: Lead architect        # optional
        nostr:
          npub: npub1...                    # or pubkey: <64 hex>; both must agree
          identity_ref: architect           # optional: local signing key name
          relays: [wss://relay.example.com]
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .errors import IdentityError, KeyFormatError
from .keys import check_hex32, decode_npub, encode_npub

# Keys that would put signing material into team.yaml: refused outright.
_SECRET_KEYS = {"private_key", "privkey", "seckey", "secret", "secret_key", "nsec", "key"}
_ALLOWED_NOSTR = {"pubkey", "npub", "identity_ref", "relays"}
_REF = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _check_relay(url: Any) -> str:
    if not isinstance(url, str) or not url.startswith(("wss://", "ws://")):
        raise IdentityError(f"relay URL must start with wss:// or ws://, got {url!r}")
    return url


@dataclass(frozen=True)
class AgentIdentity:
    agent_id: str
    display_name: str
    nostr_pubkey: str | None = None
    nostr_npub: str | None = None
    relay_urls: tuple[str, ...] = ()
    identity_ref: str | None = None

    @property
    def nostr_enabled(self) -> bool:
        return self.nostr_pubkey is not None

    @classmethod
    def from_spec(cls, agent_id: str, raw: dict | None) -> AgentIdentity:
        raw = raw or {}
        if not isinstance(raw, dict):
            raise IdentityError(f"agents.{agent_id}: must be a mapping")
        nostr = raw.get("nostr") or {}
        if not isinstance(nostr, dict):
            raise IdentityError(f"agents.{agent_id}.nostr: must be a mapping")
        leaked = (_SECRET_KEYS & set(nostr)) | (_SECRET_KEYS & set(raw))
        if leaked:
            raise IdentityError(
                f"agents.{agent_id}: {min(leaked)!r} looks like signing material; never put private keys "
                "in team.yaml -- name the key with identity_ref and store it in .agentm2m/secrets/ or "
                "AGENTM2M_NOSTR_KEY_<REF>"
            )
        unknown = set(nostr) - _ALLOWED_NOSTR
        if unknown:
            raise IdentityError(f"agents.{agent_id}.nostr: unknown field(s) {sorted(unknown)}")
        pubkey = nostr.get("pubkey")
        npub = nostr.get("npub")
        try:
            if pubkey is not None:
                check_hex32(pubkey, "pubkey")
            if npub is not None:
                from_npub = decode_npub(npub)
                if pubkey is not None and pubkey != from_npub:
                    raise IdentityError(f"agents.{agent_id}.nostr: pubkey and npub name different keys")
                pubkey = from_npub
        except KeyFormatError as exc:
            raise IdentityError(f"agents.{agent_id}.nostr: {exc}") from exc
        ref = nostr.get("identity_ref")
        if ref is not None and (not isinstance(ref, str) or not _REF.match(ref)):
            raise IdentityError(f"agents.{agent_id}.nostr.identity_ref: use letters, digits, '_', '-', '.'")
        relays = nostr.get("relays") or []
        if not isinstance(relays, list):
            raise IdentityError(f"agents.{agent_id}.nostr.relays: must be a list")
        return cls(
            agent_id=agent_id,
            display_name=str(raw.get("display_name") or agent_id),
            nostr_pubkey=pubkey,
            nostr_npub=encode_npub(pubkey) if pubkey else None,
            relay_urls=tuple(_check_relay(u) for u in relays),
            identity_ref=ref,
        )

    def to_dict(self) -> dict:
        """Public metadata only (there is nothing secret to leave out)."""
        return {
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "nostr_pubkey": self.nostr_pubkey,
            "nostr_npub": self.nostr_npub,
            "relay_urls": list(self.relay_urls),
            "identity_ref": self.identity_ref,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AgentIdentity:
        return cls(
            agent_id=d["agent_id"],
            display_name=d.get("display_name") or d["agent_id"],
            nostr_pubkey=d.get("nostr_pubkey"),
            nostr_npub=d.get("nostr_npub"),
            relay_urls=tuple(d.get("relay_urls") or ()),
            identity_ref=d.get("identity_ref"),
        )


def load_identities(spec_agents: dict | None, known_agents: Iterable[str]) -> dict[str, AgentIdentity]:
    """One identity per known agent; agents absent from `spec_agents` get a
    Nostr-less identity. Naming an agent no view owns is an error."""
    spec_agents = spec_agents or {}
    if not isinstance(spec_agents, dict):
        raise IdentityError("'agents' must be a mapping of agent name -> settings")
    known = list(known_agents)
    unknown = sorted(set(spec_agents) - set(known))
    if unknown:
        raise IdentityError(f"agents: {unknown[0]!r} does not own any view (agents: {sorted(known)})")
    return {a: AgentIdentity.from_spec(a, spec_agents.get(a)) for a in known}
