"""Phase 15: config-driven Nostr observability on a real (local) relay."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentm2m.config import WorkspaceConfig
from agentm2m.llm.mock_backend import MockBackend
from agentm2m.nostr.relay import WebSocketRelay
from agentm2m.nostr.relay_server import DevRelayServer
from agentm2m.workspace import Workspace


@pytest.fixture
def relay():
    server = DevRelayServer(port=0).start()
    yield server
    server.stop()


def _write_config(project: Path, cfg: dict) -> None:
    d = project / ".agentm2m"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text(yaml.safe_dump(cfg))


def test_config_defaults_and_validation(tmp_path: Path, monkeypatch):
    cfg = WorkspaceConfig.load(tmp_path / "missing.yaml")
    assert cfg.nostr.enabled is False and cfg.observability.timeline is True
    assert cfg.observability.privacy.mode == "standard" and cfg.context.enabled is True
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump({"nostr": {"enabled": True, "relays": ["https://bad"]}}))
    with pytest.raises(ValueError, match="relay"):
        WorkspaceConfig.load(p)
    p.write_text(yaml.safe_dump({"nostr": {"enabled": True, "relays": ["ws://x"], "private_key": "abc"}}))
    with pytest.raises(ValueError, match="secrets"):
        WorkspaceConfig.load(p)
    p.write_text(yaml.safe_dump({"observability": {"privacy": {"mode": "standard"}}}))
    monkeypatch.setenv("AGENTM2M_NOSTR_RELAYS", "ws://a:1,ws://b:2")
    monkeypatch.setenv("AGENTM2M_PRIVACY", "minimal")
    cfg = WorkspaceConfig.load(p)
    assert cfg.nostr.enabled is True and cfg.nostr.relays == ("ws://a:1", "ws://b:2")
    assert cfg.observability.privacy.mode == "minimal"


def test_workspace_publishes_signed_events_to_relay(tmp_path: Path, relay: DevRelayServer):
    _write_config(tmp_path, {"nostr": {"enabled": True, "relays": [relay.url]}})
    ws = Workspace(tmp_path, llm=MockBackend(), max_resamples=3)
    ws.init("devteam")
    r = ws.run()
    st = ws.nostr_status()
    assert st["enabled"] and st["outbox_depth"] == 0 and st["engine_pubkey"]
    assert (tmp_path / ".agentm2m/secrets/engine.key").is_file()
    got = ws.nostr_events(run_id=r["run_id"])
    types = [e["event_type"] for e in got["events"]]
    assert types[0] == "team.started" and types[-1] == "team.completed"
    assert got["verified"] == len(got["events"]) and got["rejected"] == 0
    assert all(e["nostr"]["pubkey"] == st["engine_pubkey"] for e in got["events"])


def test_agent_with_local_key_signs_its_own_events(tmp_path: Path, relay: DevRelayServer):
    _write_config(tmp_path, {"nostr": {"enabled": True, "relays": [relay.url]}})
    ws = Workspace(tmp_path, llm=MockBackend(), max_resamples=3)
    ws.init("devteam")
    key = ws.nostr_keygen("architect")
    spec = yaml.safe_load(ws.spec_path.read_text())
    spec["agents"] = {"Architect": {"nostr": {"pubkey": key["pubkey"], "identity_ref": "architect"}}}
    ws.spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))
    ws = Workspace(tmp_path, llm=MockBackend(), max_resamples=3)
    r = ws.run(max_passes=None)
    ws.edit("Req", [{"op": "set", "key": "Criterion#S2.1", "values": {"text": "returns 409"}}], "Analyst")
    r = ws.run()
    evs = ws.nostr_events(run_id=r["run_id"], agent="Architect", event_type="binding.accepted")["events"]
    assert evs and all(e["nostr"]["pubkey"] == key["pubkey"] for e in evs)
    assert "secret" not in str(ws.nostr_status()).lower() or "secrets_dir" in str(ws.nostr_status())


def test_relay_down_queues_then_flushes(tmp_path: Path, relay: DevRelayServer):
    _write_config(tmp_path, {"nostr": {"enabled": True, "relays": [relay.url], "timeout": 1}})
    ws = Workspace(tmp_path, llm=MockBackend(), max_resamples=3)
    ws.init("devteam")
    port = relay.port
    relay.stop()
    r = ws.run()
    assert ws.acceptance()["phi"] is True  # the run is unaffected
    depth = ws.nostr_status()["outbox_depth"]
    assert depth > 0
    restarted = DevRelayServer(port=port).start()
    try:
        out = ws.nostr_flush(force=True)
        assert out["remaining"] == 0 and out["published"] == depth
        client = WebSocketRelay(restarted.url)
        assert len(client.query([{"#t": [f"agentm2m-run-{r['run_id']}"]}])) > 0
        client.close()
    finally:
        restarted.stop()


def test_publish_category_filter(tmp_path: Path, relay: DevRelayServer):
    _write_config(tmp_path, {"nostr": {"enabled": True, "relays": [relay.url],
                                       "publish": {"bindings": False, "traces": False}}})
    ws = Workspace(tmp_path, llm=MockBackend(), max_resamples=3)
    ws.init("devteam")
    r = ws.run()
    types = {e["event_type"] for e in ws.nostr_events(run_id=r["run_id"])["events"]}
    assert "team.started" in types and not any(t.startswith(("binding.", "trace.")) for t in types)
    # the local timeline still has everything
    assert any(e["event_type"] == "binding.accepted" for e in ws.events(run_id=r["run_id"])["events"])


def test_nostr_disabled_by_default(tmp_path: Path):
    ws = Workspace(tmp_path, llm=MockBackend(), max_resamples=3)
    ws.init("devteam")
    ws.run()
    st = ws.nostr_status()
    assert st["enabled"] is False and st["outbox_depth"] == 0
    assert not (tmp_path / ".agentm2m/secrets").exists()
