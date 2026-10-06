"""Phase 20: shared context end to end in host mode (the plugin's flow).

Analyst -> Architect -> Developer / Tester run to phi; a Security Reviewer
joins by HOT (no engine special-casing) and reviews the architecture; it
publishes a finding into a shared context; the Developer's and Tester's
bindings consume it pinned to a version; influence_query links the finding
to the code and test artifacts; a new version obliges exactly them.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from agentm2m.workspace import Workspace
from tests._support import fill_all, good_value


def test_security_reviewer_context_flows_to_developer_and_tester(tmp_path: Path):
    ws = Workspace(tmp_path, backend="host", max_resamples=3)
    ws.init("devteam")
    fill_all(ws)
    assert ws.acceptance()["phi"] is True

    # HOT: the Security Reviewer joins through the ordinary team_evolve path
    view_spec = yaml.safe_load((ws.dir / "rules/extra/SecurityReviewer.view.yaml").read_text())
    ws.evolve("SecurityReviewer", "Sec", view_spec, "Arch2Sec", "rules/extra/Arch2Sec.agentm2m")
    fill_all(ws, agent="SecurityReviewer")
    assert ws.acceptance()["phi"] is True

    # declare the context and switch Developer/Tester bindings to read it
    spec = yaml.safe_load(ws.spec_path.read_text())
    spec["contexts"] = yaml.safe_load((ws.dir / "rules/extra/shared-context.yaml").read_text())["contexts"]
    for h in spec["handoffs"]:
        if h["name"] in ("Arch2Code", "Req2Test"):
            h["rule"] = f"rules/extra/{h['name']}.ctx.agentm2m"
    ws.spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))
    ws = Workspace(tmp_path, backend="host", max_resamples=3)
    assert ws.validate()["ok"]

    # SecurityReviewer publishes a finding tied to its review of op_s2
    review = ws.show("Sec", "SecurityReview#op_s2")["element"]
    up = ws.context_update("security-review", as_agent="SecurityReviewer", expected_version=1, items=[{
        "id": "finding-42", "type": "security-finding",
        "content": {"finding": "markTaskDone must reject unauthenticated callers"},
        "provenance": {"element": f"Sec:{review['key']}", "view": "Sec"}}])
    assert up["version"] == 2

    prompts = {}

    def answer(b):
        prompts[(b["target_key"], b["binding"])] = b
        return good_value(b)

    fill_all(ws, answer)
    assert ws.acceptance()["phi"] is True
    consumed = {k for k, b in prompts.items() if "context" in b}
    assert {b for _, b in consumed} == {"body", "oracle"} and len(consumed) == 5
    body = prompts[("Operation2CodeEdit::ce::op=Operation#op_s2", "body")]
    assert "markTaskDone must reject unauthenticated callers" in body["prompt"]
    assert body["context"][0]["version"] == 2

    # influence: finding -> Developer code body + Tester oracles
    inf = ws.influence_query("security-review", item_id="finding-42")
    elements = {(d["agent"], d["element"], d["binding"]) for d in inf["direct"]}
    assert ("Developer", "Code:CodeEdit#op_s2", "body") in elements
    assert ("Tester", "Test:TestCase#S2.1", "oracle") in elements
    assert inf["item"]["provenance"]["element"] == "Sec:SecurityReview#op_s2"
    assert inf["item"]["author"] == "SecurityReviewer"

    # observable information flow: who read which version, never the content
    timeline = ws.events(limit=100000)["events"]
    kinds = [e["event_type"] for e in timeline]
    for t in ["team.evolved", "context.updated", "context.read", "binding.requested", "binding.accepted"]:
        assert t in kinds, t
    reads = [e for e in timeline if e["event_type"] == "context.read"]
    assert {e["agent_id"] for e in reads} == {"Developer", "Tester"}
    assert "markTaskDone must reject" not in json.dumps(timeline)

    # a new version obliges exactly the context consumers
    up = ws.context_update("security-review", as_agent="SecurityReviewer", expected_version=2, items=[{
        "id": "finding-43", "type": "security-finding", "content": "rate-limit login"}])
    affected = {(a["agent"], a["binding"]) for a in up["affected_bindings"]}
    assert affected == {("Developer", "body"), ("Tester", "oracle")}
    open_now = {(s["agent"], s["binding"]) for s in ws.binding_states() if s["state"] != "fresh"}
    assert open_now == affected
    fill_all(ws)
    assert ws.acceptance()["phi"] is True
    assert all(d["pinned_version"] == 3 for d in ws.influence_query("security-review")["direct"])
