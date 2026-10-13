# Hook (sourced by stage.sh for every stage): a private Ollama server on this GPU node.
# The harness reads OLLAMA_BASE_URL (agentm2m.config.LLMConfig). The collect stage needs no LLM.
[ "${STAGE:-}" = "collect" ] && return 0
[ -x "$OLLAMA_PREFIX/bin/ollama" ] || { log_error "no ollama at $OLLAMA_PREFIX (set OLLAMA_PREFIX in cluster.env)"; return 1; }
mkdir -p "$OLLAMA_MODELS"
_port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
export OLLAMA_HOST="127.0.0.1:$_port" OLLAMA_MODELS OLLAMA_KEEP_ALIVE=24h OLLAMA_FLASH_ATTENTION=1
export OLLAMA_NUM_PARALLEL OLLAMA_MAX_LOADED_MODELS
export OLLAMA_BASE_URL="http://$OLLAMA_HOST" PATH="$OLLAMA_PREFIX/bin:$PATH"
"$OLLAMA_PREFIX/bin/ollama" serve > "$LOG_ROOT/env/ollama_${JOB_TAG}_r${SLURM_RESTART_COUNT:-0}.log" 2>&1 &
SERVICE_PIDS+=($!)
curl -sf --retry 90 --retry-delay 2 --retry-connrefused --retry-all-errors -m 5 "$OLLAMA_BASE_URL/api/version" >/dev/null \
    || { log_error "ollama did not become ready"; return 1; }
log_info "ollama ready at $OLLAMA_BASE_URL (models: $OLLAMA_MODELS)"
