"""The follow-up study harness (evaluation/harness/follow_up_context_study.py)
on the deterministic mock backend: the pipeline's invariants, not model quality.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agentm2m.llm.mock_backend import MockBackend
from evaluation.harness import follow_up_context_study as study
from evaluation.harness.devbench_loader import load_devbench_task

TASK = load_devbench_task("geotext")


@pytest.fixture(scope="module")
def runs():
    out = {}
    for policy in study.POLICIES:
        wd = Path(tempfile.mkdtemp(prefix=f"t_{policy}_"))
        rows, sc, rec = study.run_policy(TASK, MockBackend(), policy, seed=1, workdir=wd, run_tests=False)
        out[policy] = (rows, sc, rec)
    return out


def _step(rows, step):
    return next(r for r in rows if r["step"] == step)


def test_item_level_pins_give_exact_impact(runs):
    rows = runs["ctxi"][0]
    # the mock cannot satisfy @check, so nothing is stamped; exactness is checked on stamped bindings elsewhere.
    # What must hold regardless: an unrelated item or an identical rewrite never obliges a consumer.
    assert _step(rows, "R3_unrelated_item_added")["predicted"] == 0
    assert _step(rows, "R4_identical_rewrite")["predicted"] == 0


def test_whole_context_pins_overapproximate_unrelated_items(runs):
    ctx_rows, sc, _ = runs["ctx"]
    # adding an unrelated item changes a whole-context pin: every consumer is re-run
    assert _step(ctx_rows, "R3_unrelated_item_added")["calls"] > 0
    assert _step(runs["ctxi"][0], "R3_unrelated_item_added")["calls"] == 0


def test_routed_paste_reruns_only_the_affected_consumer_kind(runs):
    rows = runs["routed"][0]
    r1, r2 = _step(rows, "R1_test_finding_revised"), _step(rows, "R2_code_finding_revised")
    assert r1["predicted"] == r1["truth"] and r2["predicted"] == r2["truth"]  # a hand-maintained table is exact
    assert _step(rows, "R3_unrelated_item_added")["calls"] == 0  # the style note is routed to nobody
    assert runs["paste"][0][2]["predicted"] > r1["predicted"]


def test_paste_reruns_everything_on_any_text_change(runs):
    rows = runs["paste"][0]
    assert _step(rows, "R3_unrelated_item_added")["calls"] > 0
    assert _step(rows, "R4_identical_rewrite")["calls"] == 0  # identical text: nothing to paste


def test_timeline_reproduces_cost_exactly(runs):
    for policy in study.POLICIES:
        m = _step(runs[policy][0], "METRICS")
        assert m["event_llm_calls"] == m["counted_llm_calls"]
        assert m["event_tokens"] == m["counted_tokens"]


def test_observability_control_and_faults(runs):
    rows, sc, rec = runs["ctxi"]
    obs = study.observability_study(TASK, rec.records, "ctxi", seed=1, base_hash=sc.state_hash())
    assert obs["replay_misses"] == 0 and obs["state_hash_matches_original"]
    for k in ("nostr", "nostr_down"):
        assert obs[f"{k}_state_identical"] and obs[f"{k}_prompts_identical"]
    assert obs["reconstruction_timeline"] == 1.0 and obs["reconstruction_relay"] == 1.0
    assert obs["content_leaks_standard"] == 0
    assert obs["sync_peer_converged"] and obs["outage_outbox_depth_after_flush"] == 0
    faults = study.fault_campaign(TASK, rec.records, seed=1)
    assert len(faults) == 21
    surfaced = [f for f in faults if f["family"] != "design-limit"]
    assert all(f["detected"] for f in surfaced), [f["fault"] for f in surfaced if not f["detected"]]
    assert not any(f["silent_corruption"] for f in surfaced)
    # the design's declared limits are expected to stay silent (Proposition 1 covers declared dependencies only)
    limits = {f["fault"]: f for f in faults if f["family"] == "design-limit"}
    assert set(limits) == {"undeclared_dependency_edit", "wrong_but_authorized_knowledge"}
    # (under the mock nothing is ever accepted, so only "no signal" is checked here; the study reports
    # `silent` with real models, where the edited rule leaves every accepted value fresh)
    assert not any(f["detected"] for f in limits.values())
    assert limits["undeclared_dependency_edit"]["stale_bindings"] == 0
    assert limits["wrong_but_authorized_knowledge"]["silent_corruption"]
