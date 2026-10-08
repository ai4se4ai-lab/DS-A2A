#!/usr/bin/env bash
# NIER-evaluation replication on every reference-valid DevBench Python repo
# (see csv/reference_validity.csv), 3 seeds, one queue per model.
# usage: run_nier_replication.sh <model-tag>
set -u
cd "$(dirname "$0")/../../.."
MODEL="$1"
REPOS="ArXiv_digest chakin geotext hone lice particle-swarm-optimization readtime stocktrends"
for SEED in 1 2 3; do
  .venv/bin/python -m evaluation.harness.devbench_run_all --llm ollama --model "$MODEL" \
     --seed "$SEED" --repos $REPOS --out-dir evaluation/results/follow-up-study/raw \
     > "evaluation/results/follow-up-study/logs/nier_${MODEL//[:]/-}_s${SEED}.log" 2>&1
done
