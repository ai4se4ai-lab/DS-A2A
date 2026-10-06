"""A devteam workspace whose Developer and Tester bindings read a shared
security-review context (owned by the Architect here; by the evolved
SecurityReviewer in the end-to-end tests)."""
from __future__ import annotations

from pathlib import Path

import yaml

from agentm2m.context.model import ContextItem
from agentm2m.workspace import Workspace

FINDING = ContextItem(id="finding-001", type="security-finding",
                      content={"finding": "API accepts unauthenticated requests"})
DECISION = ContextItem(id="decision-002", type="design-decision",
                       content="Authentication middleware must run before routing")

CONTEXTS = {
    "security-review": {"owner": "Architect", "readers": ["Developer", "Tester"], "title": "Security review"},
    "product-roadmap": {"owner": "Analyst", "readers": ["Developer", "Tester"]},
}


def make_ctx_workspace(project: Path, *, body_ctx: str = "['security-review']",
                       oracle_ctx: str | None = "['security-review']", contexts: dict | None = None,
                       **ws_kwargs) -> Workspace:
    ws_kwargs.setdefault("max_resamples", 3)
    if "llm" not in ws_kwargs:
        ws_kwargs.setdefault("backend", "host")
    ws = Workspace(project, **ws_kwargs)
    ws.init("devteam")
    spec = yaml.safe_load(ws.spec_path.read_text())
    spec["contexts"] = contexts if contexts is not None else CONTEXTS
    ws.spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))
    a2c = ws.dir / "rules/Arch2Code.agentm2m"
    a2c.write_text(a2c.read_text().replace("op.implFootprint()),", f"op.implFootprint(), context={body_ctx}),"))
    if oracle_ctx:
        r2t = ws.dir / "rules/Req2Test.agentm2m"
        r2t.write_text(r2t.read_text().replace("c.text),", f"c.text, context={oracle_ctx}),"))
    fresh = Workspace(project, **ws_kwargs)
    fresh._require()
    return fresh


def publish(ws: Workspace, *items: ContextItem, context_id: str = "security-review", as_agent: str = "Architect"):
    ws._require()  # declared contexts are synced into the store on load
    cur = ws.contexts.snapshot(context_id)
    return ws.contexts.update(context_id, as_agent=as_agent, expected_version=cur.version, items=list(items))
