"""
Offline tests for the additive MCP Prompt surface
(backend/app/mcp_server/prompts.py).

Prompts are orchestration policy, not business logic. These prove:
  * the 6 recommended-workflow prompts register with their exact names;
  * each renders from its arguments without error (required args present,
    optional args default cleanly);
  * every prompt body references at least one real current tool name, and
    the task-oriented ones also reference a stealth:// resource URI;
  * no prompt body uses an obsolete tool name;
  * no prompt body instructs unattended autonomous execution.

Same import-time os.environ guard as the other offline MCP tests.
"""
from __future__ import annotations

import os

_ENV_BEFORE = dict(os.environ)

import pytest

import app.mcp_server.server as srv
import app.mcp_server.prompts as prm

for _k in set(os.environ) - set(_ENV_BEFORE):
    del os.environ[_k]
for _k, _v in _ENV_BEFORE.items():
    if os.environ.get(_k) != _v:
        os.environ[_k] = _v


_EXPECTED = {
    "solve_with_stealth", "debug_with_stealth", "research_with_stealth",
    "improve_with_stealth", "verify_with_stealth", "contribute_learning",
    "survey_repo", "plan_and_run",  # the v1 pair; v2 (this process) registers everything
}
_REAL_TOOLS = {
    "search_procedures", "find_best_way", "check_applicability", "decompose_task",
    "resolve_implementation", "report_execution", "retrieve_precedent",
    "submit_procedure", "compare_solutions", "reproduce_procedure", "find_problem",
    "decide_procedure",
}
_OBSOLETE = ("solve_task", "apply_change_set", "retrieve_precedent_procedures")
_AUTONOMY_SMELLS = (
    "without asking", "automatically execute and commit", "do not ask the user",
    "commit without review", "act unattended",
)


def _prompt_names():
    return {p.name for p in srv.server._prompt_manager.list_prompts()}


def test_all_prompts_registered():
    assert _prompt_names() == _EXPECTED


def test_existing_tools_still_registered():
    # Was a hardcoded `== 29`, the exact count when MCP Prompts was added
    # -- real intent is "prompts didn't clobber the tool surface", not
    # "the tool count is frozen at 29 forever". The MCP hardening pass
    # (B1-B38) added 7 more real tools since; >= 29 keeps proving the
    # original intent without going stale every time a tool is added.
    assert len(srv.server._tool_manager.list_tools()) >= 29


@pytest.mark.parametrize("fn,args", [
    (prm.solve_with_stealth, ("migrate pandas append",)),
    (prm.solve_with_stealth, ("migrate pandas append", "/repo")),
    (prm.debug_with_stealth, ("flaky test",)),
    (prm.debug_with_stealth, ("flaky test", "/repo")),
    (prm.research_with_stealth, ("what verifies procedure X",)),
    (prm.improve_with_stealth, ()),
    (prm.improve_with_stealth, ("prob-1", "raise pass@1")),
    (prm.verify_with_stealth, ("proc-1",)),
    (prm.contribute_learning, ()),
    (prm.contribute_learning, ("did a novel migration",)),
])
def test_prompt_renders_from_args(fn, args):
    body = fn(*args)
    assert isinstance(body, str) and len(body) > 100


def test_task_prompts_reference_real_tools_and_resources():
    for fn in (prm.solve_with_stealth, prm.debug_with_stealth,
               prm.research_with_stealth, prm.verify_with_stealth):
        body = fn("x") if fn is not prm.research_with_stealth else fn("x")
        assert any(t in body for t in _REAL_TOOLS), f"{fn.__name__}: no real tool named"
        assert "stealth://" in body, f"{fn.__name__}: no resource URI referenced"


def test_all_prompts_reference_at_least_one_real_tool():
    for name, _title, fn in prm._PROMPTS:
        # call with all-defaults where possible, else a stub arg
        try:
            body = fn()
        except TypeError:
            body = fn("x")
        assert any(t in body for t in _REAL_TOOLS), f"{name}: no real tool named"


def test_no_prompt_uses_an_obsolete_name():
    for name, _title, fn in prm._PROMPTS:
        try:
            body = fn()
        except TypeError:
            body = fn("x")
        for bad in _OBSOLETE:
            assert bad not in body, f"{name} references obsolete {bad!r}"


def test_no_prompt_directs_autonomous_execution():
    for name, _title, fn in prm._PROMPTS:
        try:
            body = fn().lower()
        except TypeError:
            body = fn("x").lower()
        for smell in _AUTONOMY_SMELLS:
            assert smell not in body, f"{name} contains autonomy smell {smell!r}"


def test_prompt_descriptions_registered_nonempty():
    for p in srv.server._prompt_manager.list_prompts():
        assert (p.description or "").strip(), f"{p.name} has no description"


# --------------------------------------------------------------------------
# real SDK dispatch: server.get_prompt(name, arguments)
# --------------------------------------------------------------------------
import asyncio


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("name,args", [
    ("solve_with_stealth", {"task": "migrate pandas append", "repo_path": "/r"}),
    ("debug_with_stealth", {"symptom": "flaky test"}),
    ("research_with_stealth", {"question": "what verifies X"}),
    ("improve_with_stealth", {}),
    ("verify_with_stealth", {"procedure_id": "p-1"}),
    ("contribute_learning", {}),
])
def test_get_prompt_renders_through_sdk(name, args):
    result = _run(srv.server.get_prompt(name, args))
    assert (result.description or "").strip()
    msg = result.messages[0]
    text = msg.content.text if hasattr(msg.content, "text") else str(msg.content)
    assert len(text) > 100
    assert any(t in text for t in _REAL_TOOLS)


def test_get_prompt_unknown_name_raises():
    with pytest.raises(Exception):
        _run(srv.server.get_prompt("no_such_prompt", {}))


# --------------------------------------------------------------------------
# v1 pair: survey_repo + plan_and_run (final_architecture.md)
# --------------------------------------------------------------------------
_V1_NAMES = {"find_ways", "report_discovery"}
_V2_ONLY = ("find_best_way", "search_procedures", "init_workspace", "compile_goal", "execute_goal",
            "commit_local_sync", "inspect_run")


def test_v1_surface_gets_only_the_two_v1_prompts():
    assert [n for n, _, _ in prm.prompts_for_surface("v1")] == ["survey_repo", "plan_and_run"]
    assert {n for n, _, _ in prm.prompts_for_surface("v2")} == _EXPECTED


def test_v1_prompts_name_only_v1_tools():
    for body in (prm.survey_repo(), prm.plan_and_run("add a docx export")):
        for bad in _V2_ONLY:
            assert bad not in body, f"v1 prompt names v2 tool {bad!r}"
        for smell in _AUTONOMY_SMELLS:
            assert smell not in body.lower()
    assert "find_ways" in prm.plan_and_run("x") and "report_discovery" in prm.plan_and_run("x")


def test_survey_repo_states_the_claims_grammar_the_parser_reads():
    from app.execution.repo_facts import parse_repo_claims

    import re

    example = re.search(r"e\.g\. `(CLAIM\|[^`]+)`", prm.survey_repo("/repo")).group(1)
    claims, _ = parse_repo_claims(example)
    assert claims == [{"claim_id": "R-001", "statement": "Node 20.11", "topic": "runtime",
                       "scope": "repository", "source": ".nvmrc:1#sha=9f2c1ab"}]


def test_plan_and_run_has_the_tiny_packet_and_node_format():
    body = prm.plan_and_run("x")
    assert 'Do node N-3. Read: rg "N-3" .stealth/run.md' in body
    assert prm.RUN_MD_FORMAT in body
    assert "survey_repo" in body  # facts first
