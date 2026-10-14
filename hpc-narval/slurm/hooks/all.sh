# Hook (sourced by stage.sh for every stage): a private Ollama server on this GPU node.
# The harness reads OLLAMA_BASE_URL (agentm2m.config.LLMConfig). The collect stage needs no LLM.
[ "${STAGE:-}" = "collect" ] && return 0
[ -x "$OLLAMA_PREFIX/bin/ollama" ] || { log_error "no ollama at $OLLAMA_PREFIX (set OLLAMA_PREFIX in cluster.env)"; return 1; }
mkdir -p "$OLLAMA_MODELS"
# Run the binary and its CUDA libraries from node-local disk: loading them from Lustre can exceed Ollama's
# 30 s GPU-discovery watchdog, after which it silently falls back to the CPU (seen on Narval).
_oll="${SLURM_TMPDIR:-$RUN_DIR/tmp}/ollama"
if [ ! -x "$_oll/bin/ollama" ]; then
    mkdir -p "$_oll" && tar -C "$OLLAMA_PREFIX" --exclude='lib/ollama/vulkan' -cf - bin lib | tar -C "$_oll" -xf - \
        || { log_error "could not copy ollama to $_oll"; return 1; }
fi
_port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
export OLLAMA_HOST="127.0.0.1:$_port" OLLAMA_MODELS OLLAMA_KEEP_ALIVE=24h OLLAMA_FLASH_ATTENTION=1 OLLAMA_VULKAN=0
export OLLAMA_NUM_PARALLEL OLLAMA_MAX_LOADED_MODELS
export OLLAMA_BASE_URL="http://$OLLAMA_HOST" PATH="$_oll/bin:$PATH"
# Start the server and require that it discovered the CUDA GPU (one restart, then fail: never run on the CPU).
for _try in 1 2; do
    _olog="$LOG_ROOT/env/ollama_${JOB_TAG}_r${SLURM_RESTART_COUNT:-0}_t${_try}.log"
    "$_oll/bin/ollama" serve > "$_olog" 2>&1 &
    _opid=$!
    curl -sf --retry 90 --retry-delay 2 --retry-connrefused --retry-all-errors -m 5 "$OLLAMA_BASE_URL/api/version" >/dev/null \
        || { log_error "ollama did not become ready"; kill "$_opid" 2>/dev/null; return 1; }
    timeout 300 bash -c "until grep -q 'inference compute' '$_olog'; do sleep 2; done"
    if grep -q 'inference compute.*library=CUDA' "$_olog"; then
        SERVICE_PIDS+=("$_opid"); break
    fi
    log_error "ollama found no CUDA GPU (try $_try): $(grep -m1 -o 'inference compute.*' "$_olog" | cut -c1-120)"
    kill "$_opid" 2>/dev/null; wait "$_opid" 2>/dev/null
    [ "$_try" = 2 ] && return 1
done
log_info "ollama ready on GPU at $OLLAMA_BASE_URL (models: $OLLAMA_MODELS; binary: $_oll)"
