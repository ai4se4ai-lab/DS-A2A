"""Shared-context evaluation: the 4-configuration matrix (spec sections 72-75).

Scenario: the DevTeam (Analyst -> Architect -> Developer, Analyst -> Tester)
plus a Security Reviewer who contributes a security finding that should
shape the Developer's code and the Tester's oracles. The finding text is
fixed (not sampled), so every configuration receives identical knowledge
and only *how it travels* differs:

| Configuration              | Structured hand-off | Traceability | Shared context | Nostr |
|----------------------------|---------------------|--------------|----------------|-------|
| free_text                  | no                  | no           | no (pasted)    | no    |
| agentm2m                   | yes                 | yes          | no             | no    |
| agentm2m_context           | yes                 | yes          | yes            | no    |
| agentm2m_context_nostr     | yes                 | yes          | yes            | yes   |

Measured per configuration: task success (phi; free text is unvalidated),
information retention (share of downstream artifacts that carry the
finding's marker, before and after the finding is revised), change precision/recall when the finding is revised
(against the ground-truth impact set: every code body and test oracle),
LLM calls and tokens (initial and for the change), latency, context reuse,
coordination failures (escalations), observability coverage and
context-induced obligations.

Experimental control (section 75): agentm2m_context and
agentm2m_context_nostr use the same model, prompts, rules, footprints,
context, temperature and resample budget; the harness asserts that the two
runs sent exactly the same multiset of prompts, so Nostr can only change
observability, never task performance.

    python -m evaluation.harness.context_eval --llm mock --out evaluation/results/context

With the deterministic mock backend the retention numbers only exercise the
pipeline (the mock ignores context); use a real model for findings.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agentm2m.llm.base import LLMBackend
from agentm2m.nostr.outbox import Outbox
from agentm2m.nostr.publisher import NostrEventSink
from agentm2m.nostr.relay import MemoryRelay
from agentm2m.nostr.signer import KeySigner
from agentm2m.observability.metrics import compute_metrics, observability_coverage
from agentm2m.workspace import Workspace

CONFIGS = ("free_text", "agentm2m", "agentm2m_context", "agentm2m_context_nostr")

FINDING_V1 = {"id": "finding-auth", "type": "security-finding",
              "content": "Every task endpoint must verify an AUTH-TOKEN before routing the request."}
MARKER_V1 = "auth-token"
FINDING_V2 = {"id": "finding-auth", "type": "security-finding",
              "content": "Every task endpoint must verify an AUTH-TOKEN before routing and write an "
                         "AUDIT-TRAIL entry for each state change."}
MARKER_V2 = "audit-trail"


class CountingBackend(LLMBackend):
    """Wraps the configuration's LLM: counts calls/tokens/latency and keeps
    each prompt's digest for the experimental control."""

    def __init__(self, inner: LLMBackend) -> None:
        self.inner = inner
        self.name = getattr(inner, "name", "llm")
        self.calls: list[dict] = []
        self.last_usage = None

    def generate(self, prompt: str, *, temperature: float = 0.2) -> str:
        t0 = time.perf_counter()
        out = self.inner.generate(prompt, temperature=temperature)
        usage = getattr(self.inner, "last_usage", None) or (self.inner.count_tokens(prompt),
                                                            self.inner.count_tokens(out))
        self.last_usage = usage
        self.calls.append({"digest": hashlib.sha256(prompt.encode()).hexdigest(), "in": usage[0], "out": usage[1],
                           "s": time.perf_counter() - t0})
        return out

    def count_tokens(self, text: str) -> int:
        return self.inner.count_tokens(text)

    def mark(self) -> int:
        return len(self.calls)

    def since(self, mark: int) -> dict:
        part = self.calls[mark:]
        return {"calls": len(part), "input_tokens": sum(c["in"] for c in part),
                "output_tokens": sum(c["out"] for c in part), "llm_seconds": round(sum(c["s"] for c in part), 4)}


