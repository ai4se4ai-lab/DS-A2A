"""Phases 2-3: NIP-01 events -- canonical serialization, ids, BIP-340 signatures."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from agentm2m.nostr.errors import InvalidEvent
from agentm2m.nostr.events import NostrEvent, UnsignedEvent, event_id, serialize_for_id
from agentm2m.nostr.signer import KeySigner, SecretStore
from agentm2m.nostr.verifier import verify_event, verify_schnorr

# BIP-340 test vectors (index 0: signing; index 1: verification)
BIP340_SK0 = "0000000000000000000000000000000000000000000000000000000000000003"
BIP340_PK0 = "f9308a019258c31049344f85f89d5229b531c845836f99b08601f113bce036f9"
BIP340_MSG0 = "00" * 32
BIP340_SIG0 = (
    "e907831f80848d1069a5371b402410364bdf1c5f8307b0084c55f1ce2dca8215"
    "25f66a4a85ea8b71e482a74f382d2ce5ebeee8fdb2172f477df4900d310536c0"
)
BIP340_PK1 = "dff1d77f2a671c5f36183726db2341be58feae1da2deced843240f7b502ba659"
BIP340_MSG1 = "243f6a8885a308d313198a2e03707344a4093822299f31d0082efa98ec4e6c89"
BIP340_SIG1 = (
    "6896bd60eeae296db48a229ff71dfe071bde413e6d43f917dc8dcf8c78de3341"
    "8906d11ac976abccb20b091292bff4ea897efcb639ea871cfa95f6de339e4b0a"
)


def _unsigned(**kw) -> UnsignedEvent:
    base = {
        "created_at": 1_760_000_000,
        "kind": 1,
        "tags": [["t", "agentm2m"], ["type", "team.started"]],
        "content": '{"hello":"world"}',
    }
    base.update(kw)
    return UnsignedEvent(**base)


def test_bip340_vectors():
    signer = KeySigner(BIP340_SK0)
    assert signer.pubkey == BIP340_PK0
    assert signer.sign_digest(bytes.fromhex(BIP340_MSG0), aux=bytes(32)).hex() == BIP340_SIG0
    assert verify_schnorr(BIP340_PK1, bytes.fromhex(BIP340_MSG1), bytes.fromhex(BIP340_SIG1))
    bad = BIP340_SIG1[:-2] + ("0b" if BIP340_SIG1[-2:] != "0b" else "0c")
    assert not verify_schnorr(BIP340_PK1, bytes.fromhex(BIP340_MSG1), bytes.fromhex(bad))


def test_event_serialization_matches_nip01():
    ev = _unsigned(content='line1\nline2 "q" \\ tab\t é \u0001')
    raw = serialize_for_id("ab" * 32, ev)
    assert raw.startswith('[0,"' + "ab" * 32 + '",1760000000,1,[["t","agentm2m"],["type","team.started"]],"')
    assert '\\n' in raw and '\\"q\\"' in raw and "\\\\" in raw and "\\t" in raw
    assert "é" in raw  # UTF-8, not \u-escaped
    assert "\\u0001" in raw
    assert " " not in raw.split('"line1')[0]  # no whitespace between tokens


def test_event_id_deterministic():
    ev = _unsigned()
    pk = "ab" * 32
    a, b = event_id(pk, ev), event_id(pk, _unsigned())
    assert a == b == hashlib.sha256(serialize_for_id(pk, ev).encode()).hexdigest()
    assert event_id(pk, _unsigned(content="other")) != a


def test_event_signature_valid_and_roundtrip():
    signer = KeySigner.generate()
    ev = signer.sign(_unsigned())
    assert ev.pubkey == signer.pubkey and len(ev.sig) == 128
    verify_event(ev)
    wire = json.loads(json.dumps(ev.to_dict()))
    again = NostrEvent.from_dict(wire)
    assert again == ev
    verify_event(wire)


def test_event_tags_preserved():
    ev = KeySigner.generate().sign(_unsigned(tags=[["e", "aa" * 32], ["p", "bb" * 32], ["run", "r1"]]))
    assert ev.tag_values("run") == ["r1"]
    assert ev.tag_values("t") == []
    assert ev.first_tag("p") == "bb" * 32


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(content=d["content"] + " "),
        lambda d: d.update(tags=[*d["tags"], ["x", "y"]]),
        lambda d: d.update(created_at=d["created_at"] + 1),
        lambda d: d.update(kind=d["kind"] + 1),
        lambda d: d.update(sig="00" * 64),
        lambda d: d.update(pubkey=KeySigner.generate().pubkey),
    ],
    ids=["content", "tags", "created_at", "kind", "sig", "pubkey"],
)
def test_event_signature_invalid(mutate):
    d = KeySigner.generate().sign(_unsigned()).to_dict()
    mutate(d)
    with pytest.raises(InvalidEvent):
        verify_event(d)


@pytest.mark.parametrize(
    "broken",
    [
        {},
        {"id": "x"},
        "not a dict",
    ],
)
def test_malformed_event_rejected(broken):
    with pytest.raises(InvalidEvent):
        verify_event(broken)


def test_malformed_fields_rejected():
    d = KeySigner.generate().sign(_unsigned()).to_dict()
    for field, value in [("kind", -1), ("kind", 70000), ("created_at", "now"), ("tags", [["ok"], [1]]), ("content", 3)]:
        bad = dict(d, **{field: value})
        with pytest.raises(InvalidEvent):
            verify_event(bad)


def test_signer_never_reveals_secret():
    s = KeySigner(BIP340_SK0)
    assert BIP340_SK0 not in repr(s) and BIP340_SK0 not in str(s)
    assert not hasattr(s, "seckey") and not hasattr(s, "secret")


def test_secret_store_env_and_file(tmp_path: Path, monkeypatch):
    store = SecretStore(tmp_path / "secrets")
    assert store.get("engine") is None
    created = store.create("engine")
    path = tmp_path / "secrets" / "engine.key"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    assert store.get("engine").pubkey == created.pubkey
    with pytest.raises(FileExistsError):
        store.create("engine")
    monkeypatch.setenv("AGENTM2M_NOSTR_KEY_SEC_REVIEWER", BIP340_SK0)
    assert store.get("sec-reviewer").pubkey == BIP340_PK0
    from agentm2m.nostr.keys import encode_nsec

    monkeypatch.setenv("AGENTM2M_NOSTR_KEY_ENGINE", encode_nsec(BIP340_SK0))
    assert store.get("engine").pubkey == BIP340_PK0  # env overrides the file
    assert store.refs() == ["engine"]


def test_secret_store_rejects_path_traversal(tmp_path: Path):
    store = SecretStore(tmp_path / "secrets")
    for ref in ["../x", "a/b", "", ".hidden"]:
        with pytest.raises(ValueError):
            store.create(ref)
