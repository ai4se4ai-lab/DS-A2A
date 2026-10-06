"""Phases 11-12 and 14: context dependencies join the footprint.

    EffectiveFootprint = SourceFootprint + pinned context content
    stamp = H(source footprint, [(context, content digest)...])

so a context change creates ordinary obligations (no second invalidation
system), an unrelated context changes nothing, and a binding that cannot
resolve its context is blocked -- never sampled with invented context.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentm2m.engine.trace import digest
from agentm2m.llm.mock_backend import MockBackend
from agentm2m.workspace import STATE_VERSION, Workspace
from tests._support import fill_all, good_value

from ._fixtures import DECISION, FINDING, make_ctx_workspace, publish

BODY_S2 = "Operation2CodeEdit::ce::op=Operation#op_s2"
SIG_S2 = "Story2Operation::op::s=UserStory#S2"
ORACLE_S21 = "Criterion2TestCase::tc::c=Criterion#S2.1"


def _states(ws: Workspace) -> dict[tuple[str, str], str]:
    return {(s["target_key"], s["binding"]): s["state"] for s in ws.binding_states()}


def _link(ws: Workspace, handoff: str, target_key: str):
    rule, _var, match = target_key.split("::", 2)
    return ws._require().team.traces[handoff].get(rule, match)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    w = make_ctx_workspace(tmp_path)
    publish(w, FINDING)
    return w


def test_declared_contexts_exist_with_policies(ws: Workspace):
    assert ws.contexts.list() == ["product-roadmap", "security-review"]
    assert ws.contexts.authorize("security-review", "Developer")
    assert not ws.contexts.authorize("security-review", "Analyst")


def test_source_only_binding_unchanged(ws: Workspace):
    fill_all(ws)
    link = _link(ws, "Req2Arch", SIG_S2)
    assert "signature" not in link.context_pins
    assert link.to_dict().get("context_pins") is None  # no new keys for context-free bindings


def test_source_and_context_binding_prompt_and_pins(ws: Workspace):
    seen = {}

    def answer(b):
        seen[(b["target_key"], b["binding"])] = b
        return good_value(b)

    fill_all(ws, answer)
    body = seen[(BODY_S2, "body")]
    assert "Authorized shared context" in body["prompt"]
    assert "API accepts unauthenticated requests" in body["prompt"]
    assert "[context security-review v2" in body["prompt"]
    assert body["context"] == [{"context_id": "security-review", "version": 2,
                                "digest": ws.contexts.snapshot("security-review").digest, "item_ids": []}]
    sig = seen[(SIG_S2, "signature")]
    assert "Authorized shared context" not in sig["prompt"] and "context" not in sig


def test_context_digest_in_stamp_and_pins_recorded(ws: Workspace):
    fill_all(ws)
    link = _link(ws, "Arch2Code", BODY_S2)
    (pin,) = link.context_pins["body"]
    assert pin["context_id"] == "security-review" and pin["version"] == 2
    dep = link.dependencies["body"]
    assert dep["effective"] == link.stamps["body"] != dep["source"]
    assert dep["context"]


def test_context_change_creates_obligation(ws: Workspace):
    fill_all(ws)
    assert ws.acceptance()["phi"] is True
    publish(ws, DECISION)
    st = _states(ws)
    stale = {k for k, v in st.items() if v != "fresh"}
    assert stale == {
        (BODY_S2, "body"), ("Operation2CodeEdit::ce::op=Operation#op_s1", "body"),
        (ORACLE_S21, "oracle"), ("Criterion2TestCase::tc::c=Criterion#S1.1", "oracle"),
        ("Criterion2TestCase::tc::c=Criterion#S3.1", "oracle"),
    }
    assert all(st[k] == "stale" for k in stale)
    assert ws.acceptance()["phi"] is False
    obligations = {(o["target_key"], o["binding"]) for o in ws.impact()["obligations"]}
    assert obligations == stale
    fill_all(ws)
    assert ws.acceptance()["phi"] is True
    assert _link(ws, "Arch2Code", BODY_S2).context_pins["body"][0]["version"] == 3


def test_unrelated_context_does_not_invalidate_binding(ws: Workspace):
    fill_all(ws)
    ws.contexts.update("product-roadmap", as_agent="Analyst", expected_version=1,
                       items=[DECISION.with_content("ship CSV export in Q3")])
    assert set(_states(ws).values()) == {"fresh"}
    assert ws.acceptance()["phi"] is True


def test_source_change_still_creates_obligation(ws: Workspace):
    fill_all(ws)
    ws.edit("Req", [{"op": "set", "key": "Criterion#S2.1", "values": {"text": "returns HTTP 409"}}], "Analyst")
    stale = {k for k, v in _states(ws).items() if v != "fresh"}
    assert stale == {(SIG_S2, "signature"), (ORACLE_S21, "oracle"), (BODY_S2, "body")}


def test_identical_context_rewrite_keeps_bindings_fresh(ws: Workspace):
    fill_all(ws)
    publish(ws, FINDING)  # same content: no new version, same digest
    assert set(_states(ws).values()) == {"fresh"}


def test_item_subset_pins_only_selected_items(tmp_path: Path):
    ws = make_ctx_workspace(tmp_path, body_ctx="['security-review#finding-001']", oracle_ctx=None)
    publish(ws, FINDING)
    fill_all(ws)
    publish(ws, DECISION)  # another item in the same context
    assert set(_states(ws).values()) == {"fresh"}
    publish(ws, FINDING.with_content({"finding": "also unauthenticated DELETE"}))
    assert _states(ws)[(BODY_S2, "body")] == "stale"


def test_missing_context_blocks_and_never_invents(tmp_path: Path):
    ws = make_ctx_workspace(tmp_path, body_ctx="['ghost']", oracle_ctx=None)
    fill_all(ws)  # everything else drains
    nb = ws.next_bindings()
    assert nb["bindings"] == [] and nb["blocked_on_upstream"] == 2
    assert _states(ws)[(BODY_S2, "body")] == "blocked"
    assert ws.acceptance()["phi"] is False
    errs = ws.events(event_type="context.error")["events"]
    assert errs and "ghost" in json.dumps(errs[-1])


def test_unauthorized_context_blocks(tmp_path: Path):
    ws = make_ctx_workspace(tmp_path, body_ctx="['product-roadmap']", oracle_ctx=None,
                            contexts={"product-roadmap": {"owner": "Analyst", "readers": ["Tester"]}})
    fill_all(ws)
    assert _states(ws)[(BODY_S2, "body")] == "blocked"
    errs = ws.events(event_type="context.error")["events"]
    assert "may not read" in errs[-1]["payload"]["error"] and "not a reader" in errs[-1]["payload"]["error"]


def test_deleted_context_turns_accepted_binding_blocked(tmp_path: Path):
    from agentm2m.context.model import ContextPolicy

    ws = make_ctx_workspace(tmp_path, body_ctx="['runtime-notes']", oracle_ctx=None)
    ws.contexts.create("runtime-notes", policy=ContextPolicy(owner="Architect", readers=frozenset({"Developer"})),
                       as_agent="Architect", items=[FINDING])
    fill_all(ws)
    assert ws.acceptance()["phi"] is True
    ws.contexts.delete("runtime-notes", as_agent="Architect")
    assert _states(ws)[(BODY_S2, "body")] == "blocked"
    assert ws.acceptance()["phi"] is False


def test_engine_backend_with_context(tmp_path: Path):
    ws = make_ctx_workspace(tmp_path, llm=MockBackend())
    publish(ws, FINDING)
    r = ws.run()
    assert r["escalations"] == [] and ws.acceptance()["phi"] is True
    publish(ws, DECISION)
    r = ws.run()
    discharged = {(o["target_key"], o["binding"]) for o in r["obligations_discharged"]}
    assert {b for _, b in discharged} == {"body", "oracle"}
    assert ws.acceptance()["phi"] is True


def test_engine_backend_missing_context_escalates_without_caching(tmp_path: Path):
    ws = make_ctx_workspace(tmp_path, llm=MockBackend(), body_ctx="['ghost']", oracle_ctx=None)
    r = ws.run()
    esc = [e for e in r["escalations"] if e["binding"] == "body"]
    assert esc and "ghost" in esc[0]["reason"]
    link = _link(ws, "Arch2Code", BODY_S2)
    assert "body" not in link.failed_stamps  # not judged: retried once the context exists


# -- Phase 14: binding-worker protocol ------------------------------------------


def test_next_binding_includes_pinned_context_versions(ws: Workspace):
    fill_all(ws, agent="Architect")
    (b, *_rest) = [x for x in ws.next_bindings(agent="Developer")["bindings"]]
    assert b["context"][0]["version"] == 2


def test_stale_context_submission_rejected(ws: Workspace):
    fill_all(ws, agent="Architect")
    b = next(x for x in ws.next_bindings(agent="Developer")["bindings"] if x["target_key"] == BODY_S2)
    publish(ws, DECISION)  # context v3 lands between prompt and answer
    r = ws.submit_binding(b["target_key"], b["binding"], good_value(b), b["footprint_version"])
    assert r["status"] == "stale"
    b2 = next(x for x in ws.next_bindings(agent="Developer")["bindings"] if x["target_key"] == BODY_S2)
    assert b2["context"][0]["version"] == 3 and "Authentication middleware" in b2["prompt"]
    assert ws.submit_binding(b2["target_key"], "body", good_value(b2), b2["footprint_version"])["status"] == "accepted"


def test_submit_records_context_dependencies(ws: Workspace):
    fill_all(ws)
    link = _link(ws, "Req2Test", ORACLE_S21)
    assert link.context_pins["oracle"][0]["digest"] == ws.contexts.snapshot("security-review").digest


# -- persistence -----------------------------------------------------------------


def test_state_version_and_v1_upgrade(ws: Workspace):
    fill_all(ws)
    state = json.loads(ws.state_path.read_text())
    assert state["version"] == STATE_VERSION == 2
    again = Workspace(ws.project_dir, backend="host", max_resamples=3)
    assert again.acceptance()["phi"] is True
    assert _link(again, "Arch2Code", BODY_S2).context_pins["body"][0]["version"] == 2


def test_stamp_of_context_free_binding_is_plain_digest(ws: Workspace):
    fill_all(ws)
    link = _link(ws, "Req2Arch", SIG_S2)
    assert link.stamps["signature"] == digest(link.footprints["signature"])
