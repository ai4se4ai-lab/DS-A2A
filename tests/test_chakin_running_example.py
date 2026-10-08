"""Keeps the running example of docs/follow-up-study-DS-A2A.tex (Sections
II-III, Tables II-III) honest: every count it quotes comes from this run."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation.harness.chakin_running_example import run

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "evaluation/benchmarks/cache/devbench/python/chakin").is_dir(), reason="DevBench cache missing")


@pytest.fixture(scope="module")
def steps():
    rep = run(None)
    return {s["step"]: s for s in rep["steps"]}, rep


def test_item_level_pins_oblige_exactly_the_declared_consumers(steps):
    by, _ = steps
    both = {"Developer:body:context": 9, "Tester:oracle:context": 9}
    assert (by["pin"]["llm_calls"], by["pin"]["obligations"]) == (18, both)
    assert (by["S1"]["context_version"], by["S1"]["llm_calls"], by["S1"]["obligations"]) == (3, 18, both)
    assert (by["S2"]["llm_calls"], by["S2"]["obligations"]) == (9, {"Developer:body:context": 9})
    assert (by["S3"]["context_version"], by["S3"]["llm_calls"], by["S3"]["obligations"]) == (5, 0, {})
    assert (by["S4"]["context_version"], by["S4"]["llm_calls"]) == (5, 0)  # identical content: no version


def test_source_edit_and_revocation(steps):
    by, _ = steps
    assert by["edit"]["llm_calls"] == 3
    assert by["edit"]["obligations"] == {"Architect:signature:source": 1, "Developer:body:source": 1,
                                         "Tester:oracle:source": 1}
    assert by["revoke"]["blocked"] == ["oracle"] and by["revoke"]["phi"] is False
    assert by["revoke"]["llm_calls"] == 0
    assert by["restore"]["phi"] is True and by["restore"]["llm_calls"] == 0


def test_influence_of_finding_path(steps):
    _, rep = steps
    assert sum(rep["influence_finding_path"].values()) == 18
