"""Phase 5: the AgentM2M event envelope, privacy redaction, sinks, presence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentm2m.observability.emitter import Emitter
from agentm2m.observability.events import (
    EVENT_TYPES,
    SCHEMA,
    SCHEMA_VERSION,
    AgentM2MEvent,
    EventSchemaError,
)
from agentm2m.observability.presence import derive_presence
from agentm2m.observability.privacy import PrivacyConfig, redact
from agentm2m.observability.sink import CompositeSink, JsonlEventSink, MemoryEventSink, NullEventSink
from agentm2m.observability.timeline import read_timeline


class Clock:
    def __init__(self, t: float = 1_760_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        self.t += 1
        return self.t


def test_event_schema_and_roundtrip():
    ev = AgentM2MEvent(event_type="binding.accepted", seq=3, ts=1.5, workspace_id="ws", run_id="r1",
                       agent_id="Architect", handoff_id="Req2Arch", rule_id="Story2Operation",
                       target_key="Story2Operation::op::s=UserStory#S1", binding="signature",
                       context_ids=("security-review",), payload={"status": "accepted"})
    d = ev.to_dict()
    assert d["schema"] == SCHEMA and d["schema_version"] == SCHEMA_VERSION == 1
    assert json.loads(json.dumps(d)) == d
    assert AgentM2MEvent.from_dict(d) == ev
    assert ev.uid == "r1:3"


@pytest.mark.parametrize("patch", [{"schema": "other"}, {"schema_version": 2}, {"event_type": "made.up"}])
def test_unknown_event_schema_rejected(patch):
    d = AgentM2MEvent(event_type="team.started", seq=1, ts=0, workspace_id="w").to_dict() | patch
    with pytest.raises(EventSchemaError):
        AgentM2MEvent.from_dict(d)


def test_event_type_registry_covers_spec():
    for t in ["team.started", "team.completed", "handoff.started", "binding.requested", "binding.accepted",
              "binding.rejected", "binding.escalated", "binding.blocked", "binding.stale", "obligation.created",
              "obligation.discharged", "trace.created", "trace.deleted", "context.created", "context.updated",
              "context.read", "context.attached", "context.detached", "agent.working", "engine.error",
              "nostr.error", "context.error", "team.evolved", "change.requested", "impact.computed"]:
        assert t in EVENT_TYPES


SECRET_PROMPT = "Implement this operation. SECRET-SOURCE-TEXT"


def test_redact_standard_hides_raw_content():
    payload = {"status": "rejected", "prompt": SECRET_PROMPT, "value": "def f(): SECRET", "reason": "bad SECRET",
               "footprint": "SECRET fp", "context_content": "SECRET ctx", "input_tokens": 10, "pins": [{"id": "c"}]}
    out = redact(payload, PrivacyConfig())
    assert "SECRET" not in json.dumps(out)
    assert out["prompt_digest"] and out["prompt_chars"] == len(SECRET_PROMPT)
    assert out["status"] == "rejected" and out["input_tokens"] == 10 and out["pins"] == [{"id": "c"}]


def test_redact_minimal_drops_standard_metadata():
    out = redact({"status": "ok", "input_tokens": 10, "pins": [1], "count": 2}, PrivacyConfig(mode="minimal"))
    assert out == {"status": "ok", "count": 2}


def test_redact_debug_needs_explicit_flags():
    p = {"prompt": SECRET_PROMPT, "value": "v"}
    assert "prompt" not in redact(p, PrivacyConfig(mode="debug"))
    out = redact(p, PrivacyConfig(mode="debug", include_prompts=True))
    assert out["prompt"] == SECRET_PROMPT and "value" not in out


def test_null_sink_does_nothing():
    em = Emitter(NullEventSink(), workspace_id="w")
    assert em.emit("team.started") is None
    assert not em.enabled


def test_memory_sink_receives_correlated_events():
    sink = MemoryEventSink()
    em = Emitter(sink, workspace_id="w", clock=Clock())
    run = em.begin_run("run")
    em.emit("team.started")
    em.emit("binding.accepted", agent_id="Architect", handoff_id="Req2Arch", binding="signature")
    assert [e.event_type for e in sink.events] == ["team.started", "binding.accepted"]
    assert all(e.run_id == run and e.workspace_id == "w" for e in sink.events)
    assert [e.seq for e in sink.events] == [1, 2]
    assert sink.events[1].ts > sink.events[0].ts


def test_emitter_rejects_unknown_type_without_raising():
    sink = MemoryEventSink()
    em = Emitter(sink, workspace_id="w")
    em.emit("not.a.type")
    assert sink.events == [] and em.errors == 1


def test_emitter_swallows_sink_failures():
    class Boom:
        enabled = True

        def publish(self, ev):
            raise RuntimeError("relay exploded")

    em = Emitter(Boom(), workspace_id="w")
    em.emit("team.started")
    em.emit("team.completed")
    assert em.errors == 2 and "relay exploded" in em.last_error


def test_composite_sink_isolates_failures():
    class Boom:
        enabled = True

        def publish(self, ev):
            raise RuntimeError("x")

    good = MemoryEventSink()
    em = Emitter(CompositeSink([Boom(), good]), workspace_id="w")
    em.emit("team.started")
    assert len(good.events) == 1 and em.errors == 1


def test_jsonl_sink_and_timeline_query(tmp_path: Path):
    path = tmp_path / "obs" / "events.jsonl"
    em = Emitter(JsonlEventSink(path), workspace_id="w", clock=Clock())
    r1 = em.begin_run("run")
    em.emit("handoff.started", handoff_id="Req2Arch")
    em.emit("binding.accepted", agent_id="Architect", handoff_id="Req2Arch")
    r2 = em.begin_run("run")
    em.emit("binding.accepted", agent_id="Tester", handoff_id="Req2Test")
    assert len(read_timeline(path)) == 3
    assert len(read_timeline(path, run_id=r1)) == 2
    assert [e.agent_id for e in read_timeline(path, event_type="binding.accepted")] == ["Architect", "Tester"]
    assert [e.run_id for e in read_timeline(path, handoff="Req2Test")] == [r2]
    first = read_timeline(path)[0].ts
    assert len(read_timeline(path, since=first + 1)) == 2
    assert len(read_timeline(path, limit=1)) == 1
    assert read_timeline(tmp_path / "missing.jsonl") == []


def _ev(t, agent, seq, **payload):
    return AgentM2MEvent(event_type=t, seq=seq, ts=float(seq), workspace_id="w", agent_id=agent, payload=payload)


def test_presence_is_derived_from_events():
    events = [
        _ev("binding.requested", "Architect", 1),
        _ev("binding.requested", "Tester", 2, deferred=True),
        _ev("binding.blocked", "Developer", 3),
        _ev("binding.accepted", "Architect", 4),
        _ev("binding.escalated", "Tester", 5),
    ]
    p = derive_presence(events, ["Analyst", "Architect", "Developer", "Tester"])
    assert p == {"Analyst": "offline", "Architect": "idle", "Developer": "blocked", "Tester": "failed"}
    p2 = derive_presence(events[:2], ["Architect", "Tester"])
    assert p2 == {"Architect": "working", "Tester": "waiting"}
