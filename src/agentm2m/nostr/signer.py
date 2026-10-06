"""Signing identities, kept out of team.yaml and state.json.

`Signer` is a protocol so a remote signer (NIP-46 bunker, NIP-55 app) can be
dropped in later without AgentM2M ever holding the private key. The built-in
`KeySigner` holds a key loaded by `SecretStore` from, in order:

  1. environment variable AGENTM2M_NOSTR_KEY_<REF>  (hex or nsec; REF upper-cased,
     non-alphanumerics -> '_')
  2. file .agentm2m/secrets/<ref>.key  (mode 0600, directory 0700)
"""
from __future__ import annotations

import os
import re
import secrets as _secrets
from pathlib import Path
from typing import Protocol, runtime_checkable

from . import _crypto
from .errors import KeyFormatError
from .events import NostrEvent, UnsignedEvent, event_id
from .keys import check_hex32, decode_nsec

_REF = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")


@runtime_checkable
class Signer(Protocol):
    @property
    def pubkey(self) -> str: ...

    def sign(self, ev: UnsignedEvent) -> NostrEvent: ...


def _parse_secret(raw: str) -> bytes:
    raw = raw.strip()
    hexkey = decode_nsec(raw) if raw.startswith("nsec1") else raw.lower()
    return bytes.fromhex(check_hex32(hexkey, "secret key"))


class KeySigner:
    """A local secp256k1 key. Never printed, serialized or exposed."""

    __slots__ = ("__key", "_pubkey")

    def __init__(self, seckey: str | bytes) -> None:
        key = seckey if isinstance(seckey, bytes) else _parse_secret(seckey)
        if len(key) != 32:
            raise KeyFormatError("secret key must be 32 bytes")
        self.__key = key
        self._pubkey = _crypto.xonly_pubkey(key).hex()

    @classmethod
    def generate(cls) -> KeySigner:
        while True:
            try:
                return cls(_secrets.token_bytes(32))
            except Exception:  # noqa: BLE001,S112 - astronomically rare out-of-range key: draw again
                continue

    @property
    def pubkey(self) -> str:
        return self._pubkey

    def sign_digest(self, digest32: bytes, aux: bytes | None = None) -> bytes:
        return _crypto.schnorr_sign(self.__key, digest32, aux)

    def sign(self, ev: UnsignedEvent) -> NostrEvent:
        eid = event_id(self._pubkey, ev)
        sig = self.sign_digest(bytes.fromhex(eid)).hex()
        return NostrEvent(
            id=eid,
            pubkey=self._pubkey,
            created_at=ev.created_at,
            kind=ev.kind,
            tags=[list(t) for t in ev.tags],
            content=ev.content,
            sig=sig,
        )

    def _export_hex(self) -> str:
        """Only for SecretStore.create (writing the key file)."""
        return self.__key.hex()

    def __repr__(self) -> str:
        return f"KeySigner(pubkey={self._pubkey[:12]}...)"

    __str__ = __repr__


def _env_name(ref: str) -> str:
    return "AGENTM2M_NOSTR_KEY_" + re.sub(r"[^A-Za-z0-9]", "_", ref).upper()


class SecretStore:
    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)

    @staticmethod
    def _check(ref: str) -> str:
        if not isinstance(ref, str) or not _REF.match(ref):
            raise ValueError(f"invalid identity ref {ref!r} (letters, digits, '_', '-', '.'; no leading '.')")
        return ref

    def _path(self, ref: str) -> Path:
        return self.dir / f"{self._check(ref)}.key"

    def get(self, ref: str) -> KeySigner | None:
        self._check(ref)
        env = os.getenv(_env_name(ref))
        if env:
            return KeySigner(env)
        p = self._path(ref)
        if p.is_file():
            return KeySigner(p.read_text())
        return None

    def create(self, ref: str) -> KeySigner:
        p = self._path(ref)
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        signer = KeySigner.generate()
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(signer._export_hex() + "\n")
        return signer

    def refs(self) -> list[str]:
        if not self.dir.is_dir():
            return []
        return sorted(p.stem for p in self.dir.glob("*.key"))