@dataclass
class ConfigResult:
    config: str
    task_success: bool | None
    retention: float
    retention_after_change: float
    change_precision: float | None
    change_recall: float
    initial: dict
    change: dict
    latency_s: float
    escalations: int = 0
    context_reuse_agents: int = 0
    context_reuse_bindings: int = 0
    context_induced_obligations: int = 0
    observability_coverage: float | None = None
    nostr_events_verified: int | None = None
    prompt_digests: list[str] = field(default_factory=list, repr=False)

    def row(self) -> dict:
        return {
            "config": self.config,
            "task_success": "" if self.task_success is None else int(self.task_success),
            "retention": round(self.retention, 4),
            "retention_after_change": round(self.retention_after_change, 4),
            "change_precision": "" if self.change_precision is None else round(self.change_precision, 4),
            "change_recall": round(self.change_recall, 4),
            "llm_calls": self.initial["calls"],
            "input_tokens": self.initial["input_tokens"],
            "output_tokens": self.initial["output_tokens"],
            "change_llm_calls": self.change["calls"],
            "change_tokens": self.change["input_tokens"] + self.change["output_tokens"],
            "latency_s": round(self.latency_s, 4),
            "escalations": self.escalations,
            "context_reuse_agents": self.context_reuse_agents,
            "context_reuse_bindings": self.context_reuse_bindings,
            "context_induced_obligations": self.context_induced_obligations,
            "observability_coverage": "" if self.observability_coverage is None else self.observability_coverage,
            "nostr_events_verified": "" if self.nostr_events_verified is None else self.nostr_events_verified,
        }


def _has(text: Any, marker: str) -> bool:
    return marker in str(text or "").lower()


# ----------------------------------------------------------------------------
# Free-text baseline: the finding must be pasted into each downstream prompt
# ----------------------------------------------------------------------------

def run_free_text(llm: LLMBackend, *, temperature: float = 0.2) -> ConfigResult:
    llm = CountingBackend(llm)
    spec = yaml.safe_load((Path(__file__).resolve().parents[2] / "src/agentm2m/templates/devteam/team.yaml")
                          .read_text())
    stories = spec["views"]["Req"]["seed"]["stories"]
    req_text = "\n".join(f"- {s['id']} ({s['status']}): {s['title']}; criteria: "
                         + "; ".join(c["text"] for c in s["criteria"]) for s in stories)
    t0 = time.perf_counter()
    analyst = llm.generate(f"You are the Analyst. Summarize these user stories in free text:\n{req_text}",
                           temperature=temperature)
    architect = llm.generate("You are the Architect. From this free-text summary, derive API signatures "
                             f"for the accepted stories:\n{analyst}", temperature=temperature)

    def downstream(finding: dict) -> tuple[str, str]:
        dev = llm.generate("You are the Developer. Implement this design as Python code only. Security "
                           f"reviewer's note (pasted by hand): {finding['content']}\n\n{architect}",
                           temperature=temperature)
        tst = llm.generate("You are the Tester. Write test oracles (Python) for these requirements. Security "
                           f"reviewer's note (pasted by hand): {finding['content']}\n\n{analyst}",
                           temperature=temperature)
        return dev, tst

    dev, tst = downstream(FINDING_V1)
    initial = llm.since(0)
    retention = sum(_has(x, MARKER_V1) for x in (dev, tst)) / 2
    mark = llm.mark()
    dev2, tst2 = downstream(FINDING_V2)  # the operator must know to re-run both by hand
    change = llm.since(mark)
    after = sum(_has(x, MARKER_V2) for x in (dev2, tst2)) / 2
    return ConfigResult(
        config="free_text", task_success=None, retention=retention, retention_after_change=after,
        change_precision=1.0, change_recall=1.0,
        initial=initial, change=change, latency_s=time.perf_counter() - t0,
        prompt_digests=[c["digest"] for c in llm.calls],
    )


# ----------------------------------------------------------------------------
# AgentM2M configurations
# ----------------------------------------------------------------------------

def _impact_set(ws: Workspace) -> set[tuple[str, str]]:
    """Ground truth: every code body and test oracle should reflect the finding."""
    return {(s["target_key"], s["binding"]) for s in ws.binding_states() if s["binding"] in ("body", "oracle")}


def _artifacts(ws: Workspace) -> list[str]:
    code = [e.get("body") for e in ws.show("Code")["model"].get("edits", [])]
    tests = [e.get("oracle") for e in ws.show("Test")["model"].get("cases", [])]
    return [x for x in code + tests if x is not None]


