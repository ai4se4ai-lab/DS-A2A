# Nostr observability and context transport

Nostr is an **optional transport** for observing an AgentM2M team and syncing shared context
across machines. It is a projection of engine state, never its source:

| | Authority |
|---|---|
| Structure, trace links, stamps, obligations, phi, omega | the engine, `.agentm2m/state/state.json` (authoritative) |
| Shared context | the local context store; the local policy decides who may write |
| Nostr events | an eventually consistent, **at-least-once** projection, deduplicated by event id |

A relay that is down, slow or hostile can delay observation. It can never change what a
transformation computes. Tests assert that `state.json` is byte-identical with and without Nostr,
including with the relay down.

## Enabling it

```bash
pip install 'agentm2m[nostr]'              # coincurve (BIP-340) + websockets
agentm2m nostr serve --port 7777            # optional: a local development relay
```

```yaml
# .agentm2m/config.yaml (optional file; everything is off without it)
nostr:
  enabled: true
  relays: [ws://127.0.0.1:7777]
  engine_identity: engine        # key name in .agentm2m/secrets/, created on first use
  timeout: 3
  publish: {execution: true, agents: true, bindings: true, traces: true, contexts: true, errors: true}
observability:
  privacy: {mode: standard}      # minimal | standard | debug
```

Environment overrides: `AGENTM2M_NOSTR=0|1`, `AGENTM2M_NOSTR_RELAYS=url,url`,
`AGENTM2M_PRIVACY=mode`.

## Identities and keys

The AgentM2M agent id (`Architect`) stays the internal identifier; a Nostr public key is optional
external metadata:

```yaml
# team.yaml
agents:
  Architect:
    nostr: {npub: npub1..., identity_ref: architect, relays: [wss://...]}
```

- **Keys never go in YAML or state.** `team.yaml` and `config.yaml` refuse fields such as
  `private_key` or `nsec`. Keys come from `AGENTM2M_NOSTR_KEY_<REF>` (hex or nsec) or from
  `.agentm2m/secrets/<ref>.key` (mode 0600, directory 0700, gitignored, kept across
  `team_init --force`). `agentm2m nostr keygen <ref>` creates one and prints only the public key.
- An agent whose `identity_ref` resolves to a local key matching its `pubkey` signs its own events.
  Other agents' events are signed by the workspace engine key and carry a `p` tag with the agent's
  pubkey.
- Signing goes through a `Signer` protocol, so a remote signer (NIP-46 bunker, NIP-55) can replace
  local keys without engine changes.
- No MCP tool or CLI command returns key material.

## Event mapping (an AgentM2M namespace, not a standardized NIP)

| Kind | Use |
|---|---|
| 4930 (regular) | every timeline event: relays keep all of them |
| 34930 (addressable, `d=<agent>`) | `agent.*` presence: the latest replaces the previous |
| 4931 (regular) | shared-context snapshots (`visibility: relay` only) |

The originally proposed 31000+ range is *addressable*: relays keep only the latest event per
(kind, pubkey, `d`), which would silently discard the timeline. That is why timeline events use a
regular kind.

- **`content`:** the `agentm2m.event` v1 envelope, already privacy-redacted.
- **Tags:**
  - `["t","agentm2m"]` and `["t","agentm2m-run-<run>"]`, single-letter so relays can filter on them;
  - `["p", agent pubkey]`;
  - `["alt", ...]` (NIP-31);
  - `type`, `schema`, `workspace`, `run`, `agent`, `handoff`, `rule`, `target`, `binding`, `trace`
    and `context`. Relays don't index multi-letter tags, so these are filtered client-side after
    verification.

## Privacy

| Mode | Payload contains |
|---|---|
| minimal | status, ids, digests, durations, counts |
| standard (default) | the above, plus pins, context versions and token counts |
| debug | the above, plus raw fields, each only behind its flag (`include_prompts`, `include_outputs`, `include_source_content`, `include_context_content`) |

Outside debug, a prompt becomes `prompt_digest` + `prompt_chars`. That is enough to prove which
prompt or context version an agent received without revealing it. Rejection reasons count as
output, since they can echo a value.

## Delivery

Every signed event is appended to `.agentm2m/state/observability/outbox.jsonl` *before* the first
publish attempt. One attempt follows. After a relay failure, a circuit breaker routes later events
straight to the outbox until the backoff expires, so a dead relay costs a run at most one fast
failure. The outbox is file-locked, compacting and replayed incrementally. Each workspace operation
and `nostr_publish` / `agentm2m nostr flush [--force]` drain it, and a restarted process picks up
what is pending. `nostr_status` reports outbox depth, failures and backoff.

## Reading events back

`nostr_events` / `agentm2m nostr events [--run --agent --type --timeline]` query the relays. An
event is returned only if it passes all of these checks:
- NIP-01 structure, recomputed id and BIP-340 signature;
- the author is the engine key or a team agent's pubkey;
- the envelope schema is understood (unknown versions are never reinterpreted);
- the workspace id is this one.

Rejections are counted by reason.

## Cross-machine shared context

For a context with `visibility: relay`, each new version is published as one kind-4931 event,
**signed by the writing agent's own key**. The engine key cannot vouch for an agent's write. A
receiving workspace (`context_sync`, `agentm2m context pull`) applies an event only after all of
these pass. Checks 1-4 and 6 run at decode time, 5 and 7 when the event is applied, so the content
digest (6) is verified before version continuity (5):

1. structure, id, signature
2. schema `agentm2m.context-snapshot` v1, kind, team namespace (`nostr.namespace`, default: team name)
3. the claimed writer is a known agent *and* the signing pubkey is that agent's key
4. the context already exists locally and the **local** policy lets that agent write it (a relay
   never creates contexts)
5. version continuity: exactly local+1, built on the local digest (older gives a duplicate or "old
   version"; another base gives a conflict; a later version waits for its predecessors)
6. the snapshot digest matches its items (tamper check)
7. every new or changed item is attributed to the writer (removals are not attributed: a permitted
   writer may remove any item)

Two valid events for the same version (a fork) are both judged; the first one applied wins and the
other is reported. There is no general convergence guarantee with several writers per context.

Content leaves the machine only for `visibility: relay` contexts. There is no NIP-44 encryption
yet, so use a private relay for confidential context.

## Module map

`agentm2m.nostr`:
- `keys` (NIP-19), `identity`, `events` (NIP-01), `signer` (`Signer`, `KeySigner`, `SecretStore`),
  `verifier`;
- `relay` (`MemoryRelay`, `WebSocketRelay`, `MultiRelay`), `relay_server` (dev relay);
- `outbox`, `publisher` (`NostrEventSink`).

`agentm2m.context.nostr_sync` (`NostrContextSync`).
