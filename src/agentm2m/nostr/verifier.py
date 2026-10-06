"""Verification of untrusted events (relay results, subscriptions).

Nothing received from a relay is used before `verify_event` passes: shape,
recomputed id, and BIP-340 signature. Authorization (is this pubkey allowed
to say this?) is a separate, later step owned by the consumer.
"""
from __future__ import annotations

import re
from typing import Any

from . import _crypto
from .errors import InvalidEvent
from .events import NostrEvent, event_id

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX128 = re.compile(r"^[0-9a-f]{128}$")


def verify_schnorr(pubkey_hex: str, msg32: bytes, sig: bytes) -> bool:
    return _crypto.schnorr_verify(bytes.fromhex(pubkey_hex), msg32, sig)


def _structure(d: Any) -> NostrEvent:
    if isinstance(d, NostrEvent):
        d = d.to_dict()
    if not isinstance(d, dict):
        raise InvalidEvent("event must be a JSON object")
    try:
        ev = NostrEvent.from_dict(d)
    except (KeyError, TypeError) as exc:
        raise InvalidEvent(f"event is missing field {exc}") from None
    if not isinstance(ev.id, str) or not _HEX64.match(ev.id):
        raise InvalidEvent("bad id")
    if not isinstance(ev.pubkey, str) or not _HEX64.match(ev.pubkey):
        raise InvalidEvent("bad pubkey")
    if not isinstance(ev.sig, str) or not _HEX128.match(ev.sig):
        raise InvalidEvent("bad sig")
    if type(ev.created_at) is not int or ev.created_at < 0:
        raise InvalidEvent("bad created_at")
    if type(ev.kind) is not int or not 0 <= ev.kind <= 65535:
        raise InvalidEvent("bad kind")
    if not isinstance(ev.content, str):
        raise InvalidEvent("bad content")
    if not all(isinstance(t, list) and t and all(isinstance(x, str) for x in t) for t in ev.tags):
        raise InvalidEvent("bad tags")
    return ev


def verify_event(d: Any) -> NostrEvent:
    ev = _structure(d)
    if event_id(ev.pubkey, ev.unsigned()) != ev.id:
        raise InvalidEvent("id does not match content")
    if not verify_schnorr(ev.pubkey, bytes.fromhex(ev.id), bytes.fromhex(ev.sig)):
        raise InvalidEvent("signature does not verify")
    return ev
