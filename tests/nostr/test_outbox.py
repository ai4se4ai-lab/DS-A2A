"""Phase 6: durable outbox + NostrEventSink (publish never blocks the engine)."""
from __future__ import annotations

import json
from pathlib import Path

from agentm2m.nostr.events import UnsignedEvent
from agentm2m.nostr.outbox import Outbox
from agentm2m.nostr.publisher import NOSTR_KIND_AGENTM2M, NostrEventSink
from agentm2m.nostr.relay import MemoryRelay
from agentm2m.nostr.signer import KeySigner
from agentm2m.nostr.verifier import verify_event
from agentm2m.observability.emitter import Emitter
from agentm2m.observability.events import AgentM2MEvent

ENGINE = KeySigner.generate()
ARCHITECT = KeySigner.generate()


class Clock:
    def __init__(self) -> None:
        self.t = 1_760_000_000.0

    def __call__(self) -> float:
        return self.t


def _nev(i: int = 0):
    return ENGINE.sign(UnsignedEvent(created_at=1000 + i, kind=NOSTR_KIND_AGENTM2M, tags=[], content=str(i))).to_dict()


def test_event_written_to_outbox(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.jsonl")
    ob.add(_nev())
    assert ob.depth() == 1
    assert (tmp_path / "outbox.jsonl").read_text().strip()


def test_successful_publish_removes_outbox_item(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.jsonl")
    d = _nev()
    ob.add(d)
    ob.ack(d["id"])
    assert ob.depth() == 0


def test_failed_publish_retained_with_backoff(tmp_path: Path):
    clock = Clock()
    ob = Outbox(tmp_path / "outbox.jsonl", clock=clock, base_backoff=10)
    d = _nev()
    ob.add(d)
    ob.fail(d["id"], "down")
    assert ob.depth() == 1
    assert ob.due() == []
    clock.t += 11
    assert [r["id"] for r in ob.due()] == [d["id"]]
    ob.fail(d["id"], "down")
    clock.t += 11
    assert ob.due() == []  # backoff doubled to 20s
    clock.t += 10
    assert len(ob.due()) == 1


def test_duplicate_safe(tmp_path: Path):
    ob = Outbox(tmp_path / "outbox.jsonl")
    d = _nev()
    ob.add(d)
    ob.add(d)
    assert ob.depth() == 1


def test_restart_recovers_outbox(tmp_path: Path):
    p = tmp_path / "outbox.jsonl"
    ob = Outbox(p)
    a, b = _nev(1), _nev(2)
    ob.add(a)
    ob.add(b)
    ob.ack(a["id"])
    again = Outbox(p)
    assert [r["id"] for r in again.pending()] == [b["id"]]


def test_compaction_keeps_only_pending(tmp_path: Path):
    p = tmp_path / "outbox.jsonl"
    ob = Outbox(p)
    for i in range(5):
        d = _nev(i)
        ob.add(d)
        if i < 4:
            ob.ack(d["id"])
    ob.compact()
    assert len(p.read_text().splitlines()) == 1
    assert Outbox(p).depth() == 1


def _envelope(event_type="binding.accepted", agent="Architect", seq=1):
    return AgentM2MEvent(event_type=event_type, seq=seq, ts=1_760_000_123.9, workspace_id="devteam-abc",
                         run_id="run-1", agent_id=agent, handoff_id="Req2Arch", rule_id="Story2Operation",
                         target_key="Story2Operation::op::s=UserStory#S1", binding="signature",
                         context_ids=("security-review",), payload={"status": "accepted"})


def _sink(tmp_path: Path, relay: MemoryRelay, clock=None):
    signers = {"Architect": ARCHITECT}
    return NostrEventSink(
        relay,
        Outbox(tmp_path / "outbox.jsonl", clock=clock or Clock()),
        signer_for=lambda agent: signers.get(agent, ENGINE),
        pubkey_for=lambda agent: {"Architect": ARCHITECT.pubkey, "Tester": "cc" * 32}.get(agent),
        clock=clock or Clock(),
    )


def test_nostr_sink_publishes_signed_tagged_event(tmp_path: Path):
    relay = MemoryRelay()
    sink = _sink(tmp_path, relay)
    res = sink.publish(_envelope())
    assert res.ok
    (stored,) = relay.query([{"kinds": [NOSTR_KIND_AGENTM2M], "#t": ["agentm2m-run-run-1"]}])
    ev = verify_event(stored)
    assert ev.pubkey == ARCHITECT.pubkey  # signed by the acting agent's own key
    assert ev.created_at == 1_760_000_123
    tags = {(t[0], t[1]) for t in ev.tags}
    for expected in [("t", "agentm2m"), ("type", "binding.accepted"), ("workspace", "devteam-abc"), ("run", "run-1"),
                     ("agent", "Architect"), ("handoff", "Req2Arch"), ("rule", "Story2Operation"),
                     ("target", "Story2Operation::op::s=UserStory#S1"), ("binding", "signature"),
                     ("context", "security-review"), ("p", ARCHITECT.pubkey)]:
        assert expected in tags
    assert ev.first_tag("alt").startswith("AgentM2M")
    assert AgentM2MEvent.from_dict(json.loads(ev.content)) == _envelope()
    assert sink.outbox.depth() == 0


def test_engine_key_signs_for_agents_without_local_key(tmp_path: Path):
    relay = MemoryRelay()
    _sink(tmp_path, relay).publish(_envelope(agent="Tester"))
    ev = verify_event(relay.query([{}])[0])
    assert ev.pubkey == ENGINE.pubkey and ev.first_tag("p") == "cc" * 32


def test_failed_publish_goes_to_outbox_and_does_not_block(tmp_path: Path):
    clock = Clock()
    relay = MemoryRelay()
    relay.down = True
    sink = _sink(tmp_path, relay, clock)
    for i in range(50):
        assert not sink.publish(_envelope(seq=i + 1)).ok
    assert relay.publish_attempts == 1  # circuit breaker: one try, then straight to the outbox
    assert sink.outbox.depth() == 50
    relay.down = False
    clock.t += 3600
    report = sink.flush()
    assert report == {"published": 50, "failed": 0, "remaining": 0}
    assert len(relay.query([{}])) == 50


def test_retry_after_partial_outage(tmp_path: Path):
    clock = Clock()
    relay = MemoryRelay()
    sink = _sink(tmp_path, relay, clock)
    sink.publish(_envelope(seq=1))
    relay.timeout = True
    sink.publish(_envelope(seq=2))
    relay.timeout = False
    assert sink.flush()["remaining"] == 1  # still backing off
    clock.t += 3600
    assert sink.flush() == {"published": 1, "failed": 0, "remaining": 0}


def test_outbox_flush_is_duplicate_safe(tmp_path: Path):
    clock = Clock()
    relay = MemoryRelay()
    sink = _sink(tmp_path, relay, clock)
    sink.publish(_envelope())
    (stored,) = relay.query([{}])
    sink.outbox.add(stored)  # e.g. crashed after publish, before ack
    clock.t += 3600
    sink.flush()
    assert len(relay.query([{}])) == 1 and sink.outbox.depth() == 0


def test_emitter_with_nostr_sink_never_raises(tmp_path: Path):
    relay = MemoryRelay()
    relay.down = True
    em = Emitter(_sink(tmp_path, relay), workspace_id="w")
    em.begin_run("run")
    for _ in range(3):
        em.emit("team.started")
    assert em.errors == 0  # a down relay is not an emitter error: it is an outbox item
