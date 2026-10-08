"""Phase 17: shared context over Nostr, with every inbound event verified
and authorized against the *local* policy before it touches the store."""
from __future__ import annotations

import json

import pytest

from agentm2m.context.model import ContextItem, ContextPolicy
from agentm2m.context.nostr_sync import NOSTR_KIND_CONTEXT, NostrContextSync
from agentm2m.context.store import MemoryContextStore
from agentm2m.nostr.events import UnsignedEvent
from agentm2m.nostr.relay import MemoryRelay
from agentm2m.nostr.signer import KeySigner

KEYS = {"SecurityReviewer": KeySigner.generate(), "Developer": KeySigner.generate()}
STRANGER = KeySigner.generate()
POLICY = ContextPolicy(owner="SecurityReviewer", writers=frozenset({"SecurityReviewer"}),
                       readers=frozenset({"Developer"}), visibility="relay")
F1 = ContextItem(id="finding-001", type="security-finding", content="auth required")
F2 = ContextItem(id="finding-002", type="security-finding", content="rate-limit login")


def _node(relay: MemoryRelay, namespace: str = "devteam") -> tuple[MemoryContextStore, NostrContextSync]:
    store = MemoryContextStore()
    store.ensure("security-review", POLICY)
    sync = NostrContextSync(store, relay, namespace=namespace,
                            pubkey_of=lambda a: KEYS[a].pubkey if a in KEYS else None,
                            signer_for=lambda a: KEYS.get(a))
    return store, sync


def _publish(store, sync, *items, as_agent="SecurityReviewer"):
    before = store.snapshot("security-review")
    snap = store.update("security-review", as_agent=as_agent, expected_version=before.version, items=list(items))
    return sync.publish(snap, previous_digest=before.digest, as_agent=as_agent)


def test_context_update_reaches_other_node():
    relay = MemoryRelay()
    a_store, a = _node(relay)
    b_store, b = _node(relay)
    res = _publish(a_store, a, F1)
    assert res["published"] is True
    report = b.pull()
    assert report["applied"] == 1 and report["rejected"] == []
    assert b_store.snapshot("security-review").digest == a_store.snapshot("security-review").digest
    assert b.pull()["applied"] == 0  # idempotent: duplicates are no-ops


def test_versions_applied_in_order_and_live_subscription():
    relay = MemoryRelay()
    a_store, a = _node(relay)
    b_store, b = _node(relay)
    _publish(a_store, a, F1)
    _publish(a_store, a, F2)
    assert b.pull()["applied"] == 2 and b_store.snapshot("security-review").version == 3
    c_store, c = _node(relay)
    sub = c.subscribe()
    assert c_store.snapshot("security-review").version == 3  # history replayed in version order
    _publish(a_store, a, F1.with_content("auth required on every route"))
    assert c_store.snapshot("security-review").version == 4
    sub.close()


def test_private_contexts_are_never_published():
    relay = MemoryRelay()
    store = MemoryContextStore()
    store.ensure("private-notes", ContextPolicy(owner="SecurityReviewer"))
    sync = NostrContextSync(store, relay, namespace="devteam", pubkey_of=lambda a: KEYS[a].pubkey,
                            signer_for=lambda a: KEYS.get(a))
    snap = store.update("private-notes", as_agent="SecurityReviewer", expected_version=1, items=[F1])
    res = sync.publish(snap, previous_digest="x", as_agent="SecurityReviewer")
    assert res["published"] is False and "visibility" in res["reason"]
    assert relay.events == {}


def test_writer_without_local_key_cannot_publish():
    relay = MemoryRelay()
    store, _ = _node(relay)
    sync = NostrContextSync(store, relay, namespace="devteam", pubkey_of=lambda a: KEYS[a].pubkey,
                            signer_for=lambda a: None)
    snap = store.update("security-review", as_agent="SecurityReviewer", expected_version=1, items=[F1])
    res = sync.publish(snap, previous_digest="x", as_agent="SecurityReviewer")
    assert res["published"] is False and "signing key" in res["reason"]


# -- malicious and broken inbound events -------------------------------------------


def _forge(store_snapshot_v1, *, signer, as_agent="SecurityReviewer", namespace="devteam", version=2,
           items=(F1,), previous=None, digest=None, context_id="security-review"):
    from agentm2m.context.model import items_digest

    its = [i.to_dict() | {"author": as_agent} for i in items]
    real = items_digest([ContextItem.from_dict(i) for i in its])
    content = {
        "schema": "agentm2m.context-snapshot", "schema_version": 1, "namespace": namespace, "as_agent": as_agent,
        "previous_digest": previous or store_snapshot_v1.digest,
        "snapshot": {"context_id": context_id, "version": version, "digest": digest or real, "items": its,
                     "created_by": as_agent, "ts": 0, "title": ""},
    }
    return signer.sign(UnsignedEvent(created_at=1000 + version, kind=NOSTR_KIND_CONTEXT,
                                     tags=[["t", "agentm2m"], ["t", "agentm2m-ctx-devteam"],  # tag spoofed: content decides
                                           ["context", context_id]], content=json.dumps(content))).to_dict()


