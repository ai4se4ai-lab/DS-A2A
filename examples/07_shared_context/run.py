#!/usr/bin/env python3
"""Shared context + HOT: a Security Reviewer joins the DevTeam at runtime
and publishes findings that the Developer and Tester consume.

    python examples/07_shared_context/run.py --llm mock [--dir /tmp/ctx-demo]

Hand-offs stay structurally deterministic (Analyst -> Architect ->
Developer, Analyst -> Tester); collaboration *knowledge* flows through a
versioned, access-controlled shared context:

    SecurityReviewer --context_update--> security-review vN
                                            |--> Developer  CodeEdit.body  (@llm ..., context=[...])
                                            '--> Tester     TestCase.oracle

The context's content digest joins those bindings' version stamps, so a
new finding obliges exactly them -- the ordinary obligation mechanism, no
second invalidation system -- and influence_query traces a finding to the
artifacts it shaped.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from agentm2m.llm.mock_backend import MockBackend
from agentm2m.workspace import Workspace


def _workspace(project: Path, llm_name: str) -> Workspace:
    if llm_name == "mock":
        return Workspace(project, llm=MockBackend(), max_resamples=3)
    return Workspace(project, backend=llm_name, max_resamples=3)


def run_demo(project: Path, llm_name: str = "mock", *, quiet: bool = False) -> dict:
    def say(*a):
        if not quiet:
            print(*a)

    ws = _workspace(project, llm_name)
    ws.init("devteam", force=True)
    r = ws.run()
    say(f"1. DevTeam run: phi={r['phi']}")

    # HOT: add the Security Reviewer while the team is running.
    view_spec = yaml.safe_load((ws.dir / "rules/extra/SecurityReviewer.view.yaml").read_text())
    ws.evolve("SecurityReviewer", "Sec", view_spec, "Arch2Sec", "rules/extra/Arch2Sec.agentm2m")
    r = ws.run()
    say(f"2. Security Reviewer added by HOT and run: phi={r['phi']}")

    # Declare the shared context and switch Developer/Tester bindings to read it.
    spec = yaml.safe_load(ws.spec_path.read_text())
    spec["contexts"] = yaml.safe_load((ws.dir / "rules/extra/shared-context.yaml").read_text())["contexts"]
    for h in spec["handoffs"]:
        if h["name"] in ("Arch2Code", "Req2Test"):
            h["rule"] = f"rules/extra/{h['name']}.ctx.agentm2m"
    ws.spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))
    ws = _workspace(project, llm_name)
    say(f"3. Context declared; validate ok={ws.validate()['ok']}")

    # The Security Reviewer publishes findings from its own reviews.
    reviews = ws.show("Sec")["model"].get("reviews", [])
    items = [
        {"id": f"finding-{rv['name']}", "type": "security-finding",
         "content": {"operation": rv["name"], "risk": rv.get("risk"), "notes": rv.get("notes")},
         "provenance": {"element": f"Sec:{rv['key']}", "view": "Sec"}}
        for rv in reviews
    ]
    cur = ws.context_get("security-review", as_agent="SecurityReviewer")
    up = ws.context_update("security-review", as_agent="SecurityReviewer",
                           expected_version=cur["version"], items=items)
    say(f"4. SecurityReviewer published security-review v{up['version']} ({len(items)} findings); "
        f"obliges {len(up['affected_bindings'])} bindings")

    r = ws.run()
    consumed = sorted({(o["agent"], o["binding"]) for o in r["obligations_discharged"]})
    say(f"5. Run: phi={r['phi']}; re-derived with context: {consumed}")

    first = items[0]["id"] if items else None
    inf = ws.influence_query("security-review", item_id=first)
    say(f"6. influence_query({first}): {len(inf['direct'])} decisions, {len(inf['downstream'])} downstream links")
    for d in inf["direct"]:
        say(f"     {d['agent']:<10} {d['element']}.{d['binding']} (pinned v{d['pinned_version']})")

    # A tightened finding obliges exactly the context consumers.
    cur = ws.context_get("security-review", as_agent="SecurityReviewer")
    up2 = ws.context_update("security-review", as_agent="SecurityReviewer", expected_version=cur["version"],
                            items=[{"id": "finding-auth", "type": "security-finding",
                                    "content": "every endpoint requires an authenticated caller"}])
    impact = ws.impact()
    agents = sorted({o["agent"] for o in impact["obligations"]})
    say(f"7. New finding -> v{up2['version']}: {len(impact['obligations'])} obligations for {agents}")
    r = ws.run()
    say(f"8. Run: phi={r['phi']}")

    timeline = ws.events(limit=100000)["events"]
    reads = sum(1 for e in timeline if e["event_type"] == "context.read")
    say(f"9. Timeline: {len(timeline)} events, {reads} context reads (content never logged)")
    return {
        "phi": r["phi"],
        "findings": len(items),
        "affected_first": len(up["affected_bindings"]),
        "consumers": consumed,
        "influence": inf,
        "second_update_obligations": impact["obligations"],
        "context_reads": reads,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--llm", default="mock", help="mock|ollama|openai|anthropic (default: mock)")
    ap.add_argument("--dir", default=None, help="project directory (default: a temporary one)")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args()
    project = Path(args.dir) if args.dir else Path(tempfile.mkdtemp(prefix="agentm2m-ctx-"))
    project.mkdir(parents=True, exist_ok=True)
    out = run_demo(project, args.llm, quiet=args.json)
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    return 0 if out["phi"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
