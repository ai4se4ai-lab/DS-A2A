---
name: nostr
description: Publish the AgentM2M timeline as signed Nostr events so external observers can follow a team across processes and machines - configure relays, generate agent keys, check the outbox, and query verified events. Use when the user wants decentralized or remote observability.
---

# Nostr observability

Nostr is optional and only observes. It never decides structure, acceptance (phi) or write rights,
and a relay outage never fails a run. Events wait in an outbox and are retried.

1. `nostr_status` shows whether it is enabled, the relays, the engine public key, the outbox depth and
   recent failures. To enable it, add to `.agentm2m/config.yaml`:
   `nostr: {enabled: true, relays: [wss://...]}`. For a local relay, the user can run
   `agentm2m nostr serve` and use `ws://127.0.0.1:7777`.
2. Keys are never stored in `team.yaml`, `config.yaml` or state. An agent gets its own signing
   identity via the CLI (`agentm2m nostr keygen <ref>`, written to `.agentm2m/secrets/`) plus
   `agents.<Agent>.nostr: {pubkey, identity_ref: <ref>}` in team.yaml. Agents without a local key are
   signed for by the engine key and tagged with their public key. Never ask the user for a private
   key or print one.
3. `nostr_events` (by `run_id`, `agent`, `handoff`, `event_type`) returns only events that pass
   signature, trusted-author, schema and workspace checks; report `rejected` counts. `nostr_publish`
   retries the outbox now; `nostr_subscribe` listens briefly for live events.

Events use a regular kind (4930, timeline) and an addressable kind (34930, presence per agent). These
are an AgentM2M application namespace, not standardized NIPs.
