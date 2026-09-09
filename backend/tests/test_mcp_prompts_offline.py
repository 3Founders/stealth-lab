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


def test_all_six_prompts_registered():
    assert _prompt_names() == _EXPECTED


def test_existing_29_tools_still_registered():
    assert len(srv.server._tool_manager.list_tools()) == 29


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
