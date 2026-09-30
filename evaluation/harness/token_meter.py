"""Token metering for Table II's "LLM tokens per task (k)" row (Sec V).

Wraps `LLMBackend.generate` and tallies input/output tokens. Uses the
provider's own reported usage (`LLMBackend.last_usage`: Ollama's
prompt_eval_count/eval_count, OpenAI/Anthropic `usage`) when available,
and falls back to `LLMBackend.count_tokens`'s len/4 approximation
otherwise (e.g. the mock backend). `exact_calls` records how many calls
were counted exactly, so a mixed run is visible rather than silent.

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
    exact_calls: int = field(default=0)

    def generate(self, prompt: str, *, temperature: float = 0.2) -> str:
        response = self.backend.generate(prompt, temperature=temperature)
        usage = getattr(self.backend, "last_usage", None)
        if usage is not None:
            self.input_tokens += usage[0]
            self.output_tokens += usage[1]
            self.exact_calls += 1
        else:
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
