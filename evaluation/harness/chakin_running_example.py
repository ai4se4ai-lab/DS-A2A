"""The running example of the follow-up study (Sections II-III of
docs/follow-up-study-DS-A2A.tex), on DevBench `chakin`.

    python -m evaluation.harness.chakin_running_example [--out DIR]

It reuses the pilot team and the scripted LLM of
`chakin_context_walkthrough`, but with the paper's *item-level* pins: the
Developer's `body` pins `finding-path` and `finding-tls`, the Tester's
`oracle` pins `finding-path` only. It then applies the change scenarios of
the paper and records, per step, the new context version, the LLM calls
made, and every obligation with its agent and recorded cause:

    pin    install the pinned rules after the first findings are published
    S1     revise finding-path
    S2     revise finding-tls
    S3     add an unrelated item (a style note)
    S4     rewrite finding-tls with identical content
    edit   the Analyst edits the acceptance criterion of `download`
    revoke the team policy drops the Tester as a reader (then restored)
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from agentm2m.context.model import ContextItem, ContextPolicy
from agentm2m.context.resolver import ContextResolver
from agentm2m.context.store import MemoryContextStore
from agentm2m.observability.emitter import Emitter
from agentm2m.observability.sink import MemoryEventSink
from agentm2m.team.hot import TeamChange, apply_hot
from agentm2m.team.runtime import TeamRuntime

from .chakin_context_walkthrough import (
    FINDING_PATH,
    FINDING_PATH_V2,
    FINDING_TLS,
    RULES,
    ScriptedLLM,
    _clock,
    _states,
    _switch_rule,
)
from .devbench_agentm2m_config import build_team_for_task
from .devbench_loader import load_devbench_task
from .devbench_sec_metamodel import build_sec_mm

STYLE_NOTE = ContextItem(id="note-style", type="style-note",
                         content="Prefer pathlib over os.path in new code.")
FINDING_TLS_V2 = FINDING_TLS.with_content(FINDING_TLS.content + " Pin the server certificate.")


def _item_rules(workdir: Path) -> Path:
    rules = workdir / "rules"
    shutil.copytree(RULES, rules, ignore=shutil.ignore_patterns("__pycache__"))
    a2c = rules / "Arch2Code.agentm2m"
    (rules / "Arch2Code.items.agentm2m").write_text(a2c.read_text().replace(
        "op.codeContext()),",
        "op.codeContext(),\n                       context = ['security-review#finding-path',\n                                  'security-review#finding-tls']),"))
    r2t = rules / "Req2Test.agentm2m"
    (rules / "Req2Test.items.agentm2m").write_text(r2t.read_text().replace(
        "c.text),", "c.text,\n                       context = ['security-review#finding-path']),"))
    return rules


def run(out_dir: Path | None = None) -> dict:
    work = Path(tempfile.mkdtemp(prefix="chakin-run-ex-"))
    rules = _item_rules(work)
    team = build_team_for_task(load_devbench_task("chakin"))
    llm = ScriptedLLM()
    sink = MemoryEventSink()
    emitter = Emitter(sink, workspace_id="chakin-running-example", clock=_clock())
    store = MemoryContextStore(clock=_clock())
    rt = TeamRuntime(team, llm, max_resamples=2, max_passes=3, emit=emitter.emit,
                     contexts=ContextResolver(store))
    emitter.begin_run("run")
    rt.run_to_fixpoint()
    sec_mm = build_sec_mm(team.views["Arch"])
    apply_hot(team, TeamChange(agent_name="SecurityReviewer", view=sec_mm, view_root=sec_mm.get("SecModel")(),
                               handoff_name="Arch2Sec", rule_path=rules / "Arch2Sec.agentm2m"))
    emitter.begin_run("run")
    rt.run_to_fixpoint()

    policy = ContextPolicy(owner="SecurityReviewer", writers=frozenset({"SecurityReviewer"}),
                           readers=frozenset({"Developer", "Tester"}))
    store.ensure("security-review", policy, title="Security review findings")
    steps: list[dict] = []

    def step(name: str) -> None:
        llm.prompts.clear()
        mark = len(sink.events)
        emitter.begin_run("run")
        rt.run_to_fixpoint()
        evs = sink.events[mark:]
        obl = [e for e in evs if e.event_type == "obligation.created"]
        steps.append({
            "step": name,
            "context_version": store.snapshot("security-review").version,
            "llm_calls": len(llm.prompts),
            "obligations": dict(Counter(f"{e.agent_id}:{e.binding}:{'+'.join(e.payload.get('cause') or [])}"
                                        for e in obl)),
            "blocked": sorted({s[1] for s in _states(rt) if s[2] == "blocked"}),
            "phi": rt.acceptance_holds(),
        })

    def write(items, remove=None):
        cur = store.snapshot("security-review").version
        return store.update("security-review", as_agent="SecurityReviewer", expected_version=cur,
                            items=items, remove=remove)

    write([FINDING_PATH, FINDING_TLS])
    _switch_rule(rt, "Arch2Code", rules / "Arch2Code.items.agentm2m")
    _switch_rule(rt, "Req2Test", rules / "Req2Test.items.agentm2m")
    step("pin")
    write([FINDING_PATH_V2])
    step("S1")
    write([FINDING_TLS_V2])
    step("S2")
    write([STYLE_NOTE])
    step("S3")
    write([FINDING_TLS_V2])
    step("S4")
    crit = next(c for s in team.roots["Req"].stories for c in s.criteria if c.id.endswith("::download.C1"))
    crit.text = crit.text + " The target directory is created if it does not exist."
    step("edit")
    store.set_policy("security-review", ContextPolicy(owner="SecurityReviewer",
                                                      writers=frozenset({"SecurityReviewer"}),
                                                      readers=frozenset({"Developer"})))
    step("revoke")
    store.set_policy("security-review", policy)
    step("restore")

    pins = []
    for hname, tm in team.traces.items():
        for link in tm.links():
            for b, ps in link.context_pins.items():
                for p in ps:
                    if "finding-path" in p["item_ids"]:
                        pins.append((hname, b, p["version"]))
    report = {"steps": steps, "influence_finding_path": dict(Counter(f"{h}:{b}:v{v}" for h, b, v in pins))}
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "chakin_running_example.json").write_text(json.dumps(report, indent=1))
    shutil.rmtree(work, ignore_errors=True)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="evaluation/results/walkthrough")
    args = ap.parse_args()
    print(json.dumps(run(Path(args.out)), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