@pytest.mark.parametrize("case,reason", [
    ("wrong_signature", "invalid"),
    ("unknown_agent", "unknown"),
    ("wrong_pubkey", "does not belong"),
    ("wrong_workspace", "namespace"),
    ("unauthorized_writer", "not a writer"),
    ("tampered_content", "digest"),
    ("unknown_context", "unknown context"),
    ("forged_item_author", "attributed"),
])
def test_malicious_events_rejected(case, reason):
    relay = MemoryRelay()
    b_store, b = _node(relay)
    v1 = b_store.snapshot("security-review")
    sr = KEYS["SecurityReviewer"]
    if case == "wrong_signature":
        ev = _forge(v1, signer=sr)
        ev["sig"] = ev["sig"][:-2] + ("00" if ev["sig"][-2:] != "00" else "11")
    elif case == "unknown_agent":
        ev = _forge(v1, signer=STRANGER, as_agent="Intruder")
    elif case == "wrong_pubkey":
        ev = _forge(v1, signer=KEYS["Developer"], as_agent="SecurityReviewer")
    elif case == "wrong_workspace":
        ev = _forge(v1, signer=sr, namespace="other-team")
    elif case == "unauthorized_writer":
        ev = _forge(v1, signer=KEYS["Developer"], as_agent="Developer")
    elif case == "tampered_content":
        ev = _forge(v1, signer=sr, digest="0" * 64)
    elif case == "unknown_context":
        ev = _forge(v1, signer=sr, context_id="not-here")
    else:  # an item claiming someone else wrote it
        forged = ContextItem(id="f-x", type="t", content="x")
        ev = _forge(v1, signer=sr, items=(forged,))
        payload = json.loads(ev["content"])
        payload["snapshot"]["items"][0]["author"] = "Developer"
        from agentm2m.context.model import items_digest

        payload["snapshot"]["digest"] = items_digest([ContextItem.from_dict(i) for i in payload["snapshot"]["items"]])
        ev = sr.sign(UnsignedEvent(created_at=ev["created_at"], kind=ev["kind"], tags=ev["tags"],
                                   content=json.dumps(payload))).to_dict()
    relay.inject(ev)  # a hostile relay serves it regardless of validity
    report = b.pull()
    assert report["applied"] == 0
    assert len(report["rejected"]) == 1 and reason in report["rejected"][0]["reason"], report
    assert b_store.snapshot("security-review").version == 1


def test_old_and_conflicting_versions_rejected():
    relay = MemoryRelay()
    a_store, a = _node(relay)
    b_store, b = _node(relay)
    _publish(a_store, a, F1)
    _publish(a_store, a, F2)
    assert b.pull()["applied"] == 2
    v1 = MemoryContextStore()
    v1.ensure("security-review", POLICY)
    sr = KEYS["SecurityReviewer"]
    # a replayed old version
    relay.inject(_forge(v1.snapshot("security-review"), signer=sr, version=2, items=(F2,)))
    # a "v4" built on a base this node never had
    relay.inject(_forge(v1.snapshot("security-review"), signer=sr, version=4, items=(F2,), previous="ab" * 32))
    report = b.pull()
    reasons = " ".join(r["reason"] for r in report["rejected"])
    assert report["applied"] == 0 and "old version" in reasons and "conflict" in reasons
    assert b_store.snapshot("security-review").version == 3


def test_forked_events_for_one_version_are_both_judged():
    """Two verified events for the same version (a fork by one writer) that arrive
    before their predecessor must both be judged: the first valid one is applied,
    the other is reported -- neither may silently displace the other."""
    relay = MemoryRelay()
    a_store, a = _node(relay)
    b_store, b = _node(relay)
    v1 = b_store.snapshot("security-review")
    _publish(a_store, a, F1)
    v2 = a_store.snapshot("security-review")
    sr = KEYS["SecurityReviewer"]
    good = _forge(v1, signer=sr, version=3, items=(F1, F2), previous=v2.digest)
    fork = _forge(v1, signer=sr, version=3, items=(F1, F2.with_content("a different finding")), previous=v2.digest)
    first_v2 = next(iter(relay.events.values()))
    report = {"applied": 0, "duplicates": 0, "rejected": []}
    for ev in (good, fork, first_v2):  # successor events arrive before v2
        b.receive(ev, report)
    assert report["applied"] == 2  # v2, then exactly one v3
    assert len(report["rejected"]) == 1 and "old version" in report["rejected"][0]["reason"]
    assert b_store.snapshot("security-review").version == 3
