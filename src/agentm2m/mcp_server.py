"""MCP server exposing an AgentM2M workspace to Claude Code (stdio).

    agentm2m-mcp                    # serves <cwd>/.agentm2m
    AGENTM2M_PROJECT_DIR=/repo agentm2m-mcp
    AGENTM2M_LLM=host|mock|ollama|anthropic|openai   (default: host)

In the default *host* mode the engine never calls an LLM itself: stochastic
bindings come back from `next_bindings` as footprint-bounded prompts and
Claude Code answers them through `submit_binding`, where the same @check
validators decide acceptance. Every tool is a thin wrapper around
`agentm2m.workspace.Workspace`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .workspace import Workspace, WorkspaceError, list_templates

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # pragma: no cover - SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]

INSTRUCTIONS = """\
AgentM2M runs a team of agents whose hand-offs are model-to-model transformations.
Each agent owns one view model; a deterministic engine creates target elements,
references and trace links; only @llm attribute values need an LLM, and each is
accepted only if its @check validator passes. Typical loop: team_status ->
run -> next_bindings -> submit_binding (repeat) -> acceptance. For a change:
model_edit (as the owning agent) -> impact -> run. Answer a binding ONLY from the
prompt next_bindings returns: it already contains the whole allowed footprint
(and any authorized shared context, pinned to a version).
Shared context: agents publish findings/decisions with context_update (as an
authorized writer); bindings that declare the context see it in their prompt,
and a new version obliges exactly those bindings. Observability: every state
transition is an event (observability_events; nostr_* when a relay is set up).
"""


def _project_dir() -> Path:
    return Path(os.getenv("AGENTM2M_PROJECT_DIR") or os.getenv("CLAUDE_PROJECT_DIR") or os.getcwd())


_workspaces: dict[str, Workspace] = {}


def _ws() -> Workspace:
    key = str(_project_dir().resolve())
    if key not in _workspaces:
        _workspaces[key] = Workspace(key)
    return _workspaces[key]


def _call(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except WorkspaceError as exc:
        # A deliberate, actionable tool error (shown to the model verbatim),
        # not a crash with a traceback.
        raise ToolError(str(exc)) from None
    except Exception as exc:  # e.g. an OCL error in a user's rule module
        raise ToolError(f"{type(exc).__name__}: {exc}") from exc


mcp = _Server("agentm2m", instructions=INSTRUCTIONS)


@mcp.tool()
def team_init(template: str = "devteam", force: bool = False) -> dict:
    """Create the AgentM2M workspace (.agentm2m/) in the project from a template
    (devteam, research, incident). force=true replaces an existing workspace
    and discards its state."""
    return _call(_ws().init, template, force=force)


@mcp.tool()
def team_status() -> dict:
    """Team overview: agents and the views they own (write rights), hand-offs,
    trace links, binding states (fresh/missing/stale/escalated), open items,
    and whether the acceptance predicate phi holds."""
    ws = _ws()
    if not ws.exists():
        return {"workspace": None, "project_dir": str(ws.project_dir), "templates": list_templates(),
                "hint": "no .agentm2m/ workspace yet; call team_init"}
    return _call(ws.status)


@mcp.tool()
def team_validate() -> dict:
    """Validate team.yaml and every hand-off rule module: parsing, declared
    views and classes, root slots, helper paths, and that rules can match."""
    return _call(_ws().validate)


@mcp.tool()
def model_show(view: str, key: str | None = None, depth: int = 3) -> dict:
    """Show a view model (or one element by key, e.g. 'UserStory#S2').
    Elements created by hand-offs are marked engine_owned."""
    return _call(_ws().show, view, key, depth)


@mcp.tool()
def model_edit(view: str, ops: list[dict[str, Any]], as_agent: str) -> dict:
    """Edit a view as the agent that owns it (write rights are enforced; elements
    created by hand-offs are read-only). ops is a list of:
    {"op":"create","feature":"stories","parent":"<key, optional>","value":{attr: value, ref: "Type#id", containment: [{...}]}}
    {"op":"set","key":"Criterion#S2.1","values":{"text":"..."}}
    {"op":"delete","key":"UserStory#S3"}
    Atomic: if any op fails nothing changes."""
    return _call(_ws().edit, view, ops, as_agent)


@mcp.tool()
def impact(view: str | None = None, ops: list[dict[str, Any]] | None = None, as_agent: str | None = None) -> dict:
    """Preview Obl(Delta) without calling any LLM or changing anything: which
    stochastic bindings a change obliges to be re-sampled (and which elements
    would be created/deleted). Pass view+ops+as_agent to preview an edit
    before applying it; without ops it previews the current state."""
    return _call(_ws().impact, view, ops, as_agent)


@mcp.tool()
def run(max_passes: int | None = None) -> dict:
    """Run every hand-off to a fixpoint: match, create/delete targets, resolve
    references, update trace links. In host mode stale @llm bindings are
    returned as pending (fill them with next_bindings/submit_binding); with
    an engine backend they are sampled and validated directly."""
    return _call(_ws().run, max_passes)


@mcp.tool()
def next_bindings(agent: str | None = None, limit: int = 5) -> dict:
    """Host mode: the next @llm bindings to fill. Each carries its complete,
    footprint-bounded prompt; answer from that prompt only and submit the bare
    value with submit_binding. Optionally filter by owning agent."""
    return _call(_ws().next_bindings, agent, limit)


@mcp.tool()
def submit_binding(target_key: str, binding: str, value: str, footprint_version: str) -> dict:
    """Host mode: submit a value for one pending binding, passing the
    footprint_version that came with its prompt. Status is accepted, rejected
    (with reason and a retry_prompt; resubmit), escalated (validator kept
    rejecting: needs a source change or a human), already_accepted, or stale
    (the footprint changed since the prompt was issued: fetch it again)."""
    return _call(_ws().submit_binding, target_key, binding, value, footprint_version)


@mcp.tool()
def trace_query(key: str, transitive: bool = True) -> dict:
    """Trace links for an element key (e.g. 'Criterion#S2.1'): what it was
    generated from (upstream) and everything generated from it (downstream,
    transitively by default)."""
    return _call(_ws().trace_query, key, transitive)


@mcp.tool()
def team_evolve(
    agent: str,
    view: str,
    handoff: str,
    rule: str,
    view_spec: dict[str, Any] | None = None,
    view_spec_file: str | None = None,
    rule_text: str | None = None,
) -> dict:
    """Higher-order transformation: add a new agent owning a new view, connected
    by a new hand-off rule module, while the team is running. Give the view as
    view_spec (same shape as a view in team.yaml, without owner) or as a YAML
    file path relative to .agentm2m/. rule is a path relative to .agentm2m/;
    pass rule_text to create that file. Existing matches become obligations
    for the new agent on the next run."""
    ws = _ws()
    if view_spec is None:
        if not view_spec_file:
            raise ToolError("give view_spec or view_spec_file")
        p = (ws.dir / view_spec_file).resolve()
        try:
            p.relative_to(ws.dir.resolve())
        except ValueError:
            raise ToolError("view_spec_file must be inside .agentm2m/") from None
        if not p.is_file():
            raise ToolError(f"{view_spec_file} not found under {ws.dir}")
        view_spec = yaml.safe_load(p.read_text()) or {}
    return _call(ws.evolve, agent, view, view_spec, handoff, rule, rule_text)


@mcp.tool()
def acceptance() -> dict:
    """The acceptance predicate phi, decided by the engine: every match covered
    by a trace link and every @llm value accepted for its current footprint
    (nothing pending, stale, or escalated). Lists what is still open."""
    return _call(_ws().acceptance)


# ----------------------------------------------------------------------------
# Agents and shared context
# ----------------------------------------------------------------------------


@mcp.tool()
def agent_identity(agent: str) -> dict:
    """An agent's identity: view, display name, optional Nostr public key/npub,
    relays, and whether a local signing key is available (never the key)."""
    return _call(_ws().agent_identity, agent)


@mcp.tool()
def agent_directory() -> dict:
    """All agents with their view, write rights, Nostr identity and presence
    (offline/idle/working/waiting/blocked/failed, derived from the event timeline)."""
    return _call(_ws().agent_directory)


@mcp.tool()
def context_list(as_agent: str | None = None) -> dict:
    """Shared contexts (metadata only, never content): version, digest, owner,
    readers, item count; with as_agent, whether that agent may read/write each."""
    return _call(_ws().context_list, as_agent)


@mcp.tool()
def context_create(
    context_id: str,
    as_agent: str,
    title: str = "",
    readers: list[str] | None = None,
    writers: list[str] | None = None,
    visibility: str = "private",
    items: list[dict[str, Any]] | None = None,
) -> dict:
    """Create a shared context owned by as_agent. items: [{"id","type","content",
    optional "provenance": {"trace"|"element"|"view"|"handoff": ...}, "confidence", "scope"}].
    visibility: private (listed readers) | team (every agent reads) | relay (also published)."""
    return _call(_ws().context_create, context_id, as_agent, title, readers, writers, visibility, items)


@mcp.tool()
def context_get(context_id: str, as_agent: str, version: int | None = None) -> dict:
    """Read a context (latest, or a specific version) as an authorized reader.
    The read is recorded as an observable context.read event."""
    return _call(_ws().context_get, context_id, as_agent, version)


@mcp.tool()
def context_snapshot(context_id: str, as_agent: str, version: int) -> dict:
    """Read one immutable version of a context (old versions stay addressable)."""
    return _call(_ws().context_snapshot, context_id, as_agent, version)


@mcp.tool()
def context_update(
    context_id: str,
    as_agent: str,
    expected_version: int,
    items: list[dict[str, Any]] | None = None,
    remove: list[str] | None = None,
    replace: bool = False,
) -> dict:
    """Publish knowledge as an authorized writer: upsert items (by id), remove
    ids, or replace all. Fails with CONTEXT_CONFLICT if expected_version is not
    current (fetch, reconcile, retry). Returns the bindings the new version obliges."""
    return _call(_ws().context_update, context_id, as_agent, expected_version, items, remove, replace)


@mcp.tool()
def context_attach(context_id: str, agent: str, as_agent: str) -> dict:
    """The context owner (as_agent) grants `agent` read access."""
    return _call(_ws().context_attach, context_id, agent, as_agent)


@mcp.tool()
def context_detach(context_id: str, agent: str, as_agent: str) -> dict:
    """The context owner revokes a read grant made with context_attach."""
    return _call(_ws().context_detach, context_id, agent, as_agent)


@mcp.tool()
def context_search(query: str, as_agent: str, type: str | None = None, limit: int = 20) -> dict:
    """Search items of the contexts as_agent may read (case-insensitive)."""
    return _call(_ws().context_search, query, as_agent, type, limit)


@mcp.tool()
def context_status() -> dict:
    """Per context: version, owner, readers, how many bindings consume it and
    how many of those are stale; plus contexts referenced by rules but missing."""
    return _call(_ws().context_status)


@mcp.tool()
def influence_query(context_id: str, item_id: str | None = None, transitive: bool = True) -> dict:
    """Which accepted agent decisions were derived from this context (or item),
    at which pinned version, whether still current, and what was generated
    downstream from them through the trace links."""
    return _call(_ws().influence_query, context_id, item_id, transitive)


# ----------------------------------------------------------------------------
# Observability and Nostr
# ----------------------------------------------------------------------------


@mcp.tool()
def observability_events(
    run_id: str | None = None,
    agent: str | None = None,
    handoff: str | None = None,
    event_type: str | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int = 200,
) -> dict:
    """The local execution timeline: typed, correlated events (team, hand-off,
    binding, obligation, trace, context). Prompts and values appear only as digests."""
    return _call(_ws().events, run_id, agent, handoff, event_type, since, until, limit)


@mcp.tool()
def nostr_status() -> dict:
    """Nostr observability: enabled, relays, kinds, engine public key, outbox
    depth (events waiting for a relay), failures, privacy mode."""
    return _call(_ws().nostr_status)


@mcp.tool()
def nostr_publish(force: bool = False) -> dict:
    """Publish queued observability events from the outbox to the relays
    (force=true retries immediately, ignoring backoff)."""
    return _call(_ws().nostr_flush, force)


@mcp.tool()
def nostr_events(
    run_id: str | None = None,
    agent: str | None = None,
    handoff: str | None = None,
    event_type: str | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int = 500,
) -> dict:
    """Query this workspace's events from the relays. Every event is verified
    (signature, trusted author, schema, workspace) before it is returned."""
    return _call(_ws().nostr_events, run_id, agent, handoff, event_type, since, until, limit)


@mcp.tool()
def nostr_subscribe(seconds: float = 5.0, limit: int = 100) -> dict:
    """Listen on the relays for up to `seconds` (max 30) and return verified events."""
    return _call(_ws().nostr_subscribe, seconds, limit)


def main() -> None:
    import sys

    if "--version" in sys.argv:
        print(__version__)
        return
    mcp.run()


if __name__ == "__main__":
    main()
