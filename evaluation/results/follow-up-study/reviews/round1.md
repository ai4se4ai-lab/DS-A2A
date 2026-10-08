# Review round 1: three critical reviewers, ICSE 2027 Research Track criteria

Reviewers were three independent model agents, each given the criteria (novelty, rigor, relevance,
verifiability and transparency, presentation), the draft with placeholder results, and read-only access to
the code and harness. They are simulated reviewers, not human ones; their value is in the issues they found
and the code they checked, which can be verified below.

| | R1 empirical rigor | R2 novelty and soundness | R3 relevance and presentation |
|---|---|---|---|
| Novelty | 3 | 2 | 3 |
| Rigor | 2 | 2 | 2 |
| Relevance | 4 | 4 | 3 |
| Verifiability | 3 | 3 | 3 |
| Presentation | 3 | 3 | 3 |
| Recommendation | weak reject (revisable) | weak reject | weak reject (borderline) |

## Major issues and how each was handled

Status: **done** = changed in code/paper; **text** = handled by honest restatement; **open** = not done, stated as a limit.

| # | Issue (reviewer) | Response | Status |
|---|---|---|---|
| 1 | Impact precision of paste is analytic (bound 0.5); H1 unfalsifiable; paste is a strawman (R1, R2, R3) | Added a `routed` paste arm (finding routed only to its consumers, only those re-run) to the harness, tests and aggregator. Impact precision/recall, spurious re-runs and influence are now labelled implementation checks and are no longer in the abstract's headline or tested. Headline is delivery, staleness and cost against routed paste. The paper says the saving over routed paste is automation, not calls. | done |
| 2 | Property 1 "iff" overstated: stamp ignores prompt/validator/model; digest covers author/provenance/scope, not just content; 64-bit truncation; version label in prompt; deletion and all-items cases (R2) | Property rewritten with the collision assumption, the 64-bit truncation and its bound, what the stamp does not cover, the record-level digest, and the version-label caveat. A probe for the rule-edit case was added to the fault campaign. docs/SHARED_CONTEXT.md corrected. | done |
| 3 | Fail-closed: stale accepted values persist; phi depends on clock/policy; liveness (R2) | Stated in the property and in Discussion. | text |
| 4 | Non-interference overstated: sync is a separate channel, sinks run synchronously, aliasing, wall clock (R2, R3) | Restated as a design invariant with explicit assumptions; sync excluded; replay is the empirical test and does not cover timing. | text |
| 5 | Seven-step Nostr verification order is inexact; removals not attributed; forks resolved first-arrival; no convergence guarantee (R2) | nostr_sync.py docstring, docs/NOSTR.md and the paper corrected. Fork handling had a real bug (a pending event keyed by version could displace another valid event); fixed with a regression test. | done |
| 6 | Fault study is an authored test suite; averaging is pseudo-replication; no undetectable faults (R1, R2, R3) | Reframed as a verification suite. Two design-limit probes added (rule prompt edit = undeclared dependency; wrong knowledge from an authorized writer) that are expected to stay silent. Not averaged as an estimate. | done |
| 7 | Statistics: units, power, pooling; equal fidelity undefined (R1, R3) | Unit is repository x model (mean over seeds); no tests on definitional quantities; non-inferiority margin of one canary of five; scope table reports what completed. | done |
| 8 | "Collateral changes" is sampling noise (R1) | Renamed regeneration churn, described as largely noise, not a cost or quality measure. A same-prompt noise floor is not measured. | text / open |
| 9 | R4 tests only store de-duplication (R1, R2) | Stated; R3 is the real test of Property 1. R4 kept to avoid invalidating the running experiment. | text |
| 10 | Scenario is toy: marker comments, two findings (R3, R1) | Scoped honestly (instruction-like, decidable ground truth) and listed as the main external threat. A semantic scenario with many findings is not run. | open |
| 11 | Novelty thin versus build systems and incremental MDE (R2) | Related work now positions against incremental ATL, EMF-IncQuery, Giese and Wagner, Shake, build systems; states what is and is not new. | done |
| 12 | Dependence on unpublished earlier work; anonymity (R3) | Earlier work summarized in a half page of Background; replication demoted from a contribution to a sanity check; repository name generalized. The tool name is removed from the title. | done |
| 13 | Verifiability: path leaks, missing model digests, hardware, stale README (R3) | Absolute paths scrubbed from reference_validity.csv; environment.json records Ollama and model digests, GPU, versions; README rewritten for the repository count and model list. | done |
| 14 | Abstract overclaims; "five families" vs four; "eight" vs reference count (R1, R3) | Abstract rewritten; families corrected (four plus control plus two probes); repository count is generated from reference_validity.csv. | done |
| 15 | Scaling with number of findings and consumers untested; routed paste drift untested (R2, R3) | Stated as the main limit; not run. | open |

## Reviewer-found defects that were real bugs (fixed)

- `NostrContextSync._pending` keyed by (context, version): two valid events for one version displaced each
  other; keyed by event id now (`tests/nostr/test_context_sync.py::test_forked_events_for_one_version_are_both_judged`).
- Host-mode `binding.accepted` events carried no pins, so context metrics were zero in host mode.
- `reference_validity.csv` leaked absolute paths.
