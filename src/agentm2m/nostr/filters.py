"""NIP-01 subscription filters, evaluated locally.

Relays index single-letter tags only (`#t`, `#p`, `#e`); AgentM2M's
multi-letter tags (`run`, `handoff`, ...) are filtered client-side after
verification -- see `agentm2m.nostr.subscriber`.
"""
from __future__ import annotations

from typing import Any

from .events import NostrEvent


def matches(f: dict[str, Any], ev: NostrEvent) -> bool:
    if "ids" in f and ev.id not in f["ids"]:
        return False
    if "authors" in f and ev.pubkey not in f["authors"]:
        return False
    if "kinds" in f and ev.kind not in f["kinds"]:
        return False
    if "since" in f and ev.created_at < f["since"]:
        return False
    if "until" in f and ev.created_at > f["until"]:
        return False
    for key, wanted in f.items():
        if key.startswith("#") and len(key) == 2 and not set(ev.tag_values(key[1])) & set(wanted):
            return False
    return True


def matches_any(filters: list[dict[str, Any]], ev: NostrEvent) -> bool:
    return any(matches(f, ev) for f in filters) if filters else True
