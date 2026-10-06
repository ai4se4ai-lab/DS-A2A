"""What an event payload may reveal.

    minimal   status, ids, digests, durations, counts
    standard  + footprint/context metadata (pins, versions), token counts   (default)
    debug     + raw payload fields, each only behind its explicit include_* flag

Raw prompts, sampled values, rejection reasons (they can echo values),
footprints and context content are never published unless debug mode *and*
the matching flag allow it; otherwise they become `<field>_digest` +
`<field>_chars`, so an observer can still prove *which* prompt or context
an agent saw without learning what it said.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

MODES = ("minimal", "standard", "debug")

# payload field -> PrivacyConfig flag that allows it raw (debug mode only)
SENSITIVE = {
    "prompt": "include_prompts",
    "value": "include_outputs",
    "reason": "include_outputs",
    "footprint": "include_source_content",
    "context_content": "include_context_content",
}
# metadata shown in standard and debug, dropped in minimal
STANDARD_ONLY = {"pins", "context", "footprint_meta", "input_tokens", "output_tokens", "items", "readers", "writers"}


@dataclass(frozen=True)
class PrivacyConfig:
    mode: str = "standard"
    include_prompts: bool = False
    include_outputs: bool = False
    include_source_content: bool = False
    include_context_content: bool = False

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"privacy mode must be one of {MODES}, got {self.mode!r}")


def content_digest(value: Any) -> str:
    raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def redact(payload: dict[str, Any], cfg: PrivacyConfig) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key in SENSITIVE:
            if cfg.mode == "debug" and getattr(cfg, SENSITIVE[key]):
                out[key] = value
            else:
                out[f"{key}_digest"] = content_digest(value)
                if cfg.mode != "minimal" or key != "reason":
                    out[f"{key}_chars"] = len(value) if isinstance(value, str) else len(json.dumps(value, default=str))
        elif key in STANDARD_ONLY:
            if cfg.mode != "minimal":
                out[key] = value
        else:
            out[key] = value
    if cfg.mode == "minimal":
        out = {k: v for k, v in out.items() if not k.endswith("_chars")}
    return out
