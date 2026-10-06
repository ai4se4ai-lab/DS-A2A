"""Regression test for examples/07_shared_context (HOT + shared context)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "07_shared_context" / "run.py"


def test_shared_context_example(tmp_path: Path):
    spec = importlib.util.spec_from_file_location("example07_run", EXAMPLE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.run_demo(tmp_path, "mock", quiet=True)
    assert out["phi"] is True
    assert out["findings"] == 2  # one review per accepted story's operation
    assert out["affected_first"] == 5  # 2 code bodies + 3 test oracles read the context
    assert out["consumers"] == [("Developer", "body"), ("Tester", "oracle")]
    agents = {d["agent"] for d in out["influence"]["direct"]}
    assert agents == {"Developer", "Tester"}
    assert {o["agent"] for o in out["second_update_obligations"]} == {"Developer", "Tester"}
    assert not any(o["binding"] in ("signature", "notes", "risk") for o in out["second_update_obligations"])
    assert out["context_reads"] > 0
