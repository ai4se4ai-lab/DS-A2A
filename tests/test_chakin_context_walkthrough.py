"""Keeps docs/AgentM2M-explained.tex honest: the shared-context and
observability sections quote this run's output (stamps, digests, counts)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation.harness.chakin_context_walkthrough import run

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "evaluation/benchmarks/cache/devbench/python/chakin").is_dir(), reason="DevBench cache missing")


@pytest.fixture(scope="module")
def rep():
    return run(None)


def test_team_reaches_phi_and_hot(rep):
    assert rep["phi_initial"] and rep["phi_after_context"] and rep["phi_after_revision"]
    assert "path traversal" in rep["review_download"]["notes"]
    assert rep["review_download"]["risk_stamp"] == "8dec46b3fdb87fa5"


def test_context_prompt_pins_and_stamps(rep):
    assert rep["context_v2"]["digest"].startswith("47d98089c005")
    assert rep["context_v2"]["items"][0]["author"] == "SecurityReviewer"
    p = rep["prompt_body_download"]
    assert "[context security-review v2 sha256:47d98089c005] -- Security review findings" in p
    assert "- finding-path (security-finding, by SecurityReviewer):" in p
    link = rep["trace_link_body_download"]
    assert link["stamps"]["body"] == "3e0a20f81349f199"
    assert link["dependencies"]["body"] == {"source": "71599f67c7a21b83", "context": "00a4f03750fa6202",
                                            "effective": "3e0a20f81349f199"}
    assert link["context_pins"]["body"][0]["version"] == 2


def test_obligations_from_context(rep):
    assert len(rep["stale_after_switch"]) == 18
    assert len(rep["stale_after_revision"]) == 18 and rep["revision_calls"] == 18
    assert all(o["cause"] == ["context"] for o in rep["revision_obligations"])
    assert rep["context_v3_digest"].startswith("52bb563795b8")
    assert rep["tls_stale"] == [["body", "stale"]] and rep["outage"]["llm_calls"] == 9
    assert len(rep["influence_finding_path"]) == 18


def test_fail_safe_and_observability(rep):
    assert rep["blocked_after_revoke"] == ["oracle"] and rep["phi_after_revoke"] is False
    assert rep["presence_after_revoke"]["Tester"] == "failed" and rep["phi_after_restore"] is True
    assert "may not read context 'security-review' (not a reader)" in rep["escalations_after_revoke"][0]["reason"]
    assert rep["presence"] == {"Analyst": "offline", "Architect": "idle", "Developer": "idle",
                               "SecurityReviewer": "idle", "Tester": "idle"}
    m = rep["metrics"]
    assert (m["runs"], m["bindings_accepted"], m["obligations_created"], m["context_induced_obligations"],
            m["context_reads"], m["escalations"]) == (6, 81, 36, 36, 36, 9)
    assert m["context_reuse"]["security-review"] == {"agents": ["Developer", "Tester"], "bindings": 18}
    assert rep["nostr"]["published"] == rep["nostr"]["verified"] == rep["event_count"] == 677
    ex = rep["nostr"]["example"]
    assert ex["kind"] == 4930 and json.loads(ex["content"])["payload"]["pins"][0]["version"] == 2
    assert rep["outage"]["phi"] and rep["outage"]["outbox_depth"] == 94
    assert rep["outage"]["relay_attempts_while_down"] == 5
    assert rep["outage"]["flush"] == {"published": 94, "failed": 0, "remaining": 0}
    ev = {e["seq"]: e for e in rep["events_context_run"]}
    assert ev[323]["event_type"] == "binding.stale" and ev[323]["payload"]["cause"] == ["context"]
    assert ev[326]["payload"]["items"] == ["finding-path", "finding-tls"]
    assert ev[327]["payload"]["prompt_digest"] == "6428eda1a989b23d"
    assert ev[383]["agent_id"] == "Tester" and ev[383]["payload"]["items"] == ["finding-path"]
    assert ev[466]["event_type"] == "team.completed"
