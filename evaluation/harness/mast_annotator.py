"""LLM-based MAST failure-mode annotator for a run's transcript (Sec V
"Design": "Traces are labelled with the MAST LLM annotator released by
Cemri et al.").

APPROXIMATION NOTICE: Cemri et al.'s original MAST LLM annotator
(arXiv:2503.13657, `cemri2025mast`) has not been publicly released. This
module is *our own approximation* of it: one `@llm`-style classification
call given the 14-mode taxonomy (Table I / `tab:mast` in docs/DS-A2A.tex)
and one run's transcript, asked to return which modes are present as a
small JSON object. It is not a faithful reproduction of the original
tool -- that is exactly why manual_relabel.py exists: to let a human
check/correct a sample of this annotator's output, as Sec V's protocol
calls for ("a sample is re-labelled manually").
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from agentm2m.llm.base import LLMBackend

# Table I (tab:mast): mode id -> name and the coordination deficit it maps
# to ("-" for modes the paper marks as outside a coordination layer's
# reach, e.g. FM-1.1/2.2/2.6 -- kept here for completeness/negative
# controls even though Table II only aggregates P1/P2 counts).
MAST_MODES: dict[str, dict[str, str]] = {
    "1.1": {"name": "Disobey task specification", "deficit": "-"},
    "1.2": {"name": "Disobey role specification", "deficit": "P3"},
    "1.3": {"name": "Step repetition", "deficit": "P2"},
    "1.4": {"name": "Loss of conversation history", "deficit": "P1"},
    "1.5": {"name": "Unaware of termination conditions", "deficit": "P3"},
    "2.1": {"name": "Conversation reset", "deficit": "P1"},
    "2.2": {"name": "Fail to ask for clarification", "deficit": "-"},
    "2.3": {"name": "Task derailment", "deficit": "P2"},
    "2.4": {"name": "Information withholding", "deficit": "P1"},
    "2.5": {"name": "Ignored other agent's input", "deficit": "P1"},
    "2.6": {"name": "Reasoning-action mismatch", "deficit": "-"},
    "3.1": {"name": "Premature termination", "deficit": "P2"},
    "3.2": {"name": "No or incomplete verification", "deficit": "P2"},
    "3.3": {"name": "Incorrect verification", "deficit": "P2"},
}

P1_MODES = {mid for mid, v in MAST_MODES.items() if v["deficit"] == "P1"}
P2_MODES = {mid for mid, v in MAST_MODES.items() if v["deficit"] == "P2"}


@dataclass
class MastAnnotation:
    instance_id: str
    config: str
    modes_present: list[str] = field(default_factory=list)
    raw_response: str = ""

    @property
    def p1_count(self) -> int:
        return len(set(self.modes_present) & P1_MODES)

    @property
    def p2_count(self) -> int:
        return len(set(self.modes_present) & P2_MODES)


def _build_prompt(transcript: str) -> str:
    taxonomy_lines = "\n".join(f"{mid}: {info['name']}" for mid, info in MAST_MODES.items())
    return (
        "You are annotating a multi-agent LLM transcript against the MAST failure-mode "
        "taxonomy (Cemri et al., 2025). Given the taxonomy below and a transcript of an "
        "agent hand-off pipeline, list ONLY the failure-mode ids that clearly occur in "
        "this transcript.\n\n"
        f"Taxonomy:\n{taxonomy_lines}\n\n"
        f"Transcript:\n{transcript}\n\n"
        'Respond with ONLY a JSON object of the form {"modes_present": ["1.4", "2.1"]} '
        "(use an empty list if none apply)."
    )


def annotate(instance_id: str, config: str, transcript: str, llm: LLMBackend, *, temperature: float = 0.0) -> MastAnnotation:
    prompt = _build_prompt(transcript)
    raw = llm.generate(prompt, temperature=temperature)
    return MastAnnotation(instance_id=instance_id, config=config, modes_present=_parse_modes(raw), raw_response=raw)


def _parse_modes(raw: str) -> list[str]:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    candidate = match.group(0) if match else raw
    try:
        data = json.loads(candidate)
        modes = data.get("modes_present", [])
        return sorted({m for m in modes if m in MAST_MODES})
    except (json.JSONDecodeError, AttributeError, TypeError):
        # Fallback for a backend that doesn't reliably emit JSON (e.g. a
        # small local model, or MockBackend's generic template): scan the
        # raw text for mode-id-shaped tokens rather than crashing.
        return sorted({mid for mid in MAST_MODES if re.search(rf"\b{re.escape(mid)}\b", raw)})
