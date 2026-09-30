# AgentM2M Preliminary Evaluation — Summary

*Condensed from [`sec_eval_revised.tex`](sec_eval_revised.tex). All numbers below are taken directly from `evaluation/results/csv/devbench_summary_{qwen2.5-coder-7b,devstral-24b,qwen3-coder-30b,pooled}.json`; nothing here is projected or invented.*

## Prototype and benchmark

The prototype executes coordination rules over EMF-compatible metamodels with pluggable LLM backends. The evaluated pipeline (`AgentM2M`) uses four view metamodels and four hand-offs between agent roles. We evaluate against DevBench, the closest public benchmark available: its repositories ship with a PRD, a UML/architecture design, and executable acceptance/unit tests. These map onto the prototype's own views — `M_req` (PRD → UserStory/Criterion), `M_arch` (UML → Component/Operation) — and DevBench's own unmodified tests serve as the oracle. Of DevBench's 22 repositories across four languages, we target the 10 Python ones, since the prototype's validators (`python_compiles`, `run_pytest_oracle`) are Python-specific. Given measured per-repository latency (roughly 46 tok/s for `qwen2.5-coder:7b`, 15 tok/s for `devstral:24b`, 45 tok/s for `qwen3-coder:30b`), only the smallest repository, `chakin` (9 operations, including 5 seeded canary operations), has complete runs across all three models and configurations so far.

## Research questions

Three configurations are compared under identical roles, prompts, and LLM: **free-text** hand-offs, a **PatchBoard-style shared schema** (which validates each write but does not relate views to each other), and **AgentM2M**. Each runs on three open-weight models spanning small, agentic-specialist, and large points on the current open-model landscape: `qwen2.5-coder:7b`, `devstral:24b`, and `qwen3-coder:30b`.

- **RQ1 (fidelity).** Does AgentM2M reduce coordination failure modes (MAST "P1" modes)? We measure canary retention (5 seeded operations with their own tests, absent from every real repository so memorization cannot help), P1 modes per trace via a MAST-annotator approximation, and DevBench's own acceptance pass rate.
- **RQ2 (change).** Under three injected PRD changes per repository (tighten/add/remove a criterion), what are the precision and recall of AgentM2M's change-impact operator against the declared-footprint impact set — the one operation each changed UserStory targets by construction of the rule set? We compare against an LLM asked to list affected artifacts (free text) and against regenerating everything (shared schema), also counting tokens.
- **RQ3 (evolution).** Can a higher-order transformation alone add a Security Reviewer mid-run, and at what cost versus manual rewiring? We report glue-code lines changed and the share of pre-existing operations retroactively covered.

## Results

On `chakin`, **no** model/configuration pair (9 of 9) retained any canary or passed the real acceptance test — a genuine, if unencouraging, result at this scale (a scripted backend that returns correct canary bodies passes 5/5 through the same code path, confirming the harness works). MAST P1 modes gave a mixed, model-dependent signal: `qwen2.5-coder:7b` showed 0 P1 modes in all three configurations; `devstral:24b` showed *more* P1 modes for AgentM2M (3) than free text (2) or shared schema (1); `qwen3-coder:30b` showed *fewer* P1 modes for AgentM2M (0) than free text (2), matching shared schema (0). Averaged, P1 modes per trace were 1.33 (free text), 0.33 (shared schema), and 1.00 (AgentM2M): AgentM2M sits between the two baselines, and the ranking flips by model.

RQ2 was the one metric where all three models agreed exactly: AgentM2M reached recall 1.00 and precision 1.00 on all 9 real trials (3 models × 3 injected changes), never missing and never over-including. The baselines did not hold as steady — shared schema was recall 1.00 / precision 0.23 identically across models (it regenerates everything, as expected), while free-text precision varied non-monotonically with model size: 1.00 (`qwen2.5-coder:7b`), 0.75 (`devstral:24b`), 0.23 (`qwen3-coder:30b`, indistinguishable from "regenerate everything"). AgentM2M cost more tokens per RQ1 task (25.5k mean) than free text (14.9k) or shared schema (4.8k), and 34.0k tokens per RQ2 change versus 0.23k (free text) and 0k (shared schema, no LLM calls).

RQ3 was deterministic and identical across all three models: the added Security Reviewer covered all 9 pre-existing operations (100% retroactive coverage) for 7 lines of glue code, versus an estimated 22 lines for hand-written baseline retrofits.

## Discussion

Canary retention was 0% everywhere, so RQ1 is **inconclusive** at this sample size — a paired bootstrap isn't computable with one repository, and a null result cannot distinguish "no effect" from "underpowered." RQ2 is the strongest result: recall and precision were both 1.00 for AgentM2M on every real trial, following structurally from the change-impact operator's design rather than generation quality. RQ3 behaved identically across models for the same structural reason.

The clearest limitation is scale: a **single-repository (n=1), 3-model pilot**, not a statistically powered comparison. The MAST annotator is our own approximation of an unreleased tool and, being the same class of model it evaluates, may be miscalibrated for AgentM2M's non-conversational, per-operation transcript shape. AgentM2M's token cost is higher than either baseline at generation time, so its practical case rests on the RQ2 change-time savings above, not cheaper initial generation. Converting token counts into an energy estimate remains pending.
