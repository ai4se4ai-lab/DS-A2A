"""Phase 13: `@llm(prompt, footprint, context=[...])` -- opt-in, backward compatible."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentm2m.rules.ast import ContextRefSpec, StochasticBinding
from agentm2m.rules.parser import parse_module, parse_module_file

REPO = Path(__file__).resolve().parents[2]

HEAD = "module M; create OUT : Code from IN : Arch;\n"


def _binding(llm: str) -> StochasticBinding:
    src = HEAD + f"rule R {{ from op : Arch!Operation to ce : Code!CodeEdit ( body <- {llm} ) }}"
    (b,) = parse_module(src).rules[0].to_clause.patterns[0].bindings
    assert isinstance(b, StochasticBinding)
    return b


def test_old_llm_syntax():
    b = _binding("@llm('Implement it.', op.signature)")
    assert b.context_refs == ()
    assert b.footprint_expr is not None


def test_new_llm_syntax_single_string():
    b = _binding("@llm('Implement it.', op.signature, context = 'security-review')")
    assert b.context_refs == (ContextRefSpec("security-review"),)


def test_context_argument_list_and_items():
    b = _binding("@llm('p', op.signature, context = ['security-review#finding-001', 'security-review#d-2'])")
    assert b.context_refs == (ContextRefSpec("security-review", ("finding-001", "d-2")),)


def test_multiple_contexts():
    b = _binding("@llm('p', op.signature, context=['security-review', 'product-roadmap'])")
    assert [r.context_id for r in b.context_refs] == ["security-review", "product-roadmap"]


def test_whole_context_wins_over_item_subset():
    b = _binding("@llm('p', op.signature, context=['sec#a', 'sec'])")
    assert b.context_refs == (ContextRefSpec("sec"),)


@pytest.mark.parametrize("bad", ["'has space'", "''", "['ok', 'bad id']", "'ctx#'", "op.signature"])
def test_invalid_context_reference(bad):
    with pytest.raises(Exception, match="(?i)context|unexpected"):
        _binding(f"@llm('p', op.signature, context = {bad})")


def test_context_reference_is_not_source_footprint():
    plain = _binding("@llm('p', op.signature)")
    ctx = _binding("@llm('p', op.signature, context='sec')")
    assert plain.footprint_expr == ctx.footprint_expr


def test_check_still_attaches_after_context_binding():
    src = HEAD + ("rule R { from op : Arch!Operation to ce : Code!CodeEdit ( "
                  "body <- @llm('p', op.signature, context=['sec']), @check body.compiles() ) }")
    (b,) = parse_module(src).rules[0].to_clause.patterns[0].bindings
    assert b.context_refs == (ContextRefSpec("sec"),) and b.check_expr is not None


def test_context_is_still_an_ordinary_identifier():
    src = ("module M; create OUT : Arch from IN : Req;\n"
           "rule R { from context : Req!Epic (context.name <> '') to c : Arch!Component ( name <- context.name ) }")
    rule = parse_module(src).rules[0]
    assert rule.from_clause.patterns[0].var == "context"


def test_every_shipped_rule_file_still_parses():
    files = [*REPO.glob("src/agentm2m/templates/**/*.agentm2m"), *REPO.glob("evaluation/harness/**/*.agentm2m"),
             *REPO.glob("examples/**/*.agentm2m")]
    assert files
    for f in files:
        module = parse_module_file(f)
        for rule in module.rules:
            for tp in rule.to_clause.patterns:
                for b in tp.bindings:
                    if isinstance(b, StochasticBinding):
                        assert b.context_refs == ()
