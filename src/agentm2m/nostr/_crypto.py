"""BIP-340 Schnorr over secp256k1, via the optional `coincurve` dependency."""
from __future__ import annotations

from typing import Any

from .errors import NostrUnavailable


def coincurve() -> Any:
    try:
        import coincurve as cc
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise NostrUnavailable(
            "Nostr signing needs the optional dependencies: pip install 'agentm2m[nostr]'"
        ) from exc
    return cc


def xonly_pubkey(seckey: bytes) -> bytes:
    return coincurve().PrivateKey(seckey).public_key_xonly.format()


def schnorr_sign(seckey: bytes, msg32: bytes, aux: bytes | None = None) -> bytes:
    return coincurve().PrivateKey(seckey).sign_schnorr(msg32, aux)


def schnorr_verify(pubkey: bytes, msg32: bytes, sig: bytes) -> bool:
    cc = coincurve()
    try:
        return bool(cc.PublicKeyXOnly(pubkey).verify(sig, msg32))
    except (ValueError, TypeError):
        return False
