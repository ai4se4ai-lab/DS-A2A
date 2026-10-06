"""Phase 7: engine instrumentation -- every meaningful transition is
observable, and observing never changes what the engine computes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentm2m.llm.mock_backend import MockBackend
from agentm2m.nostr.outbox import Outbox
from agentm2m.nostr.publisher import NostrEventSink
from agentm2m.nostr.relay import MemoryRelay
from agentm2m.nostr.signer import KeySigner
from agentm2m.observability.sink import MemoryEventSink
from agentm2m.workspace import Workspace
from tests._support import fill_all

TIGHTEN_S21 = [{"op": "set", "key": "Criterion#S2.1", "values": {"text": "completing a done task returns HTTP 409"}}]


class Boom:
    enabled = True

    def publish(self, ev):
        raise RuntimeError("sink exploded")


def _nostr_down(tmp: Path) -> NostrEventSink:
    relay = MemoryRelay()
    relay.down = True
    key = KeySigner.generate()
    return NostrEventSink(relay, Outbox(tmp / "outbox.jsonl"), signer_for=lambda _a: key)


def _mock_run(project: Path, sink) -> Workspace:
    ws = Workspace(project, llm=MockBackend(), max_resamples=3, event_sink=sink)
    ws.init("devteam")
    ws.run()
    ws.edit("Req", TIGHTEN_S21, "Analyst")
    ws.run()
    return ws


@pytest.mark.parametrize("make_sink", [
    lambda tmp: MemoryEventSink(),
    lambda tmp: Boom(),
    _nostr_down,
], ids=["memory", "raising", "nostr-relay-down"])
def test_state_identical_with_any_sink(tmp_path: Path, make_sink):
    baseline = _mock_run(tmp_path / "null", None)
    observed = _mock_run(tmp_path / "obs", make_sink(tmp_path))
    assert observed.state_path.read_bytes() == baseline.state_path.read_bytes()
    assert observed.acceptance() == baseline.acceptance()


def test_backend_run_lifecycle_events(tmp_path: Path):
    sink = MemoryEventSink()
    ws = Workspace(tmp_path, llm=MockBackend(), max_resamples=3, event_sink=sink)
    ws.init("devteam")
    ws.run()
    types = sink.types()
    run_events = [e for e in sink.events if e.operation == "run"]
    run_id = run_events[0].run_id
    assert all(e.run_id == run_id for e in run_events)
    assert types[types.index("team.started")] == "team.started"
    assert types[-1] == "team.completed"
    for t in ["handoff.started", "handoff.matching", "handoff.target_created", "trace.created",
              "binding.requested", "binding.prompt_prepared", "binding.accepted", "handoff.completed"]:
        assert t in types, t
    acc = [e for e in sink.events if e.event_type == "binding.accepted"]
    assert len(acc) == 7  # every stochastic binding of the devteam template
    a = next(e for e in acc if e.binding == "signature")
    assert a.handoff_id == "Req2Arch" and a.rule_id == "Story2Operation" and a.agent_id == "Architect"
    assert a.target_key.startswith("Story2Operation::op::") and a.trace_id
    assert a.payload["footprint_version"] and a.payload["attempts"] == 1
    assert "duration_ms" in a.payload and "input_tokens" in a.payload
    # ordering: a hand-off starts before its bindings and completes after them
    first = sink.events.index(next(e for e in sink.events if e.event_type == "handoff.started"))
    assert first < sink.events.index(a)


def test_host_mode_events_and_presence(tmp_path: Path):
    sink = MemoryEventSink()
    ws = Workspace(tmp_path, backend="host", max_resamples=3, event_sink=sink)
    ws.init("devteam")
    ws.next_bindings()
    req = [e for e in sink.events if e.event_type == "binding.requested"]
    assert req and all(e.payload["deferred"] for e in req)
    assert any(e.event_type == "binding.blocked" for e in sink.events)  # CodeEdit.body waits on signature
    assert ws.agent_directory()["agents"]["Architect"]["presence"] == "waiting"
    fill_all(ws)
    types = sink.types()
    assert "binding.submitted" in types and "binding.accepted" in types
    presence = {a: v["presence"] for a, v in ws.agent_directory()["agents"].items()}
    assert presence == {"Analyst": "offline", "Architect": "idle", "Developer": "idle", "Tester": "idle"}


def test_change_events_obligations(tmp_path: Path):
    sink = MemoryEventSink()
    ws = Workspace(tmp_path, backend="host", max_resamples=3, event_sink=sink)
    ws.init("devteam")
    fill_all(ws)
    sink.events.clear()
    ws.impact("Req", TIGHTEN_S21, "Analyst")
    assert sink.types() == ["impact.computed"]  # a preview emits no engine events
    assert sink.events[0].payload["obligations"] == 3
    ws.edit("Req", TIGHTEN_S21, "Analyst")
    assert sink.types()[0] == "change.requested"
    created = [e for e in sink.events if e.event_type == "obligation.created"]
    assert {(e.target_key, e.binding) for e in created} == {
        ("Story2Operation::op::s=UserStory#S2", "signature"),
        ("Criterion2TestCase::tc::c=Criterion#S2.1", "oracle"),
        ("Operation2CodeEdit::ce::op=Operation#op_s2", "body"),
    }
    assert all(e.event_type != "binding.stale" or e.binding for e in sink.events)
    fill_all(ws)
    assert any(e.event_type == "obligation.discharged" for e in sink.events)


def test_rejection_and_escalation_events(tmp_path: Path):
    sink = MemoryEventSink()
    ws = Workspace(tmp_path, llm=MockBackend(script=["not a signature !!"]), max_resamples=2, event_sink=sink)
    ws.init("devteam")
    ws.run()
    rej = [e for e in sink.events if e.event_type == "binding.rejected"]
    esc = [e for e in sink.events if e.event_type == "binding.escalated"]
    assert rej and esc
    assert "reason_digest" in rej[0].payload and "value_digest" in rej[0].payload
    assert "not a signature" not in json.dumps([e.to_dict() for e in sink.events])


def test_no_prompt_or_source_text_in_standard_events(tmp_path: Path):
    sink = MemoryEventSink()
    ws = Workspace(tmp_path, backend="host", max_resamples=3, event_sink=sink)
    ws.init("devteam")
    prompts: list[str] = []

    def answer(b):
        prompts.append(b["prompt"])
        from tests._support import good_value

        return good_value(b)

    fill_all(ws, answer)
    blob = json.dumps([e.to_dict() for e in sink.events])
    for p in prompts:
        assert p not in blob
    for secret in ["a title is required", "completing a done task is rejected", "markTaskDone", "def op("]:
        assert secret not in blob


def test_default_workspace_writes_local_timeline(tmp_path: Path):
    ws = Workspace(tmp_path, llm=MockBackend(), max_resamples=3)
    ws.init("devteam")
    r = ws.run()
    tl = ws.events(run_id=r["run_id"])
    assert tl["events"] and tl["events"][-1]["event_type"] == "team.completed"
