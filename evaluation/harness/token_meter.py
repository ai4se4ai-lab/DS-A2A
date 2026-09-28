"""Token metering for Table II's "LLM tokens per task (k)" row (Sec V).

Wraps `LLMBackend.generate` and tallies input/output tokens via
`LLMBackend.count_tokens`, the cheap provider-agnostic approximation
already on the base class (src/agentm2m/llm/base.py) -- used instead of a
provider's own usage stats because Ollama/local-model backends don't
expose exact counts, and we want one consistent metric across all three
LLM providers this evaluation might run against.

A `TokenMeter` is a drop-in replacement for an `LLMBackend` wherever only
`.generate(...)` is called (TeamRuntime, the *_config.py runners,
mast_annotator.annotate): it forwards the call and tallies as a side
effect, and `__getattr__` delegates anything else (e.g. `.name`) to the
wrapped backend.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agentm2m.llm.base import LLMBackend


@dataclass
class TokenMeter:
    backend: LLMBackend
    input_tokens: int = field(default=0)
    output_tokens: int = field(default=0)
    calls: int = field(default=0)

    def generate(self, prompt: str, *, temperature: float = 0.2) -> str:
        response = self.backend.generate(prompt, temperature=temperature)
        self.input_tokens += self.backend.count_tokens(prompt)
        self.output_tokens += self.backend.count_tokens(response)
        self.calls += 1
        return response

    def count_tokens(self, text: str) -> int:
        return self.backend.count_tokens(text)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __getattr__(self, item):
        return getattr(self.backend, item)
