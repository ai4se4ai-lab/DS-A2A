"""Phase 20: full DevTeam lifecycle observed over a real (local) Nostr relay.

The relay must receive the complete, verifiable lifecycle, and the local
AgentM2M state must be byte-identical to the same run without Nostr.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentm2m.llm.mock_backend import MockBackend
from agentm2m.nostr.relay import WebSocketRelay
from agentm2m.nostr.relay_server import DevRelayServer
from agentm2m.nostr.verifier import verify_event
from agentm2m.workspace import Workspace

TIGHTEN = [{"op": "set", "key": "Criterion#S2.1", "values": {"text": "completing a done task returns HTTP 409"}}]


def _scenario(project: Path) -> tuple[Workspace, list[str]]:
    ws = Workspace(project, llm=MockBackend(), max_resamples=3)
    ws.init("devteam")
    runs = [ws.run()["run_id"]]
    ws.edit("Req", TIGHTEN, "Analyst")
    runs.append(ws.run()["run_id"])
    return ws, runs


@pytest.fixture
def relay():
    server = DevRelayServer(port=0).start()
    yield server
    server.stop()


def test_full_lifecycle_on_relay_and_identical_state(tmp_path: Path, relay: DevRelayServer):
    plain, _ = _scenario(tmp_path / "plain")
    observed_dir = tmp_path / "observed"
    (observed_dir / ".agentm2m").mkdir(parents=True)
    (observed_dir / ".agentm2m/config.yaml").write_text(
        yaml.safe_dump({"nostr": {"enabled": True, "relays": [relay.url]}}))
    observed, runs = _scenario(observed_dir)

    # 1. authoritative state is unaffected by observability
    assert observed.state_path.read_bytes() == plain.state_path.read_bytes()
    assert observed.acceptance() == plain.acceptance()

    # 2. the relay has the whole lifecycle of the first run, in order
    got = observed.nostr_events(run_id=runs[0])
    assert got["rejected"] == 0
    types = [e["event_type"] for e in got["events"]]
    expected_order = ["team.started", "handoff.started", "handoff.matching", "handoff.target_created",
                      "trace.created", "binding.requested", "binding.prompt_prepared", "binding.accepted",
                      "handoff.completed", "team.completed"]
    positions = [types.index(t) for t in expected_order]
    assert positions == sorted(positions), types
    assert types.count("binding.accepted") == 7

    # 3. the change propagation run shows obligations created and discharged
    second = [e["event_type"] for e in observed.nostr_events(run_id=runs[1])["events"]]
    assert second.count("obligation.created") == 3 and second.count("obligation.discharged") == 3

    # 4. observability coverage: every locally recorded event reached the relay
    local = {(e["run_id"], e["seq"]) for e in observed.events(limit=100000)["events"]}
    remote = {(e["run_id"], e["seq"]) for e in observed.nostr_events(limit=100000)["events"]}
    assert local == remote

    # 5. an independent observer can verify every event without AgentM2M state
    client = WebSocketRelay(relay.url)
    raw = client.query([{"#t": ["agentm2m"], "limit": 100000}])
    client.close()
    assert raw and all(verify_event(d) for d in raw)
    assert observed.nostr_status()["outbox_depth"] == 0
