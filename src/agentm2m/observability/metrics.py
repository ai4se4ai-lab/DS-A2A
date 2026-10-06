"""Metrics from the event timeline (no access to engine state needed).

Operational (spec section 71): run / hand-off / binding durations, LLM
calls, tokens, rejections, escalations, obligations, context reads,
writes and versions. Research (section 72):

    context_reuse                distinct agents / bindings whose accepted values pinned a context
    context_amplification        per context version: accepted bindings derived from it
    context_induced_obligations  obligations whose cause includes a context change (deduplicated)
    collaboration_latency_s      context version published -> first accepted value derived from it
    observability_coverage       engine-reported transitions that appear as events

Counts that the engine may re-report across passes or calls (obligations,
escalations) are deduplicated by (target, binding, footprint version).
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable
from typing import Any


def _as_dicts(events: Iterable[Any]) -> list[dict]:
    return [e.to_dict() if hasattr(e, "to_dict") else e for e in events]


def _summary(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0, "total": 0.0, "mean": 0.0, "max": 0.0}
    return {"n": len(xs), "total": round(sum(xs), 6), "mean": round(statistics.fmean(xs), 6),
            "max": round(max(xs), 6)}


def compute_metrics(events: Iterable[Any]) -> dict:
    evs = sorted(_as_dicts(events), key=lambda e: (e["ts"], e.get("seq", 0)))
    by_type: dict[str, list[dict]] = defaultdict(list)
    for e in evs:
        by_type[e["event_type"]].append(e)

    # durations -------------------------------------------------------------
    runs: list[float] = []
    started: dict[str | None, float] = {}
    for e in evs:
        if e["event_type"] == "team.started":
            started[e["run_id"]] = e["ts"]
        elif e["event_type"] in ("team.completed", "team.failed") and e["run_id"] in started:
            runs.append(e["ts"] - started.pop(e["run_id"]))
    handoffs: dict[str, list[float]] = defaultdict(list)
    open_h: dict[tuple, float] = {}
    for e in evs:
        key = (e["run_id"], e["handoff_id"])
        if e["event_type"] == "handoff.started":
            open_h[key] = e["ts"]
        elif e["event_type"] == "handoff.completed" and key in open_h:
            handoffs[e["handoff_id"]].append(e["ts"] - open_h.pop(key))

    accepted = by_type["binding.accepted"]
    engine_accepted = [e for e in accepted if not e["payload"].get("host")]
    engine_rejected = [e for e in by_type["binding.rejected"] if not e["payload"].get("host")]
    transport = [e for e in by_type["engine.error"] if e["payload"].get("kind") == "llm_transport"]
    tok_in = sum(int(e["payload"].get("input_tokens") or 0) for e in engine_accepted + engine_rejected)
    tok_out = sum(int(e["payload"].get("output_tokens") or 0) for e in engine_accepted + engine_rejected)
    latency_ms = [float(e["payload"]["duration_ms"]) for e in engine_accepted if "duration_ms" in e["payload"]]

    def dedup(items: list[dict]) -> set[tuple]:
        return {(e["target_key"], e["binding"], e["payload"].get("footprint_version")) for e in items}

    obligations = by_type["obligation.created"]
    ctx_obligations = [e for e in obligations if "context" in (e["payload"].get("cause") or [])]

    # shared context -----------------------------------------------------------
    versions: dict[str, int] = {}
    for t in ("context.created", "context.updated", "context.read", "context.received"):
        for e in by_type[t]:
            v = e["payload"].get("version")
            for cid in e["context_ids"]:
                if isinstance(v, int):
                    versions[cid] = max(versions.get(cid, 0), v)
    reuse_agents: dict[str, set] = defaultdict(set)
    reuse_bindings: dict[str, set] = defaultdict(set)
    amplification: dict[str, set] = defaultdict(set)
    first_use: dict[str, float] = {}
    for e in accepted:
        for pin in e["payload"].get("pins") or []:
            cid, v = pin["context_id"], pin["version"]
            reuse_agents[cid].add(e["agent_id"])
            reuse_bindings[cid].add((e["target_key"], e["binding"]))
            key = f"{cid}@{v}"
            amplification[key].add((e["target_key"], e["binding"]))
            first_use.setdefault(key, e["ts"])
    published = {}
    for e in by_type["context.created"] + by_type["context.updated"] + by_type["context.received"]:
        for cid in e["context_ids"]:
            published.setdefault(f"{cid}@{e['payload'].get('version')}", e["ts"])
    latency = {k: round(first_use[k] - published[k], 6) for k in first_use if k in published}

    return {
        "runs": len(by_type["team.started"]),
        "run_duration_s": _summary(runs),
        "handoff_duration_s": {h: _summary(xs) for h, xs in sorted(handoffs.items())},
        "binding_latency_ms": _summary(latency_ms),
        "bindings_accepted": len(accepted),
        "llm_calls": len(engine_accepted) + len(engine_rejected) + len(transport),
        "host_submissions": len(by_type["binding.submitted"]),
        "input_tokens": tok_in,
        "output_tokens": tok_out,
        "rejections": len(by_type["binding.rejected"]),
        "retries": len(engine_rejected),
        "escalations": len(dedup(by_type["binding.escalated"])),
        "obligations_created": len(dedup(obligations)),
        "obligations_discharged": len(by_type["obligation.discharged"]),
        "context_reads": len(by_type["context.read"]),
        "context_writes": len(by_type["context.created"]) + len(by_type["context.updated"]),
        "context_versions": dict(sorted(versions.items())),
        "context_reuse": {c: {"agents": sorted(a for a in reuse_agents[c] if a), "bindings": len(reuse_bindings[c])}
                          for c in sorted(reuse_agents)},
        "context_amplification": {k: len(v) for k, v in sorted(amplification.items())},
        "context_induced_obligations": len(dedup(ctx_obligations)),
        "collaboration_latency_s": dict(sorted(latency.items())),
        "errors": {t: len(by_type[t]) for t in sorted(by_type) if t.endswith(".error")},
    }


def observability_coverage(run_result: dict, events: Iterable[Any]) -> dict:
    """Engine-reported state transitions of one `Workspace.run` result that
    appear as events of that run: targets created/deleted, values sampled,
    escalations."""
    evs = _as_dicts(events)
    h = run_result.get("handoffs", {})
    expected = (sum(x["created"] + x["deleted"] + x["resampled"] for x in h.values())
                + len({(e["target_key"], e["binding"]) for e in run_result.get("escalations", [])}))
    seen = {t: [e for e in evs if e["event_type"] == t] for t in
            ("handoff.target_created", "handoff.target_deleted", "binding.accepted", "binding.escalated")}
    observed = (len(seen["handoff.target_created"]) + len(seen["handoff.target_deleted"])
                + len([e for e in seen["binding.accepted"] if not e["payload"].get("host")])
                + len({(e["target_key"], e["binding"]) for e in seen["binding.escalated"]}))
    return {"expected": expected, "observed": observed,
            "coverage": round(observed / expected, 6) if expected else 1.0}