def run_agentm2m(llm: LLMBackend, project: Path, *, context: bool, nostr: bool, max_resamples: int = 3,
                 temperature: float = 0.2) -> ConfigResult:
    counting = CountingBackend(llm)
    sink = None
    relay = None
    if nostr:
        relay = MemoryRelay()
        key = KeySigner.generate()
        sink = NostrEventSink(relay, Outbox(project / ".agentm2m/state/observability/outbox.jsonl"),
                              signer_for=lambda _a: key)

    def open_ws() -> Workspace:
        ws = Workspace(project, llm=counting, max_resamples=max_resamples, event_sink=sink)
        ws.temperature = temperature
        return ws

    t0 = time.perf_counter()
    ws = open_ws()
    ws.init("devteam", force=True)
    view = yaml.safe_load((ws.dir / "rules/extra/SecurityReviewer.view.yaml").read_text())
    ws.evolve("SecurityReviewer", "Sec", view, "Arch2Sec", "rules/extra/Arch2Sec.agentm2m")
    if context:
        spec = yaml.safe_load(ws.spec_path.read_text())
        spec["contexts"] = yaml.safe_load((ws.dir / "rules/extra/shared-context.yaml").read_text())["contexts"]
        for h in spec["handoffs"]:
            if h["name"] in ("Arch2Code", "Req2Test"):
                h["rule"] = f"rules/extra/{h['name']}.ctx.agentm2m"
        ws.spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))
        ws = open_ws()
        ws.context_update("security-review", as_agent="SecurityReviewer", expected_version=1, items=[FINDING_V1])
    runs = [ws.run()]
    initial = counting.since(0)
    retention_hits = [_has(a, MARKER_V1) for a in _artifacts(ws)]
    impacted = _impact_set(ws)

    # the finding is revised
    mark = counting.mark()
    if context:
        cur = ws.context_get("security-review", as_agent="SecurityReviewer")["version"]
        ws.context_update("security-review", as_agent="SecurityReviewer", expected_version=cur, items=[FINDING_V2])
    obliged = {(o["target_key"], o["binding"]) for o in ws.impact()["obligations"]}
    runs.append(ws.run())
    change = counting.since(mark)
    latency = time.perf_counter() - t0
    after_hits = [_has(a, MARKER_V2) for a in _artifacts(ws)]

    events = ws.events(limit=10**7)["events"]
    metrics = compute_metrics(events)
    coverage = min(observability_coverage(r, ws.events(run_id=r["run_id"], limit=10**7)["events"])["coverage"]
                   for r in runs)
    reuse = metrics["context_reuse"].get("security-review", {"agents": [], "bindings": 0})
    hit = len(obliged & impacted)
    verified = None
    if nostr:
        from agentm2m.nostr.verifier import verify_event

        raw = relay.query([{"#t": ["agentm2m"]}])
        verified = sum(1 for d in raw if verify_event(d))
    name = "agentm2m" + ("_context" if context else "") + ("_nostr" if nostr else "")
    return ConfigResult(
        config=name,
        task_success=bool(runs[-1]["phi"]),
        retention=(sum(retention_hits) / len(retention_hits)) if retention_hits else 0.0,
        retention_after_change=(sum(after_hits) / len(after_hits)) if after_hits else 0.0,
        change_precision=(hit / len(obliged)) if obliged else None,
        change_recall=hit / len(impacted) if impacted else 1.0,
        initial=initial, change=change, latency_s=latency,
        escalations=metrics["escalations"],
        context_reuse_agents=len(reuse["agents"]), context_reuse_bindings=reuse["bindings"],
        context_induced_obligations=metrics["context_induced_obligations"],
        observability_coverage=coverage, nostr_events_verified=verified,
        prompt_digests=[c["digest"] for c in counting.calls],
    )


def run_matrix(make_llm, workdir: Path, *, configs: tuple[str, ...] = CONFIGS) -> list[ConfigResult]:
    """`make_llm()` returns a fresh backend per configuration (same model and
    settings for all, so only the hand-off/collaboration discipline differs)."""
    results: list[ConfigResult] = []
    for cfg in configs:
        if cfg == "free_text":
            results.append(run_free_text(make_llm()))
        else:
            project = workdir / cfg
            project.mkdir(parents=True, exist_ok=True)
            results.append(run_agentm2m(make_llm(), project, context="context" in cfg, nostr=cfg.endswith("nostr")))
    by = {r.config: r for r in results}
    if "agentm2m_context" in by and "agentm2m_context_nostr" in by:
        a, b = by["agentm2m_context"], by["agentm2m_context_nostr"]
        if Counter(a.prompt_digests) != Counter(b.prompt_digests):
            raise AssertionError("experimental control violated: Nostr changed the prompts sent to the LLM")
    return results


def write_results(results: list[ConfigResult], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [r.row() for r in results]
    path = out_dir / "context_eval.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out_dir / "context_eval.json").write_text(json.dumps(rows, indent=2))
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--llm", default="mock", help="mock|ollama|openai|anthropic")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default="evaluation/results/context")
    args = ap.parse_args()
    from agentm2m.config import LLMConfig
    from agentm2m.llm.factory import make_backend

    def make_llm() -> LLMBackend:
        return make_backend(LLMConfig.from_env(), override_provider=args.llm, override_model=args.model)

    with tempfile.TemporaryDirectory(prefix="agentm2m-ctx-eval-") as tmp:
        results = run_matrix(make_llm, Path(tmp))
    path = write_results(results, Path(args.out))
    for r in results:
        print(json.dumps(r.row()))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
