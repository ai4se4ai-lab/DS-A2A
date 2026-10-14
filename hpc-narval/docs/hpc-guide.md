# How AgentM2M's DevBench study runs on HPC (Slurm, GPU nodes)

This guide covers the evaluation in `docs/sec_eval_revised.tex` (`\section{Preliminary Evaluation and Discussion}`, also in
`docs/DS-A2A.tex`): RQ1 fidelity, RQ2 change propagation and RQ3 evolution, on DevBench's Python repositories with
open-weight models served by Ollama. The target cluster is **Narval** (Alliance), which has A100-40GB GPUs and **no internet on compute nodes**.

Contents: 1 Big picture · 2 Source code on HPC · 3 Design to jobs · 4 Coverage matrix · 5 Anatomy of a job ·
6 Slurm and GPU techniques · 7 Auditing · 8 Reproducibility · 9 Results · 10 Operating · 11 Limitations

## 1. The big picture

The study compares three hand-off configurations, all run inside every cell:

- **free text**;
- **shared schema** (PatchBoard-style);
- **AgentM2M**.

They run with identical roles, prompts and LLM, across **2 models × 8 repositories = 16 cells**. Each cell writes 13 records:

- RQ1: 3 build runs (one per configuration);
- RQ2: 9 changes (3 injected changes × 3 configurations);
- RQ3: 1 HOT-added Security Reviewer run.

The full study therefore writes 208 records.

```
setup (1 job, a100:1, afterok gate)                   checks staged models, verifies DevBench references,
  │                                                    pings both models on the GPU, mock pre-flight
  ▼ afterok
devbench (array 0-15 %8, a100:1 each, requeue)        cell = repo × model; steps rq1 → rq2 → rq3
  │                                                    (devbench_run_all, one RQ per step)
  ▼ afterany
collect (1 job, a100_1g.5gb:1 MIG slice)              paper table + RQ1 decision rule, aggregate, figures,
                                                       generic CSV/MD/LaTeX tables, ledger, manifest
```

**Scale (measured in the smoke runs, 2026-10-13).** The `chakin` × qwen3.8:27b cell takes about 21 min on one A100,
including model load (9 operations including 5 canaries). Cell time grows roughly with the number of operations.
The 8 repositories total 118 operations, so the full study is about 2 × 118/9 × 21 min ≈ **9–10 GPU-hours**.
Add up to about 1 h of ArXiv_digest acceptance-test timeouts (§11). With 8 cells in parallel, the wall time is the
largest cells (hone and lice, 22 operations, about 1 h) plus the queue wait. A100 queue waits on Narval were 1–2 h
on the day of testing.

## 2. How the source code is used on HPC

| Repository path | Role in the study |
|---|---|
| `src/agentm2m/` (engine, metamodel, rules, team, llm) | imported (system under test); on `PYTHONPATH` via `SRC_DIRS` |
| `evaluation/harness/devbench_run_all.py` | run as a module by every devbench step (`--skip-rq*` selects one RQ per step) |
| `evaluation/harness/devbench_loader.py`, `devbench_common.py` | imported (task loading, test runner) |
| `evaluation/benchmarks/cache/devbench/python/` | DevBench Python subset (fetched by `evaluation/benchmarks/fetch_devbench.py`) |
| `evaluation/analysis/aggregate_devbench.py` | `collect/aggregate` |
| `evaluation/analysis/make_two_model_figures.py` | `collect/figures` |
| `hpc/scripts/paper_table.py` | `collect/paper_table`: Table `tab:prelim` and the RQ1 decision rule |
| `hpc/scripts/verify_devbench.py`, `llm_ping.py` | `setup` gates |

Outputs are redirected into `$RESULTS` with environment variables, without changing the code:

- `DEVBENCH_LOGS_DIR` controls the harness JSONL logs;
- `EVAL_OUT_DIR` controls the analysis outputs;
- `--stamp {repo}` gives each cell its own, stable log name, so reruns overwrite instead of duplicating.

