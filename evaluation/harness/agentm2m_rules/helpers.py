"""Helpers for the evaluation harness's own Issue->Plan->Patch rule modules
(evaluation/harness/agentm2m_config.py), loaded via each module's
`uses 'helpers.py';` declaration -- the same mechanism examples/01_devteam
uses (agentm2m.engine.helpers_loader).
"""
from __future__ import annotations


def not_blank(text: str) -> bool:
    return bool(text) and bool(text.strip())


def looks_like_patch(diff_text: str) -> bool:
    """Lenient patch-shape @check: a real end-to-end SWE-bench-solving
    agent is out of scope for this evaluation (Sec V: what's compared is
    hand-off discipline, not solving accuracy), so this only rejects
    empty/near-empty output rather than requiring a strict unified-diff
    grammar match."""
    return bool(diff_text) and len(diff_text.strip()) >= 10
