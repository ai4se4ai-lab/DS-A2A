"""agentm2m CLI: small utilities around the engine.

Scenario orchestration itself (building metamodels, seeding models, wiring
a Team, running to a fixpoint) is Python code -- see any `examples/*/run.py`
-- because that's what building view metamodels and seed data actually
requires. The CLI covers the cross-cutting bits: checking a rule file
parses, inspecting a saved trace, and checking the configured LLM backend
is reachable before you burn a run on it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import LLMConfig
from .engine.trace import TraceModel
from .llm.factory import make_backend
from .rules.parser import parse_module_file


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.rule_file)
    try:
        module = parse_module_file(path)
    except Exception as exc:  # noqa: BLE001
        print(f"INVALID: {path}: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {path} -- module {module.name}, {len(module.rules)} rule(s):")
    for rule in module.rules:
        n_struct = sum(1 for tp in rule.to_clause.patterns for b in tp.bindings if hasattr(b, "expr"))
        n_stoch = sum(1 for tp in rule.to_clause.patterns for b in tp.bindings if hasattr(b, "prompt_expr"))
        print(f"  - {rule.name}: {n_struct} structural binding(s), {n_stoch} stochastic binding(s)")
    return 0


def cmd_trace_show(args: argparse.Namespace) -> int:
    path = Path(args.trace_file)
    trace = TraceModel.load(path)
    payload = {"handoff": trace.handoff, "links": [l.to_dict() for l in trace.links()]}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_llm_check(args: argparse.Namespace) -> int:
    cfg = LLMConfig.from_env()
    provider = args.provider or cfg.provider
    model = args.model or cfg.model
    print(f"provider={provider} model={model}")
    try:
        backend = make_backend(cfg, override_provider=provider, override_model=model)
        reply = backend.generate("Reply with the single word: ok", temperature=0.0)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"OK: backend responded ({len(reply)} chars): {reply[:80]!r}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentm2m")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="parse-check a .agentm2m rule file")
    p_validate.add_argument("rule_file")
    p_validate.set_defaults(func=cmd_validate)

    p_trace = sub.add_parser("trace", help="trace model utilities")
    trace_sub = p_trace.add_subparsers(dest="trace_command", required=True)
    p_trace_show = trace_sub.add_parser("show", help="pretty-print a saved trace model")
    p_trace_show.add_argument("trace_file")
    p_trace_show.set_defaults(func=cmd_trace_show)

    p_llm = sub.add_parser("llm-check", help="check the configured LLM backend is reachable")
    p_llm.add_argument("--provider", default=None)
    p_llm.add_argument("--model", default=None)
    p_llm.set_defaults(func=cmd_llm_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
