"""Phase 23: metrics computed from the event timeline (spec sections 71-72)."""
from __future__ import annotations

from pathlib import Path

from agentm2m.llm.mock_backend import MockBackend
from agentm2m.observability.metrics import compute_metrics, observability_coverage
from tests._support import fill_all
from tests.context._fixtures import DECISION, FINDING, make_ctx_workspace, publish


def test_metrics_from_engine_run(tmp_path: Path):
    ws = make_ctx_workspace(tmp_path, llm=MockBackend())
    publish(ws, FINDING)
    r1 = ws.run()
    ws.context_update("security-review", as_agent="Architect", expected_version=2, items=[DECISION.to_dict()])
    r2 = ws.run()
    m = compute_metrics(ws.events(limit=10**6)["events"])
    assert m["runs"] >= 2 and m["run_duration_s"]["total"] >= 0
    assert m["bindings_accepted"] == 7 + 5  # first run + the 5 context consumers again
    assert m["llm_calls"] >= m["bindings_accepted"] and m["input_tokens"] > 0
    assert m["escalations"] == 0 and m["rejections"] >= 0
    assert set(m["handoff_duration_s"]) == {"Req2Arch", "Arch2Code", "Req2Test"}
    assert m["context_writes"] == 2 and m["context_reads"] >= 10
    assert m["context_versions"] == {"security-review": 3}
    assert m["context_induced_obligations"] == 5
    assert m["obligations_created"] >= 5
    reuse = m["context_reuse"]["security-review"]
    assert reuse["agents"] == ["Developer", "Tester"] and reuse["bindings"] == 5
    assert m["context_amplification"]["security-review@3"] == 5
    assert m["collaboration_latency_s"]["security-review@3"] >= 0
    for r in (r1, r2):
        cov = observability_coverage(r, ws.events(run_id=r["run_id"], limit=10**6)["events"])
        assert cov["coverage"] == 1.0, cov


def test_metrics_host_mode_and_dedup(tmp_path: Path):
    ws = make_ctx_workspace(tmp_path)
    publish(ws, FINDING)
    fill_all(ws)
    ws.next_bindings()
    publish(ws, DECISION)
    ws.next_bindings()
    ws.next_bindings()  # re-reporting the same open obligations must not inflate the count
    m = compute_metrics(ws.events(limit=10**6)["events"])
    assert m["context_induced_obligations"] == 5
    assert m["obligations_created"] == 5
    assert m["llm_calls"] == 0  # host mode: the engine never called an LLM
    assert m["host_submissions"] == 7
    # host-submitted values report the context versions they were derived from, so the
    # context metrics are not blind in host mode
    assert m["context_reuse"]["security-review"]["bindings"] == 5
    assert m["context_amplification"]["security-review@2"] == 5


def test_metrics_empty():
    m = compute_metrics([])
    assert m["runs"] == 0 and m["bindings_accepted"] == 0 and m["context_reuse"] == {}
