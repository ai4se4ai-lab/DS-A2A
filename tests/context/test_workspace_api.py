"""Phase 15: the workspace collaboration API (what MCP and the CLI expose)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentm2m.workspace import Workspace, WorkspaceError
from tests._support import fill_all

from ._fixtures import make_ctx_workspace

FINDING = {"id": "finding-001", "type": "security-finding", "content": {"finding": "API accepts unauthenticated requests"}}
DECISION = {"id": "decision-002", "type": "design-decision", "content": "Authentication middleware before routing"}


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return make_ctx_workspace(tmp_path)


def test_context_update_reports_affected_bindings(ws: Workspace):
    r = ws.context_update("security-review", as_agent="Architect", expected_version=1, items=[FINDING])
    assert r["version"] == 2 and r["changed"] is True
    fill_all(ws)
    r = ws.context_update("security-review", as_agent="Architect", expected_version=2, items=[DECISION])
    assert r["version"] == 3
    assert {(a["binding"]) for a in r["affected_bindings"]} == {"body", "oracle"}
    assert len(r["affected_bindings"]) == 5


def test_context_update_conflict_and_denied(ws: Workspace):
    ws.context_update("security-review", as_agent="Architect", expected_version=1, items=[FINDING])
    with pytest.raises(WorkspaceError, match="CONTEXT_CONFLICT"):
        ws.context_update("security-review", as_agent="Architect", expected_version=1, items=[DECISION])
    with pytest.raises(WorkspaceError, match="may not write"):
        ws.context_update("security-review", as_agent="Developer", expected_version=2, items=[DECISION])
    with pytest.raises(WorkspaceError, match="unknown agent"):
        ws.context_update("security-review", as_agent="Ghost", expected_version=2, items=[DECISION])


def test_context_get_requires_read_rights_and_is_observed(ws: Workspace):
    ws.context_update("security-review", as_agent="Architect", expected_version=1, items=[FINDING])
    got = ws.context_get("security-review", as_agent="Developer")
    assert got["version"] == 2 and got["items"][0]["content"]["finding"].startswith("API")
    assert ws.context_get("security-review", as_agent="Tester", version=1)["items"] == []
    with pytest.raises(WorkspaceError, match="may not read"):
        ws.context_get("security-review", as_agent="Analyst")
    reads = ws.events(event_type="context.read")["events"]
    assert reads[-1]["agent_id"] == "Tester" and reads[-1]["payload"]["purpose"] == "agent-request"
    assert "API accepts" not in str(reads)


def test_context_create_list_attach_detach(ws: Workspace):
    r = ws.context_create("test-notes", as_agent="Tester", title="Flaky tests", readers=["Developer"],
                          items=[{"id": "n1", "type": "note", "content": "S2 oracle is timing sensitive"}])
    assert r["version"] == 1
    listing = {c["context_id"]: c for c in ws.context_list(as_agent="Analyst")["contexts"]}
    assert set(listing) == {"product-roadmap", "security-review", "test-notes"}
    assert listing["test-notes"]["can_read"] is False and "items" not in listing["test-notes"]
    ws.context_attach("test-notes", agent="Analyst", as_agent="Tester")
    assert ws.context_get("test-notes", as_agent="Analyst")["version"] == 1
    ws.context_detach("test-notes", agent="Analyst", as_agent="Tester")
    with pytest.raises(WorkspaceError):
        ws.context_get("test-notes", as_agent="Analyst")
    with pytest.raises(WorkspaceError, match="owner"):
        ws.context_attach("test-notes", agent="Analyst", as_agent="Developer")
    types = [e["event_type"] for e in ws.events()["events"]]
    assert "context.created" in types and "context.attached" in types and "context.detached" in types


def test_context_create_rejects_declared_and_bad_input(ws: Workspace):
    with pytest.raises(WorkspaceError, match="already exists"):
        ws.context_create("security-review", as_agent="Architect")
    with pytest.raises(WorkspaceError):
        ws.context_create("bad id", as_agent="Architect")
    with pytest.raises(WorkspaceError):
        ws.context_create("x", as_agent="Architect", items=[{"id": "a", "type": "", "content": "x"}])


def test_context_search_only_readable(ws: Workspace):
    ws.context_update("security-review", as_agent="Architect", expected_version=1, items=[FINDING, DECISION])
    hits = ws.context_search("authentication", as_agent="Developer")["hits"]
    assert {h["item_id"] for h in hits} == {"finding-001", "decision-002"}
    assert ws.context_search("authentication", as_agent="Analyst")["hits"] == []
    assert [h["item_id"] for h in ws.context_search("x", as_agent="Developer", type="design-decision")["hits"]] in (
        [], ["decision-002"])


def test_context_status_counts_consumers(ws: Workspace):
    ws.context_update("security-review", as_agent="Architect", expected_version=1, items=[FINDING])
    fill_all(ws)
    st = {c["context_id"]: c for c in ws.context_status()["contexts"]}
    assert st["security-review"]["consumers"] == 5 and st["security-review"]["stale_consumers"] == 0
    assert st["product-roadmap"]["consumers"] == 0
    assert st["security-review"]["declared_in"] == "team.yaml"


def test_influence_query_follows_traces(ws: Workspace):
    ws.context_update("security-review", as_agent="Architect", expected_version=1, items=[FINDING])
    fill_all(ws)
    inf = ws.influence_query("security-review", item_id="finding-001")
    direct = {(d["handoff"], d["binding"]) for d in inf["direct"]}
    assert direct == {("Arch2Code", "body"), ("Req2Test", "oracle")}
    assert all(d["pinned_version"] == 2 and d["current"] for d in inf["direct"])
    assert inf["item"]["type"] == "security-finding"
    assert ws.influence_query("security-review", item_id="ghost")["direct"] == []
    assert ws.influence_query("product-roadmap")["direct"] == []


def test_validate_checks_context_references(tmp_path: Path):
    ws = make_ctx_workspace(tmp_path, body_ctx="['ghost']", oracle_ctx="['product-roadmap']",
                            contexts={"product-roadmap": {"owner": "Analyst", "readers": ["Developer"]}})
    res = ws.validate()
    assert not res["ok"]
    joined = " ".join(res["errors"])
    assert "ghost" in joined and "Tester" in joined  # undeclared context; Tester is not a reader


def test_validate_warns_about_unknown_policy_agents(tmp_path: Path):
    ws = make_ctx_workspace(tmp_path, contexts={
        "security-review": {"owner": "SecurityReviewer", "readers": ["Developer", "Tester"]},
        "product-roadmap": {"owner": "Analyst"}})
    res = ws.validate()
    assert res["ok"], res["errors"]
    assert any("SecurityReviewer" in w for w in res["warnings"])


def test_status_lists_contexts(ws: Workspace):
    st = ws.status()
    assert {c["context_id"] for c in st["contexts"]} == {"product-roadmap", "security-review"}
