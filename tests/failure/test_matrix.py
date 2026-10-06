"""Phase 21: the failure matrix (spec section 65), one test per row.

| failure                         | expected                                   |
|---------------------------------|--------------------------------------------|
| relay offline                   | AgentM2M continues, phi unaffected          |
| relay timeout                   | event enters the outbox                     |
| invalid Nostr signature         | event rejected                              |
| unauthorized context read/write | denied                                      |
| context conflict                | explicit CONTEXT_CONFLICT                   |
| context deleted                 | consuming binding blocked, phi false        |
| context version changed         | affected bindings stale                     |
| unrelated context changed       | no effect                                   |
| LLM rejects value               | existing escalation (cached on footprint)   |
| LLM transport failure           | existing transport semantics (not cached)   |
| Nostr down + LLM failure        | both independently observable               |
| process restart                 | state and outbox recover                    |
| concurrent context updates      | exactly one winner                          |
| old workspace / old rule syntax | load and run unchanged                      |
"""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest
import yaml

from agentm2m.context.model import ContextPolicy
from agentm2m.llm.base import LLMBackend, LLMError
from agentm2m.llm.mock_backend import MockBackend
from agentm2m.nostr.events import UnsignedEvent
from agentm2m.nostr.relay_server import DevRelayServer
from agentm2m.nostr.signer import KeySigner
from agentm2m.workspace import Workspace, WorkspaceError
from tests._support import fill_all
from tests.context._fixtures import DECISION, FINDING, make_ctx_workspace, publish

FIXTURES = Path(__file__).resolve().parents[1] / "compat" / "fixtures"
BODY_S2 = ("Operation2CodeEdit::ce::op=Operation#op_s2", "body")


def _states(ws):
    return {(s["target_key"], s["binding"]): s["state"] for s in ws.binding_states()}


def _nostr_ws(project: Path, url: str, **cfg) -> Workspace:
    (project / ".agentm2m").mkdir(parents=True, exist_ok=True)
    (project / ".agentm2m/config.yaml").write_text(
        yaml.safe_dump({"nostr": {"enabled": True, "relays": [url], "timeout": 1, **cfg}}))
    return Workspace(project, llm=MockBackend(), max_resamples=3)


@pytest.fixture
def relay():
    server = DevRelayServer(port=0).start()
    yield server
    server.stop()


def test_relay_offline_engine_continues(tmp_path: Path, relay):
    ws = _nostr_ws(tmp_path, relay.url)
    ws.init("devteam")
    relay.stop()
    r = ws.run()
    assert r["phi"] is True and ws.acceptance()["phi"] is True
    assert ws.nostr_status()["outbox_depth"] > 0


def test_relay_timeout_event_enters_outbox(tmp_path: Path, relay):
    ws = _nostr_ws(tmp_path, relay.url)
    ws.init("devteam")
    relay.backend.timeout = True  # the dev relay answers with an error instead of OK
    ws.run()
    st = ws.nostr_status()
    assert st["outbox_depth"] > 0 and st["publish_failures"] > 0
    relay.backend.timeout = False


def test_invalid_nostr_signature_rejected(tmp_path: Path, relay):
    ws = _nostr_ws(tmp_path, relay.url)
    ws.init("devteam")
    r = ws.run()
    good = ws.nostr_events(run_id=r["run_id"])
    forged = KeySigner.generate().sign(UnsignedEvent(
        created_at=1, kind=4930, tags=[["t", "agentm2m"], ["t", f"agentm2m-run-{r['run_id']}"]],
        content=json.dumps({"forged": True}))).to_dict()
    forged["content"] = "tampered after signing"
    relay.backend.inject(forged)
    impostor = KeySigner.generate().sign(UnsignedEvent(
        created_at=2, kind=4930, tags=[["t", "agentm2m"], ["t", f"agentm2m-run-{r['run_id']}"]],
        content=json.dumps(good["events"][0] | {"seq": 999999}))).to_dict()
    relay.backend.publish_raw(impostor)  # validly signed, by a key this team does not trust
    again = ws.nostr_events(run_id=r["run_id"])
    assert again["verified"] == good["verified"] and again["rejected"] == 2
    assert again["rejection_reasons"] == {"invalid": 1, "untrusted author": 1}


def test_unauthorized_context_read_and_write_denied(tmp_path: Path):
    ws = make_ctx_workspace(tmp_path)
    with pytest.raises(WorkspaceError, match="may not read"):
        ws.context_get("security-review", as_agent="Analyst")
    with pytest.raises(WorkspaceError, match="may not write"):
        ws.context_update("security-review", as_agent="Tester", expected_version=1, items=[])


def test_context_conflict_is_explicit(tmp_path: Path):
    ws = make_ctx_workspace(tmp_path)
    publish(ws, FINDING)
    with pytest.raises(WorkspaceError, match="CONTEXT_CONFLICT.*version 2"):
        ws.context_update("security-review", as_agent="Architect", expected_version=1, items=[])


