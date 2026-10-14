# AgentM2M evaluation pipeline

Implements the evaluation protocol of Sec V ("Evaluation Plan and
Discussion") of `docs/DS-A2A.tex`, and reproduces Table II (`tab:pilot`) as
real CSV, a LaTeX snippet, and figures under `evaluation/results/`.

## Research questions (Sec V)

- **RQ1 (fidelity).** Does AgentM2M reduce the incidence of P1 (lossy
  hand-off) MAST failure modes? The paper predicts a reduction for
  FM-1.4, 2.1, 2.4, 2.5, and *no* change for FM-1.1, 2.2, 2.6 (a built-in
  negative control from Table I, `tab:mast` -- these are modes rooted in a
  single agent's reasoning, outside what any coordination layer can fix).
- **RQ2 (change).** Under an injected requirement change, what are the
  precision and recall of `Obl(Delta)` (the set of stochastic bindings the
  engine re-invokes) against a manually labelled impact set? Proposition 2
  predicts recall = 1; precision measures how much the footprint
  over-approximates the true impact.
- **RQ3 (evolution).** Can a reviewer agent be added mid-run by a
  higher-order transformation alone, and at what cost relative to manual
  rewiring? (Demonstrated by `examples/03_security_reviewer_hot/`, not by
  this evaluation pipeline.)

**Design (Sec V).** Three configurations with the same LLM and the same
three roles (Analyst / Architect / Developer):

| Configuration | File | Hand-off discipline |
|---|---|---|
| Free text | `harness/free_text_config.py` | raw string output passed forward, no structure, no validation |
| Shared schema | `harness/shared_schema_config.py` | one shared, hand-validated dict (PatchBoard-style; validates each write, relations between views stay implicit) |
| AgentM2M | `harness/agentm2m_config.py` | real engine hand-offs (`MetamodelBuilder`/`Team`/`TeamRuntime`), trace-linked, footprint-bounded `@llm`+`@check` bindings |

## Quick start (zero setup: no network, no Docker, no download)

```bash
source .venv/bin/activate   # from repo root

# 1. Run all three configs over the 3 synthetic fixture tasks with MockBackend.
python -m evaluation.harness.run_all_configs --llm mock

# 2. Aggregate the log into Table II's shape.
python -m evaluation.analysis.aggregate

# 3. Render the figures.
python -m evaluation.analysis.make_figures

# 4. Emit the LaTeX tabular snippet for the paper.
python -m evaluation.analysis.make_table
```

This uses `evaluation/benchmarks/fixtures/toy_tasks.jsonl` -- three small,
**hand-written, clearly synthetic** "SWE-bench-shaped" task records (not
real SWE-bench instances) -- and `agentm2m.llm.mock_backend.MockBackend`, a
deterministic, network-free LLM stand-in already used throughout
`tests/` and `examples/`. Outputs land in `evaluation/results/csv/` and
`evaluation/results/figures/` (both gitignored except for `.gitkeep`).

## A real pass (SWE-bench_Lite sample + a real LLM, still no Docker)

```bash
python evaluation/benchmarks/fetch_swebench_lite.py --n 20 --seed 0
# -> evaluation/benchmarks/cache/swebench_lite_sample.jsonl

python -m evaluation.harness.run_all_configs \
    --tasks-file evaluation/benchmarks/cache/swebench_lite_sample.jsonl \
    --llm ollama --model qwen3:8b

python -m evaluation.analysis.aggregate
python -m evaluation.analysis.make_figures
python -m evaluation.analysis.make_table
```

`fetch_swebench_lite.py` pulls `princeton-nlp/SWE-bench_Lite` via the
HuggingFace `datasets` library (`pip install -e .[eval]`) and writes a
normalized JSONL file with (at minimum) `instance_id`, `repo`,
`base_commit`, `problem_statement`, `patch`, `test_patch`, `FAIL_TO_PASS`,
`PASS_TO_PASS` -- every other field the real dataset provides is passed
through too. If `datasets` isn't installed or there's no network, the
script fails with a clear message and points back at the fixture file as
the documented manual fallback; nothing downstream (`run_all_configs.py`
et al.) requires a real download, since every harness entry point takes
`--tasks-file` pointing at *either* a real cache file or
`evaluation/benchmarks/fixtures/toy_tasks.jsonl`.

Every harness script is a thin CLI wrapper around importable functions
(e.g. `evaluation.harness.run_all_configs.run_all`,
`evaluation.analysis.aggregate.compute_table`), so `tests/` and other
scripts can call the logic directly instead of shelling out.

## The full, opt-in SWE-bench Docker evaluation

By default, "task success" in Table II is a **lightweight local proxy**:
does the produced patch look like a well-formed unified diff, and (if
`--repo-dir` is given) does `git apply --check` accept it against a real
checkout at `base_commit`? This is *not* the paper's real success metric
and is labelled as a proxy everywhere it's reported.

The real metric -- running each instance's `FAIL_TO_PASS`/`PASS_TO_PASS`
tests inside the official SWE-bench Docker evaluation images via the
`swebench` PyPI package -- is implemented in
`evaluation/harness/swebench_eval.py` but only runs when `--docker` is
passed explicitly:

```bash
pip install -e .[eval]   # installs `swebench` (and `datasets`, `pandas`, `matplotlib`)
# Docker must be installed and running; the harness pulls one image per instance.

python evaluation/harness/swebench_eval.py \
    --tasks-file evaluation/benchmarks/cache/swebench_lite_sample.jsonl \
    --patches-file <a JSONL of {instance_id, patch_text} from a run's log> \
    --docker
```

If Docker isn't available or `swebench` isn't installed, `--docker` mode
raises a clear `RuntimeError` ("swebench/Docker not available in this
environment; install the `eval` extra and Docker to run --docker mode")
rather than crashing; every other deliverable in this pipeline (Table II,
figures, tests) works without it.

## MAST annotation: an approximation, not the original tool

`evaluation/harness/mast_annotator.py` classifies which of the 14 MAST
failure modes (Table I / `tab:mast`, `docs/DS-A2A.tex`) appear in a run's
transcript, via one classification call to the configured LLM backend
given the mode taxonomy. **This is our own approximation of Cemri et
al.'s MAST LLM annotator (`cemri2025mast`, arXiv:2503.13657), which has
not been publicly released** -- not a faithful reproduction of it. Because
of that, `evaluation/harness/manual_relabel.py` writes
`evaluation/results/csv/manual_relabel_sample.csv`: the annotator's guess
for a sample of runs, plus empty `human_agrees` / `human_modes_present` /
`human_notes` columns for a human to fill in, exactly as Sec V's protocol
calls for ("a sample is re-labelled manually").

## RQ2 in detail: `impact_injector.py`

`evaluation/harness/impact_injector.py` runs the real engine on two small
seed "issues" (`evaluation/harness/agentm2m_config.py`'s Issue -> Plan ->
Patch hand-offs), injects a synthetic requirement change into one of them,
re-runs to a fixpoint, and reads `Obl(Delta)` straight off
`TeamRunReport.obligations` (Algorithm 1's stamp-check result) -- the same
mechanism `examples/02_devteam_change_propagation` exercises. The
predicted impact set is compared against a hand-written oracle (only the
edited task's downstream elements should be obligated; the untouched
second task is a negative control) to report precision/recall. The
shared-schema column's RQ2 numbers are a fixed "regenerate everything
downstream" strategy (no LLM calls): a flat shared-schema pipeline has no
footprint mechanism to consult, so that's the most it can principled do on
any upstream edit, and it is the reason Table II's RQ2 row shows a
recall-1/lower-precision pair for that column instead of a *measured*
selective-impact result.

## Layout

```
evaluation/
  benchmarks/
    fetch_swebench_lite.py     # real SWE-bench_Lite fetcher (opt-in network)
    fixtures/toy_tasks.jsonl   # 3 synthetic tasks, no download required
    cache/                     # gitignored; real fetches land here
  harness/
    common.py                  # shared ConfigRunResult/Turn/task-loading
    free_text_config.py        # baseline: raw-text hand-offs
    shared_schema_config.py    # baseline: one shared, validated dict
    agentm2m_config.py         # real engine hand-offs (+ agentm2m_rules/*.agentm2m)
    mast_annotator.py          # approximate MAST LLM classifier
    manual_relabel.py          # writes the human-relabelling CSV template
    impact_injector.py         # RQ2: Obl(Delta) precision/recall
    token_meter.py             # RQ-adjacent: tokens/task
    swebench_eval.py           # proxy success check + opt-in --docker eval
    run_all_configs.py         # orchestrator -> harness/logs/run_*.jsonl
    logs/                      # gitignored
  analysis/
    aggregate.py                # logs -> results/csv/{pilot_table,raw_metrics}.csv
    make_figures.py             # logs -> results/figures/*.png
    make_table.py                # logs -> results/csv/pilot_table.tex
  results/
    csv/                        # gitignored except pilot_table.csv etc. once committed intentionally
    figures/
```

## Tests

`tests/test_evaluation_pipeline.py` runs the quick/mock path end to end
(fixture tasks + `MockBackend`, no network, no Docker) and is part of
`python -m pytest tests/ -q`.

## The DevBench pilot (the paper's "Preliminary Evaluation" section)

The SWE-bench-Lite track above is a small, Issue->Plan->Patch-shaped
hand-off comparison; it does not use real PRD/UML/architecture-design
documents or a repo's own real acceptance/unit tests, and has no RQ3 or
canary-criteria mechanism. The **DevBench pilot** is a second, parallel
track that does, built on **Li et al.'s DevBench**
(`github.com/open-compass/DevBench`, Apache-2.0) -- *not* the
similarly-named `github.com/microsoft/devbench` telemetry/code-completion
benchmark described in `docs/devbench.md`, which is a different benchmark
entirely (see the note at the top of that file). DevBench's 10 Python
repositories each ship `docs/{PRD.md, UML_class.md, UML_sequence.md,
architecture_design.md}` plus real, executable `acceptance_tests/` and
`unit_tests/`, which map directly onto $M_{req}$ (PRD -> UserStory/
Criterion), $M_{arch}$ (UML -> Component/Operation), and the oracles of
$M_{test}$ (the real tests -- not a generated stand-in).

**Scope of this pilot** (reported here rather than only in the paper): 6 of
DevBench's 10 Python repos (`chakin`, `geotext`, `readtime`,
`particle-swarm-optimization`, `Hybrid_Images`, `stocktrends`), 1 seed, 3
configs (free-text / shared-schema / AgentM2M, same 4 DevTeam roles as
`examples/01_devteam`), run once per model for 3 literature-anchored,
already-pulled local Ollama models (`qwen2.5-coder:7b`, `devstral:24b`,
`qwen3-coder:30b`). The original plan called for all 10 Python repos;
measured per-repo latency (a single repo's AgentM2M config alone took
5-15 minutes on this hardware, dominated by per-operation `@llm` calls) made
that impractical within this session for 3 models, so the 4 excluded repos
were dropped for disclosed, non-cherry-picked reasons: `TextCNN` (needs a
full `torch`/`transformers` training pipeline, orthogonal to what this
pilot measures), `ArXiv_digest` (its acceptance test shells out to the
live arXiv API, making pass/fail conflate network availability with
implementation correctness), and `hone`/`lice` (the two largest
repositories by operation count, dropped purely to bound runtime). This is
smaller than the paper's own aspirational protocol (22 repos x 3 languages
x 3 seeds) -- a deliberate, disclosed trade-off for a *preliminary* pilot,
not the "Evidence at scale" future work in `sec:future`.

### Fetching the data

```bash
python evaluation/benchmarks/fetch_devbench.py
# -> evaluation/benchmarks/cache/devbench/python/<repo>/... (gitignored)
```

### Running the pilot (one invocation per model; produces RQ1+RQ2+RQ3)

```bash
python -m evaluation.harness.devbench_run_all --llm mock                        # smoke test, no network
python -m evaluation.harness.devbench_run_all --llm ollama --model qwen2.5-coder:7b
python -m evaluation.harness.devbench_run_all --llm ollama --model devstral:24b
python -m evaluation.harness.devbench_run_all --llm ollama --model qwen3-coder:30b
```

Each invocation writes three logs to `harness/logs/`:
`devbench_rq1_<model>_<ts>.jsonl` (canary retention, MAST modes, real
acceptance/unit test pass -- see "Task success" below), `devbench_rq2_<model>_<ts>.jsonl`
(3 injected PRD changes per repo x 3 configs), and
`devbench_rq3_<model>_<ts>.jsonl` (the HOT-added Security Reviewer's
retroactive coverage).

### Aggregating

```bash
python -m evaluation.analysis.aggregate_devbench                 # pooled across all 3 models
python -m evaluation.analysis.aggregate_devbench --model qwen2.5-coder:7b   # one model only
```

Writes `evaluation/results/csv/devbench_raw_metrics_*.csv` and
`devbench_summary_*.json` (the exact numbers behind `tab:prelim`), and
prints the paired 95% bootstrap CI (percentile bootstrap over the 10
repos) for the canary-retention AgentM2M-vs-shared-schema difference --
the paper's own RQ1 decision rule.

### What's genuinely new here vs. the SWE-bench-Lite track

- **Canaries** (`harness/canary.py`): 5 fixed, repo-independent synthetic
  operations with behavior pinned to an arbitrary constant (e.g. "append
  the literal suffix `-9f2`"), injected into every repo's Req view
  alongside the real operations. None appear in any real PRD, so passing
  their test can't be explained by memorizing a reference solution --
  "canary retention" measures whether the *exact* acceptance criterion
  survived the hand-off chain, independent of whether the agent team
  could also solve the (much harder) real repo.
- **Real task success** (`harness/devbench_common.py`): generated code is
  assembled into a runnable module and the repo's own, unmodified
  `acceptance_test_script`/`unit_test_script` (from `repo_config.json`)
  are actually executed via subprocess -- not a diff-well-formedness
  proxy. Assembly writes one assembled module to every file
  `code_file_DAG` names; for the 8/10 repos with exactly one target file
  this is exact, for the 2 multi-file repos it over- rather than
  under-provides (see the module docstring).
- **RQ2 ground truth is derived, not hand-typed**
  (`harness/impact_injector_devbench.py`): because every UserStory maps to
  exactly one Operation/CodeEdit and no rule reads another story's data,
  the declared footprint of a change to one story is, by construction of
  the rules as written, exactly `{that operation}` -- for *every* repo and
  change, not a value hand-picked per task.
- **RQ3 is actually measured** (`harness/hot_reviewer_rq3.py`): adds a
  Security Reviewer via `agentm2m.team.hot.apply_hot` after each repo's
  first fixpoint and reports real retroactive-coverage percentages; "glue
  lines changed" is a one-time architectural comparison (the HOT call vs.
  the hand-written retrofit both baselines would need), not a per-repo
  measurement, since the integration cost doesn't vary by repo.
- **Independent-judge relabeling** (`harness/judge_relabel.py`) stands in
  for the unavailable human annotator: a 20% sample is re-judged by one of
  the *other two* pilot models (never self-judged), and agreement is
  reported as a limitation, not a substitute for real human validation.
