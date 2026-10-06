---
name: context
description: Share collaboration knowledge between AgentM2M agents through versioned shared contexts - publish findings or decisions as an authorized writer, see which bindings a new version obliges, and trace which artifacts a finding influenced. Use when an agent learns something other agents must take into account.
argument-hint: "[context id]"
---

# Shared context

A shared context is a typed, versioned set of facts (findings, decisions, constraints) that one agent
publishes and explicitly authorized agents consume. It is not a chat log. Hand-offs move structured
artifacts between views; contexts move knowledge. Bindings opt in with
`@llm(prompt, footprint, context=['security-review'])`, and the engine then puts the pinned context
version into that binding's prompt.

1. `context_status` (or `context_list` with `as_agent`) shows what exists, who reads it, how many
   bindings consume it and how many of those are stale. Missing referenced contexts are listed too.
2. To publish knowledge, call `context_get` for the current version, then `context_update` as the
   writing agent with `expected_version` set to that version. Each item is
   `{"id", "type", "content", "provenance": {"trace"|"element"|"view"|"handoff": ...}}`; authorship
   is always the writing agent. On `CONTEXT_CONFLICT`, re-read, reconcile and retry. Never overwrite blindly.
3. The update reports its affected bindings (field affected_bindings): exactly the bindings whose pinned context changed, as
   ordinary obligations. Discharge them with `/agentm2m:run`.
4. `context_create` makes a runtime context owned by the calling agent; `context_attach` and
   `context_detach` (owner only) grant or revoke readers. Declared contexts and their policies live
   in `team.yaml` under `contexts:`.
5. `influence_query` with `context_id` (and optionally `item_id`) answers "which decisions did this
   finding influence?": the accepted values that pinned it, at which version, whether still current,
   and what was generated downstream.

Rules: read only as an authorized agent; never copy context content into a binding by hand (the
engine already includes it, pinned); never treat context as a substitute for editing the owning view.
