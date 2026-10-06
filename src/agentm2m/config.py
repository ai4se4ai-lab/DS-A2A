"""Loads LLM backend configuration from a .env file / environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> None:
    # Walk up from the CWD looking for a .env, then fall back to repo root.
    here = Path.cwd()
    for candidate in [here, *here.parents]:
        env_path = candidate / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
            return
    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(repo_root / ".env", override=False)


_load_env()


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    ollama_base_url: str
    openai_api_key: str | None
    openai_base_url: str
    anthropic_api_key: str | None
    temperature: float
    max_resamples: int

    @classmethod
    def from_env(cls) -> LLMConfig:
        return cls(
            provider=os.getenv("LLM_PROVIDER", "ollama").strip().lower(),
            model=os.getenv("LLM_MODEL", "qwen3:8b").strip(),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
            max_resamples=int(os.getenv("LLM_MAX_RESAMPLES", "3")),
        )


# ----------------------------------------------------------------------------
# Workspace configuration: .agentm2m/config.yaml (optional; absent = defaults)
# ----------------------------------------------------------------------------
#
#   observability:
#     timeline: true                 # local JSONL timeline (state/observability/events.jsonl)
#     privacy: {mode: standard, include_prompts: false, include_outputs: false,
#               include_source_content: false, include_context_content: false}
#   nostr:
#     enabled: false
#     relays: [wss://relay.example.com]
#     engine_identity: engine        # key name in .agentm2m/secrets/ (created on first use)
#     kind: 4930                     # regular kind: timeline events
#     presence_kind: 34930           # addressable kind: agent presence (d=<agent>)
#     timeout: 3                     # seconds per relay round trip
#     publish: {execution: true, agents: true, bindings: true, traces: true,
#               contexts: true, errors: true}
#   context:
#     enabled: true
#     backend: local
#
# Environment overrides: AGENTM2M_NOSTR=0|1, AGENTM2M_NOSTR_RELAYS=url,url
# (implies enabled), AGENTM2M_PRIVACY=minimal|standard|debug.

PUBLISH_CATEGORIES = ("execution", "agents", "bindings", "traces", "contexts", "errors")
_SECRETISH = {"private_key", "privkey", "seckey", "secret", "secret_key", "nsec", "key"}


def _as_bool(v: object, where: str) -> bool:
    if isinstance(v, bool):
        return v
    raise ValueError(f"config {where}: expected true/false, got {v!r}")


@dataclass(frozen=True)
class NostrConfig:
    enabled: bool = False
    relays: tuple[str, ...] = ()
    engine_identity: str = "engine"
    kind: int = 4930
    presence_kind: int = 34930
    timeout: float = 3.0
    publish: tuple[tuple[str, bool], ...] = tuple((c, True) for c in PUBLISH_CATEGORIES)

    def publishes(self, category: str) -> bool:
        return dict(self.publish).get(category, True)


@dataclass(frozen=True)
class ObservabilityConfig:
    timeline: bool = True
    privacy: object = None  # agentm2m.observability.privacy.PrivacyConfig


@dataclass(frozen=True)
class ContextConfig:
    enabled: bool = True
    backend: str = "local"


@dataclass(frozen=True)
class WorkspaceConfig:
    observability: ObservabilityConfig
    nostr: NostrConfig
    context: ContextConfig

    @classmethod
    def load(cls, path: str | Path) -> WorkspaceConfig:
        import yaml

        from .observability.privacy import PrivacyConfig

        p = Path(path)
        raw = (yaml.safe_load(p.read_text()) or {}) if p.is_file() else {}
        if not isinstance(raw, dict):
            raise ValueError(f"{p}: top level must be a mapping")  # noqa: TRY004 - a config error, not a type error
        obs_raw = raw.get("observability") or {}
        nostr_raw = dict(raw.get("nostr") or {})
        ctx_raw = raw.get("context") or {}
        leaked = _SECRETISH & set(nostr_raw)
        if leaked:
            raise ValueError(f"config nostr.{min(leaked)}: never put keys in config.yaml; use .agentm2m/secrets/ "
                             "or AGENTM2M_NOSTR_KEY_<REF>")

        priv_raw = dict(obs_raw.get("privacy") or {})
        if os.getenv("AGENTM2M_PRIVACY"):
            priv_raw["mode"] = os.environ["AGENTM2M_PRIVACY"].strip().lower()
        privacy = PrivacyConfig(**{k: v for k, v in priv_raw.items() if k in PrivacyConfig.__dataclass_fields__})
        observability = ObservabilityConfig(timeline=_as_bool(obs_raw.get("timeline", True), "observability.timeline"),
                                            privacy=privacy)

        enabled = _as_bool(nostr_raw.get("enabled", False), "nostr.enabled")
        relays = list(nostr_raw.get("relays") or [])
        if os.getenv("AGENTM2M_NOSTR_RELAYS"):
            relays = [u.strip() for u in os.environ["AGENTM2M_NOSTR_RELAYS"].split(",") if u.strip()]
            enabled = True
        if os.getenv("AGENTM2M_NOSTR") in ("0", "1"):
            enabled = os.environ["AGENTM2M_NOSTR"] == "1"
        for u in relays:
            if not isinstance(u, str) or not u.startswith(("ws://", "wss://")):
                raise ValueError(f"config nostr.relays: relay URL must start with ws:// or wss://, got {u!r}")
        publish_raw = nostr_raw.get("publish") or {}
        unknown = set(publish_raw) - set(PUBLISH_CATEGORIES)
        if unknown:
            raise ValueError(f"config nostr.publish: unknown categories {sorted(unknown)}")
        publish = tuple((c, _as_bool(publish_raw.get(c, True), f"nostr.publish.{c}")) for c in PUBLISH_CATEGORIES)
        nostr = NostrConfig(
            enabled=enabled,
            relays=tuple(relays),
            engine_identity=str(nostr_raw.get("engine_identity") or "engine"),
            kind=int(nostr_raw.get("kind", 4930)),
            presence_kind=int(nostr_raw.get("presence_kind", 34930)),
            timeout=float(nostr_raw.get("timeout", 3.0)),
            publish=publish,
        )
        context = ContextConfig(enabled=_as_bool(ctx_raw.get("enabled", True), "context.enabled"),
                                backend=str(ctx_raw.get("backend") or "local"))
        if context.backend != "local":
            raise ValueError(f"config context.backend: only 'local' is supported, got {context.backend!r}")
        return cls(observability=observability, nostr=nostr, context=context)