The models are reached through `OLLAMA_BASE_URL`, which points to a private Ollama server on the job's own GPU
(`hpc/slurm/hooks/all.sh`).

## 3. From the document's experimental design to jobs

| Design element | Where configured |
|---|---|
| Subjects: 8 DevBench Python repos whose reference passes its own tests | `experiments.json` → `devbench.matrix.repo`, `vars.REQUIRE_REPOS` |
| Models: `qwen2.5-coder:7b`, `qwen3.8:27b` | `devbench.matrix.model`, `vars.MODELS_TO_PULL` |
| Configurations: free text / shared schema / AgentM2M | inside the harness (all three per cell) |
| Seed 0, temperature 0.2 | `vars.SEED`, `vars.TEMP` |
| 5 canaries per repo, 3 injected changes, 1 HOT reviewer | the harness (`devbench_run_all`) |
| Ollama serving: 1 request at a time, 1 model loaded | `cluster.env` (`OLLAMA_NUM_PARALLEL`, `OLLAMA_MAX_LOADED_MODELS`) |

**Why the graph looks like this:**

- `setup` is a hard `afterok` gate. If a reference implementation fails its own tests, or a model is missing or
  does not answer on the GPU, no GPU-hours are spent on the matrix.
- `devbench` cells are independent, so they run as an array, 8 at a time.
- `collect` uses `afterany`, so a failed cell never blocks aggregation. The figure script reports missing
  model × repository cells as a warning (see `make_two_model_figures.main`).

## 4. Coverage matrix: every claim → step → output → artefact

