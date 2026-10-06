"""Phase 1: optional Nostr identity per agent (public metadata only)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agentm2m.nostr.identity import AgentIdentity, IdentityError, load_identities
from agentm2m.nostr.keys import (
    KeyFormatError,
    decode_npub,
    decode_nsec,
    encode_npub,
    encode_nsec,
)
from agentm2m.workspace import Workspace, WorkspaceError

# NIP-19 published test vectors
PUB_HEX = "3bf0c63fcb93463407af97a5e5ee64fa883d107ef9e558472c4eb9aaaefa459d"
NPUB = "npub180cvv07tjdrrgpa0j7j7tmnyl2yr6yr7l8j4s3evf6u64th6gkwsyjh6w6"
SEC_HEX = "67dea2ed018072d675f5415ecfaed7d2597555e202d85b3d65ea4e58d2d92ffa"
NSEC = "nsec1vl029mgpspedva04g90vltkh6fvh240zqtv9k0t9af8935ke9laqsnlfe5"


def test_npub_derivation():
    assert encode_npub(PUB_HEX) == NPUB
    assert decode_npub(NPUB) == PUB_HEX
    assert encode_nsec(SEC_HEX) == NSEC
    assert decode_nsec(NSEC) == SEC_HEX


@pytest.mark.parametrize("bad", ["", "abc", "zz" * 32, PUB_HEX[:-2], PUB_HEX + "00", PUB_HEX.upper() + "x"])
def test_invalid_pubkey(bad):
    with pytest.raises(IdentityError):
        AgentIdentity.from_spec("Architect", {"nostr": {"pubkey": bad}})


@pytest.mark.parametrize("bad", ["npub1", NPUB[:-1] + ("q" if NPUB[-1] != "q" else "p"), NSEC, "nprofile1xyz"])
def test_invalid_npub(bad):
    with pytest.raises((IdentityError, KeyFormatError)):
        AgentIdentity.from_spec("Architect", {"nostr": {"npub": bad}})


def test_agent_identity_without_nostr():
    ident = AgentIdentity.from_spec("Analyst", None)
    assert ident.agent_id == "Analyst"
    assert ident.display_name == "Analyst"
    assert ident.nostr_pubkey is None and ident.nostr_npub is None
    assert ident.relay_urls == ()
    assert not ident.nostr_enabled


def test_agent_identity_with_pubkey():
    ident = AgentIdentity.from_spec(
        "Architect", {"display_name": "The Architect", "nostr": {"pubkey": PUB_HEX, "relays": ["wss://r.example"]}}
    )
    assert ident.nostr_pubkey == PUB_HEX
    assert ident.nostr_npub == NPUB
    assert ident.relay_urls == ("wss://r.example",)
    assert ident.display_name == "The Architect"
    assert ident.nostr_enabled


def test_npub_only_and_consistent_pair():
    assert AgentIdentity.from_spec("A", {"nostr": {"npub": NPUB}}).nostr_pubkey == PUB_HEX
    assert AgentIdentity.from_spec("A", {"nostr": {"npub": NPUB, "pubkey": PUB_HEX}}).nostr_npub == NPUB
    other = "f" * 64
    with pytest.raises(IdentityError):
        AgentIdentity.from_spec("A", {"nostr": {"npub": NPUB, "pubkey": other}})


def test_relay_urls_must_be_websocket():
    with pytest.raises(IdentityError):
        AgentIdentity.from_spec("A", {"nostr": {"pubkey": PUB_HEX, "relays": ["https://not-a-relay"]}})


def test_identity_serialization_and_roundtrip():
    ident = AgentIdentity.from_spec("Architect", {"nostr": {"pubkey": PUB_HEX, "identity_ref": "architect"}})
    d = ident.to_dict()
    assert json.loads(json.dumps(d)) == d
    assert AgentIdentity.from_dict(d) == ident


@pytest.mark.parametrize("secret_key", ["private_key", "nsec", "secret", "privkey", "seckey"])
def test_private_key_never_accepted_or_serialized(secret_key):
    with pytest.raises(IdentityError, match="secrets"):
        AgentIdentity.from_spec("A", {"nostr": {"pubkey": PUB_HEX, secret_key: SEC_HEX}})
    ident = AgentIdentity.from_spec("A", {"nostr": {"pubkey": PUB_HEX, "identity_ref": "a"}})
    blob = json.dumps(ident.to_dict())
    assert SEC_HEX not in blob and "nsec" not in blob


def test_load_identities_defaults_and_unknown_agent():
    ids = load_identities({"Architect": {"nostr": {"pubkey": PUB_HEX}}}, ["Analyst", "Architect"])
    assert set(ids) == {"Analyst", "Architect"}
    assert ids["Architect"].nostr_npub == NPUB and ids["Analyst"].nostr_pubkey is None
    with pytest.raises(IdentityError, match="Ghost"):
        load_identities({"Ghost": {}}, ["Analyst"])


def test_existing_team_yaml_files_continue_to_load(tmp_path: Path):
    ws = Workspace(tmp_path, backend="host")
    ws.init("devteam")
    team = ws._require().team
    assert set(team.identities) == set(team.agents)
    assert all(not i.nostr_enabled for i in team.identities.values())


def _set_agents(ws: Workspace, agents: dict) -> None:
    spec = yaml.safe_load(ws.spec_path.read_text())
    spec["agents"] = agents
    ws.spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))


def test_team_yaml_agents_section(tmp_path: Path):
    ws = Workspace(tmp_path, backend="host")
    ws.init("devteam")
    _set_agents(ws, {"Architect": {"nostr": {"npub": NPUB, "identity_ref": "architect"}}})
    team = Workspace(tmp_path, backend="host")._require().team
    assert team.identities["Architect"].nostr_pubkey == PUB_HEX
    assert team.identities["Architect"].identity_ref == "architect"


def test_team_yaml_with_private_key_is_rejected(tmp_path: Path):
    ws = Workspace(tmp_path, backend="host")
    ws.init("devteam")
    _set_agents(ws, {"Architect": {"nostr": {"pubkey": PUB_HEX, "private_key": SEC_HEX}}})
    res = Workspace(tmp_path, backend="host").validate()
    assert not res["ok"] and "secrets" in res["errors"][0]
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path, backend="host").status()
