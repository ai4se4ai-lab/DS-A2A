#!/usr/bin/env bash
# More seeds for the context study (all four policies):
#   7B seed 3 (8 repos) after 7B seed 2 finishes; 27B seed 2 (4 repos) immediately.
set -u
cd "$(dirname "$0")/../../.."
R=evaluation/results/follow-up-study
PY=.venv/bin/python
M=evaluation.harness.follow_up_context_study
SMALL="ArXiv_digest chakin geotext hone lice particle-swarm-optimization readtime stocktrends"
LARGE="geotext chakin particle-swarm-optimization ArXiv_digest"
( $PY -m $M --llm ollama --model qwen3.8:27b --seed 2 --repos $LARGE --out-dir $R/raw > $R/logs/context_qwen3.8-27b_s2.log 2>&1 ) &
while pgrep -f "follow_up_context_study --llm ollama --model qwen2.5-coder:7b --seed 2 " >/dev/null; do sleep 30; done
$PY -m $M --llm ollama --model qwen2.5-coder:7b --seed 3 --repos $SMALL --out-dir $R/raw > $R/logs/context_qwen2.5-coder-7b_s3.log 2>&1
wait
