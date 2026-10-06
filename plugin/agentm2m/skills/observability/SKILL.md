---
name: observability
description: Inspect the AgentM2M execution timeline - typed, correlated events for runs, hand-offs, bindings, obligations, traces and shared-context reads - and agent presence. Use when the user asks what happened during a run, why a binding was re-derived, or what each agent is doing.
argument-hint: "[run id or agent]"
---

# Execution timeline

Every engine state transition is an event: `team.*`, `handoff.*`, `binding.*` (requested, accepted,
rejected, escalated, blocked, stale), `obligation.*`, `trace.*`, `context.*`, `*.error`. Each event
carries correlation ids: run, agent, hand-off, rule, target, binding, trace and context. Prompts,
values and context content appear only as digests and lengths unless the privacy mode is `debug`
with the matching flag set.

1. `run` returns a `run_id`. Call `observability_events` with `run_id` (or `agent`, `handoff`,
   `event_type`) and present it as a timeline: time, event, agent, hand-off/target/binding, context.
2. `agent_directory` shows each agent's view, Nostr identity and presence (offline, idle, working,
   waiting, blocked, failed). Presence is derived from the events, never stored separately.
3. To explain a re-derivation, follow `binding.stale` -> `obligation.created` -> `binding.accepted`
   for that target, and check whether a `context.updated` or `change.requested` preceded it.

Events are a projection: `team_status` and `acceptance` remain the source of truth.
