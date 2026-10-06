"""Phase 22: performance budgets (marked slow; run with `pytest -m slow`).

Wall-clock comparisons of two runs are too noisy for a CI budget, so the
observability overhead is measured directly: the time spent inside
`Emitter.emit` as a fraction of the instrumented run.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from agentm2m.engine.binding import stamp_for
from agentm2m.llm.mock_backend import MockBackend
from agentm2m.nostr.events import UnsignedEvent
from agentm2m.nostr.outbox import Outbox
from agentm2m.nostr.publisher import NostrEventSink
from agentm2m.nostr.relay import MemoryRelay
from agentm2m.nostr.signer import KeySigner
from agentm2m.observability.emitter import Emitter
from agentm2m.observability.events import AgentM2MEvent
from tests.context._fixtures import FINDING, make_ctx_workspace, publish

pytestmark = pytest.mark.slow

N_STORIES = 200


def _big_team(project: Path, **kw):
    ws = make_ctx_workspace(project, llm=MockBackend(), **kw)
    publish(ws, FINDING)
    ops = [{"op": "create", "feature": "stories", "value": {
        "id": f"P{i}", "title": f"story {i}", "status": "accepted", "epic": "Epic#E1",
        "criteria": [{"id": f"P{i}.1", "text": f"criterion {i}"}]}} for i in range(N_STORIES)]
    ws.edit("Req", ops, "Analyst")
    return ws


def test_instrumentation_overhead_under_10_percent(tmp_path: Path, monkeypatch):
    spent = [0.0]
    original = Emitter.emit

    def timed(self, *a, **kw):
        t0 = time.perf_counter()
        try:
            return original(self, *a, **kw)
        finally:
            spent[0] += time.perf_counter() - t0

    monkeypatch.setattr(Emitter, "emit", timed)
    ws = _big_team(tmp_path)
    spent[0] = 0.0
    t0 = time.perf_counter()
    r = ws.run()
    total = time.perf_counter() - t0
    assert r["phi"] is True
    events = len(ws.events(run_id=r["run_id"], limit=10**6)["events"])
    assert events > 5 * N_STORIES
    share = spent[0] / total
    print(f"\n{events} events, emit {spent[0] * 1000:.0f} ms of {total * 1000:.0f} ms run ({share:.1%})")
    assert share < 0.10


def test_outbox_flush_throughput(tmp_path: Path):
    relay = MemoryRelay()
    relay.down = True
    key = KeySigner.generate()
    sink = NostrEventSink(relay, Outbox(tmp_path / "outbox.jsonl"), signer_for=lambda _a: key)
    n = 1000
    for i in range(n):
        sink.publish(AgentM2MEvent(event_type="binding.accepted", seq=i + 1, ts=1_760_000_000.0 + i,
                                   workspace_id="w", run_id="r"))
    assert sink.outbox.depth() == n
    relay.down = False
    t0 = time.perf_counter()
    out = sink.flush(force=True)
    dt = time.perf_counter() - t0
    print(f"\nflushed {n} events in {dt * 1000:.0f} ms ({n / dt:.0f}/s)")
    assert out == {"published": n, "failed": 0, "remaining": 0}
    assert dt < 10.0


def test_signing_cost():
    key = KeySigner.generate()
    ev = UnsignedEvent(created_at=1, kind=4930, tags=[["t", "agentm2m"]], content="x" * 2000)
    t0 = time.perf_counter()
    for _ in range(500):
        key.sign(ev)
    per = (time.perf_counter() - t0) / 500
    assert per < 0.005


def test_context_resolution_cost_per_binding(tmp_path: Path):
    ws = _big_team(tmp_path)
    loaded = ws._require()
    module = loaded.runtime.module_for("Arch2Code")
    rule = module.rules[0]
    (b,) = [x for x in rule.to_clause.patterns[0].bindings if hasattr(x, "context_refs")]
    from agentm2m.engine.helpers_loader import load_helpers
    from agentm2m.engine.matcher import compute_matches

    helpers = load_helpers(module.uses, loaded.team.handoffs["Arch2Code"].rule_path.parent)
    matches = compute_matches(rule, {"Arch": loaded.team.roots["Arch"]}, helpers)
    assert len(matches) > N_STORIES
    t0 = time.perf_counter()
    for m in matches:
        stamp_for(b, m, helpers, ws.context_resolver, "Developer")
    per = (time.perf_counter() - t0) / len(matches)
    print(f"\ncontext-aware stamp: {per * 1e6:.0f} us per binding")
    assert per < 0.005