| # | Claim / measurement (doc ref) | Stage → step | Raw output (under `$RESULTS`) | Collected as | Document artefact | Decision rule checked in |
|---|---|---|---|---|---|---|
| 1 | Reference implementations pass their own tests (Prototype and benchmark) | setup → verify_reference | `setup/devbench_reference.json` | `raw/setup/` | subject selection | gate (exit 1) |
| 2 | RQ1 canary retention | devbench → rq1 | `devbench_logs/devbench_rq1_<model>_<repo>.jsonl` | `csv/rq1_runs.csv`, `tables/rq1_by_model_config.*` | `tab:prelim` row 1, `fig:prelim` | `analysis/rq1_decision.json` |
| 3 | RQ1 P1 modes per trace (MAST approximation) | devbench → rq1 | same | same (`mast_p1_count`) | `tab:prelim` row 2, `fig:prelim`(a) | — (descriptive) |
| 4 | RQ1 acceptance pass rate (DevBench's own tests) | devbench → rq1 | same | same (`acceptance_pass`) | `tab:prelim` row 3 | — |
| 5 | RQ1 tokens per task (Sustainability paragraph) | devbench → rq1 | same | same (`total_tokens`) | `fig:prelim`(c), Discussion | — |
| 6 | RQ2 impact recall / precision | devbench → rq2 | `devbench_logs/devbench_rq2_<model>_<repo>.jsonl` | `csv/rq2_changes.csv`, `tables/rq2_by_model_config.*` | `tab:prelim` row 4, `fig:prelim`(b) | `analysis/tab_results.*` |
| 7 | RQ2 tokens per change | devbench → rq2 | same | same (`tokens`) | `tab:prelim` row 5 | — |
| 8 | RQ3 glue lines, retroactive coverage | devbench → rq3 | `devbench_logs/devbench_rq3_<model>_<repo>.jsonl` | `csv/rq3_reviewer.csv`, `tables/rq3_by_model.*` | `tab:prelim` rows 6–7 | — |
| 9 | Results table (mean over models) | collect → paper_table | `analysis/tab_results.{csv,md,tex}` | `raw/analysis/` | `tab:prelim` | RQ1 rule in `analysis/rq1_decision.json` |
| 10 | Per-model / per-repo figures | collect → figures | `analysis/figures/devbench_two_model_*.{pdf,png}`, `analysis/csv/devbench_two_model_per_repo.csv` | `raw/analysis/` | `fig:prelim` (two-model version) | — |
| 11 | Pooled raw metrics and summary | collect → aggregate | `analysis/csv/devbench_raw_metrics_pooled.csv`, `devbench_summary_pooled.json` | `raw/analysis/`, `csv/rq1_runs.csv` | per-model breakdown | — |

**Not covered:**

- the paired 95% bootstrap of the RQ1 decision rule, which is computable only with several repositories
  (`paper_table.py` records the rule's inputs);
- the energy estimate (`\hyp` in the paper);
- the companion SWE-bench-Lite pilot;
- the paper's third model (see §11).

## 5. Anatomy of a job

`slurm/stage.sh` sources `slurm/common.sh`, which does the following in order:

1. `module load StdEnv/2023 python/3.11`.
2. Builds a virtualenv in `$SLURM_TMPDIR` from the Alliance wheelhouse plus `$WORK/wheels`. These are the wheels
   missing from the wheelhouse: pyecore, future-fstrings, pytest-json-report and pyquery (`--no-deps`).
3. Starts the GPU monitor (`nvidia-smi` every 60 s → `logs/<RUN_ID>/gpu/`).
4. Runs the hook `hooks/all.sh`. The hook copies Ollama to node-local disk, starts `ollama serve` on a free port,
   and **fails unless Ollama reports a CUDA GPU** (one restart, then exit).
5. Arms requeue on `USR1`, 900 s before the time limit.
6. Calls `scripts/run_stage.py`, which:
   - runs the cell's steps;
   - checks `needs` before each step and `produces` after it;
   - writes the ledger (`steps.tsv`) and per-step logs;
   - writes `.done` markers, so reruns and requeues skip finished steps;
   - retries steps per `retries`.

## 6. Slurm and GPU techniques used

| Technique | Where | What it buys |
|---|---|---|
| Job array with throttle (`--array=0-15%8`) | devbench | one cell per task, bounded share of the queue |
| `afterok` gate / `afterany` collection | dependencies | no wasted GPU-hours; collection even after failures |
| `--requeue --signal=B:USR1@900`, up to 20 requeues | devbench | long cells survive the 8 h limit; finished steps are skipped |
| Full A100 for the LLM stages, MIG `a100_1g.5gb` for collect | resources | 27B (17 GB plus KV cache) fits fully on the GPU; collect uses the smallest slice |
| Ollama binary on node-local disk | `hooks/all.sh` | avoids Lustre latency that made GPU discovery time out and fall back to the CPU |
| CUDA check of the Ollama server | `hooks/all.sh` | a cell can never run silently on the CPU |
| Offline staging | §10 | GPU nodes have no internet |
| Smoke mode (`--smoke`) | `experiments.json.smoke` | every stage, 2 cells, 1 h limits |

To look at a running job's GPU: `srun --jobid <JOBID> --overlap --pty watch -n 30 nvidia-smi`.

## 7. Auditing every phase

- **Intent and code identity:** `logs/<RUN_ID>/config/` (frozen `cluster.env`, `experiments.json`, resolved graph) and
  `run_manifest.json` (git commit, dirty state, content hashes of `src`, `evaluation/harness`, `evaluation/analysis`).
- **Environment:**
  - `logs/<RUN_ID>/env/pip_freeze_*.txt`;
  - `env/ollama_<job>_r<restart>_t<try>.log`, which contains Ollama's `inference compute ... library=CUDA` line.
- **Gates:** `setup/devbench_reference.json`, `setup/models.json`, `setup/llm_ping.json`.
- **Jobs and steps:** `jobs.tsv`, `steps.tsv`, `steps/<stage>/<cell>/<step>.log`, `slurm/*.out|err`.
- **Resources:** `gpu/gpu_<stage>_<job>_r<n>.csv`.
- **Live view:** `bash hpc/status.sh <RUN_ID>`, with `--only failed` or `--steps` for detail.

## 8. Reproducibility and integrity controls

- The configuration is frozen per run; later edits do not change a running study.
- The seed and temperature are fixed (`SEED=0`, `TEMP=0.2`).
- Model digests are recorded in `setup/models.json`; the Ollama version is fixed by `OLLAMA_PREFIX` (0.40.0).
- The DevBench tests are unmodified and must pass on the reference implementation before the matrix starts.
- Smoke runs are labelled `SMOKE: not for reporting` in `summary.md`.

## 9. How results are collected

`$RESULTS` = `$WORK/runs/<RUN_ID>/results` on scratch. `collect` copies the following to `hpc/logs/<RUN_ID>/results/`
on `/project`:

```
summary.md                   tables, figures and completeness of every cell
csv/                         rq1_runs, rq2_changes, rq3_reviewer and their summaries
tables/                      *.md and *.tex of the summaries
figures/                     fig_rq1_canaries, fig_rq2_precision, fig_rq2_tokens
raw/analysis/                tab_results.{csv,md,tex} (tab:prelim), rq1_decision.json, devbench_two_model_* figures,
                             pooled CSV/JSON
raw/devbench_logs/           every harness JSONL
raw/setup/                   gate reports
pipeline/                    jobs, steps (latest and all attempts), GPU usage, job endings, completeness
run_manifest.json
```

## 10. Operating the study

One-time staging from the **login node**, because the GPU nodes are offline. This is already done on Narval for
user mbabaei.

```bash
# Ollama 0.40.0 → $SCRATCH/autom2m_hpc/opt/ollama-0.40.0 (release tarball ollama-linux-amd64.tar.zst)
# Model weights → $WORK/ollama_models (a temporary `ollama serve` on the login node, then `ollama pull` for each model)
# Wheels missing from the wheelhouse → $WORK/wheels:
pip download --no-deps -d $WORK/wheels pyecore future-fstrings pytest-json-report pyquery
# DevBench data excluded by .gitignore (geotext/geotext/data_file/, lice/templates/), from open-compass/DevBench
```

Then:

```bash
bash hpc/scripts/selftest.sh
python3 hpc/slurm/submit.py --dry-run
python3 hpc/slurm/submit.py --smoke                          # must reach collect with every cell OK
python3 hpc/slurm/submit.py                                  # full study
bash hpc/status.sh <RUN_ID> [--only failed]
python3 hpc/slurm/submit.py --run-id <RUN_ID> --only devbench,collect   # repair; finished steps are skipped
python3 hpc/slurm/submit.py --run-id <RUN_ID> --only collect            # refresh results
```

## 11. Limitations and open points

- **Models differ from the paper.** The paper names qwen2.5-coder:7b, devstral:24b and qwen3-coder:30b. This
  configuration runs qwen2.5-coder:7b and qwen3.8:27b, matching the code's two-model figures. To add a model,
  extend `matrix.model` and `MODELS_TO_PULL`, and stage the weights.
- **Subjects.** 8 of the 10 Python repositories are used. TextCNN and Hybrid_Images fail their own reference tests
  in this environment, which is the paper's selection criterion.
- **ArXiv_digest acceptance tests need live internet** (`export.arxiv.org`). They cannot pass on Narval's GPU nodes,
  for the reference or for generated code, so ArXiv_digest's acceptance pass rate is not measurable here.
  The setup gate checks only its unit tests (`NETWORK_REPOS`). Its canary, MAST, RQ2 and RQ3 metrics are unaffected.
- **Ollama version.** Ollama 0.40.0 replaces 0.30.6, because qwen3.8 needs a newer server.
- **No statistics.** The RQ1 bootstrap rule is not computed (see §4). The energy estimate is still pending.
- **Storage.** About 24 GB of model weights on scratch; scratch is purged, so re-stage the weights if they disappear.
  Results are copied to `/project`.
