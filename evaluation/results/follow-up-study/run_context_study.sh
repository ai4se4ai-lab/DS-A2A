#!/usr/bin/env bash
# Context / observability study (follow_up_context_study.py), one queue per model.
# usage: run_context_study.sh <model-tag> "<seeds>"
set -u
cd "$(dirname "$0")/../../.."
MODEL="$1"; SEEDS="${2:-1 2 3}"
for SEED in $SEEDS; do
  .venv/bin/python -m evaluation.harness.follow_up_context_study --llm ollama --model "$MODEL" \
     --seed "$SEED" --out-dir evaluation/results/follow-up-study/raw \
     > "evaluation/results/follow-up-study/logs/context_${MODEL//[:]/-}_s${SEED}.log" 2>&1
done
