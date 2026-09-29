"""Local Ollama backend (default). Talks to a locally running `ollama serve`.

Auto-detects whether the configured model tag is pulled; if not, pulls it
(streaming) before the first generation. This is what makes `examples/`
runnable with no API key: `ollama serve` + this backend is enough.
"""
from __future__ import annotations

import json

import requests

from .base import LLMBackend, LLMError


class OllamaBackend(LLMBackend):
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        auto_pull: bool = True,
        timeout: float = 120.0,
        max_tokens: int = 800,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        # Every `@llm` binding in this codebase expects a short, specific
        # answer (a signature line, a short function body, a brief prose
        # summary); with no cap, a smaller/repetition-prone model can run
        # away generating thousands of tokens of degenerate output for a
        # single call, which both wastes wall-clock time and can exceed
        # `timeout` outright (observed in practice: a single call passing
        # 2700+ generated tokens and still climbing). Capping bounds worst-
        # case cost without changing what's being measured (coordination
        # structure, not raw generation length).
        self.max_tokens = max_tokens
        if auto_pull:
            self._ensure_model_available()

    def _ensure_model_available(self) -> None:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(
                f"Could not reach Ollama at {self.base_url}. Is `ollama serve` running? ({exc})"
            ) from exc
        tags = {m["name"] for m in resp.json().get("models", [])}
        if self.model in tags:
            return
        if ":" not in self.model:
            # No explicit tag requested (e.g. "llama3.1"): any pulled variant
            # of that base model counts as available (ollama defaults to
            # ":latest"). A model requested *with* an explicit tag (e.g.
            # "qwen2.5:7b") must match that tag exactly -- a differently
            # sized/quantized variant sharing the base name (e.g. an
            # already-pulled "qwen2.5:3b") is a different model, not a match.
            base_tags = {t.split(":")[0] for t in tags}
            if self.model in base_tags:
                return
        self._pull()

    def _pull(self) -> None:
        with requests.post(
            f"{self.base_url}/api/pull",
            json={"name": self.model, "stream": True},
            stream=True,
            timeout=self.timeout,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                status = json.loads(line).get("status", "")
                if status.startswith("error"):
                    raise LLMError(f"ollama pull {self.model} failed: {status}")

    def generate(self, prompt: str, *, temperature: float = 0.2) -> str:
        self.last_usage = None
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": temperature, "num_predict": self.max_tokens},
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(f"Ollama generate() failed: {exc}") from exc
        data = resp.json()
        if "prompt_eval_count" in data or "eval_count" in data:
            self.last_usage = (int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0)))
        return data.get("response", "").strip()
