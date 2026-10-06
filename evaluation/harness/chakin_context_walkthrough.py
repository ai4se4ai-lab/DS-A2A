"""Shared context and observability on DevBench `chakin`: the run behind the
"Shared context" and "Observability" sections of docs/AgentM2M-explained.tex.

    python -m evaluation.harness.chakin_context_walkthrough [--out DIR]

It continues the walkthrough's story on the real pilot team
(build_team_for_task + devbench_rules): run the team, add the Security
Reviewer by HOT, declare a `security-review` shared context, switch the
Developer's and Tester's rules to context-aware variants, publish a finding,
revise it, and show what happens when the Tester loses read access. Every
prompt, trace link, pin, stamp, obligation cause and event it prints is real
engine output. The LLM is scripted (deterministic answers keyed by the
prompt), like the walkthrough's other listings, so keys and stamps are
reproducible; a fixed clock and a fixed demonstration key make the event
timeline and the signed Nostr event reproducible too.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from pathlib import Path

from agentm2m.context.model import ContextItem, ContextPolicy
from agentm2m.context.resolver import ContextResolver
from agentm2m.context.store import MemoryContextStore
from agentm2m.llm.base import LLMBackend
from agentm2m.nostr.outbox import Outbox
from agentm2m.nostr.publisher import NostrEventSink
from agentm2m.nostr.relay import MemoryRelay
from agentm2m.nostr.signer import KeySigner
from agentm2m.nostr.verifier import verify_event
from agentm2m.observability.emitter import Emitter
from agentm2m.observability.metrics import compute_metrics
from agentm2m.observability.presence import derive_presence
from agentm2m.observability.sink import CompositeSink, MemoryEventSink
from agentm2m.team.hot import TeamChange, apply_hot
from agentm2m.team.runtime import TeamRuntime

from .devbench_agentm2m_config import build_team_for_task
from .devbench_loader import load_devbench_task
from .devbench_sec_metamodel import build_sec_mm

RULES = Path(__file__).resolve().parent / "devbench_rules"

# The walkthrough's logged qwen3-coder:30b signatures (Sec. "Step 4").
SIGNATURES = {
    "load_datasets": "load_datasets() -> dict",
    "download": "download(number: int, name: str, save_dir: str) -> None",
    "search": "search(lang: str) -> Any",
    "test_download_by_name": "test_download_by_name(self, name) -> bool",
    "canary_checksum_1": "canary_checksum_1(payload: str) -> str",
    "canary_threshold_2": "canary_threshold_2(value: int) -> bool",
    "canary_format_3": "canary_format_3(name: str, count: int) -> str",
    "canary_merge_4": "canary_merge_4(a: list, b: list) -> list",
    "canary_flag_5": "canary_flag_5(text: str) -> str",
}

FINDING_PATH = ContextItem(
    id="finding-path", type="security-finding",
    content="download(): `save_dir` comes from the caller. Resolve the target path and refuse "
            "to write outside save_dir (no '..', no absolute file names).",
    provenance={"trace": "Arch2Sec::Operation2Review::op=Operation#Global_functions::download",
                "view": "Sec"},
)
FINDING_TLS = ContextItem(
    id="finding-tls", type="security-finding",
    content="download(): fetch vectors over https only; never fall back to http.",
    provenance={"trace": "Arch2Sec::Operation2Review::op=Operation#Global_functions::download",
                "view": "Sec"},
)
FINDING_PATH_V2 = FINDING_PATH.with_content(
    FINDING_PATH.content + " Also reject a `name` that contains a path separator.")

# BIP-340 test-vector secret key 3: a public, well-known key, used ONLY so the
# signed event in the document is reproducible. Never use it for real.
DEMO_KEY = "0000000000000000000000000000000000000000000000000000000000000003"


class ScriptedLLM(LLMBackend):
    """Deterministic answers keyed by the prompt. A body or oracle that was
    given authorized shared context cites each finding id in a comment (what a
    model that follows its context does), so the effect of context is visible."""

    name = "scripted"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, temperature: float = 0.2) -> str:
        self.prompts.append(prompt)
        findings = re.findall(r"^- (finding-[a-z]+) \(", prompt, flags=re.MULTILINE)
        cite = "".join(f"    # honours {f}\n" for f in findings)
        if prompt.startswith("Derive a precise Python signature"):
            m = re.search(r"Implement `([A-Za-z_0-9]+)\(([^)]*)\)", prompt)
            name = m.group(1) if m else "op"
            return SIGNATURES.get(name, f"{name}({m.group(2) if m else ''}) -> str")
        if prompt.startswith("Implement this operation"):
            m = re.search(r"Signature: ([A-Za-z_0-9]+)\(", prompt)
            name = m.group(1) if m else "op"
            return f"def {name}(*args, **kwargs):\n{cite}    return None\n"
        if prompt.startswith("Write a test oracle"):
            return f"def test_oracle():\n{cite}    assert implementation() is not None\n"
        if prompt.startswith("Summarize any authentication"):
            if "save_dir" in prompt:
                return ("download writes into a caller-supplied save_dir and fetches from the "
                        "network: path traversal and plain-http download are the risks.")
            return "No authentication or authorization concerns for a read-only lookup."
        if prompt.startswith("Rate the security risk"):
            return "high" if "save_dir" in prompt else "low"
        return "ok"


def _clock():
    t = [1_760_000_000.0]

    def tick() -> float:
        t[0] += 0.25
        return t[0]

    return tick


def _context_rules(workdir: Path) -> Path:
    """The pilot rules, plus context-aware variants of Arch2Code and Req2Test."""
    rules = workdir / "rules"
    shutil.copytree(RULES, rules, ignore=shutil.ignore_patterns("__pycache__"))
    a2c = rules / "Arch2Code.agentm2m"
    (rules / "Arch2Code.ctx.agentm2m").write_text(a2c.read_text().replace(
        "op.codeContext()),", "op.codeContext(),\n                       context = ['security-review']),"))
    r2t = rules / "Req2Test.agentm2m"
    (rules / "Req2Test.ctx.agentm2m").write_text(r2t.read_text().replace(
        "c.text),", "c.text,\n                       context = ['security-review#finding-path']),"))
    return rules


def _switch_rule(rt: TeamRuntime, handoff: str, path: Path) -> None:
    rt.team.handoffs[handoff].rule_path = path
    rt._modules.pop(handoff, None)


def _link(team, handoff: str, rule: str, match_key: str):
    return team.traces[handoff].get(rule, match_key)


def _states(rt: TeamRuntime) -> list[tuple[str, str, str]]:
    """(target_key, binding, stale|fresh|blocked) for every Developer/Tester binding."""
    from agentm2m.context.resolver import ContextUnavailable
    from agentm2m.engine.binding import stamp_for
    from agentm2m.engine.helpers_loader import load_helpers
    from agentm2m.engine.matcher import compute_matches
    from agentm2m.rules.ast import StochasticBinding

    out = []
    for hname, spec in rt.team.handoffs.items():
        module = rt.module_for(hname)
        helpers = load_helpers(module.uses, spec.rule_path.parent)
        roots = {sm.mm_name: rt.team.roots[sm.mm_name] for sm in module.sources}
        owner = rt._owner_name(spec.target_mm)
        for rule in module.rules:
            for m in compute_matches(rule, roots, helpers):
                link = rt.team.traces[hname].get(rule.name, m.match_key)
                for tp in rule.to_clause.patterns:
                    for b in tp.bindings:
                        if not isinstance(b, StochasticBinding):
                            continue
                        try:
                            stamp = stamp_for(b, m, helpers, rt.contexts, owner).stamp
                            state = "fresh" if link and link.stamps.get(b.name) == stamp else "stale"
                        except ContextUnavailable:
                            state = "blocked"
                        out.append((f"{rule.name}::{tp.var}::{m.match_key}", b.name, state))
    return out


def _publish(store, emitter, items, expected_version: int):
    """The Security Reviewer publishes a new version (an observable write)."""
    snap = store.update("security-review", as_agent="SecurityReviewer", expected_version=expected_version,
                        items=items)
    emitter.emit("context.updated", agent_id="SecurityReviewer", context_ids=("security-review",),
                 payload={"version": snap.version, "digest": snap.digest, "items": [i.id for i in snap.items],
                          "context_content": [i.to_dict() for i in snap.items]})
    return snap


def run(out_dir: Path | None = None) -> dict:
    work = Path(tempfile.mkdtemp(prefix="chakin-ctx-"))
    rules = _context_rules(work)
    task = load_devbench_task("chakin")
    team = build_team_for_task(task)
    llm = ScriptedLLM()
    sink = MemoryEventSink()
    relay = MemoryRelay()
    demo_key = KeySigner(DEMO_KEY)
    nostr = NostrEventSink(relay, Outbox(work / "outbox.jsonl"), signer_for=lambda _a: demo_key, clock=_clock())
    emitter = Emitter(CompositeSink([sink, nostr]), workspace_id="chakin-pilot", clock=_clock())
    store = MemoryContextStore(clock=_clock())
    rt = TeamRuntime(team, llm, max_resamples=2, max_passes=3, emit=emitter.emit,
                     contexts=ContextResolver(store))
    report: dict = {}

    # 1. the pilot team, as in Steps 1-7
    emitter.begin_run("run")
    rt.run_to_fixpoint()
    report["phi_initial"] = rt.acceptance_holds()

    # 2. HOT: the Security Reviewer joins (Sec. "Adding a new agent at runtime")
    sec_mm = build_sec_mm(team.views["Arch"])
    apply_hot(team, TeamChange(agent_name="SecurityReviewer", view=sec_mm, view_root=sec_mm.get("SecModel")(),
                               handoff_name="Arch2Sec", rule_path=rules / "Arch2Sec.agentm2m"))
    emitter.begin_run("run")
    rt.run_to_fixpoint()
    review = _link(team, "Arch2Sec", "Operation2Review", "op=Operation#Global_functions::download")
    report["review_download"] = {"notes": next(r.notes for r in team.roots["Sec"].reviews
                                               if r.operation.name == "download"),
                                 "risk_stamp": review.stamps["risk"]}

    # 3. the shared context is declared, and the reviewer publishes two findings
    policy = ContextPolicy(owner="SecurityReviewer", writers=frozenset({"SecurityReviewer"}),
                           readers=frozenset({"Developer", "Tester"}))
    store.ensure("security-review", policy, title="Security review findings")
    v2 = _publish(store, emitter, [FINDING_PATH, FINDING_TLS], 1)
    report["context_v2"] = v2.to_dict()

    # 4. Developer and Tester bindings now declare the context
    _switch_rule(rt, "Arch2Code", rules / "Arch2Code.ctx.agentm2m")
    _switch_rule(rt, "Req2Test", rules / "Req2Test.ctx.agentm2m")
    stale_after_switch = [s for s in _states(rt) if s[2] != "fresh"]
    llm.prompts.clear()
    mark = len(sink.events)
    run_ctx = emitter.begin_run("run")
    r = rt.run_to_fixpoint()
    report["context_run_id"] = run_ctx
    report["stale_after_switch"] = stale_after_switch
    report["discharged_after_switch"] = [(o.handoff, o.target_key, o.binding) for o in r.obligations]
    report["prompt_body_download"] = next(p for p in llm.prompts
                                          if p.startswith("Implement") and "Signature: download(" in p)
    report["prompt_signature_download"] = None
    body_link = _link(team, "Arch2Code", "Operation2CodeEdit", "op=Operation#Global_functions::download")
    report["trace_link_body_download"] = json.loads(json.dumps(
        {k: v for k, v in body_link.to_dict().items()
         if k in ("rule", "match_key", "target_key", "stamps", "context_pins", "dependencies")}))
    report["body_download"] = next(e.body for e in team.roots["Code"].edits if e.name == "download")
    report["events_context_run"] = [e.to_dict() for e in sink.events[mark:]]
    report["phi_after_context"] = rt.acceptance_holds()

    # 5. the reviewer revises one finding: exactly the consumers are obliged
    v3 = _publish(store, emitter, [FINDING_PATH_V2], 2)
    report["context_v3_digest"] = v3.digest
    report["stale_after_revision"] = [s for s in _states(rt) if s[2] != "fresh"]
    llm.prompts.clear()
    mark = len(sink.events)
    report["revision_run_id"] = emitter.begin_run("run")
    r = rt.run_to_fixpoint()
    report["revision_calls"] = len(llm.prompts)
    report["revision_obligations"] = [
        {k: e.to_dict()[k] for k in ("target_key", "binding", "agent_id")} | {"cause": e.payload.get("cause")}
        for e in sink.events[mark:] if e.event_type == "obligation.created"]
    report["phi_after_revision"] = rt.acceptance_holds()

    # 6. influence: which accepted values derive from finding-path?
    influence = []
    for hname, tm in team.traces.items():
        for link in tm.links():
            for b, pins in link.context_pins.items():
                for pin in pins:
                    if pin["context_id"] == "security-review":
                        snap = store.snapshot("security-review", pin["version"])
                        if any(i.id == "finding-path" for i in snap.items):
                            influence.append({"handoff": hname, "target_key": link.target_key, "binding": b,
                                              "agent": rt._owner_name(team.handoffs[hname].target_mm),
                                              "pinned_version": pin["version"]})
    report["influence_finding_path"] = influence

    # 7. the Tester loses read access: its oracles are blocked, never guessed
    store.set_policy("security-review", ContextPolicy(owner="SecurityReviewer",
                                                      writers=frozenset({"SecurityReviewer"}),
                                                      readers=frozenset({"Developer"})))
    mark = len(sink.events)
    emitter.begin_run("run")
    r = rt.run_to_fixpoint()
    report["blocked_after_revoke"] = sorted({s[1] for s in _states(rt) if s[2] == "blocked"})
    report["escalations_after_revoke"] = [{"target_key": e.target_key, "binding": e.binding, "reason": e.reason}
                                          for e in r.escalations][:2]
    report["context_errors_after_revoke"] = [e.to_dict() for e in sink.events[mark:]
                                             if e.event_type == "context.error"][:1]
    report["phi_after_revoke"] = rt.acceptance_holds()
    report["presence_after_revoke"] = derive_presence(sink.events, sorted(team.agents))
    store.set_policy("security-review", policy)
    emitter.begin_run("run")
    rt.run_to_fixpoint()  # access restored: the oracles are fresh again, nothing to sample
    report["phi_after_restore"] = rt.acceptance_holds()

    # 8. observability views over the whole run
    all_events = [e.to_dict() for e in sink.events]
    report["event_count"] = len(all_events)
    report["presence"] = derive_presence(sink.events, sorted(team.agents))
    report["metrics"] = compute_metrics(all_events)
    report["nostr"] = {
        "published": len(relay.events),
        "verified": sum(1 for d in relay.events.values() if verify_event(d)),
        "example": next(d for d in relay.events.values()
                        if json.loads(d["content"])["event_type"] == "binding.accepted"
                        and json.loads(d["content"])["binding"] == "body"
                        and json.loads(d["content"])["payload"].get("pins")
                        and "download" in (json.loads(d["content"])["target_key"] or "")),
        "pubkey": demo_key.pubkey,
    }
    # relay outage: events queue in the outbox and the engine is unaffected
    relay.down = True
    attempts_before = relay.publish_attempts
    llm.prompts.clear()
    _publish(store, emitter, [FINDING_TLS.with_content(FINDING_TLS.content + " Pin the server certificate.")], 3)
    report["tls_stale"] = sorted({(b, st) for _t, b, st in _states(rt) if st != "fresh"})
    emitter.begin_run("run")
    r = rt.run_to_fixpoint()
    report["outage"] = {"phi": rt.acceptance_holds(), "outbox_depth": nostr.outbox.depth(),
                        "llm_calls": len(llm.prompts),
                        "relay_attempts_while_down": relay.publish_attempts - attempts_before}
    relay.down = False
    nostr._retry_at = 0.0
    report["outage"]["flush"] = nostr.flush(force=True)

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "chakin_context_walkthrough.json").write_text(json.dumps(report, indent=1, default=str))
    report = json.loads(json.dumps(report, default=str))
    shutil.rmtree(work, ignore_errors=True)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="evaluation/results/walkthrough")
    args = ap.parse_args()
    rep = run(Path(args.out))
    print(json.dumps({k: rep[k] for k in ("phi_initial", "phi_after_context", "phi_after_revision",
                                          "phi_after_revoke", "revision_calls", "event_count")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
