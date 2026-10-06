"""Phase 15: CLI for shared context, observability and Nostr. Existing
commands (`workspace validate|status|run|impact`) are unchanged."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agentm2m.cli import main
from agentm2m.nostr.relay_server import DevRelayServer


def run(capsys, *argv) -> tuple[int, str, str]:
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def project(tmp_path: Path, capsys) -> Path:
    assert run(capsys, "workspace", "--dir", str(tmp_path), "--llm", "mock", "init", "devteam")[0] == 0
    return tmp_path


def test_existing_workspace_commands_unchanged(project: Path, capsys):
    d = str(project)
    code, out, _ = run(capsys, "workspace", "--dir", d, "validate", "--brief")
    assert code == 0 and out.startswith("agentm2m: workspace OK")
    code, out, _ = run(capsys, "workspace", "--dir", d, "--llm", "mock", "run")
    assert code == 0 and json.loads(out)["phi"] is True
    code, out, _ = run(capsys, "workspace", "--dir", d, "status", "--brief")
    assert code == 0 and "phi=True" in out
    code, out, _ = run(capsys, "workspace", "--dir", d, "impact")
    assert code == 0 and json.loads(out)["obligations"] == []


def test_context_commands(project: Path, capsys, tmp_path: Path):
    d = str(project)
    items = tmp_path / "items.json"
    items.write_text(json.dumps([{"id": "f1", "type": "security-finding", "content": "auth required"}]))
    code, out, _ = run(capsys, "context", "--dir", d, "create", "security-review", "--as", "Architect",
                       "--title", "Security", "--readers", "Developer,Tester", "--items", str(items))
    assert code == 0 and json.loads(out)["version"] == 1
    code, out, _ = run(capsys, "context", "--dir", d, "list")
    assert code == 0 and "security-review" in out and "auth required" not in out
    code, out, _ = run(capsys, "context", "--dir", d, "show", "security-review", "--as", "Developer")
    assert code == 0 and json.loads(out)["items"][0]["content"] == "auth required"
    code, _, err = run(capsys, "context", "--dir", d, "show", "security-review", "--as", "Analyst")
    assert code == 1 and "may not read" in err
    items.write_text(json.dumps([{"id": "f2", "type": "design-decision", "content": "auth before routing"}]))
    code, out, _ = run(capsys, "context", "--dir", d, "update", "security-review", "--as", "Architect",
                       "--expected-version", "1", "--items", str(items))
    assert code == 0 and json.loads(out)["version"] == 2
    code, _, err = run(capsys, "context", "--dir", d, "update", "security-review", "--as", "Architect",
                       "--expected-version", "1", "--items", str(items))
    assert code == 1 and "CONTEXT_CONFLICT" in err
    code, out, _ = run(capsys, "context", "--dir", d, "validate")
    assert code == 0
    code, out, _ = run(capsys, "context", "--dir", d, "graph", "--format", "dot")
    assert code == 0 and out.startswith("digraph") and '"ctx:security-review"' in out


def test_workspace_events(project: Path, capsys):
    d = str(project)
    code, out, _ = run(capsys, "workspace", "--dir", d, "--llm", "mock", "run")
    run_id = json.loads(out)["run_id"]
    code, out, _ = run(capsys, "workspace", "--dir", d, "events", "--run", run_id, "--type", "binding.accepted")
    assert code == 0
    evs = json.loads(out)["events"]
    assert len(evs) == 7 and all(e["run_id"] == run_id for e in evs)
    code, out, _ = run(capsys, "workspace", "--dir", d, "events", "--run", run_id, "--timeline")
    assert code == 0 and "team.started" in out and "binding.accepted" in out


def test_nostr_commands(project: Path, capsys):
    d = str(project)
    code, out, _ = run(capsys, "nostr", "--dir", d, "status")
    assert code == 0 and json.loads(out)["enabled"] is False
    code, out, _ = run(capsys, "nostr", "--dir", d, "keygen", "architect")
    key = json.loads(out)
    assert code == 0 and key["npub"].startswith("npub1") and "nsec" not in out
    relay = DevRelayServer(port=0).start()
    try:
        cfg = project / ".agentm2m" / "config.yaml"
        cfg.write_text(yaml.safe_dump({"nostr": {"enabled": True, "relays": [relay.url]}}))
        code, out, _ = run(capsys, "nostr", "--dir", d, "relays")
        assert code == 0 and relay.url in out
        code, out, _ = run(capsys, "nostr", "--dir", d, "test")
        assert code == 0 and json.loads(out)["relays"][0]["ok"] is True
        code, out, _ = run(capsys, "workspace", "--dir", d, "--llm", "mock", "run")
        run_id = json.loads(out)["run_id"]
        code, out, _ = run(capsys, "nostr", "--dir", d, "events", "--run", run_id)
        got = json.loads(out)
        assert code == 0 and got["verified"] > 0 and got["rejected"] == 0
        code, out, _ = run(capsys, "nostr", "--dir", d, "identity", "Architect")
        assert code == 0 and json.loads(out)["agent_id"] == "Architect"
        code, out, _ = run(capsys, "nostr", "--dir", d, "flush")
        assert code == 0 and json.loads(out)["remaining"] == 0
    finally:
        relay.stop()
