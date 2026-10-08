#!/usr/bin/env bash
# Follow-on queue: after the seed-1 context studies finish, run the routed-paste arm for seed 1
# (7B: 8 repos, 27B: 4 repos) and then 7B seed 2 with all four policies.
set -u
cd "$(dirname "$0")/../../.."
R=evaluation/results/follow-up-study
PY=.venv/bin/python
M=evaluation.harness.follow_up_context_study
SMALL="ArXiv_digest chakin geotext hone lice particle-swarm-optimization readtime stocktrends"
LARGE="geotext chakin particle-swarm-optimization ArXiv_digest"
while pgrep -f "follow_up_context_study --llm ollama --model qwen2.5-coder:7b --seed 1 " >/dev/null; do sleep 30; done
$PY -m $M --llm ollama --model qwen2.5-coder:7b --seed 1 --policies routed --repos $SMALL --out-dir $R/raw > $R/logs/context_routed_qwen2.5-coder-7b_s1.log 2>&1
$PY -m $M --llm ollama --model qwen2.5-coder:7b --seed 2 --repos $SMALL --out-dir $R/raw > $R/logs/context_qwen2.5-coder-7b_s2.log 2>&1
