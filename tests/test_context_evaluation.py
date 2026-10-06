"""Phase 23: smoke test of the shared-context evaluation matrix (mock LLM,
no network): all four configurations, the Nostr experimental control, CSV
and tables."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentm2m.llm.mock_backend import MockBackend
from evaluation.analysis.aggregate_context import load, to_latex, to_markdown
from evaluation.harness.context_eval import run_matrix, write_results


class MarkerEchoMock(MockBackend):
    """Test double for a model that *uses* what its prompt gives it: echoes
    any security marker it sees as a code comment (still valid Python)."""

    def generate(self, prompt: str, *, temperature: float = 0.2) -> str:
        out = super().generate(prompt, temperature=temperature)
        marks = [m for m in ("AUTH-TOKEN", "AUDIT-TRAIL") if m in prompt]
        if marks and ("def " in out):
            out = out.rstrip("\n") + "\n" + "".join(f"# security: {m.lower()}\n" for m in marks)
        return out


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    return {r.config: r for r in run_matrix(MarkerEchoMock, tmp_path_factory.mktemp("ctx-eval"))}


def test_matrix_shape_and_success(results):
    assert set(results) == {"free_text", "agentm2m", "agentm2m_context", "agentm2m_context_nostr"}
    assert results["free_text"].task_success is None  # nothing validates free text
    assert all(results[c].task_success for c in ("agentm2m", "agentm2m_context", "agentm2m_context_nostr"))


def test_information_retention(results):
    assert results["free_text"].retention == 1.0  # pasted by hand into every prompt
    assert results["agentm2m"].retention == 0.0  # no channel from the reviewer to code/tests
    assert results["agentm2m_context"].retention == 1.0
    assert results["agentm2m_context_nostr"].retention == 1.0
    # after the finding is revised, only channels that propagate it carry the new marker
    assert results["agentm2m"].retention_after_change == 0.0
    assert results["agentm2m_context"].retention_after_change == 1.0
    assert results["free_text"].retention_after_change == 1.0


def test_change_propagation(results):
    plain, ctx = results["agentm2m"], results["agentm2m_context"]
    assert plain.change_recall == 0.0 and plain.change["calls"] == 0
    assert ctx.change_recall == 1.0 and ctx.change_precision == 1.0
    assert ctx.change["calls"] == 5 and ctx.context_induced_obligations == 5
    assert ctx.context_reuse_agents == 2 and ctx.context_reuse_bindings == 5


def test_nostr_is_a_pure_observability_change(results):
    ctx, nostr = results["agentm2m_context"], results["agentm2m_context_nostr"]
    assert sorted(ctx.prompt_digests) == sorted(nostr.prompt_digests)  # also asserted by run_matrix
    for k in ("retention", "change_precision", "change_recall", "task_success"):
        assert getattr(ctx, k) == getattr(nostr, k)
    assert ctx.initial["calls"] == nostr.initial["calls"]
    assert nostr.nostr_events_verified > 0 and ctx.nostr_events_verified is None
    assert ctx.observability_coverage == nostr.observability_coverage == 1.0


def test_csv_and_tables(results, tmp_path: Path):
    path = write_results(list(results.values()), tmp_path)
    rows = load(path)
    assert [r["config"] for r in rows] == list(results)
    md = to_markdown(rows)
    assert "AgentM2M + Context + Nostr" in md and md.count("\n") == len(rows) + 1
    assert to_latex(rows).startswith(r"\begin{tabular}")
