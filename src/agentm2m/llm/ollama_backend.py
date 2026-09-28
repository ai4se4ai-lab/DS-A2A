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

    def __init__(self, base_url: str, model: str, *, auto_pull: bool = True, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
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
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": temperature},
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(f"Ollama generate() failed: {exc}") from exc
        return resp.json().get("response", "").strip()
