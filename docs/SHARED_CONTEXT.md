# Shared context

Shared context is AgentM2M's collaboration plane. It lets one agent publish knowledge, such as a
security finding, a design decision or a constraint, that other agents have been explicitly
authorized to use. Hand-offs move structured *artifacts* between views; contexts move
*knowledge*. The two are deliberately different mechanisms:

```
Agent A --view A--> M2M transformation --view B--> Agent B     (artifacts: deterministic, traced)
Agent A --publishes--> Shared Context --authorized read--> Agent B's @llm binding   (knowledge)
```

A context never creates model elements, never grants write rights (omega), and never bypasses a
validator or the acceptance predicate (phi).

## Model (`agentm2m.context`)

| Concept | Meaning |
|---|---|
| `ContextItem` | One typed fact: `id`, `type`, `content` (string or object), `author`, `provenance` (`agent`, `view`, `element`, `trace`, `handoff`, `nostr_event`), `confidence`, `scope`. Not a transcript. |
| `ContextSnapshot` | An immutable version: items plus `digest` = sha256 of the canonical item records (id, type, content, author, provenance, confidence, scope; sorted by id, independent of time). Identical records give an identical digest, so a revision that changes only an item's author or provenance also changes the digest. Every version stays addressable. |
| `ContextPolicy` | `owner`, `readers`, `writers`, `visibility` (`private`: listed readers; `team`: every agent may read; `relay`: also synced over Nostr), `expiry`. Runtime read grants (`context_attach`) are owner-only. |
| `ContextReference` / pin | `(context_id, version, digest, item_ids)`: what a binding consumed. |

Stores: `LocalContextStore` (`.agentm2m/state/context.json`, file-locked, reloads when another
process writes; the default) and `MemoryContextStore`. The store enforces authorization for every
caller and fails closed:
- reads need read rights;
- writes need write rights;
- an expired or missing context grants nothing;
- every written item is attributed to the authenticated writer, and claiming another agent's
  authorship is refused.

Updates use optimistic concurrency. `context_update(..., expected_version=n)` fails with
`CONTEXT_CONFLICT` if the context is no longer at version `n`; it never silently overwrites.

## Declaring and consuming context

```yaml
# team.yaml
contexts:
  security-review:
    owner: SecurityReviewer
    writers: [SecurityReviewer]
    readers: [Developer, Tester]
    visibility: private
```

```
body <- @llm('Implement this operation ...', op.implFootprint(), context = ['security-review'])
-- or select items: context = ['security-review#finding-001', 'product-roadmap']
```

The two-argument form `@llm(prompt, footprint)` is unchanged.

## How context joins the footprint

```
EffectiveFootprint = SourceFootprint + pinned context selection
stamp              = H(footprint, [(context_id, content_digest of the selected items)...])
```

- **Context-free bindings keep exactly the 0.2 stamp** (`digest(footprint)`) and the same prompt
  byte for byte, so existing workspaces stay fresh.
- A new context version whose selected items changed alters the stamp. Every consuming binding
  becomes stale, which is an ordinary obligation that `impact`, `run` and `next_bindings` already
  handle. There is no second invalidation system.
- A version bump that leaves the selected items unchanged, or a change to an unrelated context,
  changes nothing.
- The resolver authorizes the agent that owns the binding's target view. If a context is missing,
  unreadable or expired, the binding is **blocked** (host mode) or **escalated without caching**
  (engine mode). It is never sampled with empty or invented context. `team_validate` reports
  undeclared or unreadable references in advance.
- The prompt gets a labelled section, so an audit can see exactly what each value was derived from:

  ```
  Authorized shared context (read-only collaboration knowledge; use it, do not invent more):
  [context security-review v2 sha256:1a2b3c4d5e6f] -- Security review findings
  - finding-001 (security-finding, by SecurityReviewer): {...}
  ```
- `next_bindings` returns the pins with each prompt, and `footprint_version` is the effective stamp.
  If the context changes between `next_bindings` and `submit_binding`, the submission is refused as
  `stale`, exactly like a footprint change.
- Trace links record `context_pins` and `dependencies = {source, context, effective}` for each
  accepted context-aware value (state format 2; a format-1 `state.json` loads unchanged).

## Traceability of knowledge

`influence_query(context_id, item_id)` answers *which agent decisions were influenced by this
finding?* It returns:
- every accepted value that pinned the item, with its agent, element, binding and pinned version;
- whether that value is still current;
- what was generated downstream from it through the trace links.

`agentm2m context graph --format dot` renders the same information as a graph.

## Information flow is observable

Each context write emits `context.created` / `context.updated`. Each grant emits
`context.attached` / `context.detached`. Every read emits `context.read`, with the agent, the
version, the digest, the item ids and a purpose (`binding-prompt`, `agent-request`, `search`).
Outside debug mode, content is never logged. Obligations record their cause (`source`, `context`,
or both), which gives the research metrics in `agentm2m.observability.metrics`: context reuse,
amplification, context-induced obligations and collaboration latency.

## Interfaces

- **MCP:** `context_list`, `context_create`, `context_get`, `context_snapshot`, `context_update`,
  `context_attach`, `context_detach`, `context_search`, `context_status`, `context_sync`,
  `influence_query`.
- **CLI:** `agentm2m context list|show|create|update|pull|validate|graph`.
- **Plugin:** `/agentm2m:context` and the `context-curator` agent. The `binding-worker` has no
  context tools, because context reaches it only inside its pinned prompt.

Example: `examples/07_shared_context`. Cross-machine sync: see [NOSTR.md](NOSTR.md).
