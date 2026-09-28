"""Sec V "Design": three hand-off configurations (free text, shared schema,
AgentM2M) run over the same tasks with the same pluggable LLM backend, plus
the supporting instrumentation (MAST annotator, token meter, impact
injector, proxy/Docker success evaluator) and the orchestrator
(run_all_configs.py) that ties them together into one JSONL log per run.
"""
