# Changelog

## 0.3.0

Shared context and observability. Existing tools, rules and workspaces work unchanged: a 0.2
`state.json` loads with every stamp still fresh, and rules without `context=` get byte-identical prompts.

- Shared context (collaboration plane): typed, versioned, content-addressed contexts with owner,
  readers, writers, visibility and expiry policies, declared in `team.yaml` under `contexts:` or
  created at runtime. Bindings opt in with `@llm(prompt, footprint, context=['id' | 'id#item'])`: the
  pinned context goes into the prompt and into the version stamp. A new context version therefore
  obliges exactly the consuming bindings, through the existing obligation mechanism, and a missing or
  unreadable context blocks the binding instead of sampling it.
- Observability plane: every engine transition is a typed, correlated event (`agentm2m.event` v1),
  written to a local timeline (`state/observability/events.jsonl`). Privacy modes minimal / standard /
  debug control content: prompts, values and context content are digests by default. Agent presence
  is derived from the events.
- Optional Nostr transport (`pip install 'agentm2m[nostr]'`, `.agentm2m/config.yaml`): BIP-340-signed
  NIP-01 events (kind 4930; presence 34930) published through a durable outbox, which retries with
  backoff, never blocks a run, and delivers at least once. Relay results are verified before use. Agent
  identities are given as `agents.<A>.nostr` in `team.yaml`; keys live in `.agentm2m/secrets/` or
  the environment, never in YAML or state.
- Cross-machine shared context: a `visibility: relay` context version is published signed by its
  writer's key (kind 4931). `context_sync` applies remote versions only after signature, namespace,
  writer-pubkey, local-policy, version-continuity and content-digest checks.
- MCP: 18 new tools (`agent_identity`, `agent_directory`, `context_*` including `context_sync`,
  `influence_query`, `observability_events`, `nostr_*`); the 12 existing tools are unchanged.
- CLI: `agentm2m context ...`, `agentm2m nostr ...` (including a local dev relay, `nostr serve`), and
  `agentm2m workspace events`.
- Plugin: skills `/agentm2m:context`, `observability`, `nostr`; agent `context-curator`. The
  `binding-worker` still has only `next_bindings`/`submit_binding`: context reaches it inside the prompt.

## 0.2.0

First plugin release.

- MCP server `agentm2m` (engine `agentm2m==0.2.0` via `uvx`): 12 tools covering the team lifecycle
  (`team_init`, `team_status`, `team_validate`, `model_show`, `model_edit`, `impact`, `run`,
  `next_bindings`, `submit_binding`, `trace_query`, `team_evolve`, `acceptance`).
- Host mode (default): Claude Code fills `@llm` bindings from their footprint-bounded prompts; values
  pass the same `@check` validators and escalation budget as engine-sampled ones.
- Skills: `/agentm2m:init`, `run`, `change`, `evolve`, `status`, `author-handoff`, plus background
  concepts. Agents: `binding-worker`, `handoff-architect`.
- Hooks: workspace announcement at session start; re-validation after edits to the team spec, rules, or helpers.
- Templates: `devteam` (the paper's running example, with a Security Reviewer HOT extension), `research`
  (n:m hand-off), `incident` (Lift binding, executable-oracle validator).
