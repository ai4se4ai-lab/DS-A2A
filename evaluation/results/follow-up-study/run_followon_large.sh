#!/usr/bin/env bash
# 27B: routed-paste arm for seed 1 after the seed-1 context study finishes.
set -u
cd "$(dirname "$0")/../../.."
R=evaluation/results/follow-up-study
while pgrep -f "follow_up_context_study --llm ollama --model qwen3.8:27b --seed 1 " >/dev/null; do sleep 30; done
.venv/bin/python -m evaluation.harness.follow_up_context_study --llm ollama --model qwen3.8:27b --seed 1 --policies routed \
  --repos geotext chakin particle-swarm-optimization ArXiv_digest --out-dir $R/raw > $R/logs/context_routed_qwen3.8-27b_s1.log 2>&1
