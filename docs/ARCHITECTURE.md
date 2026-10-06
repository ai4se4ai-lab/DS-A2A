# Architecture: AgentM2M prototype

This maps the `agentm2m` package (`src/agentm2m/`) onto the paper
(`docs/DS-A2A.tex`, "Towards Deterministic Structure for Stochastic
Agents"). Read `docs/DS-A2A.tex` Sec III first — this document assumes it.

## Package map

| Paper concept | Module | Notes |
|---|---|---|
| View metamodel `MM_i`, model `M_i in M(MM_i)` (Def. of a team, Sec III-A) | `agentm2m.metamodel` (`MetamodelBuilder`, pyecore) | The only part of the system that is literally EMF: every agent-exchanged artifact is a pyecore model conforming to a pyecore `EPackage`, and `MetamodelBuilder` can serialize either to real `.ecore`/`.xmi` files (`agentm2m.metamodel.io`). |
| Rule dialect (the `Story2Operation` listing) | `agentm2m.rules` (`grammar.lark`, `parser.py`, `ast.py`) | A Lark grammar for the ATL-flavored dialect the paper's listing uses, extended with `@llm(prompt, footprint)` and `@check`. Structural parts (module/rule/pattern/binding shape) become the `ast.py` dataclasses; expression subtrees (guards, RHS expressions, prompts, footprints, checks) are left as raw `lark.Tree`/`Token` objects, evaluated per-match at run time. |
| OCL guards/bindings | `agentm2m.engine.expr` | A bounded OCL *subset* (navigation, boolean connectives, comparisons, `->select/collect/forAll/exists/notEmpty/...`), not a full OCL implementation. Anything it can't express natively (`s.id.toOpName()`, `signature.parses()`) falls back to a per-rule-module Python helpers dict, populated from the file named in `uses "helpers.py";` — this is the prototype's substitute for ATL `helper context ... def: ...` blocks. |
| Definition 1 (hybrid hand-off `T=(R^str, B^sem, L, chk)`) | `agentm2m.rules.ast` + `agentm2m.engine.binding` | A parsed `Module` *is* `R^str union B^sem` (each `StochasticBinding` carries its own `check_expr`, i.e. its own `chk_b`); the configured `LLMBackend` *is* `L`. |
| Match set `Mt = {(r,m) | m |= p_r and g_r(m)}` (Algorithm 1, line 1) | `agentm2m.engine.matcher` | Reads only the source roots (never mutates them) — this is the textual proof, not just the intent, of Proposition 1's "sources are read-only" premise. |
| Trace model `TL_ij subseteq Match(M_i) x M_j x R`, version stamps | `agentm2m.engine.trace` | `TraceModel`/`TraceLink`, persisted as JSON. `element_key()` gives every matched element a stable `"Type#id_or_name"` key (so every type used in a `from`/`to` pattern needs an `id` or `name` EAttribute). `digest()` is the version stamp `#den(e)_m`: a content hash over an element's *primitive* EAttributes only (never EReferences, to keep the footprint bounded and acyclic). |
| Algorithm 1 (executing a hybrid hand-off) | `agentm2m.engine.executor.run_handoff` | Implements the algorithm's five phases in order: (1) fixed match set, (2)-(3) create elements for new matches + persist trace links, (4) delete stale links/elements, (5) structural bindings (trace-resolved), (6) stochastic bindings gated by the stamp check, with resample-until-`@check`, budget `k` (`max_resamples`), escalate-not-loop on exhaustion. |
| ATL's implicit target resolution (`component <- s.epic`) | `agentm2m.engine.binding.resolve_structural_value` | A structural binding's RHS that evaluates to a *source* element is resolved to the corresponding *target* element via the trace -- but **only** when it doesn't already conform to the target feature's declared type (checked via `pyecore.ecore.EcoreUtils.isinstance`). This is what lets a binding like `operation <- op` (already the right type) skip resolution while `component <- s.epic` (needs translating) doesn't. |
| Footprint-bounded prompting (`pi = prompt_b (+) den(e_b)_m`) | `agentm2m.engine.binding.apply_stochastic_binding` | The prompt sent to the LLM is built *only* from `prompt_expr` and the evaluated `footprint_expr` — the backend never sees the rest of the source or target model. |
| `Lift` (text-to-model) | `agentm2m.engine.lift` | Convention: a stochastic binding named exactly `self` has its sampled text parsed as JSON and validated/written onto the target's EAttributes, rejected (-> resample/escalate) unless every key names a real attribute. |
| `chk_b` (type/OCL constraint, parser, or executable oracle) | `agentm2m.engine.validators` + rule-module helpers | Generic reusable validators (`python_compiles`, `signature_parses`, `run_pytest_oracle`, ...); most `@check` validators are just OCL expressions dispatching into a rule module's own `helpers.py`. |
| `Obl(Delta)` (Sec III-C) | `agentm2m.engine.obligations` + `HandoffReport.resampled` | Not a separately-computed diff: it *is* the set of stamp mismatches Algorithm 1 finds on a given run. `from_handoff_report` just names that result and attaches the owning agent for reporting. |
| Team megamodel `Team = (A, V, T, omega, phi)` (Sec III-A, Fig. 1) | `agentm2m.team.model.Team` | Kept as plain Python state, not an Ecore-modeled instance -- see "Why isn't Team modeled in EMF too?" below. |
| models@run.time, cross-hand-off obligation propagation | `agentm2m.team.runtime.TeamRuntime` | `run_to_fixpoint()` just re-runs every registered hand-off in registration order until nothing changes (capped by `max_passes`, escalating rather than looping forever). No explicit message-passing is needed for propagation: a downstream hand-off's *own* stamp check automatically notices when an upstream hand-off changed a value its footprint reads. |
| Higher-order transformation (Sec III-D, Security Reviewer scenario) | `agentm2m.team.hot` | `TeamChange` is the declarative "relation model"; `apply_hot` registers the new agent/view/hand-off. Because the new hand-off's `TraceModel` starts empty, its first run treats every pre-existing match as new -- retroactive obligations with no hand-written glue, "for free" from Algorithm 1's own logic. |
| Acceptance predicate `phi` | `agentm2m.engine.executor.acceptance_holds`, `TeamRuntime.acceptance_holds` | Every match covered by a trace link, no open obligation (no stamp mismatch), all validators passed (no escalations) on the last run, and every stochastic binding's stamp fresh for its current footprint (`TeamRuntime.stamps_fresh`). |
| Host mode: the LLM `L` is the host (Claude Code) | `agentm2m.llm.host_backend.HostBackend`, `TeamRuntime.submit_binding` | A *deferred* backend: a stale binding is not sampled but reported as a `PendingBinding` (prompt = `prompt_b (+) den(e_b)_m`, plus the footprint digest it was built from). The host's value goes through the same `accept_sample` path as an in-engine sample (`@check`, Lift, stamping), is refused if the footprint moved since the prompt was issued, and escalates after `k` rejections on one footprint. Bindings whose footprint still reads an unfilled upstream value are reported as *blocked*, never offered with empty context. |
| Team as data (`team.yaml`) | `agentm2m.team.spec` | View metamodels, owners (omega), seed models and hand-offs declared in YAML, built through `MetamodelBuilder`/`Team`. |
| Persistent workspace, models@run.time across processes | `agentm2m.workspace.Workspace`, `agentm2m.store` | `.agentm2m/state/state.json` (format 2; format 1 loads unchanged) holds every view model (JSON, with cross-view references and engine target keys), every trace model (with shared-context pins), and runtime HOT evolutions; `context.json`, `observability/` and the optional `config.yaml` / `secrets/` sit beside it. An edit re-runs `R^str` immediately (deferred backend, no sampling), so obligations are visible at once; `impact` computes `Obl(Delta)` on a scratch copy. |
| Claude Code plugin | `agentm2m.mcp_server`, `plugin/agentm2m/` | MCP tools over `Workspace`; see `plugin/DEVELOPMENT_PLAN.md`. |
| Shared context (collaboration plane, 0.3) | `agentm2m.context` (`model`, `store`, `resolver`, `nostr_sync`), `agentm2m.engine.binding.stamp_for` | Typed, versioned, content-addressed knowledge with owner/reader/writer policies. `@llm(prompt, footprint, context=[...])` adds an authorized, pinned context selection to the prompt and to the version stamp (`effective_stamp`): a context change is an ordinary obligation, and an unresolvable context blocks the binding. Context-free bindings keep the exact 0.2 stamp and prompt. See `docs/SHARED_CONTEXT.md`. |
| Observability plane (0.3) | `agentm2m.observability` (`events`, `emitter`, `sink`, `privacy`, `presence`, `metrics`, `timeline`) | `run_handoff` / `TeamRuntime` / `Workspace` report every transition through an injected `emit` (a no-op by default), in typed `agentm2m.event` v1 envelopes with correlation ids and privacy redaction. `Emitter` never raises. Presence and metrics are derived from events, never stored. |
| Nostr transport (0.3, optional) | `agentm2m.nostr` (`keys`, `identity`, `events`, `signer`, `verifier`, `relay`, `relay_server`, `outbox`, `publisher`), `agentm2m.workspace_collab` | Signed NIP-01 events through a durable outbox; relay data is verified before use. Agent identities live in team.yaml `agents:`, keys in `.agentm2m/secrets/`. See `docs/NOSTR.md`. |

## Three planes (0.3)

```
collaboration plane   shared context: knowledge, policies, pins, provenance
        |  authorized, pinned context joins an @llm binding's prompt and stamp
transformation plane  hybrid M2M hand-offs, footprints, validators, traces, obligations, HOT, phi, omega
        |  every transition is emitted (a side channel; never read back)
observability plane   typed events -> local timeline, optional signed Nostr events
```

The non-negotiable invariant: **the transformation plane alone decides what the collaboration
means.** Context can only add knowledge to bindings that declare it, and it changes acceptance only
through the ordinary stamp/obligation path. Events never feed back into engine decisions. A relay is
an eventually consistent, at-least-once projection whose failure cannot make a transformation
incorrect: `tests/observability/test_engine_events.py` asserts byte-identical state with any sink,
including a raising one and a down relay.

## Why isn't `Team` modeled in EMF too?

Definition 1 and Sec III-A require every *artifact agents exchange* to be a
model conforming to a metamodel (`M_i in M(MM_i)`) -- that's the view
models, and that's what `agentm2m.metamodel` builds with pyecore. The team
megamodel and the trace model are the *engine's own bookkeeping*, never
serialized as an artifact an agent reads or writes. Modeling them formally
in Ecore too would be a reasonable extension, but for a prototype it adds
indirection without changing any guarantee the paper claims, so `Team` and
`TraceModel` are plain Python dataclasses/JSON instead.

## Known prototype limitations

- **Incremental re-execution across processes needs the workspace store.**
  Each created target element is tagged with a Python-only bookkeeping
  attribute (`_amt_target_key`, not an EMF feature) that lets `run_handoff`
  find "the element I created for this match last time". XMI
  (`agentm2m.metamodel.io`) does not carry it, but the JSON store used by
  `agentm2m.workspace` (`agentm2m.store`) does, together with cross-view
  references, so a team saved by one process resumes incrementally in
  another. Scripts that hold models in memory (`examples/`, `evaluation/`)
  are unaffected.
- **The OCL subset is intentionally small.** See `agentm2m.engine.expr`'s
  module docstring for exactly what's supported; anything else is meant to
  go through a rule module's `uses "helpers.py";` Python helpers, not a
  growing OCL grammar.
- **`uses` replaces ATL's `helper` blocks.** The paper's grammar listing
  keeps `helper`/`def`/`context`/`lazy`/`unique`/`thisModule` as ATL
  keywords; this prototype only implements `uses "file.py";` (import a
  Python module as OCL-callable helpers) rather than a second DSL for
  helper bodies -- equally expressive for a prototype, far less grammar to
  maintain.
