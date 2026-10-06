# Baseline before the Nostr observability / shared-context work (0.2.0, 2026-10-05)

- `pytest tests plugin/tests -q`: 109 passed in ~11.3 s.
- `plugin/scripts/validate.sh`: every template validates; both suites pass.
- `ruff check .`: 403 pre-existing findings (43 in `src/ tests/ plugin/`). The gate
  for new work is that new or changed files add none.
- CLI: `agentm2m workspace {templates,init,validate,status,run,impact}`,
  `agentm2m validate`, `agentm2m trace show`, `agentm2m llm-check`.
- MCP tools: team_init, team_status, team_validate, model_show, model_edit, impact,
  run, next_bindings, submit_binding, trace_query, team_evolve, acceptance.
- Known 0.2.0 quirks pinned rather than fixed: `TraceLink.to_dict` re-serializes a
  reloaded dict footprint lossily (debug field only); the incident template does
  not reach phi with the mock backend.

`test_api_compat.py` turns these into executable checks; fixtures were generated
by 0.2.0 itself.