def test_context_deleted_blocks_binding(tmp_path: Path):
    ws = make_ctx_workspace(tmp_path, body_ctx="['notes']", oracle_ctx=None)
    ws.contexts.create("notes", policy=ContextPolicy(owner="Architect", readers=frozenset({"Developer"})),
                       as_agent="Architect")
    fill_all(ws)
    ws.contexts.delete("notes", as_agent="Architect")
    assert _states(ws)[BODY_S2] == "blocked" and ws.acceptance()["phi"] is False
    assert ws.next_bindings()["bindings"] == []  # never offered with invented context


def test_context_version_change_makes_affected_bindings_stale(tmp_path: Path):
    ws = make_ctx_workspace(tmp_path)
    publish(ws, FINDING)
    fill_all(ws)
    publish(ws, DECISION)
    stale = {k for k, v in _states(ws).items() if v == "stale"}
    assert BODY_S2 in stale and {b for _, b in stale} == {"body", "oracle"}


def test_unrelated_context_has_no_effect(tmp_path: Path):
    ws = make_ctx_workspace(tmp_path)
    publish(ws, FINDING)
    fill_all(ws)
    publish(ws, DECISION, context_id="product-roadmap", as_agent="Analyst")
    assert set(_states(ws).values()) == {"fresh"}


def test_llm_rejects_value_existing_escalation(tmp_path: Path):
    ws = Workspace(tmp_path, llm=MockBackend(script=["not a signature"]), max_resamples=2)
    ws.init("devteam")
    r = ws.run()
    esc = [e for e in r["escalations"] if e["binding"] == "signature"]
    assert esc
    link = ws._require().team.traces["Req2Arch"].get("Story2Operation", "s=UserStory#S2")
    assert "signature" in link.failed_stamps  # cached: not re-sampled on an unchanged footprint


class _DeadLLM(LLMBackend):
    name = "dead"

    def generate(self, prompt, *, temperature=0.2):
        raise LLMError("connection refused")


def test_llm_transport_failure_existing_semantics(tmp_path: Path):
    ws = Workspace(tmp_path, llm=_DeadLLM(), max_resamples=2)
    ws.init("devteam")
    r = ws.run()
    assert r["escalations"] and all("connection refused" in e["reason"] for e in r["escalations"])
    link = ws._require().team.traces["Req2Arch"].get("Story2Operation", "s=UserStory#S2")
    assert "signature" not in link.failed_stamps  # never judged: retried next run


def test_nostr_down_and_llm_failure_both_observable(tmp_path: Path, relay):
    (tmp_path / ".agentm2m").mkdir()
    (tmp_path / ".agentm2m/config.yaml").write_text(
        yaml.safe_dump({"nostr": {"enabled": True, "relays": [relay.url], "timeout": 1}}))
    ws = Workspace(tmp_path, llm=_DeadLLM(), max_resamples=2)
    ws.init("devteam")
    relay.stop()
    r = ws.run()
    assert r["escalations"]
    engine_errors = ws.events(run_id=r["run_id"], event_type="engine.error")["events"]
    assert engine_errors and engine_errors[0]["payload"]["kind"] == "llm_transport"
    st = ws.nostr_status()
    assert st["outbox_depth"] > 0 and st["publish_failures"] > 0 and st["last_error"]


def test_process_restart_recovers_state_and_outbox(tmp_path: Path, relay):
    ws = _nostr_ws(tmp_path, relay.url)
    ws.init("devteam")
    port = relay.port
    relay.stop()
    run_id = ws.run()["run_id"]
    depth = ws.nostr_status()["outbox_depth"]
    del ws
    restarted_relay = DevRelayServer(port=port).start()
    try:
        fresh = _nostr_ws(tmp_path, restarted_relay.url)  # a new process would see exactly this
        assert fresh.acceptance()["phi"] is True
        assert fresh.nostr_status()["outbox_depth"] == depth
        assert fresh.nostr_flush(force=True)["remaining"] == 0
        assert fresh.nostr_events(run_id=run_id)["verified"] == depth
    finally:
        restarted_relay.stop()


def test_concurrent_context_updates_deterministic(tmp_path: Path):
    project = tmp_path / "p"
    project.mkdir()
    make_ctx_workspace(project)
    results: list[str] = []
    barrier = threading.Barrier(5)

    def writer(i: int) -> None:
        ws = Workspace(project, backend="host")
        ws._require()
        barrier.wait()
        try:
            ws.context_update("security-review", as_agent="Architect", expected_version=1,
                              items=[{"id": f"n{i}", "type": "note", "content": str(i)}])
            results.append("ok")
        except WorkspaceError as exc:
            results.append("conflict" if "CONTEXT_CONFLICT" in str(exc) else str(exc))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["conflict"] * 4 + ["ok"]


def test_old_workspace_loads_without_migration(tmp_path: Path):
    ws = Workspace(tmp_path, backend="host", max_resamples=3)
    ws.init("devteam")
    shutil.copy(FIXTURES / "devteam_state_v1.json", ws.state_path)
    old = Workspace(tmp_path, backend="host", max_resamples=3)
    assert old.acceptance()["phi"] is True
    old.run()
    assert json.loads(old.state_path.read_text())["version"] == 2


def test_old_rule_syntax_continues_working(tmp_path: Path):
    ws = Workspace(tmp_path, llm=MockBackend(), max_resamples=3)
    ws.init("devteam")  # every shipped rule uses @llm(prompt, footprint)
    assert ws.run()["phi"] is True
