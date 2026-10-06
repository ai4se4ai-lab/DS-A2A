"""Phases 8-11: shared-context model, stores, authorization, versioning."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentm2m.context.errors import (
    ContextAccessDenied,
    ContextConflict,
    ContextError,
    ContextInvalid,
    ContextNotFound,
)
from agentm2m.context.model import ContextItem, ContextPolicy, ContextReference
from agentm2m.context.store import LocalContextStore, MemoryContextStore

POLICY = ContextPolicy(owner="SecurityReviewer", readers=frozenset({"Developer", "Tester"}),
                       writers=frozenset({"SecurityReviewer"}))
FINDING = ContextItem(id="finding-001", type="security-finding",
                      content={"finding": "API accepts unauthenticated request"})
DECISION = ContextItem(id="decision-002", type="design-decision",
                       content="Authentication middleware must run before routing")


@pytest.fixture(params=["memory", "local"])
def store(request, tmp_path: Path):
    if request.param == "memory":
        return MemoryContextStore()
    return LocalContextStore(tmp_path / "context.json")


def _create(store, items=(FINDING,)):
    return store.create("security-review", policy=POLICY, as_agent="SecurityReviewer", title="Security review",
                        items=list(items))


def test_create_context(store):
    snap = _create(store)
    assert snap.context_id == "security-review" and snap.version == 1
    assert [i.id for i in snap.items] == ["finding-001"]
    assert snap.items[0].author == "SecurityReviewer"  # defaulted from the writer
    assert len(snap.digest) == 64
    with pytest.raises(ContextError):
        _create(store)


def test_get_context(store):
    _create(store)
    snap = store.get("security-review", as_agent="Developer")
    assert snap.item("finding-001").content == {"finding": "API accepts unauthenticated request"}


def test_update_context_and_version_increment(store):
    _create(store)
    v2 = store.update("security-review", as_agent="SecurityReviewer", expected_version=1, items=[DECISION])
    assert v2.version == 2
    assert sorted(i.id for i in v2.items) == ["decision-002", "finding-001"]
    v3 = store.update("security-review", as_agent="SecurityReviewer", expected_version=2, remove=["finding-001"])
    assert v3.version == 3 and [i.id for i in v3.items] == ["decision-002"]


def test_snapshot_and_old_version_remains_addressable(store):
    _create(store)
    store.update("security-review", as_agent="SecurityReviewer", expected_version=1, items=[DECISION])
    assert store.snapshot("security-review").version == 2
    v1 = store.snapshot("security-review", version=1)
    assert [i.id for i in v1.items] == ["finding-001"]
    assert store.get("security-review", version=1, as_agent="Tester").digest == v1.digest
    with pytest.raises(ContextNotFound):
        store.snapshot("security-review", version=9)


def test_digest_changes_with_content(store):
    v1 = _create(store)
    changed = FINDING.with_content({"finding": "API accepts unauthenticated requests on /tasks"})
    v2 = store.update("security-review", as_agent="SecurityReviewer", expected_version=1, items=[changed])
    assert v2.digest != v1.digest


def test_same_content_same_digest(store):
    v1 = _create(store)
    other = MemoryContextStore()
    w1 = other.create("security-review", policy=POLICY, as_agent="SecurityReviewer", items=[FINDING])
    assert v1.digest == w1.digest  # content-addressed, independent of time or store
    same = store.update("security-review", as_agent="SecurityReviewer", expected_version=1, items=[FINDING])
    assert same.version == 1 and same.digest == v1.digest  # an identical update is a no-op


def test_digest_independent_of_item_order():
    a = MemoryContextStore().create("c", policy=POLICY, as_agent="SecurityReviewer", items=[FINDING, DECISION])
    b = MemoryContextStore().create("c", policy=POLICY, as_agent="SecurityReviewer", items=[DECISION, FINDING])
    assert a.digest == b.digest


def test_authorized_and_unauthorized_read(store):
    _create(store)
    assert store.authorize("security-review", "Developer", "read")
    assert store.authorize("security-review", "SecurityReviewer", "read")  # owner
    assert not store.authorize("security-review", "Analyst", "read")
    with pytest.raises(ContextAccessDenied):
        store.get("security-review", as_agent="Analyst")


def test_authorized_and_unauthorized_write(store):
    _create(store)
    assert store.authorize("security-review", "SecurityReviewer", "write")
    assert not store.authorize("security-review", "Developer", "write")
    with pytest.raises(ContextAccessDenied):
        store.update("security-review", as_agent="Developer", expected_version=1, items=[DECISION])
    with pytest.raises(ContextAccessDenied):
        store.create("other", policy=POLICY, as_agent="Developer")  # only the declared owner may create


def test_missing_context(store):
    with pytest.raises(ContextNotFound):
        store.get("nope", as_agent="Developer")
    with pytest.raises(ContextNotFound):
        store.update("nope", as_agent="SecurityReviewer", expected_version=1, items=[FINDING])
    assert not store.authorize("nope", "Developer", "read")


def test_context_conflict(store):
    _create(store)
    store.update("security-review", as_agent="SecurityReviewer", expected_version=1, items=[DECISION])
    with pytest.raises(ContextConflict) as exc:
        store.update("security-review", as_agent="SecurityReviewer", expected_version=1,
                     items=[FINDING.with_content("x")])
    assert exc.value.current_version == 2


def test_team_visibility_and_expiry():
    s = MemoryContextStore(clock=lambda: 100.0)
    s.create("roadmap", policy=ContextPolicy(owner="Analyst", visibility="team"), as_agent="Analyst")
    assert s.authorize("roadmap", "Tester", "read") and not s.authorize("roadmap", "Tester", "write")
    s.create("tmp", policy=ContextPolicy(owner="Analyst", readers=frozenset({"Tester"}), expiry=50.0),
             as_agent="Analyst")
    assert not s.authorize("tmp", "Tester", "read")  # expired: fail closed


def test_grants_attach_and_detach(store):
    _create(store)
    store.grant("security-review", "Architect", as_agent="SecurityReviewer")
    assert store.authorize("security-review", "Architect", "read")
    store.revoke("security-review", "Architect", as_agent="SecurityReviewer")
    assert not store.authorize("security-review", "Architect", "read")
    with pytest.raises(ContextAccessDenied):
        store.grant("security-review", "Analyst", as_agent="Developer")


def test_writer_cannot_grant_readers(store):
    policy = ContextPolicy(owner="SecurityReviewer", writers=frozenset({"Architect"}))
    store.create("c2", policy=policy, as_agent="SecurityReviewer")
    with pytest.raises(ContextAccessDenied):
        store.grant("c2", "Analyst", as_agent="Architect")


def test_authorship_and_provenance_cannot_be_forged(store):
    _create(store)
    forged = ContextItem(id="f-x", type="security-finding", content="x", author="Tester")
    with pytest.raises(ContextAccessDenied):
        store.update("security-review", as_agent="SecurityReviewer", expected_version=1, items=[forged])
    spoof = ContextItem(id="f-y", type="security-finding", content="y", provenance={"agent": "Tester"})
    with pytest.raises(ContextAccessDenied):
        store.update("security-review", as_agent="SecurityReviewer", expected_version=1, items=[spoof])
    ok = store.update("security-review", as_agent="SecurityReviewer", expected_version=1,
                      items=[ContextItem(id="f-z", type="t", content="z", provenance={"trace": "T::r::m"})])
    z = ok.item("f-z")
    assert z.author == "SecurityReviewer" and z.provenance == {"trace": "T::r::m", "agent": "SecurityReviewer"}


def test_resolve_selects_items_and_authorizes(store):
    _create(store, items=(FINDING, DECISION))
    snap, items = store.resolve("security-review", as_agent="Developer", item_ids=("decision-002",))
    assert [i.id for i in items] == ["decision-002"] and snap.version == 1
    with pytest.raises(ContextNotFound):
        store.resolve("security-review", as_agent="Developer", item_ids=("ghost",))
    with pytest.raises(ContextAccessDenied):
        store.resolve("security-review", as_agent="Analyst")


def test_delete(store):
    _create(store)
    with pytest.raises(ContextAccessDenied):
        store.delete("security-review", as_agent="Developer")
    store.delete("security-review", as_agent="SecurityReviewer")
    with pytest.raises(ContextNotFound):
        store.snapshot("security-review")


@pytest.mark.parametrize("bad", [
    {"id": "", "type": "t", "content": "x"},
    {"id": "has space", "type": "t", "content": "x"},
    {"id": "ok", "type": "", "content": "x"},
    {"id": "ok", "type": "t", "content": 3},
    {"id": "ok", "type": "t", "content": "x", "confidence": 2.0},
])
def test_invalid_items_rejected(bad):
    with pytest.raises(ContextInvalid):
        ContextItem.from_dict(bad)


def test_item_roundtrip_with_provenance():
    item = ContextItem(id="f-42", type="security-finding", content={"finding": "x"}, author="SecurityReviewer",
                       provenance={"trace": "Arch2Sec::Operation2Review::op=Operation#op_s2",
                                   "element": "Sec:SecurityReview#op_s2"}, confidence=0.9, scope="Code")
    assert ContextItem.from_dict(item.to_dict()) == item


def test_reference_from_snapshot():
    snap = MemoryContextStore().create("c", policy=POLICY, as_agent="SecurityReviewer", items=[FINDING])
    ref = ContextReference.of(snap, ("finding-001",))
    assert ref.to_dict() == {"context_id": "c", "version": 1, "digest": snap.digest, "item_ids": ["finding-001"]}


def test_local_store_persists_and_reloads(tmp_path: Path):
    p = tmp_path / "context.json"
    a = LocalContextStore(p)
    a.create("security-review", policy=POLICY, as_agent="SecurityReviewer", items=[FINDING])
    b = LocalContextStore(p)
    assert b.snapshot("security-review").digest == a.snapshot("security-review").digest
    b.update("security-review", as_agent="SecurityReviewer", expected_version=1, items=[DECISION])
    assert a.snapshot("security-review").version == 2  # a sees b's write (reload by fingerprint)
    with pytest.raises(ContextConflict):
        a.update("security-review", as_agent="SecurityReviewer", expected_version=1, items=[DECISION])
