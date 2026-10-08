"""Follow-up study: shared context, knowledge traceability and observability on
real DevBench teams (docs/follow-up-study-DS-A2A.tex).

Scenario (per repo, model, seed). The pilot DevTeam (Analyst -> Architect ->
Developer, Analyst -> Tester) builds a repo from its PRD + UML. A Security
Reviewer contributes knowledge that must reach the Developer and the Tester:

    finding-code   "every implementation must contain the comment # SEC-C-<tag>"
    finding-test   "every test oracle must contain the comment # SEC-T-<tag>"

The tag is an unguessable constant (the same device as the NIER canaries), so
"did the knowledge reach the artifact" is a string test, not a judgement. The
finding is then revised five times (REVISIONS); the ground-truth set of
artifacts each revision must change is known by construction.

Knowledge-distribution policies compared (same model, rules, footprints,
temperature, resample budget):

    paste  no tracking: both findings are pasted by hand into every consumer's
           prompt; on any text change the operator re-runs every consumer
    ctx    shared context, whole-context pin: `context=['security-review']`
    ctxi   shared context, item-level pin: Developer pins finding-code only,
           Tester pins finding-test only
    ctxi_nostr  ctxi replayed with the Nostr projection attached (control)

`ctxi_nostr` is a *replay* of the ctxi run (identical prompts answered from
the recorded responses), so it costs no LLM tokens and isolates the effect of
the Nostr projection from sampling noise.

    python -m evaluation.harness.follow_up_context_study --llm mock --repos geotext
    python -m evaluation.harness.follow_up_context_study --llm ollama --model qwen2.5-coder:7b --seed 1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import statistics
import tempfile
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from agentm2m.context.errors import ContextError
from agentm2m.context.model import ContextItem, ContextPolicy
from agentm2m.context.nostr_sync import NostrContextSync
from agentm2m.context.resolver import ContextResolver
from agentm2m.context.store import MemoryContextStore
from agentm2m.engine.executor import _index_existing_targets
from agentm2m.llm.base import LLMBackend, LLMError
from agentm2m.nostr.outbox import Outbox
from agentm2m.nostr.publisher import NostrEventSink
from agentm2m.nostr.relay import MemoryRelay
from agentm2m.nostr.signer import KeySigner
from agentm2m.nostr.verifier import verify_event
from agentm2m.observability.emitter import Emitter
from agentm2m.observability.metrics import compute_metrics, observability_coverage
from agentm2m.observability.sink import CompositeSink, MemoryEventSink
from agentm2m.team.runtime import TeamRuntime

from .canary import canary_test_files
from .chakin_context_walkthrough import _states, _switch_rule
from .devbench_agentm2m_config import build_team_for_task
from .devbench_common import GeneratedSymbol, assemble_module, run_devbench_tests
from .devbench_loader import ALL_REPOS, DevBenchTask, load_devbench_task

RULES = Path(__file__).resolve().parent / "devbench_rules"
# paste:  the text of every item is pasted into every consumer's prompt; any text change re-runs every consumer
# routed: each finding is pasted only into the prompts of the consumers that need it and only those are re-run
#         (the strongest manual practice: a hand-maintained routing table)
# ctx / ctxi: shared context pinned whole / per item
POLICIES = ("paste", "routed", "ctx", "ctxi")
PASTE_LIKE = ("paste", "routed")
ROUTE = {"Arch2Code": "finding-code", "Req2Test": "finding-test"}
CTX = "security-review"
CONSUMERS = (("Arch2Code", "body"), ("Req2Test", "oracle"))

# (name, what changes, ground-truth binding kind that must change; None = nothing)
REVISIONS = (
    ("R1_test_finding_revised", "finding-test", "oracle"),
    ("R2_code_finding_revised", "finding-code", "body"),
    ("R3_unrelated_item_added", "finding-misc", None),
    ("R4_identical_rewrite", "finding-test", None),
    ("R5_code_finding_revised", "finding-code", "body"),
)


# ----------------------------------------------------------------------------
# LLM wrappers: record / replay / fault
# ----------------------------------------------------------------------------

def _pdigest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


class RecordingLLM(LLMBackend):
    """Counts calls, tokens and latency and records every (prompt, response)."""

    def __init__(self, inner: LLMBackend) -> None:
        self.inner = inner
        self.name = getattr(inner, "name", "llm")
        self.records: list[dict] = []
        self.last_usage = None

    def generate(self, prompt: str, *, temperature: float = 0.2) -> str:
        t0 = time.perf_counter()
        out = self.inner.generate(prompt, temperature=temperature)
        usage = getattr(self.inner, "last_usage", None) or (self.inner.count_tokens(prompt),
                                                            self.inner.count_tokens(out))
        self.last_usage = usage
        self.records.append({"prompt": prompt, "response": out, "in": usage[0], "out": usage[1],
                             "s": time.perf_counter() - t0})
        return out

    def count_tokens(self, text: str) -> int:
        return self.inner.count_tokens(text)

    def mark(self) -> int:
        return len(self.records)

    def since(self, mark: int) -> dict:
        part = self.records[mark:]
        return {"calls": len(part), "input_tokens": sum(r["in"] for r in part),
                "output_tokens": sum(r["out"] for r in part), "llm_seconds": round(sum(r["s"] for r in part), 3)}


class ReplayLLM(LLMBackend):
    """Answers every prompt from a recording (a queue per distinct prompt, so
    resamples replay in order). A prompt the recording never saw is a control
    violation: it is counted in `misses` and answered with an empty value."""

    name = "replay"

    def __init__(self, records: list[dict]) -> None:
        self.queues: dict[str, deque] = defaultdict(deque)
        for r in records:
            self.queues[r["prompt"]].append(r)
        self.sent: list[str] = []
        self.misses = 0
        self.last_usage = None

    def generate(self, prompt: str, *, temperature: float = 0.2) -> str:
        self.sent.append(prompt)
        q = self.queues.get(prompt)
        if not q:
            self.misses += 1
            self.last_usage = (0, 0)
            return ""
        r = q.popleft() if len(q) > 1 else q[0]
        self.last_usage = (r["in"], r["out"])
        return r["response"]

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)


class FaultLLM(LLMBackend):
    """Wraps a backend and misbehaves on demand: `garbage` returns an
    unparseable value; `transport` raises LLMError."""

    def __init__(self, inner: LLMBackend, mode: str) -> None:
        self.inner, self.mode, self.name = inner, mode, "fault"
        self.calls = 0
        self.last_usage = None

    def generate(self, prompt: str, *, temperature: float = 0.2) -> str:
        self.calls += 1
        if self.mode == "transport":
            raise LLMError("injected transport failure")
        if self.mode == "garbage":
            self.last_usage = (1, 1)
            return "}{ not python ((("
        out = self.inner.generate(prompt, temperature=temperature)
        self.last_usage = self.inner.last_usage
        return out

    def count_tokens(self, text: str) -> int:
        return self.inner.count_tokens(text)


# ----------------------------------------------------------------------------
# Findings, rules
# ----------------------------------------------------------------------------

def _tag(repo: str, seed: int, kind: str, version: int) -> str:
    return hashlib.sha256(f"{repo}:{seed}:{kind}:{version}".encode()).hexdigest()[:6]


def finding_items(repo: str, seed: int, code_v: int, test_v: int, misc: bool = False) -> list[ContextItem]:
    items = [
        ContextItem(id="finding-code", type="security-finding", content=(
            f"Security finding (code): every implementation MUST contain the literal comment line "
            f"# SEC-C-{_tag(repo, seed, 'code', code_v)} inside the function body.")),
        ContextItem(id="finding-test", type="security-finding", content=(
            f"Security finding (test): every test oracle MUST contain the literal comment line "
            f"# SEC-T-{_tag(repo, seed, 'test', test_v)} inside the function.")),
    ]
    if misc:
        items.append(ContextItem(id="finding-misc", type="style-note", content="Style note: prefer f-strings."))
    return items


def _paste_text(items: list[ContextItem]) -> str:
    return " ".join(i.content.replace("'", "") for i in items if i.id != "finding-misc") + (
        " Style note: prefer f-strings." if any(i.id == "finding-misc" for i in items) else "")


def write_rules(workdir: Path, policy: str, items: list[ContextItem]) -> dict[str, Path]:
    """Rule variants for one policy. paste: the (current) finding text is part of
    the prompt literal. ctx/ctxi: a `context = [...]` argument."""
    rules = workdir / "rules"
    if not rules.exists():
        shutil.copytree(RULES, rules, ignore=shutil.ignore_patterns("__pycache__"))
    a2c = (RULES / "Arch2Code.agentm2m").read_text()
    r2t = (RULES / "Req2Test.agentm2m").read_text()
    if policy == "paste":
        note = " SECURITY NOTES (pasted by hand): " + _paste_text(items)
        a2c = a2c.replace("explanation.',", f"explanation.{note}',")
        r2t = r2t.replace("explanation.',", f"explanation.{note}',")
    elif policy == "routed":
        by_id = {i.id: i.content.replace("'", "") for i in items}
        a2c = a2c.replace("explanation.',", f"explanation. SECURITY NOTES (pasted by hand): {by_id['finding-code']}',")
        r2t = r2t.replace("explanation.',", f"explanation. SECURITY NOTES (pasted by hand): {by_id['finding-test']}',")
    elif policy == "ctx":
        a2c = a2c.replace("op.codeContext()),", f"op.codeContext(),\n context = ['{CTX}']),")
        r2t = r2t.replace("c.text),", f"c.text,\n context = ['{CTX}']),")
    elif policy == "ctxi":
        a2c = a2c.replace("op.codeContext()),", f"op.codeContext(),\n context = ['{CTX}#finding-code']),")
        r2t = r2t.replace("c.text),", f"c.text,\n context = ['{CTX}#finding-test']),")
    else:
        raise ValueError(policy)
    assert a2c != (RULES / "Arch2Code.agentm2m").read_text() and r2t != (RULES / "Req2Test.agentm2m").read_text()
    out = {"Arch2Code": rules / f"Arch2Code.{policy}.agentm2m", "Req2Test": rules / f"Req2Test.{policy}.agentm2m"}
    out["Arch2Code"].write_text(a2c)
    out["Req2Test"].write_text(r2t)
    return out


# ----------------------------------------------------------------------------
# One configuration run
# ----------------------------------------------------------------------------

class Scenario:
    """A DevBench team wired to a context store, an emitter and an LLM."""

    def __init__(self, task: DevBenchTask, llm: LLMBackend, policy: str, *, seed: int, workdir: Path,
                 nostr: bool = False, relay_down: bool = False, observability: str = "timeline",
                 max_resamples: int = 2) -> None:
        self.task, self.llm, self.policy, self.seed, self.workdir = task, llm, policy, seed, workdir
        self.team = build_team_for_task(task)
        self.sink = MemoryEventSink()
        sinks: list = [self.sink] if observability != "none" else []
        self.relay = MemoryRelay() if nostr else None
        self.nostr_sink = None
        self.key = KeySigner.generate()  # the workspace engine key (timeline events)
        self.keys = {"SecurityReviewer": KeySigner.generate(), "Developer": KeySigner.generate()}
        if nostr:
            self.relay.down = relay_down
            self.nostr_sink = NostrEventSink(self.relay, Outbox(workdir / "outbox.jsonl"),
                                             signer_for=lambda _a: self.key)
            sinks.append(self.nostr_sink)
        self.emitter = Emitter(CompositeSink(sinks), workspace_id=f"{task.repo}-{policy}")
        self.emit = self.emitter.emit if observability != "none" else (lambda *a, **k: None)
        self.store = MemoryContextStore()
        self.resolver = ContextResolver(self.store)
        self.code_v, self.test_v, self.misc = 1, 1, False
        self._prev_content = {i.id: i.content for i in finding_items(task.repo, seed, 1, 1)}
        self.rt = TeamRuntime(self.team, llm, max_resamples=max_resamples, max_passes=2, emit=self.emit,
                              contexts=self.resolver if policy not in PASTE_LIKE else None)
        self.last_rerun: set[tuple[str, str]] = set()
        self.sync = None
        self.peer_store = None
        items = self.items()
        if policy in PASTE_LIKE:
            self._rules(items)
        else:
            vis = "relay" if nostr else "team"
            pol = ContextPolicy(owner="SecurityReviewer", writers=frozenset({"SecurityReviewer"}),
                                readers=frozenset({"Developer", "Tester"}), visibility=vis)
            self.store.ensure(CTX, pol, title="Security review findings")
            if nostr:
                self.peer_store = MemoryContextStore()
                self.peer_store.ensure(CTX, pol, title="Security review findings")
                self.sync = NostrContextSync(self.store, self.relay, namespace=task.repo,
                                             pubkey_of=self.pubkey_of, signer_for=self.keys.get)
            self._rules(items)
            self.publish(items)

    def pubkey_of(self, agent: str) -> str | None:
        k = self.keys.get(agent)
        return k.pubkey if k else None

    def peer_sync(self, on_event=None) -> NostrContextSync:
        """A second workspace (its own store) that receives context over the relay."""
        return NostrContextSync(self.peer_store, self.relay, namespace=self.task.repo, pubkey_of=self.pubkey_of,
                                signer_for=lambda a: None, on_event=on_event)

    def items(self) -> list[ContextItem]:
        return finding_items(self.task.repo, self.seed, self.code_v, self.test_v, self.misc)

    def _rules(self, items: list[ContextItem]) -> None:
        paths = write_rules(self.workdir, self.policy, items)
        for h, p in paths.items():
            _switch_rule(self.rt, h, p)

    def publish(self, items: list[ContextItem]) -> None:
        before = self.store.snapshot(CTX)
        snap = self.store.update(CTX, as_agent="SecurityReviewer", expected_version=before.version, items=items)
        if snap.version == before.version:
            return  # identical content: the store created no version, so nothing is published
        self.emit("context.updated", agent_id="SecurityReviewer", context_ids=(CTX,),
                  payload={"version": snap.version, "digest": snap.digest, "items": [i.id for i in snap.items]})
        if self.sync is not None:
            self.sync.publish(snap, previous_digest=before.digest, as_agent="SecurityReviewer")

    # -- values and state ---------------------------------------------------

    def values(self) -> dict[tuple[str, str], str]:
        out = {}
        for hname, binding in CONSUMERS:
            idx = _index_existing_targets(self.team.roots[self.team.handoffs[hname].target_mm])
            for link in self.team.traces[hname].links():
                obj = idx.get(link.target_key)
                if obj is not None:
                    out[(link.target_key, binding)] = getattr(obj, binding) or ""
        return out

    def state_hash(self) -> str:
        blob = json.dumps({"values": sorted((list(k), v) for k, v in self.values().items()),
                           "stamps": sorted((h, l.target_key, sorted(l.stamps.items()))
                                            for h, tm in self.team.traces.items() for l in tm.links())},
                          sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def module_text(self) -> str:
        syms = [GeneratedSymbol(component=ce.operation.component.name, name=ce.operation.name, body=ce.body or "")
                for ce in self.team.roots["Code"].edits if ce.operation is not None]
        return assemble_module(syms)

    def run(self, op: str = "run"):
        run_id = self.emitter.begin_run(op)
        t0 = time.perf_counter()
        report = self.rt.run_to_fixpoint()
        return run_id, report, time.perf_counter() - t0

    def clear_consumer_stamps(self) -> None:
        for hname, binding in CONSUMERS:
            for link in self.team.traces[hname].links():
                link.stamps.pop(binding, None)
                link.failed_stamps.pop(binding, None)

    def revise(self, kind: str, what: str) -> bool:
        """Apply one revision; return whether the knowledge text changed."""
        before = [i.content for i in self.items()]
        if what == "finding-code" and kind != "R4_identical_rewrite":
            self.code_v += 1
        elif what == "finding-test" and kind == "R1_test_finding_revised":
            self.test_v += 1
        elif what == "finding-misc":
            self.misc = True
        changed = [i.content for i in self.items()] != before
        items = self.items()
        self.last_rerun = set()
        if self.policy in PASTE_LIKE:
            if changed:
                self._rules(items)
                after_c = {i.id: i.content for i in items}
                if self.policy == "paste":
                    kinds = tuple(ROUTE)  # no routing information: re-run every consumer
                else:
                    kinds = tuple(h for h, fid in ROUTE.items() if after_c.get(fid) != self._prev_content.get(fid))
                for hname in kinds:
                    binding = dict(CONSUMERS)[hname]
                    for link in self.team.traces[hname].links():
                        link.stamps.pop(binding, None)
                        link.failed_stamps.pop(binding, None)
                        self.last_rerun.add((link.target_key, binding))
        else:
            self.publish(items)  # an identical rewrite is idempotent: the store creates no new version
        self._prev_content = {i.id: i.content for i in items}
        return changed


def _obligation_set(sc: Scenario) -> set[tuple[str, str]]:
    """Engine-derived impact of the pending change: consumer bindings that hold an
    accepted value (a stamp) whose stamp no longer matches the current effective
    footprint. A never-accepted (escalated) binding is a failure, not an obligation."""
    from agentm2m.context.resolver import ContextUnavailable
    from agentm2m.engine.binding import stamp_for
    from agentm2m.engine.helpers_loader import load_helpers
    from agentm2m.engine.matcher import compute_matches
    from agentm2m.rules.ast import StochasticBinding

    out: set[tuple[str, str]] = set()
    rt = sc.rt
    for hname, spec in rt.team.handoffs.items():
        module = rt.module_for(hname)
        helpers = load_helpers(module.uses, spec.rule_path.parent)
        roots = {sm.mm_name: rt.team.roots[sm.mm_name] for sm in module.sources}
        owner = rt._owner_name(spec.target_mm)
        for rule in module.rules:
            for m in compute_matches(rule, roots, helpers):
                link = rt.team.traces[hname].get(rule.name, m.match_key)
                if link is None:
                    continue
                for tp in rule.to_clause.patterns:
                    for b in tp.bindings:
                        if not isinstance(b, StochasticBinding) or b.name not in ("body", "oracle"):
                            continue
                        if b.name not in link.stamps:
                            continue
                        try:
                            stamp = stamp_for(b, m, helpers, rt.contexts, owner).stamp
                        except ContextUnavailable:
                            continue
                        if link.stamps[b.name] != stamp:
                            out.add((f"{rule.name}::{tp.var}::{m.match_key}", b.name))
    return out


def _tags(sc: Scenario) -> tuple[str, str]:
    return (f"SEC-C-{_tag(sc.task.repo, sc.seed, 'code', sc.code_v)}".lower(),
            f"SEC-T-{_tag(sc.task.repo, sc.seed, 'test', sc.test_v)}".lower())


def _carry(values: dict, binding: str, tag: str) -> float:
    vs = [v for (k, b), v in values.items() if b == binding]
    return sum(tag in v.lower() for v in vs) / len(vs) if vs else 0.0


def _influence_from_pins(sc: Scenario, item_id: str) -> set[tuple[str, str]]:
    """Which accepted values pinned this item (the logic of Workspace.influence_query)."""
    out = set()
    for tm in sc.team.traces.values():
        for link in tm.links():
            for b, pins in link.context_pins.items():
                for pin in pins:
                    if pin["context_id"] != CTX:
                        continue
                    ids = pin.get("item_ids") or [i.id for i in sc.store.snapshot(CTX, pin["version"]).items]
                    if item_id in ids:
                        out.add((link.target_key, b))
    return out


_ITEM_LINE = re.compile(r"^- (finding-[a-z]+) \(", re.MULTILINE)


def _influence_from_prompts(sc: Scenario, records: list[dict]) -> dict[str, set[tuple[str, str]]]:
    """Independent ground truth: the context items the LLM actually saw in the
    last prompt prepared for each binding (timeline digest -> recorded prompt)."""
    by_digest = {_pdigest(r["prompt"]): r["prompt"] for r in records}
    last: dict[tuple[str, str], str] = {}
    for e in sc.sink.events:
        if e.event_type == "binding.prompt_prepared":
            d = (e.payload or {}).get("prompt_digest")
            if d in by_digest:
                last[(e.target_key, e.binding)] = by_digest[d]
    accepted = {(l.target_key, b) for tm in sc.team.traces.values() for l in tm.links() for b in l.stamps}
    out: dict[str, set] = defaultdict(set)
    for key, prompt in last.items():
        if key not in accepted:
            continue
        for item in _ITEM_LINE.findall(prompt):
            out[item].add(key)
    return out


def _consumer_keys(sc: Scenario, binding: str) -> set[tuple[str, str]]:
    return {(k, b) for (k, b) in sc.values() if b == binding}


def _prf(pred: set, truth: set) -> tuple[float, float]:
    p = len(pred & truth) / len(pred) if pred else (1.0 if not truth else 0.0)
    r = len(pred & truth) / len(truth) if truth else 1.0
    return round(p, 4), round(r, 4)


def run_policy(task: DevBenchTask, llm: LLMBackend, policy: str, *, seed: int, workdir: Path,
               run_tests: bool = True) -> tuple[list[dict], Scenario, RecordingLLM]:
    rec = RecordingLLM(llm)
    sc = Scenario(task, rec, policy, seed=seed, workdir=workdir)
    rows: list[dict] = []
    base = {"repo": task.repo, "policy": policy, "seed": seed}

    run_id, report, secs = sc.run()
    vals = sc.values()
    ct, tt = _tags(sc)
    build = rec.since(0)
    row = {**base, "step": "R0_build", **build, "seconds": round(secs, 2), "phi": sc.rt.acceptance_holds(),
           "retention_code": _carry(vals, "body", ct), "retention_test": _carry(vals, "oracle", tt),
           "escalations": len(report.escalations), "n_bodies": len(_consumer_keys(sc, "body")),
           "n_oracles": len(_consumer_keys(sc, "oracle"))}
    if run_tests:
        tr = run_devbench_tests(task, sc.module_text(), canary_test_sources=canary_test_files())
        row.update(canary_pass=tr.canary_pass, canary_total=tr.canary_total, unit_pass=tr.unit_pass)
    rows.append(row)

    for kind, what, truth_binding in REVISIONS:
        before_vals = sc.values()
        old_ct, old_tt = ct, tt
        changed = sc.revise(kind, what)
        ct, tt = _tags(sc)
        if policy in PASTE_LIKE:
            predicted = set(sc.last_rerun)
        else:
            predicted = _obligation_set(sc)
        truth = _consumer_keys(sc, truth_binding) if truth_binding else set()
        mark = rec.mark()
        run_id, report, secs = sc.run()
        after_vals = sc.values()
        cost = rec.since(mark)
        changed_keys = {k for k in after_vals if after_vals[k] != before_vals.get(k)}
        precision, recall = _prf(predicted, truth)
        if truth_binding == "oracle":
            new_ok, stale = _carry(after_vals, "oracle", tt), _carry(after_vals, "oracle", old_tt)
        elif truth_binding == "body":
            new_ok, stale = _carry(after_vals, "body", ct), _carry(after_vals, "body", old_ct)
        else:
            new_ok, stale = None, None
        rows.append({**base, "step": kind, **cost, "seconds": round(secs, 2), "phi": sc.rt.acceptance_holds(),
                     "text_changed": changed, "predicted": len(predicted), "truth": len(truth),
                     "impact_precision": precision, "impact_recall": recall,
                     "values_changed": len(changed_keys), "collateral_changes": len(changed_keys - truth),
                     "carries_new": new_ok, "carries_stale": stale, "escalations": len(report.escalations),
                     "obligations": len(report.obligations)})

    # knowledge traceability at the end of the run
    final = {**base, "step": "FINAL"}
    vals = sc.values()
    if run_tests:
        tr = run_devbench_tests(task, sc.module_text(), canary_test_sources=canary_test_files())
        final.update(canary_pass=tr.canary_pass, canary_total=tr.canary_total, unit_pass=tr.unit_pass)
    final["phi"] = sc.rt.acceptance_holds()
    final["retention_code"], final["retention_test"] = _carry(vals, "body", ct), _carry(vals, "oracle", tt)
    if policy not in PASTE_LIKE:
        truth_prompts = _influence_from_prompts(sc, rec.records)
        for item in ("finding-code", "finding-test"):
            p, r = _prf(_influence_from_pins(sc, item), truth_prompts.get(item, set()))
            final[f"influence_precision_{item}"], final[f"influence_recall_{item}"] = p, r
            final[f"influence_size_{item}"] = len(truth_prompts.get(item, set()))
    final["total_calls"], final["total_tokens"] = len(rec.records), sum(r["in"] + r["out"] for r in rec.records)
    rows.append(final)
    events = [e.to_dict() for e in sc.sink.events]
    m = compute_metrics(events)
    rows.append({**base, "step": "METRICS", "timeline_events": len(events),
                 "event_llm_calls": m["llm_calls"], "counted_llm_calls": len(rec.records),
                 "event_tokens": m["input_tokens"] + m["output_tokens"],
                 "counted_tokens": sum(r["in"] + r["out"] for r in rec.records),
                 "context_reuse_agents": len(m["context_reuse"].get(CTX, {}).get("agents", [])),
                 "context_reuse_bindings": m["context_reuse"].get(CTX, {}).get("bindings", 0),
                 "context_induced_obligations": m["context_induced_obligations"],
                 "obligations_created": m["obligations_created"], "context_reads": m["context_reads"],
                 "collab_latency_mean_s": (round(statistics.fmean(m["collaboration_latency_s"].values()), 4)
                                           if m["collaboration_latency_s"] else None),
                 "amplification_max": max(m["context_amplification"].values(), default=0)})
    return rows, sc, rec


# ----------------------------------------------------------------------------
# RQ3: observability -- reconstruction, projection control, overhead, leaks
# ----------------------------------------------------------------------------

def _replay_policy(task, records, policy, *, seed, workdir, nostr=False, relay_down=False, observability="timeline"):
    """Re-run the whole revision schedule with every prompt answered from `records`."""
    replay = ReplayLLM(records)
    sc = Scenario(task, replay, policy, seed=seed, workdir=workdir, nostr=nostr, relay_down=relay_down,
                  observability=observability)
    t0 = time.perf_counter()
    sc.run()
    for kind, what, _tb in REVISIONS:
        sc.revise(kind, what)
        sc.run()
    return sc, replay, time.perf_counter() - t0


def reconstruct_from_events(events: list[dict]) -> dict[tuple[str, str], tuple]:
    """(target_key, binding) -> (footprint_version, sorted pins) of the last accepted value."""
    out = {}
    for e in sorted(events, key=lambda e: (e["ts"], e.get("seq", 0))):
        if e["event_type"] == "binding.accepted" and e["payload"].get("footprint_version"):
            pins = tuple(sorted((p["context_id"], p["version"], p.get("digest", "")) for p in
                                (e["payload"].get("pins") or [])))
            out[(e["target_key"], e["binding"])] = (e["payload"]["footprint_version"], pins)
    return out


def _engine_truth(sc: Scenario) -> dict[tuple[str, str], tuple]:
    truth = {}
    for tm in sc.team.traces.values():
        for link in tm.links():
            for b, stamp in link.stamps.items():
                pins = tuple(sorted((p["context_id"], p["version"], p.get("digest", "")) for p in
                                    link.context_pins.get(b, [])))
                truth[(link.target_key, b)] = (stamp, pins)
    return truth


def observability_study(task, records, policy, *, seed, base_hash) -> dict:
    """Projection control, relay outage, timeline/Nostr reconstruction, overhead, leaks."""
    tmp = Path(tempfile.mkdtemp(prefix="obs_"))
    out: dict = {"repo": task.repo, "policy": policy, "seed": seed, "step": "OBSERVABILITY"}
    try:
        runs = {}
        for name, kw in (("timeline", {}), ("nostr", {"nostr": True}), ("nostr_down", {"nostr": True, "relay_down": True})):
            wd = tmp / name
            wd.mkdir()
            sc, replay, secs = _replay_policy(task, records, policy, seed=seed, workdir=wd, **kw)
            runs[name] = (sc, replay, secs)
        base_sc, base_replay, _ = runs["timeline"]
        out["replay_misses"] = base_replay.misses
        out["state_hash_matches_original"] = base_sc.state_hash() == base_hash
        for name in ("nostr", "nostr_down"):
            sc, replay, _ = runs[name]
            out[f"{name}_state_identical"] = sc.state_hash() == base_sc.state_hash()
            out[f"{name}_prompts_identical"] = Counter(replay.sent) == Counter(base_replay.sent)
            out[f"{name}_misses"] = replay.misses
        sc = runs["nostr"][0]
        raw = sc.relay.query([{"#t": ["agentm2m"]}])
        verified = [d for d in raw if _safe_verify(d)]
        out["nostr_events_published"], out["nostr_events_verified"] = len(raw), len(verified)
        if sc.peer_store is not None:
            rep = sc.peer_sync().pull()
            out["sync_applied"] = rep["applied"]
            out["sync_rejected"] = len(rep["rejected"])
            out["sync_peer_converged"] = (sc.peer_store.snapshot(CTX).digest == sc.store.snapshot(CTX).digest)
        down = runs["nostr_down"][0]
        out["outage_outbox_depth"] = down.nostr_sink.outbox.depth()
        down.relay.down = False
        down.nostr_sink._retry_at = 0.0
        down.nostr_sink.flush(force=True)
        out["outage_outbox_depth_after_flush"] = down.nostr_sink.outbox.depth()
        # reconstruction of accepted values from events alone
        truth = _engine_truth(base_sc)
        timeline = reconstruct_from_events([e.to_dict() for e in base_sc.sink.events])
        relay_events = []
        for d in verified:
            try:
                env = json.loads(d["content"])
            except ValueError:
                continue
            if isinstance(env, dict) and env.get("schema") == "agentm2m.event":
                relay_events.append(env)
        from_relay = reconstruct_from_events(relay_events)
        for label, rec_ in (("timeline", timeline), ("relay", from_relay)):
            ok = sum(1 for k, v in truth.items() if rec_.get(k) == v)
            out[f"reconstruction_{label}"] = round(ok / len(truth), 4) if truth else 1.0
        out["accepted_values"] = len(truth)
        # privacy: no raw content in standard-mode events or Nostr payloads
        timeline_raw = [d for d in raw if d.get("kind") != 4931]
        blob = json.dumps([e.to_dict() for e in base_sc.sink.events]) + json.dumps(timeline_raw)
        secrets = [t for t in _tags(base_sc)] + ["def ", "SECURITY NOTES", "literal comment"]
        out["content_leaks_standard"] = sum(blob.lower().count(s.lower()) for s in secrets)
        # kind-4931 snapshots carry content by design (visibility: relay contexts only)
        out["snapshot_events_with_content"] = sum(
            1 for d in raw if d.get("kind") == 4931 and "literal comment" in d.get("content", ""))
        # overhead (engine time only: the LLM answers instantly from the recording)
        levels = {}
        for name, kw in (("none", {"observability": "none"}), ("timeline", {}), ("nostr", {"nostr": True}),
                         ("nostr_down", {"nostr": True, "relay_down": True})):
            ts = []
            for _ in range(3):
                wd = tmp / f"ov_{name}"
                shutil.rmtree(wd, ignore_errors=True)
                wd.mkdir()
                _, _, secs = _replay_policy(task, records, policy, seed=seed, workdir=wd, **kw)
                ts.append(secs)
            levels[name] = statistics.median(ts)
        for name, v in levels.items():
            out[f"overhead_{name}_s"] = round(v, 3)
        out["overhead_timeline_pct"] = round(100 * (levels["timeline"] / levels["none"] - 1), 1)
        out["overhead_nostr_pct"] = round(100 * (levels["nostr"] / levels["none"] - 1), 1)
        n_ev = max(1, len(base_sc.sink.events))
        out["overhead_timeline_ms_per_event"] = round(1000 * (levels["timeline"] - levels["none"]) / n_ev, 4)
        out["overhead_nostr_ms_per_event"] = round(1000 * (levels["nostr"] - levels["timeline"]) / n_ev, 4)
        out["timeline_events"] = len(base_sc.sink.events)
        out["timeline_bytes"] = len(json.dumps([e.to_dict() for e in base_sc.sink.events]))
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _safe_verify(d) -> bool:
    try:
        return bool(verify_event(d))
    except Exception:  # noqa: BLE001
        return False


# ----------------------------------------------------------------------------
# RQ3: fault injection campaign
# ----------------------------------------------------------------------------

def _forge_snapshot(sc: Scenario, *, signer, as_agent="SecurityReviewer", namespace=None, version=None,
                    items=None, previous=None, digest=None, context_id=CTX, tamper=False) -> dict:
    from agentm2m.context.model import items_digest
    from agentm2m.context.nostr_sync import NOSTR_KIND_CONTEXT
    from agentm2m.nostr.events import UnsignedEvent

    cur = sc.peer_store.snapshot(CTX)
    its = items or [ContextItem(id="finding-code", type="security-finding", content="forged", author=as_agent)]
    its = [i if i.author else ContextItem(**{**i.to_dict(), "author": as_agent}) for i in its]
    real = items_digest(its)
    content = {"schema": "agentm2m.context-snapshot", "schema_version": 1,
               "namespace": namespace or sc.task.repo, "as_agent": as_agent,
               "previous_digest": previous or cur.digest,
               "snapshot": {"context_id": context_id, "version": version or cur.version + 1,
                            "digest": digest or real, "items": [i.to_dict() for i in its],
                            "created_by": as_agent, "ts": 0, "title": ""}}
    ev = signer.sign(UnsignedEvent(created_at=int(time.time()), kind=NOSTR_KIND_CONTEXT,
                                   tags=[["t", "agentm2m"], ["t", f"agentm2m-ctx-{sc.task.repo}"], ["context", context_id]],
                                   content=json.dumps(content))).to_dict()
    if tamper:
        ev["sig"] = ev["sig"][:-2] + ("00" if ev["sig"][-2:] != "00" else "11")
    return ev


def fault_campaign(task, records, *, seed) -> list[dict]:
    """Inject faults into a replayed ctxi team; each row records whether the fault was
    surfaced (detected) and whether state was silently corrupted."""
    rows: list[dict] = []
    tmp = Path(tempfile.mkdtemp(prefix="faults_"))
    counter = [0]

    def fresh(nostr=False, llm_mode=None, relay_down=False):
        counter[0] += 1
        wd = tmp / f"f{counter[0]}"
        wd.mkdir()
        replay = ReplayLLM(records)
        llm = FaultLLM(replay, llm_mode) if llm_mode else replay
        sc = Scenario(task, llm, "ctxi", seed=seed, workdir=wd, nostr=nostr, relay_down=relay_down)
        sc.run()
        return sc, replay

    def ev_types(sc, since=0):
        return Counter(e.event_type for e in sc.sink.events[since:])

    def row(fault, family, detected, signal, silent, **extra):
        rows.append({"repo": task.repo, "seed": seed, "step": "FAULT", "fault": fault, "family": family,
                     "detected": bool(detected), "signal": signal, "silent_corruption": bool(silent), **extra})

    # --- engine / context faults ------------------------------------------------
    def revision_calls(sc, replay):
        mark_ev, mark_sent = len(sc.sink.events), len(replay.sent)
        sc.revise("R1_test_finding_revised", "finding-test")
        return mark_ev, mark_sent

    def run_blocked(fault, mutate):
        sc, replay = fresh()
        mark_ev, mark_sent = revision_calls(sc, replay)
        mutate(sc)
        sc.run()
        t = ev_types(sc, mark_ev)
        new_prompts = replay.sent[mark_sent:]
        oracle_prompts_without_ctx = sum(1 for p in new_prompts if "Write a test oracle" in p and "Authorized shared context" not in p)
        detected = t["context.error"] > 0 and (t["binding.escalated"] + t["binding.blocked"]) > 0
        silent = sc.rt.acceptance_holds() or oracle_prompts_without_ctx > 0
        first = next((i for i, e in enumerate(sc.sink.events[mark_ev:]) if e.event_type == "context.error"), None)
        row(fault, "context-access", detected, "context.error+binding.escalated", silent,
            events_to_detection=first, phi_after=sc.rt.acceptance_holds(), unguarded_prompts=oracle_prompts_without_ctx)

    run_blocked("reader_revoked", lambda sc: sc.store.set_policy(CTX, ContextPolicy(
        owner="SecurityReviewer", writers=frozenset({"SecurityReviewer"}), readers=frozenset({"Developer"}))))
    run_blocked("context_expired", lambda sc: sc.store.set_policy(CTX, ContextPolicy(
        owner="SecurityReviewer", writers=frozenset({"SecurityReviewer"}), readers=frozenset({"Developer", "Tester"}),
        expiry=time.time() - 10)))

    def _undeclared(sc):
        sc.resolver.store = MemoryContextStore()
    run_blocked("context_missing", _undeclared)

    sc, _ = fresh()
    before = sc.store.snapshot(CTX).digest
    try:
        sc.store.update(CTX, as_agent="Developer", expected_version=sc.store.snapshot(CTX).version, items=[
            ContextItem(id="finding-code", type="security-finding", content="rogue", author="Developer")])
        denied = False
    except ContextError:
        denied = True
    row("unauthorized_write", "context-access", denied, "ContextError", sc.store.snapshot(CTX).digest != before)

    sc, _ = fresh()
    v = sc.store.snapshot(CTX).version
    sc.store.update(CTX, as_agent="SecurityReviewer", expected_version=v, items=[
        ContextItem(id="finding-extra-a", type="note", content="writer A")])
    try:
        sc.store.update(CTX, as_agent="SecurityReviewer", expected_version=v, items=[
            ContextItem(id="finding-extra-b", type="note", content="writer B")])
        conflict = False
    except ContextError as exc:
        conflict = "CONFLICT" in str(getattr(exc, "code", exc)).upper() or "conflict" in str(exc).lower()
    row("concurrent_writers", "context-access", conflict, "CONTEXT_CONFLICT", sc.store.snapshot(CTX).version != v + 1)

    sc, _ = fresh()
    try:
        sc.store.update(CTX, as_agent="SecurityReviewer", expected_version=sc.store.snapshot(CTX).version, items=[
            ContextItem(id="finding-code", type="security-finding", content="x", author="Developer")])
        refused = False
    except ContextError:
        refused = True
    row("forged_item_authorship", "context-access", refused, "ContextError", not refused)

    sc, _ = fresh()
    other = "unrelated-notes"
    sc.store.ensure(other, ContextPolicy(owner="SecurityReviewer", writers=frozenset({"SecurityReviewer"}),
                                         readers=frozenset({"Developer", "Tester"})))
    sc.store.update(other, as_agent="SecurityReviewer", expected_version=1,
                    items=[ContextItem(id="n1", type="note", content="unrelated")])
    stale = sorted(_obligation_set(sc))
    row("unrelated_context_changed", "benign-control", not stale, "no obligations", bool(stale), stale_bindings=len(stale))

    # --- limits: dependencies the design cannot see ------------------------------
    # A fault is "surfaced" only if the runtime reports it. These two are expected NOT to be:
    # they are the boundary of Proposition 1 (declared dependencies only) and of authorization
    # (a permitted writer may publish wrong knowledge).
    sc, _ = fresh()
    paths = write_rules(sc.workdir, "ctxi", sc.items())
    edited = paths["Arch2Code"].read_text().replace("explanation.'", "explanation. Also add a docstring.'", 1)
    paths["Arch2Code"].write_text(edited)
    _switch_rule(sc.rt, "Arch2Code", paths["Arch2Code"])
    stale = sorted(_obligation_set(sc))
    row("undeclared_dependency_edit", "design-limit", bool(stale),
        "prompt edited in the rule text (not a declared dependency): no obligation", sc.rt.acceptance_holds(),
        stale_bindings=len(stale))

    sc, _ = fresh()
    mark_ev = len(sc.sink.events)
    v = sc.store.snapshot(CTX).version
    sc.store.update(CTX, as_agent="SecurityReviewer", expected_version=v, items=[
        ContextItem(id="finding-code", type="security-finding", author="SecurityReviewer",
                    content="Security finding (code): every implementation MUST NOT contain any comment line.")])
    obliged = sorted(_obligation_set(sc))
    row("wrong_but_authorized_knowledge", "design-limit", False,
        "authorized writer published contradictory content: treated as an ordinary revision", True,
        obligations=len(obliged))

    # --- LLM faults -------------------------------------------------------------
    for mode, fault in (("garbage", "llm_rejects_value"), ("transport", "llm_transport_failure")):
        counter[0] += 1
        wd = tmp / f"f{counter[0]}"
        wd.mkdir()
        replay = ReplayLLM(records)
        sc = Scenario(task, FaultLLM(replay, mode), "ctxi", seed=seed, workdir=wd)
        _, rep, _ = sc.run()
        t = ev_types(sc)
        if mode == "garbage":
            detected = t["binding.escalated"] > 0 and not sc.rt.acceptance_holds()
        else:
            detected = t["engine.error"] > 0 and not sc.rt.acceptance_holds()
        row(fault, "llm", detected, "binding.escalated" if mode == "garbage" else "engine.error",
            sc.rt.acceptance_holds(), escalations=len(rep.escalations))

    # --- Nostr / sync faults ------------------------------------------------------
    def nostr_sc():
        sc, _ = fresh(nostr=True)
        return sc, sc.peer_sync(on_event=lambda k, i: seen.append((k, i)))

    seen: list = []
    stranger = KeySigner.generate()
    for fault, kw in (
        ("nostr_bad_signature", {"signer": None, "tamper": True}),
        ("nostr_wrong_pubkey", {"signer": stranger}),
        ("nostr_unknown_agent", {"signer": stranger, "as_agent": "Mallory"}),
        ("nostr_wrong_namespace", {"signer": None, "namespace": "other-team"}),
        ("nostr_unauthorized_writer", {"signer": "Developer", "as_agent": "Developer"}),
        ("nostr_tampered_content", {"signer": None, "digest": "0" * 64}),
        ("nostr_unknown_context", {"signer": None, "context_id": "invented-context"}),
        ("nostr_forked_base", {"signer": None, "previous": "f" * 64}),
    ):
        seen.clear()
        sc, peer = nostr_sc()
        # the peer starts converged with the origin
        peer.pull()
        before = sc.peer_store.snapshot(CTX).digest
        signer = kw.pop("signer")
        signer = sc.keys[signer] if isinstance(signer, str) else (signer or sc.keys["SecurityReviewer"])
        ev = _forge_snapshot(sc, signer=signer, **kw)
        rep = peer.receive(ev)
        rejected = len(rep["rejected"]) > 0
        row(fault, "nostr-integrity", rejected, rep["rejected"][0]["reason"][:60] if rejected else "none",
            sc.peer_store.snapshot(CTX).digest != before)

    sc, peer = nostr_sc()
    peer.pull()
    v = sc.peer_store.snapshot(CTX).version
    sc.revise("R1_test_finding_revised", "finding-test")
    again = peer.pull()
    twice = peer.pull()
    row("nostr_duplicate_delivery", "nostr-integrity", twice["applied"] == 0 and sc.peer_store.snapshot(CTX).version == v + 1,
        "idempotent re-pull", sc.peer_store.snapshot(CTX).digest != sc.store.snapshot(CTX).digest, applied_first=again["applied"])

    up, _ = fresh(nostr=True)
    down, _ = fresh(nostr=True, relay_down=True)
    depth = down.nostr_sink.outbox.depth()
    same = down.state_hash() == up.state_hash() and down.rt.acceptance_holds() == up.rt.acceptance_holds()
    down.relay.down = False
    down.nostr_sink._retry_at = 0.0
    down.nostr_sink.flush(force=True)
    row("nostr_relay_down", "nostr-availability", depth > 0 and down.nostr_sink.outbox.depth() == 0,
        "outbox depth>0, drained on flush", not same, outbox_depth=depth, state_identical_to_relay_up=same)

    shutil.rmtree(tmp, ignore_errors=True)
    return rows


# ----------------------------------------------------------------------------
# driver
# ----------------------------------------------------------------------------

def run_repo(repo: str, llm: LLMBackend, *, model: str, seed: int, fh, run_tests: bool, policies=POLICIES) -> None:
    task = load_devbench_task(repo)
    recorded: dict[str, tuple[list[dict], Scenario]] = {}
    for policy in policies:
        wd = Path(tempfile.mkdtemp(prefix=f"ctxstudy_{repo}_{policy}_"))
        print(f"[{datetime.now().isoformat(timespec='seconds')}] {model} s{seed} {repo} / {policy}", flush=True)
        try:
            rows, sc, rec = run_policy(task, llm, policy, seed=seed, workdir=wd, run_tests=run_tests)
            recorded[policy] = (rec.records, sc)
        except Exception as exc:  # noqa: BLE001 - record the failure honestly, keep going
            rows = [{"repo": repo, "policy": policy, "seed": seed, "step": "ERROR",
                     "error": f"{type(exc).__name__}: {exc}"}]
            print(f"   ERROR {rows[0]['error']}", flush=True)
        for r in rows:
            fh.write(json.dumps({"model": model, **r}) + "\n")
        fh.flush()
        shutil.rmtree(wd, ignore_errors=True)
    if "ctxi" in recorded:
        records, sc = recorded["ctxi"]
        try:
            obs = observability_study(task, records, "ctxi", seed=seed, base_hash=sc.state_hash())
            fh.write(json.dumps({"model": model, **obs}) + "\n")
            for r in fault_campaign(task, records, seed=seed):
                fh.write(json.dumps({"model": model, **r}) + "\n")
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            fh.write(json.dumps({"model": model, "repo": repo, "seed": seed, "step": "ERROR",
                                 "error": f"observability/faults: {type(exc).__name__}: {exc}"}) + "\n")
        fh.flush()


def main() -> int:
    from agentm2m.config import LLMConfig
    from agentm2m.llm.factory import make_backend

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repos", nargs="*", default=[r for r in ALL_REPOS if r not in ("TextCNN", "Hybrid_Images")])
    ap.add_argument("--llm", default="ollama")
    ap.add_argument("--model", default=None)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out-dir", default="evaluation/results/follow-up-study/raw")
    ap.add_argument("--no-tests", action="store_true", help="skip the DevBench canary/unit grading")
    ap.add_argument("--policies", nargs="*", default=list(POLICIES), choices=list(POLICIES),
                    help="subset of policies to run (the observability study needs ctxi)")
    args = ap.parse_args()
    cfg = LLMConfig.from_env()
    llm = make_backend(cfg, override_provider=args.llm, override_model=args.model)
    if hasattr(llm, "seed"):
        llm.seed = args.seed
    model = args.model or cfg.model
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"context_study_{model.replace(':', '-')}_s{args.seed}_{stamp}.jsonl"
    print(f"writing {path}", flush=True)
    with path.open("w") as fh:
        for repo in args.repos:
            run_repo(repo, llm, model=model, seed=args.seed, fh=fh, run_tests=not args.no_tests,
                     policies=tuple(args.policies))
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
