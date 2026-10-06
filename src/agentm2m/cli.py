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


def cmd_workspace(args: argparse.Namespace) -> int:
    from .workspace import Workspace, WorkspaceError, list_templates

    if args.ws_command == "templates":
        print("\n".join(list_templates()))
        return 0
    ws = Workspace(args.dir, backend=args.llm)
    try:
        if args.ws_command == "init":
            result = ws.init(args.template, force=args.force)
        elif args.ws_command == "validate":
            result = ws.validate()
            if args.brief:
                if result["ok"]:
                    print(f"agentm2m: workspace OK ({len(result['handoffs'])} hand-off(s))")
                else:
                    print("agentm2m: workspace INVALID\n" + "\n".join(f"  - {e}" for e in result["errors"]), file=sys.stderr)
                return 0 if result["ok"] else 1
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1
        elif args.ws_command == "status":
            result = ws.status()
            if args.brief:
                b = result["bindings"]
                print(
                    f"agentm2m: team {result['team']} -- {len(result['agents'])} agents, "
                    f"{len(result['handoffs'])} hand-offs, bindings {b}, phi={result['phi']}"
                )
                return 0
        elif args.ws_command == "run":
            result = ws.run()
        elif args.ws_command == "impact":
            result = ws.impact()
        elif args.ws_command == "events":
            result = ws.events(run_id=args.run, agent=args.agent, handoff=args.handoff, event_type=args.type,
                               limit=args.limit)
            if args.timeline:
                print(_timeline_text(result["events"]))
                return 0
        else:  # pragma: no cover - argparse enforces choices
            raise WorkspaceError(f"unknown command {args.ws_command}")
    except WorkspaceError as exc:
        print(f"agentm2m: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


def _timeline_text(events: list[dict]) -> str:
    """One line per event, like an execution trace."""
    import time as _time

    lines = []
    for e in events:
        ts = _time.strftime("%H:%M:%S", _time.localtime(e["ts"]))
        where = " ".join(x for x in (e.get("agent_id"), e.get("handoff_id"), e.get("rule_id"),
                                     e.get("target_key"), e.get("binding")) if x)
        ctx = f" ctx={','.join(e['context_ids'])}" if e.get("context_ids") else ""
        lines.append(f"{ts} {e['event_type']:<24} {where}{ctx}")
    return "\n".join(lines)


def _items_arg(path: str | None) -> list[dict] | None:
    if not path:
        return None
    raw = sys.stdin.read() if path == "-" else Path(path).read_text()
    data = json.loads(raw)
    return data if isinstance(data, list) else [data]


def _csv(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def cmd_context(args: argparse.Namespace) -> int:
    from .workspace import Workspace, WorkspaceError

    ws = Workspace(args.dir, backend="host")
    try:
        c = args.ctx_command
        if c == "list":
            result = ws.context_list(args.as_agent)
        elif c == "show":
            result = ws.context_get(args.context_id, args.as_agent, args.version)
        elif c == "create":
            result = ws.context_create(args.context_id, args.as_agent, args.title or "", _csv(args.readers),
                                       _csv(args.writers), args.visibility, _items_arg(args.items))
        elif c == "update":
            result = ws.context_update(args.context_id, args.as_agent, args.expected_version,
                                       _items_arg(args.items), _csv(args.remove), args.replace)
        elif c == "pull":
            result = ws.context_pull()
        elif c == "validate":
            result = ws.validate()
            result = {"ok": result["ok"], "errors": result["errors"], "warnings": result.get("warnings", []),
                      "contexts": ws.context_status()["contexts"]}
            print(json.dumps(result, indent=2, default=str))
            return 0 if result["ok"] else 1
        elif c == "graph":
            print(_context_graph(ws, args.context_id, args.format))
            return 0
        else:  # pragma: no cover
            raise WorkspaceError(f"unknown command {c}")
    except WorkspaceError as exc:
        print(f"agentm2m: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


def _context_graph(ws, only: str | None, fmt: str) -> str:
    """context -> item -> accepted binding -> downstream artifacts."""
    edges: list[tuple[str, str, str]] = []
    for c in ws.context_status()["contexts"]:
        cid = c["context_id"]
        if only and cid != only:
            continue
        ctx_node = f"ctx:{cid}"
        snap = ws.contexts.snapshot(cid)
        for item in snap.items:
            edges.append((ctx_node, f"item:{cid}#{item.id}", "contains"))
        inf = ws.influence_query(cid)
        for d in inf["direct"]:
            b = f"{d['element'] or d['target_key']}.{d['binding']}"
            edges.append((ctx_node, b, f"v{d['pinned_version']}" + ("" if d["current"] else " (stale)")))
        for h in inf["downstream"]:
            edges.append((h["via"], h["target"], h["handoff"]))
        if not snap.items and not inf["direct"]:
            edges.append((ctx_node, ctx_node, ""))
    if fmt == "dot":
        out = ["digraph agentm2m_context {", "  rankdir=LR;"]
        for a, b, label in dict.fromkeys(edges):
            if a == b:
                out.append(f'  "{a}";')
            else:
                out.append(f'  "{a}" -> "{b}" [label="{label}"];')
        out.append("}")
        return "\n".join(out)
    return "\n".join(f"{a} --{label}--> {b}" for a, b, label in dict.fromkeys(edges) if a != b) or "(no contexts)"


def cmd_nostr(args: argparse.Namespace) -> int:
    from .workspace import Workspace, WorkspaceError

    c = args.nostr_command
    if c == "serve":
        from .nostr.relay_server import DevRelayServer

        server = DevRelayServer(host=args.host, port=args.port)
        print(f"agentm2m dev relay on ws://{args.host}:{args.port} (Ctrl+C to stop)", file=sys.stderr)
        server.serve_forever()
        return 0
    ws = Workspace(args.dir, backend="host")
    try:
        if c == "status":
            result = ws.nostr_status()
        elif c == "identity":
            result = ws.agent_identity(args.agent)
        elif c == "keygen":
            result = ws.nostr_keygen(args.ref)
        elif c == "relays":
            result = {"enabled": ws.config.nostr.enabled, "relays": list(ws.config.nostr.relays),
                      "agent_relays": {a: i["nostr"]["relays"] for a, i in ws.agent_directory()["agents"].items()
                                       if i["nostr"]["relays"]}}
        elif c == "test":
            result = {"relays": [_probe(u, ws.config.nostr.timeout) for u in ws.config.nostr.relays]}
            print(json.dumps(result, indent=2))
            return 0 if result["relays"] and all(r["ok"] for r in result["relays"]) else 1
        elif c == "events":
            result = ws.nostr_events(run_id=args.run, agent=args.agent, handoff=args.handoff,
                                     event_type=args.type, limit=args.limit)
            if args.timeline:
                print(_timeline_text(result["events"]))
                return 0
        elif c == "flush":
            result = ws.nostr_flush(force=args.force)
        else:  # pragma: no cover
            raise WorkspaceError(f"unknown command {c}")
    except WorkspaceError as exc:
        print(f"agentm2m: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


def _probe(url: str, timeout: float) -> dict:
    import time as _time

    from .nostr.errors import NostrError
    from .nostr.relay import WebSocketRelay

    t0 = _time.monotonic()
    client = WebSocketRelay(url, timeout=timeout)
    try:
        client.query([{"kinds": [4930], "limit": 1}])
        return {"url": url, "ok": True, "latency_ms": round((_time.monotonic() - t0) * 1000, 1)}
    except NostrError as exc:
        return {"url": url, "ok": False, "error": str(exc)}
    finally:
        client.close()


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

    p_ws = sub.add_parser("workspace", help="manage a persistent .agentm2m/ team workspace")
    p_ws.add_argument("--dir", default=".", help="project directory containing .agentm2m/ (default: .)")
    p_ws.add_argument("--llm", default=None, help="host|mock|ollama|openai|anthropic (default: $AGENTM2M_LLM or host)")
    ws_sub = p_ws.add_subparsers(dest="ws_command", required=True)
    ws_sub.add_parser("templates", help="list built-in team templates")
    p_init = ws_sub.add_parser("init", help="create .agentm2m/ from a template")
    p_init.add_argument("template", nargs="?", default="devteam")
    p_init.add_argument("--force", action="store_true")
    for name in ("validate", "status"):
        p = ws_sub.add_parser(name)
        p.add_argument("--brief", action="store_true", help="one-line output (used by the Claude Code plugin hooks)")
    ws_sub.add_parser("run", help="run all hand-offs to a fixpoint")
    ws_sub.add_parser("impact", help="preview open obligations without any LLM call")
    p_ev = ws_sub.add_parser("events", help="query the local observability timeline")
    _event_filters(p_ev, limit=200)
    p_ws.set_defaults(func=cmd_workspace)

    p_ctx = sub.add_parser("context", help="shared context: list, show, create, update, validate, graph")
    p_ctx.add_argument("--dir", default=".", help="project directory containing .agentm2m/ (default: .)")
    ctx_sub = p_ctx.add_subparsers(dest="ctx_command", required=True)
    p = ctx_sub.add_parser("list", help="contexts (metadata only)")
    p.add_argument("--as", dest="as_agent", default=None)
    p = ctx_sub.add_parser("show", help="read a context as an authorized agent")
    p.add_argument("context_id")
    p.add_argument("--as", dest="as_agent", required=True)
    p.add_argument("--version", type=int, default=None)
    p = ctx_sub.add_parser("create", help="create a context owned by --as")
    p.add_argument("context_id")
    p.add_argument("--as", dest="as_agent", required=True)
    p.add_argument("--title", default="")
    p.add_argument("--readers", default="", help="comma-separated agents")
    p.add_argument("--writers", default="", help="comma-separated agents")
    p.add_argument("--visibility", default="private", choices=["private", "team", "relay"])
    p.add_argument("--items", default=None, help="JSON file with a list of items ('-' for stdin)")
    p = ctx_sub.add_parser("update", help="publish a new version (optimistic concurrency)")
    p.add_argument("context_id")
    p.add_argument("--as", dest="as_agent", required=True)
    p.add_argument("--expected-version", type=int, required=True)
    p.add_argument("--items", default=None, help="JSON file with items to upsert ('-' for stdin)")
    p.add_argument("--remove", default="", help="comma-separated item ids to remove")
    p.add_argument("--replace", action="store_true", help="replace all items")
    ctx_sub.add_parser("pull", help="apply verified context updates from the Nostr relays")
    ctx_sub.add_parser("validate", help="check context references and policies")
    p = ctx_sub.add_parser("graph", help="influence graph: context -> items -> bindings -> artifacts")
    p.add_argument("context_id", nargs="?", default=None)
    p.add_argument("--format", default="text", choices=["text", "dot"])
    p_ctx.set_defaults(func=cmd_context)

    p_n = sub.add_parser("nostr", help="Nostr observability: status, identity, keys, relays, events")
    p_n.add_argument("--dir", default=".", help="project directory containing .agentm2m/ (default: .)")
    n_sub = p_n.add_subparsers(dest="nostr_command", required=True)
    n_sub.add_parser("status", help="relays, engine key, outbox depth")
    p = n_sub.add_parser("identity", help="an agent's identity")
    p.add_argument("agent")
    p = n_sub.add_parser("keygen", help="create a signing key in .agentm2m/secrets/")
    p.add_argument("ref")
    n_sub.add_parser("relays", help="configured relays")
    n_sub.add_parser("test", help="check every configured relay answers")
    p = n_sub.add_parser("events", help="query and verify this workspace's events on the relays")
    _event_filters(p, limit=500)
    p = n_sub.add_parser("flush", help="publish queued events from the outbox")
    p.add_argument("--force", action="store_true", help="ignore backoff")
    p = n_sub.add_parser("serve", help="run a local development relay")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7777)
    p_n.set_defaults(func=cmd_nostr)

    return parser


def _event_filters(p: argparse.ArgumentParser, *, limit: int) -> None:
    p.add_argument("--run", default=None, help="run id")
    p.add_argument("--agent", default=None)
    p.add_argument("--handoff", default=None)
    p.add_argument("--type", default=None, help="event type, e.g. binding.accepted")
    p.add_argument("--limit", type=int, default=limit)
    p.add_argument("--timeline", action="store_true", help="one line per event instead of JSON")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
