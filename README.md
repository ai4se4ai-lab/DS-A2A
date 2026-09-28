# AgentM2M (DS-A2A)

**Towards Deterministic Structure for Stochastic Agents: M2M Transformations for Agentic Collaborations**

A prototype implementing the ICSE-NIER paper at [`docs/DS-A2A.tex`](docs/DS-A2A.tex).
The core idea: treat every hand-off between LLM agents in a team as a
**model-to-model (M2M) transformation** in the style of ATL. A deterministic
engine owns matching, element creation, and reference resolution; the LLM is
confined to **stochastic bindings** — sampled values for primitive
attributes, computed from a bounded **footprint**, and accepted only after a
`@check` validator passes. Trace links persist and drive **obligations**
when upstream models change; evolving the team at runtime (adding an agent)
is a **higher-order transformation** over a team model kept live
(models@run.time).

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how the code maps
onto the paper's Definition 1, Algorithm 1, and Propositions 1–3.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .            # core engine
pip install -e ".[eval]"    # + evaluation deps (pandas, matplotlib, datasets, swebench)
cp .env.example .env        # then edit LLM_PROVIDER / LLM_MODEL, see below
```

## LLM backend

Configured via `.env` (`python-dotenv`), pluggable at run time with
`--llm`/`--model` flags on every example/evaluation script:

- **`ollama` (default)** — a local model on `localhost:11434`. Run
  `ollama serve` and `ollama pull <model>` (e.g. `qwen3:8b`), or just run an
  example: `OllamaBackend` auto-detects and pulls the configured model.
- **`openai`** / **`anthropic`** — set `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`
  in `.env`.
- **`mock`** — deterministic, no network, no API key. Used by the test
  suite and by every example's `--llm mock` flag.

```bash
agentm2m llm-check                    # verify the configured backend is reachable
agentm2m llm-check --provider mock
```

## Quickstart

```bash
python examples/01_devteam/run.py --llm mock
```

Runs the paper's own running example — Analyst → Architect → Developer,
plus a direct Analyst → Tester hand-off — end to end, and prints the
generated models, the obligations discharged, and whether the acceptance
predicate `phi` holds.

## Repository layout

```
src/agentm2m/       the engine: metamodel/ (pyecore), rules/ (DSL parser),
                     engine/ (Algorithm 1, trace, validators, LLM binding),
                     llm/ (pluggable backends), team/ (megamodel, runtime, HOT)
examples/            several worked multi-agent collaboration scenarios,
                     each self-contained with its own metamodels, rules,
                     and README explaining what it demonstrates
evaluation/          scripts that fetch SWE-bench Lite and run the paper's
                     evaluation protocol (RQ1-RQ3) across three
                     configurations (free-text, shared-schema, AgentM2M),
                     producing CSVs and figures
tests/               pytest suite (parser, engine core, HOT, examples,
                     evaluation pipeline), all runnable offline with
                     the mock LLM backend
docs/                the paper (DS-A2A.tex) and ARCHITECTURE.md
```

Run everything (offline, no LLM backend needed):

```bash
python -m pytest tests/ -q
```

## Examples

| # | Scenario | Demonstrates |
|---|---|---|
| [`01_devteam`](examples/01_devteam/) | The paper's own running example: Analyst/Architect/Developer/Tester | Hybrid rules, trace-based reference resolution (Prop. 1), footprint-bounded prompting, validator-gated acceptance |
| [`02_devteam_change_propagation`](examples/02_devteam_change_propagation/) | Re-runs 01 after editing a requirement | Sound change impact (Prop. 2): obligations are exactly the affected bindings, nothing else re-invoked |
| [`03_security_reviewer_hot`](examples/03_security_reviewer_hot/) | Adds a Security Reviewer agent mid-run | Runtime team evolution via a higher-order transformation (Sec III-D): retroactive obligations, no hand-written glue |
| [`04_research_team`](examples/04_research_team/) | Literature-Reviewer / Experiment-Designer / Report-Writer | A multi-source (n:m) hand-off |
| [`05_incident_response_team`](examples/05_incident_response_team/) | Monitor / Triage / Remediation / Postmortem | `Lift` (text-to-model), an executable-oracle validator, a genuine escalation |
| [`06_baseline_comparison`](examples/06_baseline_comparison/) | The same toy task run 3 ways | Free-text vs. shared-schema vs. AgentM2M, side by side |

Each example directory has its own `README.md` explaining the scenario and
mapping it back to specific paper mechanisms.

## Evaluation

`evaluation/` reproduces the paper's Sec V protocol (RQ1: fidelity, RQ2:
change impact, RQ3: evolution) on SWE-bench Lite, comparing free-text,
shared-schema, and AgentM2M configurations under the same LLM and roles,
and regenerates Table II (`tab:pilot`) as CSVs, a LaTeX snippet, and
figures. See [`evaluation/README.md`](evaluation/README.md) for the quick
(offline, no Docker) vs. full (real SWE-bench, Docker-based official
evaluation, opt-in) modes.

## Data availability

The MAST failure-mode mapping (Table I) and its rationale, the DevTeam
metamodels and rules, and this prototype are all in this repository.
