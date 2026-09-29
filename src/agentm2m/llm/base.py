"""Pluggable LLM backend interface.

A stochastic binding samples v ~ D(prompt) (Definition 1 / Sec III-B of the
paper). `LLMBackend.generate` is that sampling function: it takes the fully
built prompt (prompt_text ⊕ footprint, already assembled by the engine so
the backend never sees anything the footprint didn't authorize) and returns
one sampled string.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMBackend(ABC):
    name: str = "base"
    # (input_tokens, output_tokens) the provider reported for the most recent
    # generate() call, when it reports them; None -> fall back to count_tokens.
    last_usage: tuple[int, int] | None = None

    @abstractmethod
    def generate(self, prompt: str, *, temperature: float = 0.2) -> str:
        """Return one sampled completion for `prompt`."""
        raise NotImplementedError

    def count_tokens(self, text: str) -> int:
        # Cheap, provider-agnostic approximation used for the token_meter in
        # evaluation/ when a provider doesn't expose exact usage counts.
        return max(1, len(text) // 4)


class LLMError(RuntimeError):
    """Raised when a backend fails to produce a completion (used to trigger escalation)."""
